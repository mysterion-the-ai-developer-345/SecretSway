import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

from secretsway import apps as A


def write_desktop(root, directory, filename, body):
    app_dir = os.path.join(root, directory, "applications")
    os.makedirs(app_dir, exist_ok=True)
    with open(os.path.join(app_dir, filename), "w", encoding="utf-8") as handle:
        handle.write(body)
    return app_dir


class DesktopFixture(unittest.TestCase):
    """Builds a throwaway XDG tree so tests never touch the real system."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="secretsway-test-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.data_home = os.path.join(self.root, "home")
        self.sysdir = os.path.join(self.root, "system")
        os.makedirs(self.data_home, exist_ok=True)
        os.makedirs(self.sysdir, exist_ok=True)

    def env(self, **extra):
        # Locale vars are neutralised by default so the container's own
        # LANG/LC_MESSAGES cannot leak into the localisation tests.
        base = {
            "XDG_DATA_HOME": self.data_home,
            "XDG_DATA_DIRS": self.sysdir,
            "XDG_CURRENT_DESKTOP": "sway",
            "LC_MESSAGES": "",
            "LC_ALL": "",
            "LANG": "",
            "LANGUAGE": "",
        }
        base.update(extra)
        return mock.patch.dict(os.environ, base, clear=False)

    def load(self, **extra):
        with self.env(**extra):
            return {app.name: app for app in A.load_apps()}


SIMPLE = """[Desktop Entry]
Type=Application
Name=Simple
Exec=/usr/bin/simple
"""


class TestFiltering(DesktopFixture):
    def test_plain_entry_is_listed(self):
        write_desktop(self.root, self.data_home, "simple.desktop", SIMPLE)
        self.assertIn("Simple", self.load())

    def test_type_not_application_is_excluded(self):
        write_desktop(self.root, self.data_home, "link.desktop",
                      "[Desktop Entry]\nType=Link\nName=ALink\nURL=http://x\n")
        self.assertNotIn("ALink", self.load())

    def test_missing_type_treated_as_application(self):
        write_desktop(self.root, self.data_home, "notype.desktop",
                      "[Desktop Entry]\nName=NoType\nExec=/bin/true\n")
        self.assertIn("NoType", self.load())

    def test_hidden_is_excluded(self):
        write_desktop(self.root, self.data_home, "h.desktop",
                      "[Desktop Entry]\nName=Hidden\nExec=/bin/true\nHidden=true\n")
        self.assertNotIn("Hidden", self.load())

    def test_nodisplay_is_excluded(self):
        write_desktop(self.root, self.data_home, "n.desktop",
                      "[Desktop Entry]\nName=NoShow\nExec=/bin/true\nNoDisplay=true\n")
        self.assertNotIn("NoShow", self.load())

    def test_nodisplay_false_is_kept(self):
        write_desktop(self.root, self.data_home, "n2.desktop",
                      "[Desktop Entry]\nName=Shown\nExec=/bin/true\nNoDisplay=false\n")
        self.assertIn("Shown", self.load())

    def test_tryexec_missing_binary_is_excluded(self):
        write_desktop(self.root, self.data_home, "t.desktop",
                      "[Desktop Entry]\nName=Tries\nExec=/bin/true\n"
                      "TryExec=definitely-not-installed-xyz\n")
        self.assertNotIn("Tries", self.load())

    def test_tryexec_present_binary_is_kept(self):
        write_desktop(self.root, self.data_home, "t2.desktop",
                      f"[Desktop Entry]\nName=Tries\nExec=/bin/true\nTryExec={sys.executable}\n")
        self.assertIn("Tries", self.load())

    def test_onlyshowin_disjoint_is_excluded(self):
        write_desktop(self.root, self.data_home, "o.desktop",
                      "[Desktop Entry]\nName=GnomeOnly\nExec=/bin/true\n"
                      "OnlyShowIn=GNOME;KDE;\n")
        self.assertNotIn("GnomeOnly", self.load())

    def test_onlyshowin_including_sway_is_kept(self):
        write_desktop(self.root, self.data_home, "o2.desktop",
                      "[Desktop Entry]\nName=AlsoSway\nExec=/bin/true\n"
                      "OnlyShowIn=GNOME;sway;KDE;\n")
        self.assertIn("AlsoSway", self.load())

    def test_notshowin_intersecting_is_excluded(self):
        write_desktop(self.root, self.data_home, "x.desktop",
                      "[Desktop Entry]\nName=NotSway\nExec=/bin/true\n"
                      "NotShowIn=sway;GNOME;\n")
        self.assertNotIn("NotSway", self.load())

    def test_notshowin_disjoint_is_kept(self):
        write_desktop(self.root, self.data_home, "x2.desktop",
                      "[Desktop Entry]\nName=NotKde\nExec=/bin/true\nNotShowIn=KDE;\n")
        self.assertIn("NotKde", self.load())

    def test_defaults_to_sway_when_unset(self):
        write_desktop(self.root, self.data_home, "s.desktop",
                      "[Desktop Entry]\nName=SwayApp\nExec=/bin/true\nOnlyShowIn=sway;\n")
        found = self.load(XDG_CURRENT_DESKTOP="")
        self.assertIn("SwayApp", found)

    def test_home_entry_shadows_system_entry(self):
        write_desktop(self.root, self.sysdir, "dup.desktop",
                      "[Desktop Entry]\nName=Dup\nExec=/usr/bin/system\n")
        write_desktop(self.root, self.data_home, "dup.desktop",
                      "[Desktop Entry]\nName=Dup\nExec=/usr/bin/mine\n")
        found = self.load()
        self.assertEqual(found["Dup"].exec_display, "/usr/bin/mine")

    def test_empty_override_hides_system_entry(self):
        """A user file with only NoDisplay=true must hide the system app.

        Fuzzel deliberately keeps such entries rather than skipping them, so a
        user can suppress an app they cannot edit in /usr/share.  Skipping it
        instead would let the system entry win and defeat the override.
        """
        write_desktop(self.root, self.sysdir, "sup.desktop",
                      "[Desktop Entry]\nName=Suppressible\nExec=/usr/bin/s\n")
        self.assertIn("Suppressible", self.load())

        write_desktop(self.root, self.data_home, "sup.desktop",
                      "[Desktop Entry]\nNoDisplay=true\n")
        self.assertNotIn("Suppressible", self.load())

    def test_empty_override_with_hidden_also_hides(self):
        write_desktop(self.root, self.sysdir, "sup2.desktop",
                      "[Desktop Entry]\nName=Suppressible2\nExec=/usr/bin/s\n")
        write_desktop(self.root, self.data_home, "sup2.desktop",
                      "[Desktop Entry]\nHidden=true\n")
        self.assertNotIn("Suppressible2", self.load())

    def test_malformed_file_does_not_abort_the_scan(self):
        write_desktop(self.root, self.data_home, "good.desktop", SIMPLE)
        write_desktop(self.root, self.data_home, "bad.desktop",
                      "this is not a desktop file at all\n\x00\x01\n")
        self.assertIn("Simple", self.load())

    def test_entry_with_no_exec_is_skipped(self):
        write_desktop(self.root, self.data_home, "noexec.desktop",
                      "[Desktop Entry]\nName=NoExec\nIcon=thing\n")
        self.assertNotIn("NoExec", self.load())

    def test_other_groups_do_not_leak_keys(self):
        write_desktop(self.root, self.data_home, "grp.desktop",
                      "[Desktop Entry]\nName=Grp\nExec=/bin/true\n"
                      "[X-Custom]\nHidden=true\nName=ShouldNotLeak\n")
        found = self.load()
        self.assertIn("Grp", found)
        self.assertNotIn("ShouldNotLeak", found)

    def test_comments_are_ignored(self):
        write_desktop(self.root, self.data_home, "cmt.desktop",
                      "# a comment\n; another\n[Desktop Entry]\nName=Cmt\nExec=/bin/true\n")
        self.assertIn("Cmt", self.load())


class TestLocalisation(DesktopFixture):
    BODY = """[Desktop Entry]
Type=Application
Name=Fallback
Name[pt]=Portugues
Name[pt_BR]=Brasil
GenericName=Generic
GenericName[pt]=Generico
Exec=/bin/true
"""

    def setUp(self):
        super().setUp()
        write_desktop(self.root, self.data_home, "loc.desktop", self.BODY)

    def test_exact_locale_wins(self):
        found = self.load(LC_ALL="pt_BR.UTF-8")
        self.assertEqual(found["Brasil"].generic_name, "Generico")

    def test_language_only_falls_back(self):
        found = self.load(LC_ALL="pt.UTF-8")
        self.assertEqual(found["Portugues"].generic_name, "Generico")

    def test_unmatched_locale_uses_bare_name(self):
        found = self.load(LC_ALL="de_DE.UTF-8")
        self.assertIn("Fallback", found)

    def test_c_and_posix_are_ignored(self):
        found = self.load(LC_ALL="C")
        self.assertIn("Fallback", found)

    def test_encoding_and_modifier_are_stripped(self):
        found = self.load(LANGUAGE="pt_BR.UTF-8@euro")
        self.assertIn("Brasil", found)

    def test_language_var_is_consulted(self):
        found = self.load(LANGUAGE="pt", LC_ALL="", LANG="")
        self.assertEqual(found["Portugues"].name, "Portugues")

    def test_country_outranks_bare_language(self):
        """The spec orders lang_COUNTRY above lang@MODIFIER above lang."""
        entry, _ = A._parse(
            "[Desktop Entry]\n"
            "Name=Base\n"
            "Name[pt]=Lang\n"
            "Name[pt_BR]=Country\n"
            "Name[pt@euro]=LangModified\n"
            "Name[pt_BR@euro]=CountryModified\n"
        )
        with mock.patch.dict(os.environ, {"LC_ALL": "pt_BR@euro"}, clear=False):
            os.environ["LC_MESSAGES"] = os.environ["LANGUAGE"] = os.environ["LANG"] = ""
            self.assertEqual(A._localise(entry, "name"), "CountryModified")
        os.environ["LC_MESSAGES"] = os.environ["LANGUAGE"] = os.environ["LANG"] = ""
        with mock.patch.dict(os.environ, {"LC_ALL": "pt@euro"}, clear=False):
            self.assertEqual(A._localise(entry, "name"), "LangModified")
        with mock.patch.dict(os.environ, {"LC_ALL": "pt_BR"}, clear=False):
            self.assertEqual(A._localise(entry, "name"), "Country")
        with mock.patch.dict(os.environ, {"LC_ALL": "pt"}, clear=False):
            self.assertEqual(A._localise(entry, "name"), "Lang")


class TestExecExpansion(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(A.expand_exec("/usr/bin/foo"), ["/usr/bin/foo"])

    def test_strips_file_and_url_codes(self):
        self.assertEqual(
            A.expand_exec("/usr/bin/foo %U"),
            ["/usr/bin/foo"],
        )

    def test_strips_deprecated_codes(self):
        self.assertEqual(A.expand_exec("/usr/bin/foo %d %D %n %N %v %m"),
                         ["/usr/bin/foo"])

    def test_quoted_argument_with_space(self):
        self.assertEqual(
            A.expand_exec('/usr/bin/foo "two words" %U'),
            ["/usr/bin/foo", "two words"],
        )

    def test_escaped_space(self):
        self.assertEqual(A.expand_exec("/usr/bin/foo two\\ words"),
                         ["/usr/bin/foo", "two words"])

    def test_double_percent_is_literal(self):
        self.assertEqual(A.expand_exec("/usr/bin/foo 50%% done"),
                         ["/usr/bin/foo", "50%", "done"])

    def test_icon_code_expands_to_flag(self):
        self.assertEqual(
            A.expand_exec("/usr/bin/foo %i", icon="myicon"),
            ["/usr/bin/foo", "--icon", "myicon"],
        )

    def test_absolute_icon_drops_the_code(self):
        self.assertEqual(
            A.expand_exec("/usr/bin/foo %i", icon="/opt/x/icon.png"),
            ["/usr/bin/foo"],
        )

    def test_name_code_substituted(self):
        self.assertEqual(
            A.expand_exec("/usr/bin/foo %c", name="My App"),
            ["/usr/bin/foo", "My App"],
        )

    def test_name_with_space_stays_one_token(self):
        self.assertEqual(
            A.expand_exec("/usr/bin/foo %c", name="My App"),
            ["/usr/bin/foo", "My App"],
        )

    def test_empty_exec_is_empty_argv(self):
        self.assertEqual(A.expand_exec(""), [])

    def test_unbalanced_quotes_yield_no_argv(self):
        self.assertEqual(A.expand_exec('/usr/bin/foo "unterminated'), [])

    def test_field_code_only_is_empty(self):
        self.assertEqual(A.expand_exec("%U"), [])

    def test_percent_in_name_is_not_eaten_by_drop_pass(self):
        # Substitution is a single pass, so a field code inside the *value*
        # being substituted is literal text, not another code to resolve.
        self.assertEqual(
            A.expand_exec("/usr/bin/foo %c", name="Save 50%f now"),
            ["/usr/bin/foo", "Save 50%f now"],
        )

    def test_percent_in_icon_is_not_eaten_by_name_pass(self):
        # This one needs the single pass specifically: with sequential str
        # replaces, %i is expanded first and the %c inside the icon it just
        # inserted is then eaten by the %c substitution, giving "icon".
        self.assertEqual(
            A.expand_exec("/usr/bin/foo %i", icon="ic%con"),
            ["/usr/bin/foo", "--icon", "ic%con"],
        )

    def test_unknown_code_is_left_alone(self):
        # %z is not a field code the spec defines, so it is not ours to drop.
        self.assertEqual(
            A.expand_exec("/usr/bin/foo %z"),
            ["/usr/bin/foo", "%z"],
        )


class TestTerminalAndActions(DesktopFixture):
    def test_terminal_flag_parsed(self):
        write_desktop(self.root, self.data_home, "t.desktop",
                      "[Desktop Entry]\nName=Term\nExec=htop\nTerminal=true\n")
        self.assertTrue(self.load()["Term"].terminal)

    def test_terminal_defaults_false(self):
        write_desktop(self.root, self.data_home, "t2.desktop", SIMPLE)
        self.assertFalse(self.load()["Simple"].terminal)

    def test_actions_are_collected(self):
        write_desktop(self.root, self.data_home, "act.desktop",
                      "[Desktop Entry]\nName=Editor\nExec=editor %F\n"
                      "Actions=new-window;private;\n"
                      "[Desktop Action new-window]\nName=New Window\nExec=editor --new %F\n"
                      "[Desktop Action private]\nName=Private\nExec=editor --private\n")
        actions = self.load()["Editor"].actions
        self.assertEqual([a.name for a in actions], ["New Window", "Private"])
        self.assertEqual(actions[0].exec_argv, ["editor", "--new"])
        self.assertEqual(actions[1].exec_argv, ["editor", "--private"])

    def test_action_with_no_exec_is_dropped(self):
        write_desktop(self.root, self.data_home, "act2.desktop",
                      "[Desktop Entry]\nName=E2\nExec=e2\nActions=broken;\n"
                      "[Desktop Action broken]\nName=Broken\n")
        self.assertEqual(self.load()["E2"].actions, [])


class TestDataDirs(DesktopFixture):
    def test_uses_xdg_data_home(self):
        with mock.patch.dict(os.environ, {
            "XDG_DATA_HOME": self.data_home,
            "XDG_DATA_DIRS": "",
            "XDG_CURRENT_DESKTOP": "sway",
        }):
            self.assertEqual(A.data_dirs()[0], self.data_home)

    def test_xdg_data_dirs_are_appended(self):
        other = os.path.join(self.root, "other")
        with mock.patch.dict(os.environ, {
            "XDG_DATA_HOME": self.data_home,
            "XDG_DATA_DIRS": f"{self.sysdir}:{other}",
        }):
            self.assertEqual(A.data_dirs(), [self.data_home, self.sysdir, other])

    def test_empty_entries_are_dropped(self):
        with mock.patch.dict(os.environ, {
            "XDG_DATA_HOME": self.data_home,
            "XDG_DATA_DIRS": f"::{self.sysdir}:",
        }):
            self.assertEqual(A.data_dirs(), [self.data_home, self.sysdir])

    def test_missing_directory_is_survivable(self):
        with mock.patch.dict(os.environ, {
            "XDG_DATA_HOME": os.path.join(self.root, "nope"),
            "XDG_DATA_DIRS": self.sysdir,
        }):
            self.assertEqual(A.load_apps(), [])


class TestScale(DesktopFixture):
    def test_large_tree_load_time(self):
        """Roughly what a real system looks like; guards against a slow parse."""
        for i in range(600):
            write_desktop(
                self.root, self.sysdir, f"app{i}.desktop",
                f"[Desktop Entry]\nType=Application\nName=App {i}\n"
                f"GenericName=Some Application\nExec=/usr/bin/app{i} %U\n"
                f"Name[pt]=Aplicacao {i}\nIcon=app{i}\nTerminal=false\n",
            )
        start = time.perf_counter()
        found = self.load()
        elapsed = time.perf_counter() - start
        self.assertEqual(len(found), 600)
        self.assertLess(elapsed, 1.0, f"parsed 600 entries in {elapsed:.3f}s")


if __name__ == "__main__":
    unittest.main(verbosity=2)
