import AVFAudio
import Foundation
import Speech

private enum AppleSpeechError: LocalizedError {
    case unsupportedSystem
    case unsupportedLanguage
    case assetsUnavailable

    var errorDescription: String? {
        switch self {
        case .unsupportedSystem: return "Диктовка macOS требует macOS 26 или новее."
        case .unsupportedLanguage: return "Для предпочитаемых языков нет системной диктовки."
        case .assetsUnavailable: return "Языковая модель macOS недоступна. Проверьте подключение к интернету для первой загрузки."
        }
    }
}

private func appleSpeechResult(_ operation: @escaping @Sendable () async throws -> String) -> UnsafeMutablePointer<CChar>? {
    let finished = DispatchSemaphore(value: 0)
    var response: [String: String] = [:]
    Task.detached(priority: .userInitiated) {
        defer { finished.signal() }
        do {
            response = ["text": try await operation()]
        } catch {
            response = ["error": error.localizedDescription]
        }
    }
    finished.wait()
    guard let data = try? JSONSerialization.data(withJSONObject: response),
          let encoded = String(data: data, encoding: .utf8) else { return nil }
    return strdup(encoded)
}

@available(macOS 26, *)
private func prepareAppleSpeech() async throws -> String {
    for identifier in Locale.preferredLanguages {
        guard let locale = await DictationTranscriber.supportedLocale(equivalentTo: Locale(identifier: identifier)) else { continue }
        let module = DictationTranscriber(locale: locale, preset: .longDictation)
        if await AssetInventory.status(forModules: [module]) != .installed {
            if let installation = try await AssetInventory.assetInstallationRequest(supporting: [module]) {
                try await installation.downloadAndInstall()
            }
            guard await AssetInventory.status(forModules: [module]) == .installed else {
                throw AppleSpeechError.assetsUnavailable
            }
        }
        return locale.identifier
    }
    throw AppleSpeechError.unsupportedLanguage
}

@available(macOS 26, *)
private func recognizeAppleSpeech(path: String, locale: String) async throws -> String {
    let audio = try AVAudioFile(forReading: URL(fileURLWithPath: path))
    let transcriber = DictationTranscriber(locale: Locale(identifier: locale), preset: .longDictation)
    let analyzer = SpeechAnalyzer(modules: [transcriber])
    async let transcript = collectAppleSpeech(transcriber)
    do {
        if let end = try await analyzer.analyzeSequence(from: audio) {
            try await analyzer.finalizeAndFinish(through: end)
        } else {
            await analyzer.cancelAndFinishNow()
        }
    } catch {
        await analyzer.cancelAndFinishNow()
        throw error
    }
    return try await transcript
}

@available(macOS 26, *)
private func collectAppleSpeech(_ transcriber: DictationTranscriber) async throws -> String {
    var parts: [String] = []
    for try await result in transcriber.results where result.isFinal {
        let text = String(result.text.characters).trimmingCharacters(in: .whitespacesAndNewlines)
        if !text.isEmpty { parts.append(text) }
    }
    return parts.joined(separator: " ")
}

@_cdecl("spoke_apple_prepare")
public func spokeApplePrepare() -> UnsafeMutablePointer<CChar>? {
    appleSpeechResult {
        guard #available(macOS 26, *) else { throw AppleSpeechError.unsupportedSystem }
        return try await prepareAppleSpeech()
    }
}

@_cdecl("spoke_apple_transcribe")
public func spokeAppleTranscribe(_ path: UnsafePointer<CChar>, _ locale: UnsafePointer<CChar>) -> UnsafeMutablePointer<CChar>? {
    let filename = String(cString: path)
    let language = String(cString: locale)
    return appleSpeechResult {
        guard #available(macOS 26, *) else { throw AppleSpeechError.unsupportedSystem }
        return try await recognizeAppleSpeech(path: filename, locale: language)
    }
}

@_cdecl("spoke_apple_free")
public func spokeAppleFree(_ result: UnsafeMutableRawPointer) {
    free(result)
}
