import os
import socket
import tempfile
import unittest
from dataclasses import dataclass, field
from unittest import mock

from secretsway import launch as L


@dataclass
class FakeApp:
    name: str
    exec_argv: list
    terminal: bool = False
    actions: list = field(default_factory=list)


def cfg(**behaviour):
    base = {"behaviour": {"terminal": ""}}
    base["behaviour"].update(behaviour)
    return base


class TestTerminalResolution(unittest.TestCase):
    def test_explicit_config_wins(self):
        with mock.patch.dict(os.environ, {"TERMINAL": "alacritty"}, clear=False):
            self.assertEqual(
                L.resolve_terminal(cfg(terminal="alacritty -e")), ["alacritty", "-e"]
            )

    def test_falls_back_to_environment(self):
        with mock.patch.dict(os.environ, {"TERMINAL": "alacritty",
                                           "TERMINAL_EMULATOR": ""}, clear=False):
            self.assertEqual(L.resolve_terminal(cfg()), ["alacritty"])

    def test_falls_back_to_foot_last(self):
        with mock.patch.dict(os.environ, {"TERMINAL": "", "TERMINAL_EMULATOR": ""},
                             clear=False):
            self.assertEqual(L.resolve_terminal(cfg()), ["foot"])

    def test_malformed_config_terminal_falls_back(self):
        with mock.patch.dict(os.environ, {"TERMINAL": "", "TERMINAL_EMULATOR": ""},
                             clear=False):
            self.assertEqual(L.resolve_terminal(cfg(terminal='"unterminated')),
                             ["foot"])


class TestArgvBuilding(unittest.TestCase):
    def test_plain_app_is_untouched(self):
        app = FakeApp("X", ["/usr/bin/x", "--flag"])
        self.assertEqual(L.app_argv(app, cfg()), ["/usr/bin/x", "--flag"])

    def test_terminal_app_is_wrapped(self):
        app = FakeApp("X", ["htop"], terminal=True)
        self.assertEqual(L.app_argv(app, cfg()), ["foot", "-e", "htop"])

    def test_configured_terminal_is_used_for_wrapping(self):
        app = FakeApp("X", ["htop"], terminal=True)
        self.assertEqual(
            L.app_argv(app, cfg(terminal="alacritty")),
            ["alacritty", "-e", "htop"],
        )

    def test_configured_terminal_with_e_is_not_doubled(self):
        app = FakeApp("X", ["htop"], terminal=True)
        self.assertEqual(
            L.app_argv(app, cfg(terminal="alacritty -e")),
            ["alacritty", "-e", "htop"],
        )

    def test_configured_terminal_flags_are_kept(self):
        app = FakeApp("X", ["htop"], terminal=True)
        self.assertEqual(
            L.app_argv(app, cfg(terminal="kitty --single-instance")),
            ["kitty", "--single-instance", "-e", "htop"],
        )

    def test_app_argv_copies_the_list(self):
        app = FakeApp("X", ["/usr/bin/x"])
        argv = L.app_argv(app, cfg())
        argv.append("mutated")
        self.assertEqual(app.exec_argv, ["/usr/bin/x"])


class TestCommandFallback(unittest.TestCase):
    def test_shell_mode_uses_sh_c(self):
        argv = L.command_argv("git rebase -i | less", "shell", cfg())
        self.assertEqual(argv[-2:], ["-c", "git rebase -i | less"])

    def test_shell_mode_pipes_survive_as_one_argument(self):
        # The whole line is a single argv entry handed to `sh -c`, not split.
        argv = L.command_argv("a | b && c", "shell", cfg())
        self.assertEqual(argv[-1], "a | b && c")

    def test_shell_mode_preserves_metacharacters(self):
        argv = L.command_argv("a | b && c", "shell", cfg())
        self.assertIn("a | b && c", argv)

    def test_shell_mode_wraps_in_terminal(self):
        argv = L.command_argv("ls", "shell", cfg())
        self.assertEqual(argv[0], "foot")
        self.assertEqual(argv[1], "-e")

    def test_argv_mode_tokenises(self):
        self.assertEqual(
            L.command_argv("echo hello world", "argv", cfg()),
            ["echo", "hello", "world"],
        )

    def test_argv_mode_drops_metacharacters(self):
        # shlex keeps these as literal tokens; nothing interprets them.
        self.assertEqual(L.command_argv("a | b", "argv", cfg()), ["a", "|", "b"])
        self.assertNotIn("|", L.command_argv("a | b", "argv", cfg())[:1])

    def test_argv_mode_does_not_wrap_in_terminal(self):
        self.assertEqual(L.command_argv("ls", "argv", cfg()), ["ls"])

    def test_argv_mode_empty_is_none(self):
        self.assertIsNone(L.command_argv("   ", "argv", cfg()))

    def test_off_mode_is_none(self):
        self.assertIsNone(L.command_argv("ls", "off", cfg()))

    def test_empty_query_is_none_in_every_mode(self):
        for mode in ("shell", "argv", "off"):
            self.assertIsNone(L.command_argv("", mode, cfg()))

    def test_argv_mode_bad_quotes_is_none(self):
        self.assertIsNone(L.command_argv('echo "unterminated', "argv", cfg()))


class TestSingleInstance(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="secretsway-run-")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.dir, ignore_errors=True))
        patcher = mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": self.dir})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_signal_with_no_instance_is_false(self):
        self.assertFalse(L.signal_existing())

    def test_listen_then_signal_is_true(self):
        server = L.listen_for_toggle()
        self.addCleanup(L.cleanup_socket, server)
        self.assertTrue(L.signal_existing())

    def test_socket_is_owner_only(self):
        server = L.listen_for_toggle()
        self.addCleanup(L.cleanup_socket, server)
        self.assertEqual(os.stat(L.socket_path()).st_mode & 0o777, 0o600)

    def test_socket_is_owner_only_before_any_chmod(self):
        # The chmod after bind() is not the control: there is a window between
        # the two in which a world-connectable socket exists.  The node must
        # already be 0600 the instant bind() creates it, which is what the
        # 0177 umask around the bind buys.  Watch bind() itself and inspect the
        # mode before listen_for_toggle's chmod can run.
        observed = []
        real_bind = socket.socket.bind

        def spy(self, address):
            real_bind(self, address)
            observed.append(os.stat(address).st_mode & 0o777)

        server = None
        with mock.patch.object(socket.socket, "bind", spy):
            server = L.listen_for_toggle()
        self.addCleanup(L.cleanup_socket, server)
        self.assertEqual(observed, [0o600])

    def test_umask_is_restored_after_bind(self):
        before = os.umask(0o022)
        try:
            server = L.listen_for_toggle()
            self.addCleanup(L.cleanup_socket, server)
            # Tightening the umask for the bind must not leak into the rest of
            # the process, or every file we create afterwards becomes 0600.
            self.assertEqual(os.umask(0o022), 0o022)
        finally:
            os.umask(before)

    def test_umask_restored_even_when_bind_fails(self):
        before = os.umask(0o022)
        try:
            with mock.patch.object(socket.socket, "bind",
                                   side_effect=OSError("boom")):
                self.assertIsNone(L.listen_for_toggle())
            self.assertEqual(os.umask(0o022), 0o022)
        finally:
            os.umask(before)

    def test_stale_socket_is_replaced(self):
        # Simulate a crashed instance leaving its socket behind.
        with open(L.socket_path(), "w") as handle:
            handle.write("")
        server = L.listen_for_toggle()
        self.addCleanup(L.cleanup_socket, server)
        self.assertTrue(L.signal_existing())

    def test_two_instances_do_not_both_bind(self):
        first = L.listen_for_toggle()
        self.addCleanup(L.cleanup_socket, first)
        # A second bind on a live socket must fail rather than silently work.
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(sock.close)
        with self.assertRaises(OSError):
            sock.bind(L.socket_path())

    def test_socket_path_is_per_user(self):
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": "/run/user/1000"}):
            self.assertIn(str(os.getuid()), L.socket_path())


if __name__ == "__main__":
    unittest.main(verbosity=2)
