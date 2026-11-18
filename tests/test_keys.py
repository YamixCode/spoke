from spoke.keys import FLAGS_CHANGED, KEY_DOWN, KEY_UP, classify

CTRL = 0x40000


def test_modifier_press_and_release_come_from_flags():
    assert classify(FLAGS_CHANGED, 59, CTRL) == ("ctrl", True)
    assert classify(FLAGS_CHANGED, 59, 0) == ("ctrl", False)
    assert classify(FLAGS_CHANGED, 62, CTRL) == ("ctrl", True)
    assert classify(FLAGS_CHANGED, 61, 0x80000) == ("right_option", True)
    assert classify(FLAGS_CHANGED, 0, CTRL) is None


def test_regular_keys_are_reported_as_char_and_autorepeat_is_ignored():
    assert classify(KEY_DOWN, 8, 0) == ("char", True)
    assert classify(KEY_UP, 8, 0) == ("char", False)
    assert classify(KEY_DOWN, 8, 0, autorepeat=True) is None
    assert classify(KEY_DOWN, 53, 0) == ("esc", True)
    assert classify(KEY_DOWN, 105, 0) == ("f13", True)
    assert classify(99, 8, 0) is None
