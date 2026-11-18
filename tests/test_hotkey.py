from spoke.hotkey import HoldGate


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


def gate(hold_ms=250):
    clock, events = Clock(), []
    g = HoldGate("ctrl", hold_ms, lambda: events.append("start"), lambda: events.append("stop"), lambda: events.append("cancel"), clock)
    return g, clock, events


def test_hold_longer_than_threshold_records_and_release_stops():
    g, clock, events = gate()
    g.key_down("ctrl")
    clock.now = 0.1
    g.tick()
    assert events == []
    clock.now = 0.3
    g.tick()
    assert events == ["start"]
    g.key_up("ctrl")
    assert events == ["start", "stop"]


def test_quick_tap_does_nothing():
    g, clock, events = gate()
    g.key_down("ctrl")
    clock.now = 0.1
    g.tick()
    g.key_up("ctrl")
    clock.now = 1.0
    g.tick()
    assert events == []


def test_shortcut_with_another_key_never_starts_and_cancels_a_running_recording():
    g, clock, events = gate()
    g.key_down("ctrl")
    g.key_down("char")
    clock.now = 1.0
    g.tick()
    g.key_up("ctrl")
    assert events == []
    g.key_down("ctrl")
    clock.now = 2.0
    g.tick()
    g.key_down("char")
    g.key_up("ctrl")
    assert events == ["start", "cancel"]


def test_key_repeat_and_other_keys_without_target_are_ignored_and_state_recovers():
    g, clock, events = gate()
    g.key_down("char")
    g.key_up("char")
    g.key_down("ctrl")
    g.key_down("ctrl")
    clock.now = 0.5
    g.tick()
    g.tick()
    assert events == ["start"]
    g.key_up("ctrl")
    g.key_down("ctrl")
    clock.now = 0.6
    g.tick()
    assert events == ["start", "stop"] and g.state == "pending"
    clock.now = 0.8
    g.tick()
    assert events == ["start", "stop", "start"]



def locked_gate():
    clock, events = Clock(), []
    g = HoldGate("ctrl", 250, lambda: events.append("start"), lambda: events.append("stop"), lambda: events.append("cancel"), clock,
                 on_lock=lambda: events.append("lock"), double_tap_ms=400)
    return g, clock, events


def tap(g, clock, at):
    clock.now = at
    g.key_down("ctrl")
    clock.now = at + 0.05
    g.tick()
    g.key_up("ctrl")


def test_double_tap_locks_recording_and_next_tap_stops_it():
    g, clock, events = locked_gate()
    tap(g, clock, 0.0)
    clock.now = 0.2
    g.key_down("ctrl")
    g.key_up("ctrl")
    assert events == ["lock"] and g.state == "locked"
    clock.now = 5.0
    g.tick()
    g.key_down("char")
    assert events == ["lock"]
    g.key_down("ctrl")
    g.key_up("ctrl")
    assert events == ["lock", "stop"] and g.state == "idle"


def test_slow_taps_do_not_lock_and_esc_cancels_locked_recording():
    g, clock, events = locked_gate()
    tap(g, clock, 0.0)
    tap(g, clock, 1.0)
    assert events == []
    clock.now = 1.2
    g.key_down("ctrl")
    g.key_up("ctrl")
    assert events == ["lock"]
    g.key_down("esc")
    assert events == ["lock", "cancel"] and g.state == "idle"


def test_ctrl_shortcut_is_not_counted_as_tap():
    g, clock, events = locked_gate()
    g.key_down("ctrl")
    g.key_down("char")
    g.key_up("ctrl")
    clock.now = 0.1
    g.key_down("ctrl")
    assert events == [] and g.state == "pending"


def test_on_press_fires_when_target_goes_down_only_once_per_press():
    clock, pressed = Clock(), []
    g = HoldGate("ctrl", 250, lambda: None, lambda: None, lambda: None, clock, on_press=lambda: pressed.append(1))
    g.key_down("ctrl")
    g.key_down("ctrl")
    g.key_down("char")
    assert pressed == [1]
