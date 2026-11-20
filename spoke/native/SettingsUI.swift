import AppKit
import SwiftUI

private let modifierKeys: [UInt16: (NSEvent.ModifierFlags, String)] = [
    54: (.command, "Правый ⌘"), 55: (.command, "⌘"), 56: (.shift, "⇧"),
    58: (.option, "⌥"), 59: (.control, "Левый Ctrl"), 60: (.shift, "Правый ⇧"), 61: (.option, "Правый ⌥"),
    62: (.control, "Правый Ctrl"), 63: (.function, "Fn")
]
private let shortcutModifiers: [(NSEvent.ModifierFlags, String)] = [
    (.control, "⌃"), (.shift, "⇧"), (.option, "⌥"), (.command, "⌘"), (.function, "Fn")
]
private let specialKeys: [UInt16: String] = [
    36: "Return", 48: "Tab", 49: "Пробел", 51: "Delete", 53: "Esc", 76: "Enter",
    114: "Help", 115: "Home", 116: "Page Up", 117: "⌦", 119: "End", 121: "Page Down",
    123: "←", 124: "→", 125: "↓", 126: "↑"
]
private let functionKeys: [UInt16] = [122, 120, 99, 118, 96, 97, 98, 100, 101, 109, 103, 111, 105, 107, 113, 106, 64, 79, 80, 90]
private let modifierMask = NSEvent.ModifierFlags(shortcutModifiers.map(\.0))
private let controlSideFlags: [UInt16: UInt] = [59: 0x01, 62: 0x2000]

struct ProviderUsage: Decodable, Equatable {
    let seconds: Double
    let input: Int
    let output: Int
    let usd: Double
    let configured: Bool
}

final class SettingsModel: ObservableObject {
    weak var delegate: NSObject?
    let version: String
    let appleAvailable: Bool

    @Published var hotkeyTitle: String
    @Published var capturing = false
    @Published var cleanupStatus: String
    @Published var dictationMode: String { didSet { send("dictation_mode", dictationMode) } }
    @Published var elevenlabsSaved = false
    @Published var anthropicSaved = false
    @Published var cloudMessage = ""
    @Published var apiUsage: [String: [String: ProviderUsage]] = [:]
    @Published var usageMessage = ""
    @Published var autostart: Bool { didSet { send("autostart", autostart) } }
    @Published var overlay: Bool { didSet { send("overlay", overlay) } }
    @Published var overlayMargin: Double { didSet { if overlayMargin.rounded() != oldValue.rounded() { send("overlay_bottom_margin", Int(overlayMargin)) } } }
    @Published var sounds: Bool { didSet { send("sounds", sounds) } }
    @Published var checkUpdates: Bool { didSet { send("check_updates_on_start", checkUpdates) } }
    @Published var accessibility = ""
    @Published var inputMonitoring = ""
    @Published var microphone = ""
    @Published var updateStatus = ""

    private var suppress = false
    private var monitor: Any?
    private var pendingKey: (code: UInt16, flags: NSEvent.ModifierFlags, title: String)?
    private var pendingModifier: (code: UInt16, flags: NSEvent.ModifierFlags, title: String)?

    init(json: [String: Any], delegate: NSObject) {
        self.delegate = delegate
        version = json["version"] as? String ?? ""
        appleAvailable = json["apple_available"] as? Bool ?? false
        hotkeyTitle = json["hotkey_title"] as? String ?? "Ctrl"
        cleanupStatus = json["cleanup_status"] as? String ?? ""
        dictationMode = json["dictation_mode"] as? String ?? "local"
        autostart = json["autostart"] as? Bool ?? true
        overlay = json["overlay"] as? Bool ?? true
        overlayMargin = json["overlay_bottom_margin"] as? Double ?? 14
        sounds = json["sounds"] as? Bool ?? false
        checkUpdates = json["check_updates_on_start"] as? Bool ?? true
    }

    func apply(_ json: [String: Any]) {
        suppress = true
        if let value = json["accessibility"] as? String, value != accessibility { accessibility = value }
        if let value = json["input_monitoring"] as? String, value != inputMonitoring { inputMonitoring = value }
        if let value = json["microphone"] as? String, value != microphone { microphone = value }
        if let value = json["update_status"] as? String, value != updateStatus { updateStatus = value }
        if let value = json["autostart"] as? Bool, value != autostart { autostart = value }
        if let value = json["overlay"] as? Bool, value != overlay { overlay = value }
        if let value = json["overlay_bottom_margin"] as? Double, value != overlayMargin { overlayMargin = value }
        if let value = json["sounds"] as? Bool, value != sounds { sounds = value }
        if let value = json["check_updates_on_start"] as? Bool, value != checkUpdates { checkUpdates = value }
        if let value = json["hotkey_title"] as? String, value != hotkeyTitle { hotkeyTitle = value }
        if let value = json["cleanup_status"] as? String, value != cleanupStatus { cleanupStatus = value }
        if let value = json["dictation_mode"] as? String, value != dictationMode { dictationMode = value }
        if let value = json["elevenlabs_saved"] as? Bool, value != elevenlabsSaved { elevenlabsSaved = value }
        if let value = json["anthropic_saved"] as? Bool, value != anthropicSaved { anthropicSaved = value }
        if let value = json["cloud_message"] as? String, value != cloudMessage { cloudMessage = value }
        if let value = json["api_usage"], let data = try? JSONSerialization.data(withJSONObject: value),
           let usage = try? JSONDecoder().decode([String: [String: ProviderUsage]].self, from: data), usage != apiUsage {
            apiUsage = usage
        }
        if let value = json["usage_message"] as? String, value != usageMessage { usageMessage = value }
        suppress = false
    }

    func send(_ key: String, _ value: Any) {
        guard !suppress else { return }
        _ = delegate?.perform(NSSelectorFromString("settingChanged:value:"), with: key as NSString, with: "\(value)" as NSString)
    }

    func act(_ name: String) {
        _ = delegate?.perform(NSSelectorFromString("settingAction:"), with: name as NSString)
    }

    func startCapture() {
        guard monitor == nil else { return }
        capturing = true
        act("capture_start")
        monitor = NSEvent.addLocalMonitorForEvents(matching: [.flagsChanged, .keyDown, .keyUp]) { [weak self] event in
            guard let self else { return event }
            self.handleCapture(event)
            return nil
        }
        if monitor == nil { stopCapture() }
    }

    func stopCapture() {
        guard capturing else { return }
        if let monitor { NSEvent.removeMonitor(monitor) }
        monitor = nil
        pendingKey = nil
        pendingModifier = nil
        capturing = false
        act("capture_stop")
    }

    private func handleCapture(_ event: NSEvent) {
        let code = event.keyCode
        let flags = event.modifierFlags.intersection(modifierMask)
        if event.type == .flagsChanged, pendingKey == nil, let (flag, title) = modifierKeys[code] {
            let rawFlags = event.modifierFlags.rawValue
            let controlSides = rawFlags & 0x2001
            let isPressed = controlSideFlags[code].map { controlSides != 0 ? rawFlags & $0 != 0 : flags.contains(flag) }
                ?? flags.contains(flag)
            if isPressed {
                pendingModifier = (code, flags.subtracting(flag), title)
            } else if let choice = pendingModifier, choice.code == code {
                accept(choice)
            }
        } else if event.type == .keyDown, !event.isARepeat, pendingKey == nil {
            let function = functionKeys.firstIndex(of: code).map { "F\($0 + 1)" }
            let character = event.charactersIgnoringModifiers?.trimmingCharacters(in: .controlCharacters)
            let title = specialKeys[code] ?? function ?? character.flatMap { $0.isEmpty ? nil : $0.uppercased() } ?? "Клавиша \(code)"
            let modifiers = function != nil || (114...126).contains(code) ? flags.subtracting(.function) : flags
            pendingKey = (code, modifiers, title)
        } else if event.type == .keyUp, let choice = pendingKey, choice.code == code {
            accept(choice)
        }
    }

    private func accept(_ choice: (code: UInt16, flags: NSEvent.ModifierFlags, title: String)) {
        let symbols = shortcutModifiers.filter { choice.flags.contains($0.0) }.map(\.1).joined()
        let title = symbols + choice.title
        send("hotkey", "key:\(choice.code):\(choice.flags.rawValue):\(title)")
        stopCapture()
    }
}

private enum Pane: String {
    case general, recognition, usage, overlay, system
}

private struct SettingsPane: View {
    @ObservedObject var model: SettingsModel
    let pane: Pane
    @State private var elevenlabsKey = ""
    @State private var anthropicKey = ""
    @State private var usagePeriod = "month"

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            switch pane {
            case .general: general
            case .recognition: recognition
            case .usage: usage
            case .overlay: overlay
            case .system: system
            }
        }
        .padding(.horizontal, 34)
        .padding(.vertical, 24)
        .frame(width: 536)
    }

    private var general: some View {
        Group {
            row("Клавиша диктовки") {
                Button(model.capturing ? "Отменить" : model.hotkeyTitle) {
                    if model.capturing { model.stopCapture() } else { model.startCapture() }
                }
                .buttonStyle(.bordered)
                .accessibilityLabel(model.capturing ? "Отменить выбор клавиши" : "Клавиша диктовки: \(model.hotkeyTitle). Изменить")
            }
            detail(model.capturing ? "Нажмите и отпустите клавишу или сочетание." : "Нажмите, чтобы назначить клавишу или сочетание.")
            Divider()
            option("Запускать при входе в систему", isOn: $model.autostart)
        }
    }

    private var recognition: some View {
        Group {
            row("Распознавание речи") {
                Picker("Распознавание речи", selection: $model.dictationMode) {
                    Text("Диктовка macOS + Cleanup").tag("local")
                    Text("Scribe v3 Live + Haiku").tag("cloud")
                }
                .labelsHidden()
            }
            Text("В облачном режиме звук отправляется в ElevenLabs, а распознанный текст — в Anthropic. Ключи хранятся в связке ключей macOS.")
                .font(.callout).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            Divider()
            keyRow("ElevenLabs", account: "elevenlabs", saved: model.elevenlabsSaved, key: $elevenlabsKey)
            keyRow("Anthropic", account: "anthropic", saved: model.anthropicSaved, key: $anthropicKey)
            Text(model.cloudMessage.isEmpty ? "Для облачного режима нужны оба ключа." : model.cloudMessage)
                .font(.callout).foregroundStyle(.secondary).lineLimit(1).help(model.cloudMessage)
        }
        .onDisappear { elevenlabsKey = ""; anthropicKey = "" }
    }

    private func keyRow(_ title: String, account: String, saved: Bool, key: Binding<String>) -> some View {
        HStack(spacing: 10) {
            Text(title).frame(width: 110, alignment: .trailing)
            SecureField(saved ? "Ключ сохранён" : "Ключ API", text: key)
                .accessibilityLabel("Ключ API " + title)
            Button("Сохранить") {
                model.send("api_key_" + account, key.wrappedValue)
                key.wrappedValue = ""
            }.disabled(key.wrappedValue.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            Button {
                model.act("remove_key_" + account)
            } label: {
                Image(systemName: "trash")
            }.disabled(!saved).accessibilityLabel("Удалить ключ " + title)
        }
    }

    private var usage: some View {
        let current = model.apiUsage[usagePeriod] ?? [:]
        let audio = current["elevenlabs"]
        let claude = current["anthropic"]
        return VStack(alignment: .leading, spacing: 14) {
            Picker("Период", selection: $usagePeriod) {
                Text("Этот месяц").tag("month")
                Text("За всё время").tag("all")
            }.pickerStyle(.segmented).labelsHidden()
            usageRow("ElevenLabs", meter: audio, url: URL(string: "https://elevenlabs.io/app/developers")!) {
                Text("\((audio?.seconds ?? 0) / 60, format: .number.precision(.fractionLength(2))) мин аудио")
            }
            Divider()
            usageRow("Anthropic", meter: claude, url: URL(string: "https://platform.claude.com/usage")!) {
                Text("Вход: \(claude?.input ?? 0, format: .number) · Выход: \(claude?.output ?? 0, format: .number) токенов")
            }
            Text(model.usageMessage.isEmpty
                 ? "Диктовки на этом Mac с текущими ключами. Суммы рассчитаны по тарифам. Баланс и списания — в кабинетах API."
                 : model.usageMessage)
                .font(.callout).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func usageRow(_ title: String, meter: ProviderUsage?, url: URL, detail: () -> Text) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(title).font(.headline)
                Spacer()
                if let meter, meter.configured {
                    Text(meter.usd, format: .currency(code: "USD").precision(.fractionLength(2...6)))
                        .monospacedDigit()
                }
            }
            HStack {
                if meter?.configured == true {
                    detail().monospacedDigit()
                } else {
                    Text("Ключ не настроен")
                }
                Spacer()
                Link("Кабинет API ↗", destination: url)
            }.font(.callout).foregroundStyle(.secondary)
        }
    }

    private var overlay: some View {
        Group {
            option("Показывать плашку записи", isOn: $model.overlay)
            row("Высота над Dock") {
                Slider(value: $model.overlayMargin, in: 0...80, step: 1).frame(width: 145).accessibilityLabel("Высота над Dock")
                Text("\(Int(model.overlayMargin)) пт").monospacedDigit().foregroundStyle(.secondary).frame(width: 42, alignment: .trailing)
            }
            .disabled(!model.overlay)
            detail("Когда Dock скрыт, высота отсчитывается от края экрана.")
            Divider()
            option("Звук в начале и конце записи", isOn: $model.sounds)
        }
    }

    private var system: some View {
        Group {
            Text("Разрешения").font(.headline).padding(.leading, 196)
            permission("Универсальный доступ", model.accessibility, "open_accessibility")
            permission("Мониторинг ввода", model.inputMonitoring, "open_input")
            permission("Микрофон", model.microphone, "open_microphone")
            Divider()
            row("Cleanup") { Text(model.cleanupStatus).foregroundStyle(.secondary) }
            Divider()
            row("Версия") { Text(model.version).foregroundStyle(.secondary) }
            row("Обновления") {
                Text(model.updateStatus).font(.callout).foregroundStyle(.secondary).lineLimit(1)
                Button("Проверить…") { model.act("update") }.buttonStyle(.bordered)
            }
            option("Проверять обновления при запуске", isOn: $model.checkUpdates)
            row("Журнал") { Button("Показать в Finder") { model.act("show_log") }.buttonStyle(.bordered) }
        }
    }

    private func row<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        HStack(spacing: 10) {
            Text(title).frame(width: 186, alignment: .trailing)
            HStack(content: content).frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func option(_ title: String, isOn: Binding<Bool>) -> some View {
        Toggle(title, isOn: isOn).toggleStyle(.checkbox).padding(.leading, 196)
    }

    private func detail(_ title: String) -> some View {
        Text(title).font(.callout).foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true).padding(.leading, 196)
    }

    private func permission(_ title: String, _ status: String, _ action: String) -> some View {
        row(title) {
            Text(status).font(.callout).foregroundStyle(.secondary)
            Button("Открыть…") { model.act(action) }.buttonStyle(.bordered).accessibilityLabel("Открыть настройки: \(title)")
        }
    }
}

@objc(SpokeSettingsBridge)
public final class SpokeSettingsBridge: NSObject {
    let model: SettingsModel

    @objc public init(config: String, delegate: NSObject) {
        let json = (try? JSONSerialization.jsonObject(with: Data(config.utf8))) as? [String: Any] ?? [:]
        model = SettingsModel(json: json, delegate: delegate)
        super.init()
    }

    @objc public func pane(_ identifier: String) -> NSView {
        let host = NSHostingView(rootView: SettingsPane(model: model, pane: Pane(rawValue: identifier) ?? .general))
        host.setFrameSize(host.fittingSize)
        return host
    }

    @objc public func stopCapture() {
        model.stopCapture()
    }

    @objc public func update(_ config: String) {
        let json = (try? JSONSerialization.jsonObject(with: Data(config.utf8))) as? [String: Any] ?? [:]
        model.apply(json)
    }
}
