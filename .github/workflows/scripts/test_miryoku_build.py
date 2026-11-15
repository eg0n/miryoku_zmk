# Copyright 2021 Manna Harbour
# https://github.com/manna-harbour/miryoku
"""Tests for miryoku_build.py: python3 -m unittest discover .github/workflows/scripts"""

import json
import unittest
from pathlib import Path

import miryoku_build as mb

DEFAULTS = {
    key: "default"
    for key in (
        "shield",
        "alphas",
        "extra",
        "tap",
        "nav",
        "clipboard",
        "layers",
        "mapping",
        "dts_extra_cppflags",
        "custom_config",
        "kconfig",
        "branches",
        "modules",
    )
}


def matrix(**values):
    return {"board": "nice_nano//zmk", **DEFAULTS, **values}


def inputs(**lists):
    return {key: json.dumps(values) for key, values in lists.items()}


class KeyboardNames(unittest.TestCase):
    def test_first_shield_names_the_keyboard(self):
        self.assertEqual(mb.keyboard_names("xiao_ble//zmk", "totem_left rgbled_adapter"), ("totem_left", "totem"))
        self.assertEqual(mb.keyboard_names("nrf52840dongle//zmk", "totem_dongle"), ("totem_dongle", "totem"))

    def test_board_names_the_keyboard_without_a_shield(self):
        self.assertEqual(mb.keyboard_names("corneish_zen_v1_left", ""), ("corneish_zen_v1_left", "corneish_zen_v1"))
        self.assertEqual(mb.keyboard_names("planck_rev6@1.0.0", ""), ("planck_rev6@1.0.0", "planck_rev6"))


class DtsFlags(unittest.TestCase):
    def test_keyboard_only(self):
        self.assertEqual(mb.dts_flags("corne", matrix()), "-DMIRYOKU_KEYBOARD_CORNE")

    def test_keyboard_name_is_made_a_macro_name(self):
        self.assertEqual(mb.dts_flags("choc-corne", matrix()), "-DMIRYOKU_KEYBOARD_CHOC_CORNE")

    def test_everything_in_order(self):
        flags = mb.dts_flags(
            "totem",
            matrix(
                alphas="colemak",
                clipboard="mac",
                nav="invertedT",
                dts_extra_cppflags="RGBLED_WIDGET_BEHAVIORS FOO=BAR",
                custom_config="#define MIRYOKU_KLUDGE_SOFT_OFF",
            ),
        )
        self.assertEqual(
            flags,
            "-DMIRYOKU_KEYBOARD_TOTEM;-DRGBLED_WIDGET_BEHAVIORS;-DFOO=BAR;-DMIRYOKU_KLUDGE_SOFT_OFF;"
            "-DMIRYOKU_ALPHAS_COLEMAK;-DMIRYOKU_NAV_INVERTEDT;-DMIRYOKU_CLIPBOARD_MAC",
        )


class CustomConfig(unittest.TestCase):
    def test_defines_become_flags(self):
        flags, ignored = mb.custom_config_flags("#define A\n  #define B 1 \n")
        self.assertEqual((flags, ignored), (["A", "B=1"], []))

    def test_other_lines_are_reported(self):
        flags, ignored = mb.custom_config_flags("#define F(x) x\njunk\n\n#define OK")
        self.assertEqual((flags, ignored), (["OK"], ["#define F(x) x", "junk"]))


class Kconfig(unittest.TestCase):
    def test_label(self):
        self.assertEqual(mb.split_kconfig("classic|CONFIG_A=y\nCONFIG_B=y"), ("classic", "CONFIG_A=y\nCONFIG_B=y"))

    def test_no_label(self):
        self.assertEqual(mb.split_kconfig("CONFIG_A=y\nCONFIG_B=y"), ("", "CONFIG_A=y\nCONFIG_B=y"))

    def test_a_bar_after_the_first_line_is_not_a_label(self):
        self.assertEqual(mb.split_kconfig('CONFIG_A=y\nCONFIG_B="a|b"'), ("", 'CONFIG_A=y\nCONFIG_B="a|b"'))


class ArtifactName(unittest.TestCase):
    def name(self, entry, **lists):
        return mb.artifact_name(entry, mb.varying_inputs(inputs(**lists)))

    def test_single_builds_keep_the_plain_name(self):
        self.assertEqual(
            self.name(matrix(shield="totem_left rgbled_adapter", alphas="colemak")), "totem_left rgbled_adapter"
        )
        self.assertEqual(self.name(matrix(shield="settings_reset")), "settings_reset")

    def test_board_names_a_build_without_a_shield(self):
        self.assertEqual(self.name(matrix()), "nice_nano__zmk")

    def test_kconfig_label(self):
        entry = matrix(shield="totem_dongle prospector_adapter", kconfig="classic|CONFIG_X=y")
        self.assertEqual(
            self.name(entry, kconfig=["classic|CONFIG_X=y", "radii|CONFIG_Y=y"]),
            "totem_dongle prospector_adapter-classic",
        )

    def test_each_varying_input_adds_a_part(self):
        entry = matrix(shield="corne_left", alphas="qwerty", nav="vi")
        lists = {"board": ["nice_nano//zmk", "nice_nano_v2"], "alphas": ["default", "qwerty"], "nav": ["vi"]}
        self.assertEqual(self.name(entry, **lists), "corne_left-nice_nano__zmk-alphas_qwerty")

    def test_free_form_inputs_add_a_hash(self):
        first = self.name(matrix(shield="corne_left", kconfig="CONFIG_A=y"), kconfig=["CONFIG_A=y", "CONFIG_B=y"])
        second = self.name(matrix(shield="corne_left", kconfig="CONFIG_B=y"), kconfig=["CONFIG_A=y", "CONFIG_B=y"])
        self.assertRegex(first, r"^corne_left-kconfig_[0-9a-f]{8}$")
        self.assertNotEqual(first, second)

    def test_varying_inputs_ignores_single_values_and_non_lists(self):
        found = mb.varying_inputs({"alphas": '["a","b"]', "tap": '["a"]', "cmake_args": "-DX=1", "merge": True})
        self.assertEqual(found, {"alphas"})


class Outboards(unittest.TestCase):
    def test_parse(self):
        text = "# comment\n\noutboard_repository=mrzealot/zmk-config\noutboard_ref=master\noutboard_from=/\noutboard_branches='a/b/c d/e/f'\n"
        self.assertEqual(
            mb.parse_outboard(text),
            {"repository": "mrzealot/zmk-config", "ref": "master", "from": "/", "branches": "a/b/c d/e/f"},
        )

    def test_every_outboard_file_parses(self):
        files = [path for path in (Path(__file__).parent.parent / "outboards").glob("*/*") if path.is_file()]
        self.assertTrue(files)
        for path in files:
            outboard = mb.parse_outboard(path.read_text())
            self.assertTrue(outboard, path)
            self.assertLessEqual(set(outboard), {"repository", "ref", "from", "to", "branches", "modules"}, path)


class Refs(unittest.TestCase):
    def test_branch_may_hold_slashes(self):
        self.assertEqual(
            mb.parse_ref("eg0n/zmk/feat/wpm-rolling"),
            ("https://github.com/eg0n/zmk.git", "eg0n", "zmk", "feat/wpm-rolling"),
        )


class Failure(unittest.TestCase):
    def test_clean_log(self):
        self.assertIsNone(mb.failure("-- Using keymap file: /w/miryoku_zmk/config/corne.keymap\n"))

    def test_settings_reset_uses_its_own_keymap(self):
        self.assertIsNone(
            mb.failure("-- Using keymap file: /w/zmk/app/boards/shields/settings_reset/settings_reset.keymap\n")
        )

    def test_keymap_from_zmk_is_not_miryoku(self):
        message, code = mb.failure("-- Using keymap file: /w/zmk/app/boards/shields/corne/corne.keymap\n")
        self.assertEqual(code, mb.EX_UNAVAILABLE)
        self.assertIn("Miryoku keyboard keymap not found", message)

    def test_known_errors(self):
        self.assertEqual(mb.failure("Invalid BOARD; see above.")[1], mb.EX_UNAVAILABLE)
        self.assertEqual(mb.failure("Failed to locate keymap file!")[1], mb.EX_UNAVAILABLE)
        self.assertEqual(mb.failure("Invalid SHIELD(s): x")[1], mb.EX_DATAERR)


if __name__ == "__main__":
    unittest.main()
