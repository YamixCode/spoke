import AppKit
import SwiftUI

final class SettingsModel: ObservableObject {
    struct Option: Hashable { let key: String; let title: String }

    var suppress = true
    weak var delegate: NSObject?
    let hotkeys: [Option]
    let languages: [Option]
    let version: String

    @Published var hotkey: String { didSet { send("hotkey", hotkey) } }
    @Published var language: String { didSet { send("language", language) } }
    @Published var holdMs: Double { didSet { if holdMs.rounded() != oldValue.rounded() { send("hold_ms", Int(holdMs)) } } }
    @Published var doubleTapMs: Double { didSet { if doubleTapMs.rounded() != oldValue.rounded() { send("double_tap_ms", Int(doubleTapMs)) } } }
    @Published var historySize: Int { didSet { send("history_size", historySize) } }
    @Published var autoSpace: Bool { didSet { send("auto_space", autoSpace) } }
    @Published var autostart: Bool { didSet { send("autostart", autostart) } }
    @Published var overlay: Bool { didSet { send("overlay", overlay) } }
    @Published var overlayMargin: Double { didSet { if overlayMargin.rounded() != oldValue.rounded() { send("overlay_bottom_margin", Int(overlayMargin)) } } }
    @Published var sounds: Bool { didSet { send("sounds", sounds) } }
    @Published var streaming: Bool { didSet { send("streaming", streaming) } }
    @Published var keepWarm: Int { didSet { send("keep_warm_seconds", keepWarm) } }
    @Published var checkUpdates: Bool { didSet { send("check_updates_on_start", checkUpdates) } }
    @Published var vocabulary: String { didSet { send("vocabulary", vocabulary) } }
    @Published var accessibility = ""
    @Published var inputMonitoring = ""
    @Published var microphone = ""
    @Published var updateStatus = ""

    init(json: [String: Any], delegate: NSObject) {
        func options(_ name: String) -> [Option] {
            (json[name] as? [[String]] ?? []).map { Option(key: $0[0], title: $0[1]) }
        }
        self.delegate = delegate
        hotkeys = options("hotkeys")
        languages = options("languages")
        version = json["version"] as? String ?? ""
        hotkey = json["hotkey"] as? String ?? "ctrl"
        language = json["language"] as? String ?? "ru"
        holdMs = json["hold_ms"] as? Double ?? 250
        doubleTapMs = json["double_tap_ms"] as? Double ?? 400
        historySize = json["history_size"] as? Int ?? 10
        autoSpace = json["auto_space"] as? Bool ?? true
        autostart = json["autostart"] as? Bool ?? false
        overlay = json["overlay"] as? Bool ?? true
        overlayMargin = json["overlay_bottom_margin"] as? Double ?? 14
        sounds = json["sounds"] as? Bool ?? false
        streaming = json["streaming"] as? Bool ?? true
        keepWarm = json["keep_warm_seconds"] as? Int ?? 45
        checkUpdates = json["check_updates_on_start"] as? Bool ?? true
        vocabulary = (json["vocabulary"] as? [String] ?? []).joined(separator: "\n")
        suppress = false
    }

    func apply(_ json: [String: Any]) {
        suppress = true
        accessibility = json["accessibility"] as? String ?? accessibility
        inputMonitoring = json["input_monitoring"] as? String ?? inputMonitoring
        microphone = json["microphone"] as? String ?? microphone
        updateStatus = json["update_status"] as? String ?? updateStatus
        if let value = json["autostart"] as? Bool { autostart = value }
        suppress = false
    }

    func send(_ key: String, _ value: Any) {
        guard !suppress else { return }
        _ = delegate?.perform(NSSelectorFromString("settingChanged:value:"), with: key as NSString, with: "\(value)" as NSString)
    }

    func act(_ name: String) {
        _ = delegate?.perform(NSSelectorFromString("settingAction:"), with: name as NSString)
    }
}

struct SliderRow: View {
    let title: String
    @Binding var value: Double
    let range: ClosedRange<Double>
    let unit: String

    var body: some View {
        LabeledContent(title) {
            HStack {
                Slider(value: $value, in: range).frame(width: 170)
                Text("\(Int(value)) \(unit)").monospacedDigit().frame(width: 62, alignment: .trailing).foregroundStyle(.secondary)
            }
        }
    }
}

struct Footer: View {
    let text: String

    var body: some View {
        Text(text).frame(maxWidth: .infinity, alignment: .leading).multilineTextAlignment(.leading)
    }
}

struct GeneralPane: View {
    @ObservedObject var m: SettingsModel

    var body: some View {
        Form {
            Section {
                Picker("Клавиша диктовки", selection: $m.hotkey) { ForEach(m.hotkeys, id: \.key) { Text($0.title).tag($0.key) } }
                SliderRow(title: "Удержание до записи", value: $m.holdMs, range: 100...800, unit: "мс")
                SliderRow(title: "Двойное нажатие", value: $m.doubleTapMs, range: 200...800, unit: "мс")
            } footer: {
                Footer(text: "Два быстрых нажатия включают постоянную запись.")
            }
            Section {
                Picker("Язык речи", selection: $m.language) { ForEach(m.languages, id: \.key) { Text($0.title).tag($0.key) } }
                Stepper(value: $m.historySize, in: 1...50) { Text("Помнить последних диктовок: \(m.historySize)") }
                Toggle("Ставить пробел между вставками", isOn: $m.autoSpace)
            }
            Section {
                Toggle("Запускать при входе в систему", isOn: $m.autostart)
            }
        }
        .formStyle(.grouped)
        .scrollDisabled(true)
    }
}

struct OverlayPane: View {
    @ObservedObject var m: SettingsModel

    var body: some View {
        Form {
            Section {
                Toggle("Показывать плашку записи", isOn: $m.overlay)
                SliderRow(title: "Высота над Dock", value: $m.overlayMargin, range: 0...80, unit: "пт")
            } footer: {
                Footer(text: "Если Dock скрыт, отсчёт идёт от низа экрана.")
            }
            Section {
                Toggle("Звук в начале и конце записи", isOn: $m.sounds)
            }
            Section {
                Toggle("Распознавать, пока вы говорите", isOn: $m.streaming)
            } footer: {
                Footer(text: "Быстрее на длинных диктовках; числа иногда пишутся иначе.")
            }
        }
        .formStyle(.grouped)
        .scrollDisabled(true)
    }
}

struct VocabularyPane: View {
    @ObservedObject var m: SettingsModel

    var body: some View {
        Form {
            Section {
                TextEditor(text: $m.vocabulary).font(.body).frame(minHeight: 200)
            } header: {
                Text("Имена и термины")
            } footer: {
                Footer(text: "По одному в строке, например Пётр или Kubernetes. Whisper будет писать их именно так.")
            }
        }
        .formStyle(.grouped)
        .scrollDisabled(true)
    }
}

struct SystemPane: View {
    @ObservedObject var m: SettingsModel

    var body: some View {
        Form {
            Section("Разрешения") {
                LabeledContent("Универсальный доступ") { HStack { Text(m.accessibility).foregroundStyle(.secondary); Button("Открыть…") { m.act("open_accessibility") } } }
                LabeledContent("Мониторинг ввода") { HStack { Text(m.inputMonitoring).foregroundStyle(.secondary); Button("Открыть…") { m.act("open_input") } } }
                LabeledContent("Микрофон") { HStack { Text(m.microphone).foregroundStyle(.secondary); Button("Открыть…") { m.act("open_microphone") } } }
            }
            Section {
                Stepper(value: $m.keepWarm, in: 0...300, step: 5) {
                    Text(m.keepWarm == 0 ? "Поддержание модели выключено" : "Поддерживать модель каждые \(m.keepWarm) с")
                }
            } footer: {
                Footer(text: "Без этого первая диктовка после паузы идёт 5–15 секунд.")
            }
            Section("Приложение") {
                LabeledContent("Версия", value: m.version)
                LabeledContent("Обновления") {
                    HStack {
                        if !m.updateStatus.isEmpty { Text(m.updateStatus).foregroundStyle(.secondary).font(.callout) }
                        Button("Проверить…") { m.act("update") }
                    }
                }
                Toggle("Проверять обновления при запуске", isOn: $m.checkUpdates)
                LabeledContent("Журнал") { Button("Показать в Finder") { m.act("show_log") } }
            }
        }
        .formStyle(.grouped)
        .scrollDisabled(true)
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
        let view: AnyView
        switch identifier {
        case "overlay": view = AnyView(OverlayPane(m: model).frame(width: 560))
        case "vocabulary": view = AnyView(VocabularyPane(m: model).frame(width: 560))
        case "system": view = AnyView(SystemPane(m: model).frame(width: 560))
        default: view = AnyView(GeneralPane(m: model).frame(width: 560))
        }
        let host = NSHostingView(rootView: view)
        host.frame = NSRect(x: 0, y: 0, width: 560, height: host.fittingSize.height)
        return host
    }

    @objc public func update(_ config: String) {
        let json = (try? JSONSerialization.jsonObject(with: Data(config.utf8))) as? [String: Any] ?? [:]
        DispatchQueue.main.async { self.model.apply(json) }
    }
}
