import CoreML
import Foundation

private enum CleanupError: LocalizedError {
    case unavailable, invalidInput, noModel
    var errorDescription: String? {
        switch self {
        case .unavailable: return "Cleanup требует macOS 26 или новее."
        case .invalidInput: return "Неподходящий фрагмент для Cleanup."
        case .noModel: return "Модель Cleanup ещё не готова."
        }
    }
}

private struct CleanupInput: Decodable {
    let prompt: [Int32]
    let guesses: [Int32]
}

@available(macOS 26, *)
private final class CleanupModel {
    private static let lock = NSLock()
    private static var loaded: CleanupModel?
    private static let window = 512
    private static let batch = 16
    private static let eos: Int32 = 151643

    private let model: MLModel
    private let input: MLDictionaryFeatureProvider
    private let tokens: MLMultiArray
    private let positions: MLMultiArray
    private let answerRow: MLMultiArray
    private let cacheMask: MLMultiArray

    private init(path: String) throws {
        let config = MLModelConfiguration()
        config.computeUnits = .cpuAndNeuralEngine
        config.functionName = "everyRow512"
        model = try MLModel(contentsOf: URL(fileURLWithPath: path), configuration: config)
        tokens = try MLMultiArray(shape: [1, NSNumber(value: Self.batch)], dataType: .int32)
        positions = try MLMultiArray(shape: [NSNumber(value: Self.batch)], dataType: .int32)
        answerRow = try MLMultiArray(shape: [1], dataType: .int32)
        cacheMask = try MLMultiArray(shape: [1, 1, 1, NSNumber(value: Self.window)], dataType: .float16)
        input = try MLDictionaryFeatureProvider(dictionary: [
            "inputIds": tokens, "positions": positions, "answerRow": answerRow, "cacheMask": cacheMask
        ])
    }

    static func release() {
        lock.lock()
        defer { lock.unlock() }
        loaded = nil
    }

    static func prepare(path: String) throws {
        lock.lock()
        defer { lock.unlock() }
        if loaded == nil { loaded = try CleanupModel(path: path) }
    }

    static func generate(_ request: CleanupInput) throws -> [Int32] {
        lock.lock()
        defer { lock.unlock() }
        guard let loaded else { throw CleanupError.noModel }
        return try loaded.generate(request)
    }

    private func copyCache(_ rows: MLMultiArray, to cache: MLMultiArray, offset: Int, count: Int) {
        rows.withUnsafeBufferPointer(ofType: Float16.self) { source in
            cache.withUnsafeMutableBufferPointer(ofType: Float16.self) { destination, strides in
                let rowStrides = rows.strides.map(\.intValue)
                for layer in 0..<28 {
                    for head in 0..<8 {
                        let from = layer * rowStrides[0] + head * rowStrides[1]
                        let to = layer * strides[0] + head * strides[1] + offset * strides[2]
                        if rowStrides[2] == 128 && strides[2] == 128 && rowStrides[3] == 1 && strides[3] == 1 {
                            destination.baseAddress!.advanced(by: to).update(from: source.baseAddress!.advanced(by: from), count: count * 128)
                        } else {
                            for token in 0..<count {
                                for dim in 0..<128 {
                                    destination[to + token * strides[2] + dim * strides[3]] = source[from + token * rowStrides[2] + dim * rowStrides[3]]
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    private func step(_ ids: [Int32], offset: Int, state: MLState, prefill: Bool) throws -> (next: Int32, accepted: Int) {
        try autoreleasepool {
            tokens.withUnsafeMutableBufferPointer(ofType: Int32.self) { buffer, _ in
                for i in 0..<Self.batch { buffer[i] = i < ids.count ? ids[i] : Self.eos }
            }
            positions.withUnsafeMutableBufferPointer(ofType: Int32.self) { buffer, _ in
                for i in 0..<Self.batch { buffer[i] = Int32(offset + i) }
            }
            answerRow[0] = NSNumber(value: ids.count - 1)
            cacheMask.withUnsafeMutableBufferPointer(ofType: Float16.self) { buffer, _ in
                for i in 0..<Self.window { buffer[i] = i < offset ? 0 : -10000 }
            }
            let output = try model.prediction(from: input, using: state)
            guard let scores = output.featureValue(for: "nextTokens")?.multiArrayValue,
                  let keys = output.featureValue(for: "newKeys")?.multiArrayValue,
                  let values = output.featureValue(for: "newValues")?.multiArrayValue else {
                throw CleanupError.invalidInput
            }
            var accepted = prefill ? ids.count : 1
            if !prefill {
                while accepted < ids.count && scores[accepted - 1].int32Value == ids[accepted] {
                    accepted += 1
                }
            }
            state.withMultiArray(for: "keyCache") { copyCache(keys, to: $0, offset: offset, count: accepted) }
            state.withMultiArray(for: "valueCache") { copyCache(values, to: $0, offset: offset, count: accepted) }
            return (scores[accepted - 1].int32Value, accepted)
        }
    }

    private func generate(_ request: CleanupInput) throws -> [Int32] {
        let prompt = request.prompt
        let guesses = request.guesses
        guard !prompt.isEmpty, prompt.count + guesses.count < Self.window,
              (prompt + guesses).allSatisfy({ $0 >= 0 && $0 < Self.eos }) else { throw CleanupError.invalidInput }
        let limit = min(Self.window - prompt.count, max(64, guesses.count * 2 + 16))
        let state = model.makeState()
        var next = Self.eos
        var offset = 0
        for i in stride(from: 0, to: prompt.count, by: Self.batch) {
            let ids = Array(prompt[i..<min(i + Self.batch, prompt.count)])
            (next, _) = try step(ids, offset: offset, state: state, prefill: true)
            offset += ids.count
        }
        var output: [Int32] = []
        var costs = Array(0...guesses.count)
        var scratch = costs
        // Align the emitted tokens with the original text and let the model validate guesses.
        func align(_ token: Int32) -> Int {
            scratch[0] = costs[0] + 1
            for i in guesses.indices {
                scratch[i + 1] = min(costs[i + 1] + 1, scratch[i] + 1, costs[i] + (guesses[i] == token ? 0 : 1))
            }
            swap(&costs, &scratch)
            return costs.indices.min { costs[$0] < costs[$1] } ?? 0
        }
        while next != Self.eos && output.count < limit {
            let index = align(next)
            let tailCount = min(Self.batch - 1, limit - output.count - 1, guesses.count - index)
            let ids = [next] + guesses[index..<(index + tailCount)]
            let result = try step(ids, offset: offset, state: state, prefill: false)
            output.append(contentsOf: ids.prefix(result.accepted))
            for token in ids.dropFirst().prefix(result.accepted - 1) { _ = align(token) }
            next = result.next
            offset += result.accepted
        }
        guard next == Self.eos else { return [] }
        return output
    }
}

private func cleanupResponse(_ body: () throws -> [String: Any]) -> UnsafeMutablePointer<CChar>? {
    let value: [String: Any]
    do { value = try body() }
    catch { value = ["error": error.localizedDescription] }
    guard let data = try? JSONSerialization.data(withJSONObject: value) else { return nil }
    return strdup(String(decoding: data, as: UTF8.self))
}

@_cdecl("spoke_cleanup_prepare")
public func spokeCleanupPrepare(_ path: UnsafePointer<CChar>) -> UnsafeMutablePointer<CChar>? {
    cleanupResponse {
        guard #available(macOS 26, *) else { throw CleanupError.unavailable }
        try CleanupModel.prepare(path: String(cString: path))
        return [:]
    }
}

@_cdecl("spoke_cleanup_generate")
public func spokeCleanupGenerate(_ payload: UnsafePointer<CChar>) -> UnsafeMutablePointer<CChar>? {
    cleanupResponse {
        guard #available(macOS 26, *) else { throw CleanupError.unavailable }
        let request = try JSONDecoder().decode(CleanupInput.self, from: Data(String(cString: payload).utf8))
        return ["tokens": try CleanupModel.generate(request)]
    }
}

@_cdecl("spoke_cleanup_release")
public func spokeCleanupRelease() -> UnsafeMutablePointer<CChar>? {
    cleanupResponse {
        if #available(macOS 26, *) { CleanupModel.release() }
        return [:]
    }
}
