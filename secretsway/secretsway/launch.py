"""Starting applications, and making sure only one launcher exists at a time.

Deliberately *not* using `swaymsg exec`, even though this is a sway launcher.
Command expansion then happens twice more (swaymsg, then sway, then `sh` -- four
levels in all) and swaymsg additionally splits on commas, so a desktop entry
with a quoted argument containing `$` or a backtick can be expanded into
something that actually runs.  We already run inside sway and inherit
WAYLAND_DISPLAY and XDG_RUNTIME_DIR, so a direct spawn loses nothing and the
argv we hand over is exactly the argv the app receives.
"""

from __future__ import annotations

import errno
import os
import shlex
import socket
import subprocess

# Matched against the "argv[0] basename" so "foot" and "/usr/bin/foot" both work.
_TERMINAL_FALLBACK = "foot"


def resolve_terminal(config: dict) -> list[str]:
    """The terminal command to wrap Terminal=true apps and shell fallbacks in.

    This is the command *and its flags*; the ``-e`` that introduces the program
    is added by us, so `terminal = "alacritty"` and `terminal = "alacritty -e"`
    both work rather than the latter producing `-e -e`.
    """
    configured = (config.get("behaviour", {}).get("terminal") or "").strip()
    if configured:
        try:
            parts = shlex.split(configured)
        except ValueError:
            parts = []
        if parts:
            return parts

    for var in ("TERMINAL", "TERMINAL_EMULATOR"):
        value = os.environ.get(var, "").strip()
        if value:
            try:
                parts = shlex.split(value)
            except ValueError:
                continue
            if parts:
                return parts
    return [_TERMINAL_FALLBACK]


def _wrap_in_terminal(argv: list[str], config: dict) -> list[str]:
    terminal = resolve_terminal(config)
    if terminal and terminal[-1] != "-e":
        terminal = terminal + ["-e"]
    return terminal + list(argv)


def app_argv(app, config: dict) -> list[str]:
    """The argv to exec for a desktop entry, wrapped if it needs a terminal."""
    if app.terminal:
        return _wrap_in_terminal(app.exec_argv, config)
    return list(app.exec_argv)


def action_argv(action, config: dict) -> list[str]:
    argv = list(action.exec_argv)
    if getattr(action, "terminal", False):
        return _wrap_in_terminal(argv, config)
    return argv


def command_argv(query: str, mode: str, config: dict) -> list[str] | None:
    """Build the argv for a query that matched no application.

    "shell" keeps prompt semantics (pipes, redirects) at the cost of allowing
    arbitrary execution from a keybound launcher.  "argv" tokenises and drops
    metacharacters.  Returns None if there is nothing runnable.
    """
    query = query.strip()
    if not query:
        return None

    if mode == "shell":
        # $SHELL if it is set to something real, else /bin/sh.
        shell = os.environ.get("SHELL") or "/bin/sh"
        return _wrap_in_terminal([shell, "-c", query], config)

    if mode == "argv":
        try:
            parts = shlex.split(query)
        except ValueError:
            return None
        return parts or None

    return None  # "off"


def spawn(argv: list[str]) -> None:
    """Start a process fully detached from this one.

    start_new_session puts the child in its own session so it survives us being
    replaced or killed, and so a Ctrl-C aimed at the launcher does not reach it.
    """
    try:
        subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    except (OSError, ValueError) as exc:
        # A broken .desktop should not take the launcher down with it.
        print(f"secretsway: could not run {argv[0]!r}: {exc}", flush=True)


# --- single instance -------------------------------------------------------
#
# A second $mod+d must toggle the existing strip, not stack a second one on top
# of it fighting over the keyboard.

def socket_path() -> str:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    return os.path.join(runtime, f"secretsway-{os.getuid()}.sock")


def signal_existing(timeout: float = 0.25) -> bool:
    """Tell a running launcher to toggle.  True if one was there."""
    path = socket_path()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(path)
            sock.sendall(b"toggle\n")
            return True
    except OSError:
        return False


def listen_for_toggle():
    """Bind the control socket, clearing a stale one if we crashed."""
    path = socket_path()
    try:
        os.unlink(path)
    except OSError as exc:
        if exc.errno != errno.ENOENT:
            pass  # a live socket is fine to clobber; we are the new instance

    # Tighten the umask *around* the bind rather than chmod-ing afterwards.
    # bind() creates the socket node with the process umask, which is 022 on
    # most systems -- i.e. 0755, world-connectable.  There is a window between
    # bind() and a chmod where another local user could connect, and closing
    # that window is the whole point.  Under 0177 the node is 0600 from the
    # instant it exists, so the chmod below only ever narrows further.
    previous_umask = os.umask(0o177)
    try:
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            server.bind(path)
            server.listen(4)
            server.setblocking(False)
        except OSError:
            server.close()
            return None  # single-instance is a nicety, not a requirement
    finally:
        os.umask(previous_umask)

    # Belt and braces: owner-only, so another user on the box cannot toggle our
    # launcher even where the umask was already tighter than we asked for.
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return server


def cleanup_socket(server) -> None:
    path = socket_path()
    if server is not None:
        try:
            server.close()
        except OSError:
            pass
    try:
        os.unlink(path)
    except OSError:
        pass
