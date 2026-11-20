import sys
from types import SimpleNamespace

from spoke.hotkey import HoldGate
from spoke.keys import FLAGS_CHANGED, KEY_DOWN, KEY_UP, KeyListener, Shortcut, classify, valid_shortcut

CTRL = 0x40000


def test_modifier_press_and_release_come_from_flags():
    assert classify(FLAGS_CHANGED, 59, CTRL) == ("ctrl", True)
    assert classify(FLAGS_CHANGED, 59, 0) == ("ctrl", False)
    assert classify(FLAGS_CHANGED, 62, CTRL) == ("right_ctrl", True)
    assert classify(FLAGS_CHANGED, 62, CTRL | 0x01) == ("right_ctrl", False)
    assert classify(FLAGS_CHANGED, 59, CTRL | 0x2000) == ("ctrl", False)
    assert classify(FLAGS_CHANGED, 61, 0x80000) == ("right_option", True)
    assert classify(FLAGS_CHANGED, 0, CTRL) is None


def test_regular_keys_are_reported_as_char_and_autorepeat_is_ignored():
    assert classify(KEY_DOWN, 8, 0) == ("char", True)
    assert classify(KEY_UP, 8, 0) == ("char", False)
    assert classify(KEY_DOWN, 8, 0, autorepeat=True) is None
    assert classify(KEY_DOWN, 53, 0) == ("esc", True)
    assert classify(KEY_DOWN, 105, 0) == ("f13", True)
    assert classify(99, 8, 0) is None


def test_custom_shortcuts_use_physical_keys_and_exact_modifiers():
    shortcut = Shortcut.parse("key:8:393216:⌃⇧C")
    assert shortcut.keycode == 8 and shortcut.title == "⌃⇧C"
    assert shortcut.matches_flags(0x60000)
    assert not shortcut.matches_flags(CTRL)
    assert not shortcut.matches_flags(0xE0000)
    assert Shortcut.parse("key:126:0:↑").matches_flags(0x800000)
    assert not valid_shortcut("key:57:0:Caps Lock")
    assert not valid_shortcut("key:59:262144:Ctrl")


def test_modifier_shortcut_releases_when_a_required_modifier_is_released():
    chord = Shortcut.parse("key:61:262144:⌃⌥")
    assert chord.modifier_down(59, 0xC0041, held=False)
    assert not chord.modifier_down(59, 0x80040, held=True)
    right_shift = Shortcut.parse("key:60:0:Правый ⇧")
    assert right_shift.modifier_down(60, 0x20006, held=False)
    assert not right_shift.modifier_down(60, 0x20002, held=True)


def test_only_right_ctrl_triggers_its_shortcut_and_releases_with_left_held():
    right = Shortcut.parse("key:62:0:Правый Ctrl")
    assert valid_shortcut("right_ctrl")
    assert not right.modifier_down(59, CTRL | 0x01, held=False)
    assert right.modifier_down(62, CTRL | 0x2001, held=False)
    assert right.modifier_down(59, CTRL | 0x2000, held=True)
    assert not right.modifier_down(62, CTRL | 0x01, held=True)


def test_tap_replays_typing_but_consumes_the_shortcut_when_held(monkeypatch):
    now = 0.0
    output = []
    gate = HoldGate("shortcut", 250, lambda: output.append("start"), lambda: output.append("stop"),
                    lambda: output.append("cancel"), clock=lambda: now)
    quartz = SimpleNamespace(
        kCGEventTapDisabledByTimeout=-1, kCGEventTapDisabledByUserInput=-2,
        kCGEventSourceUserData="source", kCGKeyboardEventKeycode="code", kCGKeyboardEventAutorepeat="repeat",
        kCGSessionEventTap=1, kCGHeadInsertEventTap=0, kCGEventTapOptionDefault=0, kCGEventTapOptionListenOnly=1,
        kCFRunLoopCommonModes=0, CGEventMaskBit=lambda kind: 1 << kind,
        CGEventTapCreate=lambda _, __, ___, ____, callback, _____: callback,
        CFRunLoopGetCurrent=lambda: None, CFRunLoopAddSource=lambda *args: None, CFMachPortCreateRunLoopSource=lambda *args: None,
        CGEventTapEnable=lambda *args: None, CFRunLoopRun=lambda: None,
        CGEventGetIntegerValueField=lambda event, field: event.get(field, 0), CGEventGetFlags=lambda event: event["flags"],
        CGEventCreateCopy=dict, CGEventSetIntegerValueField=lambda event, field, value: event.update({field: value}),
        CGEventTapPostEvent=lambda _, event: output.append(("replayed", event["code"])),
    )
    monkeypatch.setitem(sys.modules, "Quartz", quartz)
    listener = KeyListener(gate.key_down, gate.key_up, Shortcut.parse("key:0:0:A"), gate_state=lambda: gate.state)
    listener._run()

    def event(kind, code, flags=0, repeat=False):
        return listener._tap(None, kind, {"code": code, "flags": flags, "repeat": repeat}, None)

    assert event(KEY_DOWN, 0) is None
    assert event(KEY_UP, 0) is not None
    assert output == [("replayed", 0)]

    assert event(KEY_DOWN, 0) is None
    assert event(KEY_DOWN, 8) is not None and event(KEY_DOWN, 6) is not None
    assert event(KEY_UP, 0) is not None
    assert output == [("replayed", 0), ("replayed", 0)]

    assert event(KEY_DOWN, 0) is None
    now = 0.3
    gate.tick()
    assert event(KEY_DOWN, 0, repeat=True) is None
    assert event(KEY_UP, 0) is None
    assert output[-2:] == ["start", "stop"]
