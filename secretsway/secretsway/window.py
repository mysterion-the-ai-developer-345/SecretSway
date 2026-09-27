"""The GTK4 window: layer-shell setup, key handling, and wiring the layout to
the screen.

This is the one module that cannot be exercised without a compositor, so it is
deliberately thin -- all the interesting decisions live in `render.Layout`,
which is unit-tested.  What is here is plumbing, plus the parts of the GTK API
that have to be right.
"""

from __future__ import annotations

import os
import socket
import time
import traceback

from secretsway import launch, panel, render
from secretsway.apps import load_apps
from secretsway.match import rank
from secretsway.usage import Usage

# Sway can briefly report the surface as unfocused while the layer surface is
# being committed.  Hiding on that would make the launcher flash and vanish.
_FOCUS_GRACE = 0.20

# Reverse-DNS, and deliberately not tied to a person: anyone can build this.
# Change it if you fork it, so your build does not collide with the upstream
# single-instance socket on a machine that has both installed.
APP_ID = "org.secretsway.Launcher"

_LOG_PATH = os.path.join(
    os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")),
    "secretsway", "secretsway.log",
)


def _log(message: str) -> None:
    """Append to the log file.

    SecretSway is a GUI process launched by sway, so stderr lands in the journal where
    nobody reads it.  Anything that can explain "it is blank" goes to a file the
    user can paste.
    """
    try:
        os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
        with open(_LOG_PATH, "a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%H:%M:%S')} {message}\n")
    except OSError:
        pass


def _log_exception(where: str) -> None:
    _log(f"EXCEPTION in {where}:\n{traceback.format_exc()}")


def _preload_layer_shell() -> str:
    """Load libgtk4-layer-shell ahead of libwayland-client, and say where from.

    The preload is mandatory, not cosmetic.  gtk4-layer-shell shims selected
    libwayland calls and delegates to the real client, so the dynamic linker
    has to see it *before* libwayland-client.  PyGObject pulls the latter in
    transitively via GTK/GDK, and without this the layer-shell negotiation
    fails silently -- the window comes up as an ordinary one.

    We search ~/.local as well as the system paths so that running secretsway
    directly, without the wrapper's LD_PRELOAD, still works.
    """
    import glob
    from ctypes import CDLL

    home = os.path.expanduser("~")
    candidates = ["libgtk4-layer-shell.so"]
    candidates += sorted(glob.glob(f"{home}/.local/lib/*/libgtk4-layer-shell.so"))
    candidates += sorted(glob.glob("/usr/lib/*/libgtk4-layer-shell.so"))
    candidates += sorted(glob.glob("/usr/local/lib/*/libgtk4-layer-shell.so"))

    errors = []
    for candidate in candidates:
        try:
            CDLL(candidate)
            return candidate
        except OSError as exc:
            errors.append(f"    {candidate}: {exc}")

    raise SystemExit(
        "secretsway: could not load libgtk4-layer-shell.so\n"
        + "\n".join(errors)
        + "\n  It is not packaged for Ubuntu 24.04. Build it with:\n"
        "      ./install.sh --layer-shell"
    )


def _extend_typelib_path() -> str:
    """Make Gtk4LayerShell-1.0.typelib findable wherever we put it.

    The typelib lives next to the library, and a locally built one lands in
    ~/.local, which is not a default girepository search path.  Prepending it
    to GI_TYPELIB_PATH is what lets `python3 ~/.config/secretsway` work
    without the wrapper having to set the environment for us.
    """
    import glob

    home = os.path.expanduser("~")
    patterns = [
        f"{home}/.local/lib/*/girepository-1.0",
        f"{home}/.local/lib/girepository-1.0",
        "/usr/lib/*/girepository-1.0",
        "/usr/local/lib/*/girepository-1.0",
    ]
    for pattern in patterns:
        for directory in sorted(glob.glob(pattern)):
            if not os.path.exists(
                os.path.join(directory, "Gtk4LayerShell-1.0.typelib")
            ):
                continue
            existing = os.environ.get("GI_TYPELIB_PATH", "")
            if directory not in existing.split(":"):
                os.environ["GI_TYPELIB_PATH"] = (
                    f"{directory}:{existing}" if existing else directory
                )
            return directory

    # Not found in any of our search paths.  Say so precisely, because
    # "Namespace not available" on its own is a miserable thing to debug.
    raise SystemExit(
        "secretsway: Gtk4LayerShell-1.0.typelib not found.\n"
        "  Looked in:\n"
        + "".join(f"    {p}\n" for p in patterns)
        + "  It should sit next to libgtk4-layer-shell.so. If you built it with\n"
        "  install.sh it is under ~/.local -- check:\n"
        "      find ~/.local -name 'Gtk4LayerShell-1.0.typelib'\n"
        "  If that finds nothing, rebuild it with ./install.sh --layer-shell"
    )


def _load_gi():
    """Import the GTK stack, preloading libgtk4-layer-shell first."""
    from_where = _preload_layer_shell()
    # Must happen before `import gi`, which snapshots the search path.
    _extend_typelib_path()

    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    gi.require_version("Pango", "1.0")
    gi.require_version("PangoCairo", "1.0")
    gi.require_version("Gtk4LayerShell", "1.0")

    # GTK hands the DrawingArea draw callback a cairo.Context, and PyGObject
    # cannot marshal one until it has been told about pycairo.  Without this
    # the callback raises "Couldn't find foreign struct converter for
    # 'cairo.Context'" *before* our code runs, on every frame -- so the panel is
    # never painted and the error never reaches our own handler.
    try:
        gi.require_foreign("cairo")
    except (ValueError, ImportError):
        import cairo  # noqa: F401  -- importing it registers the converter

    from gi.repository import Gdk, GLib, Gtk, Gtk4LayerShell

    Gtk4LayerShell._sp_loaded_from = from_where  # type: ignore[attr-defined]
    return Gtk, Gdk, GLib, Gtk4LayerShell


def _keymap(Gdk) -> dict:
    """Resolve the keyvals we bind, tolerating ones this Gdk release lacks.

    GI constant names vary between releases -- KP_Enter and ISO_Enter in
    particular are not everywhere -- so a missing one costs a binding rather
    than the launcher.
    """
    def k(name, fallback=0):
        return getattr(Gdk, name, fallback)

    enter = {v for v in (k("KEY_Return"), k("KEY_KP_Enter"), k("KEY_ISO_Enter")) if v}
    return {
        "escape": k("KEY_Escape", 0xFF1B),
        "enter": enter,
        "down": k("KEY_Down", 0xFF54),
        "up": k("KEY_Up", 0xFF52),
        "tab": k("KEY_Tab", 0xFF09),
        "page_up": k("KEY_Page_Up", 0xFF55),
        "page_down": k("KEY_Page_Down", 0xFF56),
        "ctrl_n": k("KEY_n", 0x006E),
        "ctrl_p": k("KEY_p", 0x0070),
        "ctrl_mask": Gdk.ModifierType.CONTROL_MASK,
    }


class Launcher:
    def __init__(self, config: dict, prefix: str | None = None):
        self.config = config
        self.ui = config["ui"]
        self.colors = config["colors"]
        self.behaviour = config["behaviour"]

        self.Gtk, self.Gdk, self.GLib, self.LayerShell = _load_gi()
        self.keys = _keymap(self.Gdk)

        self.apps = load_apps(self.behaviour.get("desktop_file_dirs") or [])
        self.usage = Usage.load()
        self.freq_ranks = self.usage.frequency_ranks(self.apps)
        self.query = ""
        self.matches: list = []        # (score, App, indices)
        self.selected = 0
        self.scroll = 0
        self.command_argv: list | None = None
        self.cursor_on = True

        self.prefix = prefix if prefix is not None else self._default_prefix()
        self.family = render.resolve_family(self.ui["font"])
        self.metrics = render.Metrics()
        self.cols = 100
        self._measured = False
        self.layout = render.Layout(prefix=self.prefix, cols=self.cols)

        self.app = None
        self.window = None
        self.area = None
        self.entry = None
        self.server = None
        self._mapped_at = 0.0
        self._blink_id = 0
        self._draws = 0
        self._src_mtimes: dict = {}

    # --- prompt ------------------------------------------------------------

    def _default_prefix(self) -> str:
        return panel.default_prefix()

    # --- state -------------------------------------------------------------

    @property
    def showing_command(self) -> bool:
        """True when the single offered row is the run-as-command fallback."""
        return not self.matches and self.command_argv is not None

    def browse_order(self) -> list:
        """Result tuples in usage order, for when the box is empty."""
        return panel.browse_matches(self.query, self.apps, self.usage)

    def refilter(self) -> None:
        self.matches = panel.browse_matches(self.query, self.apps, self.usage)
        total = len(self.matches)

        # Re-ranking can shrink the list underneath the current position, so
        # both the selection and the window have to be pulled back in range.
        self.selected = render.clamp(self.selected, 0, max(0, total - 1))
        self.scroll = render.scroll_for(
            self.scroll, self.selected, total, int(self.ui["max_rows"])
        )

        self.command_argv = None
        if not self.matches and self.query.strip():
            self.command_argv = launch.command_argv(
                self.query, self.behaviour["command_fallback"], self.config
            )
        self.refresh()

    def build_layout(self) -> render.Layout:
        layout = panel.build_layout(
            apps=self.apps,
            usage=self.usage,
            query=self.query,
            config=self.config,
            prefix=self.prefix,
            selected=self.selected,
            scroll=self.scroll,
            metrics=self.metrics,
            cols=self.cols,
            command_argv=self.command_argv,
        )
        layout.cursor_on = self.cursor_on
        return layout

    # --- drawing -----------------------------------------------------------

    def refresh(self) -> None:
        if self.area is None:
            return
        layout = self.build_layout()
        self.layout = layout
        # Always request a size, including before the first measurement.  If we
        # waited for metrics the DrawingArea would be allocated 0x0, GTK would
        # never fire `draw` on a zero-sized surface, and we would never learn
        # the metrics -- a deadlock that leaves the strip invisible.
        # GTK's size APIs take whole pixels; the fractional cell width belongs
        # in the drawing maths, not here.  Passing a float raises rather than
        # truncating, which leaves the window unsized and the panel invisible.
        self.area.set_size_request(
            int(round(self.cols * self.metrics.cell_w)), layout.height
        )
        self.area.queue_draw()

    def on_draw(self, _area, cr, width, height, _user_data) -> bool:
        try:
            self._draw(cr, width, height)
        except Exception:
            # A throw inside the draw callback leaves GTK with nothing painted,
            # which shows up as a blank box and no error anywhere visible.
            _log_exception("on_draw")
            return False
        return False

    def _draw(self, cr, width, height) -> None:
        if not self._measured:
            self._measure(cr)
        cell_w = self.metrics.cell_w
        if cell_w:
            # Trust the width we were actually given; the compositor may hand
            # over a few stray pixels beyond an exact cell multiple.
            self.cols = max(20, int(width // cell_w))
        # cols and metrics are plain attributes; height is derived, so updating
        # these two is enough for draw() to pick up the new size.
        self.layout.cols = self.cols
        self.layout.metrics = self.metrics
        render.draw(cr, self.layout, self.colors, self.family,
                    int(self.ui["border_width"]))

        if self._draws < 5:
            self._draws += 1
            _log(f"draw#{self._draws} surface={width}x{height} "
                 f"cell={self.metrics.cell_w}x{self.metrics.cell_h} "
                 f"cols={self.cols} lines={self.layout.line_count} "
                 f"layout_h={self.layout.height} rows={len(self.layout.rows)} "
                 f"family={self.family!r}")

    def _measure(self, cr) -> None:
        """Read the cell grid off the real surface, once.

        The Pango context is built from the draw callback's own cairo context
        rather than from a widget: `Pango.Layout.get_context()` is not among
        the methods PyGObject exposes, and reaching for it threw on every
        frame, which left the panel blank with the error buried in the log.

        `_measured` is set even if measurement fails, so a font problem
        degrades to approximate metrics instead of a window that never paints.
        """
        size = float(self.ui["font_size"])
        try:
            from gi.repository import PangoCairo
            ctx = PangoCairo.create_context(cr)
            self.metrics = render.measure(ctx, self.family, size)
            self._log(render.font_report(ctx, self.family, size))
            if not self.metrics.monospace:
                _log("WARNING: the resolved font is not monospaced, so the "
                     "character grid will drift. Install fonts-jetbrains-mono, "
                     "or set ui.font in secretsway.toml to a family you have.")
        except Exception:
            _log_exception("_measure")
            self.metrics = render.Metrics(size=size)
        finally:
            self._measured = True
        # The first allocation was made with guessed metrics; ask for the
        # right size now that we know it.
        self.refresh()

    def blink(self) -> bool:
        self.cursor_on = not self.cursor_on
        self.refresh()
        return self.GLib.SOURCE_CONTINUE

    # --- window ------------------------------------------------------------

    def monitor_under_pointer(self):
        try:
            display = self.Gdk.Display.get_default()
            seat = display.get_default_seat()
            ok, x, y = seat.get_pointer().get_position()
            if ok:
                return display.get_monitor_at_point(int(x), int(y))
            return display.get_primary_monitor() or display.get_monitors()[0]
        except Exception:
            try:
                return self.Gdk.Display.get_default().get_monitors()[0]
            except Exception:
                return None

    def setup_layer_surface(self, win) -> None:
        LS = self.LayerShell
        LS.init_for_window(win)
        LS.set_layer(win, LS.Layer.OVERLAY)
        # All three edges: two opposite anchored edges stretch across the
        # output, the third fixes it to the top.  BOTTOM is deliberately left
        # unanchored so the strip keeps only the height it needs.
        LS.set_anchor(win, LS.Edge.LEFT, True)
        LS.set_anchor(win, LS.Edge.RIGHT, True)
        LS.set_anchor(win, LS.Edge.TOP, True)
        LS.set_anchor(win, LS.Edge.BOTTOM, False)
        LS.set_margin(win, LS.Edge.TOP, int(self.ui["top_margin"]))
        # Must stay 0: a non-zero exclusive zone would shove your windows down
        # every time the launcher opens.
        LS.set_exclusive_zone(win, 0)
        LS.set_namespace(win, "secretsway")
        # Set before present(), so the grab is in the initial surface commit.
        LS.set_keyboard_mode(win, LS.KeyboardMode.EXCLUSIVE)
        monitor = self.monitor_under_pointer()
        if monitor is not None:
            LS.set_monitor(win, monitor)

        # Read the values back: if the compositor did not accept them, the
        # surface comes up content-sized in the corner instead of a strip.
        try:
            _log(
                "layer-shell: "
                f"supported={LS.is_supported()} "
                f"layer={LS.get_layer(win)} "
                f"anchors(L,R,T,B)="
                f"{LS.get_anchor(win, LS.Edge.LEFT)},"
                f"{LS.get_anchor(win, LS.Edge.RIGHT)},"
                f"{LS.get_anchor(win, LS.Edge.TOP)},"
                f"{LS.get_anchor(win, LS.Edge.BOTTOM)} "
                f"margin_top={LS.get_margin(win, LS.Edge.TOP)} "
                f"exclusive={LS.get_exclusive_zone(win)} "
                f"kbd={LS.get_keyboard_mode(win)} "
                f"monitor={getattr(monitor, 'get_model', lambda: '?')() if monitor else None}"
            )
        except Exception:
            _log_exception("layer-shell readback")

    def build_window(self) -> None:
        Gtk = self.Gtk
        win = Gtk.Window(application=self.app)
        win.set_decorated(False)
        win.set_resizable(False)
        # A floor under the layer surface's size, in case the DrawingArea's
        # own request is somehow ignored.
        win.set_default_size(int(round(self.cols * self.metrics.cell_w)),
                             int(self.metrics.cell_h * 3))
        self.setup_layer_surface(win)

        # A real, transparent Entry owns the text so dead keys, IMEs and paste
        # all behave normally; we draw our own cursor and hide its caret.  It
        # is overlaid rather than packed, so it contributes no height.
        #
        # The window gets the panel colour from CSS as well as from Cairo, so
        # the strip is dark grey even in the frames where our draw callback
        # does not run.  A layer surface has no compositor backdrop, so
        # anything we do not paint is whatever GTK's default is -- white.
        panel = self.colors["panel"]
        css = f"""
            window.secretsway-strip {{ background-color: {panel}; }}
            entry.secretsway-input {{
                background: transparent;
                border: none;
                box-shadow: none;
                outline: none;
                color: transparent;
                caret-color: transparent;
                padding: 0;
                margin: 0;
            }}
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode("utf-8"))
        Gtk.StyleContext.add_provider_for_display(
            self.Gdk.Display.get_default(), provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
        win.add_css_class("secretsway-strip")

        entry = Gtk.Entry()
        entry.add_css_class("secretsway-input")
        entry.set_has_frame(False)
        entry.set_can_focus(True)
        entry.set_can_target(False)   # invisible to the mouse, still focusable
        entry.connect("changed", self.on_text_changed)
        self.entry = entry

        area = Gtk.DrawingArea()
        area.set_draw_func(self.on_draw, None)
        self.area = area

        overlay = Gtk.Overlay()
        overlay.set_child(area)
        overlay.add_overlay(entry)
        win.set_child(overlay)

        controller = Gtk.EventControllerKey()
        controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        controller.connect("key-pressed", self.on_key_pressed)
        win.add_controller(controller)

        win.connect("close-request", self.on_close_request)
        win.connect("notify::is-active", self.on_active_changed)
        self.window = win

    # --- input -------------------------------------------------------------

    def on_text_changed(self, entry) -> None:
        text = entry.get_text()
        if text == self.query:
            return
        self.query = text
        # A new query is a new list: start at the top rather than wherever the
        # previous one happened to be scrolled to.
        self.selected = 0
        self.scroll = 0
        self.refilter()

    def on_key_pressed(self, _controller, keyval, _keycode, state) -> bool:
        k = self.keys
        ctrl = bool(state & k["ctrl_mask"])

        if keyval == k["escape"]:
            self.hide()
            return True
        if keyval in k["enter"]:
            if ctrl:
                self.launch_action()
            else:
                self.launch_selected()
            return True
        if keyval == k["down"] or (ctrl and keyval == k["ctrl_n"]):
            self.move(1)
            return True
        if keyval == k["up"] or (ctrl and keyval == k["ctrl_p"]):
            self.move(-1)
            return True
        if keyval == k["tab"]:
            self.move(-1 if ctrl else 1)
            return True
        if keyval == k["page_down"]:
            self.page(1)
            return True
        if keyval == k["page_up"]:
            self.page(-1)
            return True
        return False  # not ours -- let the Entry have it

    def move(self, delta: int) -> None:
        """Move the selection, keeping it inside the visible window.

        Clamps at the ends rather than cycling, then slides the window only if
        the new position would fall outside it -- so the highlight never
        disappears below the fold.
        """
        total = len(self.matches)
        if not total:
            return
        self.selected = render.move_index(
            self.selected, delta, total, bool(self.ui.get("wrap", False))
        )
        self.scroll = render.scroll_for(
            self.scroll, self.selected, total, int(self.ui["max_rows"])
        )
        self.refresh()

    def page(self, direction: int) -> None:
        """PageUp / PageDown: jump a window at a time."""
        self.move(direction * max(1, int(self.ui["max_rows"]) - 1))

    def record_use(self, app) -> None:
        """Remember this launch, then refresh the ordering that depends on it."""
        self.usage.record(app.desktop_id)
        self.usage.save()
        self.freq_ranks = self.usage.frequency_ranks(self.apps)

    def launch_selected(self) -> None:
        if self.showing_command:
            if self.command_argv:
                launch.spawn(self.command_argv)
        elif self.matches:
            app = self.matches[self.selected][1]
            launch.spawn(launch.app_argv(app, self.config))
            self.record_use(app)
        self.hide()

    def launch_action(self) -> None:
        if not self.matches:
            return
        app = self.matches[self.selected][1]
        actions = getattr(app, "actions", None)
        if not actions:
            return
        launch.spawn(launch.action_argv(actions[0], self.config))
        self.record_use(app)
        self.hide()

    # --- show / hide -------------------------------------------------------

    def _log_sizes(self) -> bool:
        """Report the sizes GTK actually allocated, once, after mapping.

        A widget that is allocated nothing is never drawn, and never drawn
        looks exactly like a widget whose draw callback is broken -- so this
        is the thing worth knowing.
        """
        try:
            win = self.window
            area = self.area
            _log(
                f"sizes window={win.get_width()}x{win.get_height()} "
                f"area={area.get_width()}x{area.get_height()} "
                f"mapped={bool(win.get_mapped())} "
                f"visible={bool(area.get_visible())} "
                f"realized={bool(area.get_realized())}"
            )
        except Exception:
            _log_exception("_log_sizes")
        return False

    def show(self) -> None:
        self.query = ""
        self.selected = 0
        self.scroll = 0
        if self.entry is not None:
            self.entry.set_text("")   # fires changed; the guard swallows it
        self.refilter()

        self.cursor_on = True
        self.refresh()

        self._mapped_at = time.monotonic()
        self.window.present()
        if self.entry is not None:
            self.entry.grab_focus()
        self.GLib.timeout_add(250, self._log_sizes)

        blink_ms = int(self.ui["cursor_blink_ms"])
        if blink_ms > 0 and not self._blink_id:
            self._blink_id = self.GLib.timeout_add(blink_ms, self.blink)

    def hide(self) -> None:
        if self._blink_id:
            self.GLib.source_remove(self._blink_id)
            self._blink_id = 0
        if self.window is not None:
            self.window.set_visible(False)

    def toggle(self) -> None:
        if self.window is not None and self.window.get_visible():
            self.hide()
        else:
            self.show()

    def on_close_request(self, _win) -> bool:
        # Closing means "dismiss", not "quit": the process stays resident so
        # the next $mod+d is instant.
        self.hide()
        return True

    def on_active_changed(self, win, _param) -> None:
        # The strip has an exclusive keyboard grab, so losing focus means the
        # user has moved on.  Without this, a missed Escape would leave the
        # launcher sitting over the desktop eating every keystroke.
        if win.get_property("is-active"):
            return
        if time.monotonic() - self._mapped_at < _FOCUS_GRACE:
            return
        if win.get_visible():
            self.hide()

    # --- single instance ---------------------------------------------------

    def on_socket_ready(self, source, _condition) -> bool:
        try:
            conn, _ = source.accept()
        except OSError:
            return True
        with conn:
            try:
                data = conn.recv(64)
            except OSError:
                return True
        if data.strip() == b"toggle":
            self.toggle()
        return True

    # --- lifecycle ---------------------------------------------------------

    def on_activate(self, _app) -> None:
        self.build_window()
        self.refresh()
        self.show()

    def _source_mtimes(self) -> dict:
        import glob
        here = os.path.dirname(os.path.abspath(__file__))
        root = os.path.dirname(here)
        paths = glob.glob(os.path.join(here, "*.py")) + [
            os.path.join(root, "__main__.py")
        ]
        stamps = {}
        for path in paths:
            try:
                stamps[path] = os.stat(path).st_mtime_ns
            except OSError:
                pass
        return stamps

    def _check_source_changed(self) -> bool:
        """Quit if the code underneath us changed.

        A launcher stays resident by design, which is great for speed and
        terrible while you are editing it: the running process keeps the old
        code, every new keypress just toggles that stale strip, and changes
        appear to do nothing.  Exiting on a source change means the next press
        of the key starts the new code.
        """
        if self._source_mtimes() == self._src_mtimes:
            return self.GLib.SOURCE_CONTINUE
        _log("source files changed; exiting so the next launch picks them up")
        try:
            self.app.quit()
        except Exception:
            if self.window is not None:
                self.window.destroy()
        return self.GLib.SOURCE_REMOVE

    def run(self) -> int:
        Gtk = self.Gtk
        GLib = self.GLib
        self.app = Gtk.Application(application_id=APP_ID)
        self.app.connect("activate", self.on_activate)
        # Single-instance is a convenience.  If the socket cannot be watched,
        # carry on without it rather than refusing to start -- the worst case
        # is two strips, which is the behaviour we had before this existed.
        try:
            self.server = launch.listen_for_toggle()
            if self.server is not None:
                GLib.io_add_watch(self.server, GLib.IOCondition.IN,
                                  self.on_socket_ready)
        except Exception as exc:
            print(f"secretsway: single-instance disabled ({exc})", flush=True)
            self.server = None

        self._src_mtimes = self._source_mtimes()
        GLib.timeout_add_seconds(2, self._check_source_changed)

        try:
            return self.app.run([])
        finally:
            launch.cleanup_socket(self.server)
            self.server = None
