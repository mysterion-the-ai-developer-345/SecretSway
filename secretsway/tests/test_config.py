import os
import tempfile
import unittest

from secretsway import config as C


class ConfigFixture(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="secretsway-conf-")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.dir, ignore_errors=True))

    def write(self, text):
        path = os.path.join(self.dir, "secretsway.toml")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path


class TestDefaults(ConfigFixture):
    def test_missing_file_yields_defaults(self):
        cfg = C.load(os.path.join(self.dir, "absent.toml"))
        self.assertEqual(cfg["colors"]["border"], "#2f6bff")
        self.assertEqual(cfg["colors"]["panel"], "#1e1e1e")

    def test_defaults_are_not_mutated_between_loads(self):
        path = self.write('[colors]\nborder = "#00ff00"\n')
        C.load(path)
        self.assertEqual(C.DEFAULTS["colors"]["border"], "#2f6bff")

    def test_partial_override_keeps_other_defaults(self):
        path = self.write('[colors]\nborder = "#00ff00"\n')
        cfg = C.load(path)
        self.assertEqual(cfg["colors"]["border"], "#00ff00")
        self.assertEqual(cfg["colors"]["panel"], "#1e1e1e")

    def test_nested_table_merge(self):
        path = self.write('[ui]\nfont_size = 20\n')
        cfg = C.load(path)
        self.assertEqual(cfg["ui"]["font_size"], 20)
        # Compared against DEFAULTS rather than a literal, so changing a
        # preference does not turn this into a churn test.
        self.assertEqual(cfg["ui"]["right_column"],
                         C.DEFAULTS["ui"]["right_column"])


class TestRenamedKeys(ConfigFixture):
    """show_exec/show_right became the right_column mode; old configs still load."""

    def test_show_exec_true_means_exec_column(self):
        path = self.write("[ui]\nshow_exec = true\n")
        self.assertEqual(C.load(path)["ui"]["right_column"], "exec")

    def test_show_exec_false_means_dots(self):
        path = self.write("[ui]\nshow_exec = false\n")
        self.assertEqual(C.load(path)["ui"]["right_column"], "dots")

    def test_show_right_true_means_exec_column(self):
        path = self.write("[ui]\nshow_right = true\n")
        self.assertEqual(C.load(path)["ui"]["right_column"], "exec")

    def test_show_right_false_means_dots(self):
        path = self.write("[ui]\nshow_right = false\n")
        self.assertEqual(C.load(path)["ui"]["right_column"], "dots")

    def test_legacy_key_is_absent_from_output(self):
        path = self.write("[ui]\nshow_exec = true\n")
        self.assertNotIn("show_exec", C.load(path)["ui"])

    def test_alias_clashing_with_canonical_is_an_error(self):
        path = self.write("[ui]\nshow_exec = true\nright_column = \"off\"\n")
        with self.assertRaises(C.ConfigError) as ctx:
            C.load(path)
        self.assertIn("right_column", str(ctx.exception))

    def test_both_legacy_spellings_do_not_crash(self):
        path = self.write("[ui]\nshow_exec = true\nshow_right = false\n")
        self.assertIn(C.load(path)["ui"]["right_column"], ("dots", "exec"))


class TestValidation(ConfigFixture):
    def test_unknown_section_is_an_error(self):
        path = self.write("[colourz]\nfoo = 'x'\n")
        with self.assertRaises(C.ConfigError) as ctx:
            C.load(path)
        self.assertIn("colourz", str(ctx.exception))

    def test_unknown_key_is_an_error(self):
        path = self.write("[colors]\nbordr = '#ffffff'\n")
        with self.assertRaises(C.ConfigError):
            C.load(path)

    def test_malformed_toml_is_an_error(self):
        path = self.write("[colors\nbroken = \n")
        with self.assertRaises(C.ConfigError):
            C.load(path)

    def test_short_hex_is_rejected(self):
        path = self.write("[colors]\nborder = '#fff'\n")
        with self.assertRaises(C.ConfigError):
            C.load(path)

    def test_non_hex_is_rejected(self):
        path = self.write("[colors]\nborder = '#gggggg'\n")
        with self.assertRaises(C.ConfigError):
            C.load(path)

    def test_eight_digit_hex_is_rejected(self):
        path = self.write("[colors]\nborder = '#2f6bffaa'\n")
        with self.assertRaises(C.ConfigError):
            C.load(path)

    def test_bad_fallback_mode_is_rejected(self):
        path = self.write('[behaviour]\ncommand_fallback = "sudo"\n')
        with self.assertRaises(C.ConfigError):
            C.load(path)

    def test_string_for_desktop_file_dirs_is_rejected(self):
        path = self.write('[behaviour]\ndesktop_file_dirs = "/tmp"\n')
        with self.assertRaises(C.ConfigError):
            C.load(path)

    def test_tilde_in_desktop_file_dirs_is_expanded(self):
        path = self.write('[behaviour]\ndesktop_file_dirs = ["~/apps"]\n')
        cfg = C.load(path)
        self.assertFalse(cfg["behaviour"]["desktop_file_dirs"][0].startswith("~"))

    def test_max_rows_must_be_positive(self):
        path = self.write("[ui]\nmax_rows = 0\n")
        with self.assertRaises(C.ConfigError):
            C.load(path)


class TestRgb(unittest.TestCase):
    def test_conversion(self):
        self.assertEqual(C.rgb("#000000"), (0.0, 0.0, 0.0))
        self.assertEqual(C.rgb("#ffffff"), (1.0, 1.0, 1.0))

    def test_cobalt(self):
        r, g, b = C.rgb("#2f6bff")
        self.assertAlmostEqual(r, 0x2f / 255)
        self.assertAlmostEqual(g, 0x6b / 255)
        self.assertAlmostEqual(b, 1.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
