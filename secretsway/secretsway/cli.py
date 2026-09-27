"""Argument parsing and process start-up.

Deliberately import-light: `--dump-apps` and `--check` must work on a machine
with no GTK installed at all, so the GTK stack is only imported once we know we
are actually going to show a window.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from secretsway import config as config_mod
from secretsway import launch

USAGE_EPILOG = """\
  secretsway.toml lives at ~/.config/secretsway/secretsway.toml and is optional.

  In your sway config, swap the launcher variable:

      -set $menu fuzzel
      +set $menu secretsway

  `bindsym $mod+d exec $menu` then picks it up unchanged.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="secretsway",
        description="A sway app launcher that looks like a terminal.",
        epilog=USAGE_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config", metavar="PATH",
        help="config file (default: $SP_CONFIG, else ~/.config/secretsway/secretsway.toml)",
    )
    parser.add_argument(
        "--prefix", metavar="TEXT",
        help="override the prompt prefix, e.g. 'dev@sway ~]$'",
    )
    parser.add_argument(
        "--dump-apps", action="store_true",
        help="print the discovered applications as JSON and exit "
             "(diff this against `fuzzel --list`)",
    )
    parser.add_argument(
        "--preview", nargs="?", const="", metavar="QUERY",
        help="draw the panel as text and exit -- no compositor needed. "
             "Use to check alignment and spacing without opening a window.",
    )
    parser.add_argument(
        "--width", type=int, default=100, metavar="COLS",
        help="column count for --preview (default: 100)",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="verify config, the app list and the GTK/layer-shell stack, then exit",
    )
    return parser


def cmd_dump_apps(config: dict) -> int:
    from secretsway.apps import load_apps

    apps = load_apps(config["behaviour"].get("desktop_file_dirs") or [])
    payload = [
        {
            "name": app.name,
            "generic_name": app.generic_name,
            "exec": app.exec_display,
            "argv": app.exec_argv,
            "terminal": app.terminal,
            "desktop_id": app.desktop_id,
            "actions": [a.name for a in app.actions],
        }
        for app in apps
    ]
    json.dump(payload, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


def cmd_check(config: dict) -> int:
    from secretsway import __version__
    from secretsway.apps import load_apps

    ok = True

    print(f"build: {__version__} ({sp_build()})")
    print(f"running from: {os.path.dirname(os.path.abspath(__file__))}/..")
    print("config: ok")

    apps = load_apps(config["behaviour"].get("desktop_file_dirs") or [])
    print(f"applications: {len(apps)} found")
    if not apps:
        print("  ! none -- check XDG_DATA_HOME / XDG_DATA_DIRS")
        ok = False

    try:
        from secretsway.window import _load_gi
        from secretsway import probe
        Gtk, Gdk, GLib, LayerShell = _load_gi()
        print(f"gtk4: {Gtk.get_major_version()}.{Gtk.get_minor_version()}")
        major = getattr(LayerShell, "get_major_version", None)
        minor = getattr(LayerShell, "get_minor_version", None)
        if major and minor:
            print(f"gtk4-layer-shell: {major()}."
                  f"{minor() if callable(minor) else minor}")
        else:
            print("gtk4-layer-shell: (version not introspected)")
        print(f"layer-shell supported: {LayerShell.is_supported()}")
        if not LayerShell.is_supported():
            print("  ! the compositor does not implement wlr-layer-shell;")
            print("    the strip will appear as an ordinary window instead.")
            ok = False
        if not probe.report():
            ok = False
        ok = _check_font(config) and ok
    except SystemExit as exc:
        print(exc)
        ok = False
    except Exception as exc:
        print(f"gtk4: unavailable ({exc})")
        ok = False

    print("OK" if ok else "PROBLEMS FOUND")
    return 0 if ok else 1


def _check_font(config: dict) -> bool:
    """Report the font the panel will actually use, and whether it is fixed-pitch.

    The panel is a character grid, so a proportional font -- or one whose
    advance is a fraction of a pixel that we round away -- makes columns drift
    in ways that are invisible in --preview and obvious on screen.  Worth
    checking directly rather than leaving to be discovered visually.
    """
    try:
        import gi
        gi.require_foreign("cairo")
        from secretsway import render
    except Exception as exc:
        print(f"  font: could not measure ({exc})")
        return True

    try:
        # Measured headlessly, off a 1x1 cairo surface.  Building a Gtk widget
        # here would need an initialised Gtk and would take the process down
        # before it could print anything.
        ctx = render.pango_context()
        family = render.resolve_family(config["ui"]["font"])
        report = render.font_report(ctx, family,
                                    float(config["ui"]["font_size"]))
        print(f"  {report}")
        return "NOT MONOSPACED" not in report
    except Exception as exc:
        print(f"  font: measurement failed ({exc})")
        return True


def sp_build() -> str:
    from secretsway import BUILD
    return BUILD


def cmd_preview(config: dict, query: str, width: int) -> int:
    """Render the panel headlessly.

    Layout bugs -- a column that drifts, text that overflows, a window that is
    the wrong height -- are all decidable here, in a terminal, without a
    compositor in the picture.
    """
    from secretsway import panel
    from secretsway.apps import load_apps
    from secretsway.usage import Usage

    apps = load_apps(config["behaviour"].get("desktop_file_dirs") or [])
    usage = Usage.load()
    layout = panel.build_layout(
        apps=apps,
        usage=usage,
        query=query,
        config=config,
        prefix=panel.default_prefix(),
        metrics=render_metrics(),
        cols=width,
    )
    print(panel.render_text(layout, width))
    return 0


def render_metrics():
    """A predictable cell size, so --preview does not depend on a font."""
    from secretsway.render import Metrics
    return Metrics(cell_w=8, cell_h=18, ascent=14)


def main(argv: list | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        config = config_mod.load(args.config)
    except config_mod.ConfigError as exc:
        print(f"secretsway: {exc}", file=sys.stderr)
        return 2

    if args.dump_apps:
        return cmd_dump_apps(config)

    if args.check:
        return cmd_check(config)

    if args.preview is not None:
        return cmd_preview(config, args.preview, args.width)

    # Hand over to a resident instance if there is one, rather than stacking a
    # second strip on top of it and fighting over the keyboard.  Say so: a
    # silent exit here is indistinguishable from the launcher doing nothing.
    if launch.signal_existing():
        if os.environ.get("SP_QUIET") != "1":
            print("secretsway: another instance is already running; toggled it")
        return 0

    try:
        from secretsway.window import Launcher
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 3

    try:
        return Launcher(config, prefix=args.prefix).run()
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
