import AppKit
import SwiftUI

private let modifierKeys: [UInt16: (NSEvent.ModifierFlags, String)] = [
    54: (.command, "Правый ⌘"), 55: (.command, "⌘"), 56: (.shift, "⇧"),
    58: (.option, "⌥"), 59: (.control, "Ctrl"), 60: (.shift, "Правый ⇧"), 61: (.option, "Правый ⌥"),
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

final class SettingsModel: ObservableObject {
    weak var delegate: NSObject?
    let version: String

    @Published var hotkeyTitle: String
    @Published var capturing = false
    @Published var autostart: Bool { didSet { send("autostart", autostart) } }
    @Published var overlay: Bool { didSet { send("overlay", overlay) } }
    @Published var overlayMargin: Double { didSet { if overlayMargin.rounded() != oldValue.rounded() { send("overlay_bottom_margin", Int(overlayMargin)) } } }
    @Published var sounds: Bool { didSet { send("sounds", sounds) } }
    @Published var keepWarm: Bool { didSet {
        if oldValue != keepWarm { send("keep_warm_seconds", keepWarm ? warmInterval : 0) }
    } }
    @Published var checkUpdates: Bool { didSet { send("check_updates_on_start", checkUpdates) } }
    @Published var accessibility = ""
    @Published var inputMonitoring = ""
    @Published var microphone = ""
    @Published var updateStatus = ""

    private let warmInterval: Int
    private var suppress = false
    private var monitor: Any?
    private var pendingKey: (code: UInt16, flags: NSEvent.ModifierFlags, title: String)?
    private var pendingModifier: (code: UInt16, flags: NSEvent.ModifierFlags, title: String)?

    init(json: [String: Any], delegate: NSObject) {
        self.delegate = delegate
        version = json["version"] as? String ?? ""
        hotkeyTitle = json["hotkey_title"] as? String ?? "Ctrl"
        autostart = json["autostart"] as? Bool ?? true
        overlay = json["overlay"] as? Bool ?? true
        overlayMargin = json["overlay_bottom_margin"] as? Double ?? 14
        sounds = json["sounds"] as? Bool ?? false
        let interval = json["keep_warm_seconds"] as? Int ?? 45
        warmInterval = interval > 0 ? interval : 45
        keepWarm = interval > 0
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
        if let value = json["keep_warm_seconds"] as? Int, (value > 0) != keepWarm { keepWarm = value > 0 }
        if let value = json["check_updates_on_start"] as? Bool, value != checkUpdates { checkUpdates = value }
        if let value = json["hotkey_title"] as? String, value != hotkeyTitle { hotkeyTitle = value }
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
            if event.modifierFlags.contains(flag) {
                pendingModifier = (code, flags.subtracting(flag), title)
            } else if let choice = pendingModifier {
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
    case general, overlay, system
}

private struct SettingsPane: View {
    @ObservedObject var model: SettingsModel
    let pane: Pane

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            switch pane {
            case .general: general
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
            option("Ускорять диктовку после простоя", isOn: $model.keepWarm)
            detail("Spoke периодически прогревает модель, чтобы первая фраза после паузы обработалась быстрее.")
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
