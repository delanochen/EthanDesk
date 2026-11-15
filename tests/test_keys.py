from common.keys import keysym_to_vk, text_for_key

SHIFT = 0x0001
CONTROL = 0x0004
ALT = 0x0008
WIN = 0x0040


def test_printable_characters_go_through_text_injection():
    assert text_for_key("a", 0) == "a"
    assert text_for_key("7", 0) == "7"
    # Shift 只是大小写，仍按字符注入：远端 CapsLock 状态不会把大小写弄反。
    assert text_for_key("A", SHIFT) == "A"
    assert text_for_key("!", SHIFT) == "!"


def test_non_ascii_also_goes_through_text_injection():
    assert text_for_key("中", 0) == "中"
    assert text_for_key("😀", 0) == "😀"


def test_control_combinations_stay_on_the_virtual_key_path():
    # Ctrl+C / Ctrl+V 这类快捷键必须还是按键，否则复制粘贴就没了。
    assert text_for_key("c", CONTROL) is None
    assert text_for_key("v", CONTROL) is None
    assert text_for_key("a", ALT) is None
    assert text_for_key("d", WIN) is None


def test_non_printable_and_empty_are_not_text():
    assert text_for_key("\r", 0) is None
    assert text_for_key("\t", 0) is None
    assert text_for_key("\x08", 0) is None
    assert text_for_key("", 0) is None
    assert text_for_key("ab", 0) is None


def test_letters_are_uppercase_vk_codes():
    assert keysym_to_vk("a") == 0x41
    assert keysym_to_vk("z") == 0x5A
    assert keysym_to_vk("A") == 0x41


def test_digits():
    assert keysym_to_vk("0") == 0x30
    assert keysym_to_vk("9") == 0x39


def test_named_keys():
    assert keysym_to_vk("Return") == 0x0D
    assert keysym_to_vk("Escape") == 0x1B
    assert keysym_to_vk("space") == 0x20
    assert keysym_to_vk("BackSpace") == 0x08
    assert keysym_to_vk("Tab") == 0x09
    assert keysym_to_vk("Delete") == 0x2E
    assert keysym_to_vk("Home") == 0x24
    assert keysym_to_vk("End") == 0x23
    assert keysym_to_vk("Prior") == 0x21
    assert keysym_to_vk("Next") == 0x22


def test_arrows():
    assert keysym_to_vk("Left") == 0x25
    assert keysym_to_vk("Up") == 0x26
    assert keysym_to_vk("Right") == 0x27
    assert keysym_to_vk("Down") == 0x28


def test_function_keys():
    assert keysym_to_vk("F1") == 0x70
    assert keysym_to_vk("F12") == 0x7B


def test_modifiers():
    assert keysym_to_vk("Shift_L") == 0xA0
    assert keysym_to_vk("Shift_R") == 0xA1
    assert keysym_to_vk("Control_L") == 0xA2
    assert keysym_to_vk("Control_R") == 0xA3
    assert keysym_to_vk("Alt_L") == 0xA4
    assert keysym_to_vk("Alt_R") == 0xA5


def test_punctuation():
    assert keysym_to_vk("period") == 0xBE
    assert keysym_to_vk("comma") == 0xBC
    assert keysym_to_vk("minus") == 0xBD
    assert keysym_to_vk("equal") == 0xBB


def test_unknown_keysym_returns_none():
    assert keysym_to_vk("Multi_key") is None
    assert keysym_to_vk("") is None
