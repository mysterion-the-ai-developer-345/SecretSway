"""Static consistency checks between the config schema and the code that reads it.

`show_exec` in secretsway.toml versus `show_right` in window.py shipped a KeyError that
only appeared once a GTK window was being built -- the config validated fine,
the default was present, and only the *name* disagreed.  Nothing that ran
without a compositor would have caught it, so it gets a test that reads the
source instead of executing it.
"""

import ast
import pathlib
import re
import unittest

from secretsway.config import DEFAULTS, _ALIASES

SP_DIR = pathlib.Path(__file__).resolve().parent.parent / "secretsway"

SECTIONS = ("ui", "behaviour", "colors")


def referenced_keys(section: str) -> set:
    """Every `self.<section>["key"]` literal in the package."""
    pattern = re.compile(rf'self\.{section}\["([a-z_]+)"\]')
    found = set()
    for path in SP_DIR.glob("*.py"):
        found |= set(pattern.findall(path.read_text()))
    return found


class TestSchemaMatchesCode(unittest.TestCase):
    def test_every_referenced_key_exists(self):
        for section in SECTIONS:
            with self.subTest(section=section):
                referenced = referenced_keys(section)
                known = set(DEFAULTS[section])
                missing = referenced - known
                self.assertFalse(
                    missing,
                    f"code reads {section}.{sorted(missing)} but DEFAULTS has "
                    f"no such key -- a rename went half-finished",
                )

    def test_alias_targets_are_real_keys(self):
        for section, mapping in _ALIASES.items():
            for old, new in mapping.items():
                with self.subTest(old=old):
                    self.assertIn(new, DEFAULTS[section])

    def test_shipped_sp_toml_matches_schema(self):
        """The secretsway.toml we install must not contain a key the schema rejects."""
        import tomllib
        path = SP_DIR.parent / "secretsway.toml"
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
        for section, keys in data.items():
            with self.subTest(section=section):
                allowed = set(DEFAULTS[section]) | set(_ALIASES.get(section, {}))
                unknown = set(keys) - allowed
                self.assertFalse(unknown, f"secretsway.toml has {section}.{unknown}")

    def test_shipped_sp_toml_is_actually_loadable(self):
        from secretsway import config as config_mod
        path = str(SP_DIR.parent / "secretsway.toml")
        try:
            loaded = config_mod.load(path)
        except config_mod.ConfigError as exc:
            self.fail(f"the secretsway.toml we ship does not validate: {exc}")
        # Every key the code reads must survive the load.
        for section in SECTIONS:
            for key in referenced_keys(section):
                self.assertIn(key, loaded[section])


class TestNoStrayGIImports(unittest.TestCase):
    """gi must not be reachable at import time in the compositor-free modules.

    `secretsway --check`, `--dump-apps` and `--preview` have to work on a machine with
    no GTK, so none of these may import gi at module scope.  Importing inside a
    function is fine and is how cli.py gets at it.
    """

    MODULES = ("apps", "match", "config", "launch", "cli", "probe", "panel",
               "usage")

    @staticmethod
    def _imports_gi(source: str) -> bool:
        tree = ast.parse(source)
        for node in tree.body:  # top level only
            if isinstance(node, ast.Import):
                if any(a.name.split(".")[0] == "gi" for a in node.names):
                    return True
            elif isinstance(node, ast.ImportFrom):
                if (node.module or "").split(".")[0] == "gi":
                    return True
        return False

    def test_no_module_level_gi_imports(self):
        for name in self.MODULES:
            with self.subTest(module=name):
                self.assertFalse(
                    self._imports_gi((SP_DIR / f"{name}.py").read_text()),
                    f"{name}.py imports gi at module level",
                )

    def test_panel_and_usage_stay_pure(self):
        """panel.py feeds --preview, so it must not need a compositor."""
        for name in ("panel", "usage", "match"):
            source = (SP_DIR / f"{name}.py").read_text()
            with self.subTest(module=name):
                self.assertNotIn("gi.repository", source)
                self.assertNotIn("cairo", source.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
