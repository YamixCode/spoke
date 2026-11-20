import Foundation
import Security

private struct CloudError: LocalizedError {
    let errorDescription: String?
    init(_ message: String) { errorDescription = message }
}

private func cloudResponse(_ body: () throws -> [String: Any]) -> UnsafeMutablePointer<CChar>? {
    let value: [String: Any]
    do { value = try body() }
    catch { value = ["error": error.localizedDescription] }
    guard let data = try? JSONSerialization.data(withJSONObject: value) else { return nil }
    return strdup(String(decoding: data, as: UTF8.self))
}

private func keychainError(_ status: OSStatus) -> CloudError {
    let detail = SecCopyErrorMessageString(status, nil) as String? ?? String(status)
    return CloudError("Связка ключей: \(detail)")
}

private final class ScribeSessionDelegate: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}

@_cdecl("spoke_cloud_key")
public func spokeCloudKey(_ operation: UnsafePointer<CChar>, _ provider: UnsafePointer<CChar>,
                          _ value: UnsafePointer<CChar>) -> UnsafeMutablePointer<CChar>? {
    cloudResponse {
        let account = String(cString: provider)
        guard ["elevenlabs", "anthropic"].contains(account) else { throw CloudError("Неизвестный поставщик API") }
        let query: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
                                    kSecAttrService as String: "local.spoke.app.cloud",
                                    kSecAttrAccount as String: account]
        let op = String(cString: operation)
        if op == "read" {
            var result: CFTypeRef?
            let status = SecItemCopyMatching(query.merging([kSecReturnData as String: true]) { _, value in value } as CFDictionary, &result)
            if status == errSecItemNotFound { return ["value": ""] }
            guard status == errSecSuccess, let data = result as? Data else { throw keychainError(status) }
            return ["value": String(decoding: data, as: UTF8.self)]
        }
        if op == "remove" {
            let status = SecItemDelete(query as CFDictionary)
            guard status == errSecSuccess || status == errSecItemNotFound else { throw keychainError(status) }
            return [:]
        }
        let data = [kSecValueData as String: Data(String(cString: value).utf8)]
        var status = SecItemUpdate(query as CFDictionary, data as CFDictionary)
        if status == errSecItemNotFound {
            status = SecItemAdd(query.merging(data) { _, value in value } as CFDictionary, nil)
        }
        guard status == errSecSuccess else { throw keychainError(status) }
        return [:]
    }
}

private final class ScribeSocket {
    private static let registryLock = NSLock()
    private static var sockets: [String: ScribeSocket] = [:]
    private static let session = URLSession(configuration: .ephemeral, delegate: ScribeSessionDelegate(), delegateQueue: nil)
    private let condition = NSCondition()
    private let task: URLSessionWebSocketTask
    private var ready = false
    private var failure: String?
    private var parts: [String] = []
    private var commits = 0
    private var partial = ""
    private var lastCommit = Date.distantPast

    init(key: String) throws {
        var request = URLRequest(url: URL(string: "wss://api.elevenlabs.io/v1/speech-to-text/realtime?model_id=scribe_v3_live&audio_format=pcm_16000&commit_strategy=manual&multilang_mode=true")!)
        request.setValue(key, forHTTPHeaderField: "xi-api-key")
        task = Self.session.webSocketTask(with: request)
        task.resume()
        receive()
        condition.lock()
        defer { condition.unlock() }
        let deadline = Date(timeIntervalSinceNow: 8)
        while !ready, failure == nil, condition.wait(until: deadline) {}
        guard ready, failure == nil else {
            task.cancel(with: .goingAway, reason: nil)
            throw CloudError(failure ?? "ElevenLabs не подтвердил начало записи")
        }
    }

    static func open(key: String) throws -> String {
        let socket = try ScribeSocket(key: key)
        let id = UUID().uuidString
        registryLock.lock()
        sockets[id] = socket
        registryLock.unlock()
        return id
    }

    static func find(_ id: String) throws -> ScribeSocket {
        registryLock.lock()
        defer { registryLock.unlock() }
        guard let socket = sockets[id] else { throw CloudError("Сессия ElevenLabs закрыта") }
        return socket
    }

    static func close(_ id: String) {
        registryLock.lock()
        let socket = sockets.removeValue(forKey: id)
        registryLock.unlock()
        socket?.task.cancel(with: .goingAway, reason: nil)
    }

    private func receive() {
        task.receive { [weak self] result in
            guard let self else { return }
            self.condition.lock()
            var again = true
            switch result {
            case .success(.string(let value)):
                if let message = try? JSONSerialization.jsonObject(with: Data(value.utf8)) as? [String: Any],
                   let type = message["message_type"] as? String {
                    switch type {
                    case "session_started": self.ready = true
                    case "partial_transcript": self.partial = message["text"] as? String ?? ""
                    case "committed_transcript":
                        let text = (message["text"] as? String ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
                        if !text.isEmpty { self.parts.append(text) }
                        self.commits += 1
                        self.partial = ""
                        self.lastCommit = Date()
                    default:
                        if message["error"] != nil {
                            self.failure = "ElevenLabs: \(type)"
                            again = false
                        }
                    }
                }
            case .failure(let error):
                self.failure = "Соединение ElevenLabs прервано (\((error as NSError).code))"
                again = false
            default: break
            }
            self.condition.broadcast()
            self.condition.unlock()
            if again { self.receive() }
        }
    }

    func send(_ json: String) throws {
        let done = DispatchSemaphore(value: 0)
        task.send(.string(json)) { [weak self] error in
            if let self, let error {
                self.condition.lock()
                if self.failure == nil { self.failure = "Не удалось отправить аудио в ElevenLabs (\((error as NSError).code))" }
                self.condition.broadcast()
                self.condition.unlock()
            }
            done.signal()
        }
        guard done.wait(timeout: .now() + 5) == .success else { throw CloudError("ElevenLabs не принимает аудио") }
        condition.lock()
        defer { condition.unlock() }
        if let failure { throw CloudError(failure) }
    }

    func finish(_ json: String) throws -> String {
        condition.lock()
        let previous = commits
        let alreadyCommitted = partial.isEmpty && !parts.isEmpty && Date().timeIntervalSince(lastCommit) < 1.5
        condition.unlock()
        try send(json)
        condition.lock()
        defer { condition.unlock() }
        let deadline = Date(timeIntervalSinceNow: alreadyCommitted ? 2 : 7)
        while failure == nil, Date() < deadline {
            if commits > previous, partial.isEmpty {
                let settle = lastCommit.addingTimeInterval(0.12)
                if Date() >= settle { break }
                _ = condition.wait(until: min(deadline, settle))
            } else {
                _ = condition.wait(until: deadline)
            }
        }
        if let failure { throw CloudError(failure) }
        guard partial.isEmpty, commits > previous || alreadyCommitted else {
            throw CloudError("ElevenLabs не завершил распознавание")
        }
        return parts.joined(separator: " ")
    }
}

@_cdecl("spoke_scribe_open")
public func spokeScribeOpen(_ key: UnsafePointer<CChar>) -> UnsafeMutablePointer<CChar>? {
    cloudResponse { ["id": try ScribeSocket.open(key: String(cString: key))] }
}

@_cdecl("spoke_scribe_send")
public func spokeScribeSend(_ id: UnsafePointer<CChar>, _ json: UnsafePointer<CChar>) -> UnsafeMutablePointer<CChar>? {
    cloudResponse {
        try ScribeSocket.find(String(cString: id)).send(String(cString: json))
        return [:]
    }
}

@_cdecl("spoke_scribe_finish")
public func spokeScribeFinish(_ id: UnsafePointer<CChar>, _ json: UnsafePointer<CChar>) -> UnsafeMutablePointer<CChar>? {
    cloudResponse { ["text": try ScribeSocket.find(String(cString: id)).finish(String(cString: json))] }
}

@_cdecl("spoke_scribe_close")
public func spokeScribeClose(_ id: UnsafePointer<CChar>) -> UnsafeMutablePointer<CChar>? {
    cloudResponse {
        ScribeSocket.close(String(cString: id))
        return [:]
    }
}
