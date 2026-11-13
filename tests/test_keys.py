from common.keys import keysym_to_vk


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
