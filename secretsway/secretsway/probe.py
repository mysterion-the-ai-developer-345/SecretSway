"""Probe the GI symbols SecretSway actually uses, before the GUI needs them.

Every name here is one secretsway calls at runtime.  GTK introspection is
version-sensitive and the names differ between releases, so a wrong guess shows
up as an AttributeError at some arbitrary point during start-up.  This module
resolves them all up front instead, so one run of `secretsway --check` reports every
name that is wrong rather than one per crash.

`REQUIRED` symbols mean secretsway cannot start without them.  `OPTIONAL` ones degrade
a feature rather than the whole launcher, and are used through `optional()` at
the call site.
"""

from __future__ import annotations

# (module, dotted path, required?)
REQUIRED: list[tuple[str, str]] = [
    # GLib
    ("GLib", "timeout_add", True),
    ("GLib", "source_remove", True),
    ("GLib", "SOURCE_CONTINUE", True),
    ("GLib", "io_add_watch", True),
    ("GLib", "IOCondition.IN", True),

    # Gtk
    ("Gtk", "Window", True),
    ("Gtk", "Application", True),
    ("Gtk", "Box", True),
    ("Gtk", "Overlay", True),
    ("Gtk", "Entry", True),
    ("Gtk", "DrawingArea", True),
    ("Gtk", "CssProvider", True),
    ("Gtk", "EventControllerKey", True),
    ("Gtk", "Orientation.VERTICAL", True),
    ("Gtk", "PropagationPhase.CAPTURE", True),
    ("Gtk", "STYLE_PROVIDER_PRIORITY_APPLICATION", True),
    ("Gtk", "StyleContext.add_provider_for_display", True),
    ("Gtk", "get_major_version", True),

    # Gdk
    ("Gdk", "Display.get_default", True),
    ("Gdk", "ModifierType.CONTROL_MASK", True),
    ("Gdk", "KEY_Escape", True),
    ("Gdk", "KEY_Return", True),
    ("Gdk", "KEY_KP_Enter", True),
    ("Gdk", "KEY_ISO_Enter", True),
    ("Gdk", "KEY_Down", True),
    ("Gdk", "KEY_Up", True),
    ("Gdk", "KEY_Tab", True),
    ("Gdk", "KEY_Page_Up", False),
    ("Gdk", "KEY_Page_Down", False),
    # Monitor-under-pointer; falls back to the first monitor.
    ("Gdk", "Display.get_default_seat", False),
    ("Gdk", "Display.get_monitor_at_point", False),
    ("Gdk", "Display.get_primary_monitor", False),
    ("Gdk", "Display.get_monitors", False),
    ("Gdk", "Seat.get_pointer", False),
    ("Gdk", "Pointer.get_position", False),
    # Ctrl+N / Ctrl+P history-style movement; ignored if absent.
    ("Gdk", "KEY_n", False),
    ("Gdk", "KEY_p", False),

    # Gtk4LayerShell
    ("Gtk4LayerShell", "init_for_window", True),
    ("Gtk4LayerShell", "set_layer", True),
    ("Gtk4LayerShell", "set_anchor", True),
    ("Gtk4LayerShell", "set_margin", True),
    ("Gtk4LayerShell", "set_exclusive_zone", True),
    ("Gtk4LayerShell", "set_namespace", True),
    ("Gtk4LayerShell", "set_keyboard_mode", True),
    ("Gtk4LayerShell", "is_supported", True),
    ("Gtk4LayerShell", "Layer.OVERLAY", True),
    ("Gtk4LayerShell", "KeyboardMode.EXCLUSIVE", True),
    ("Gtk4LayerShell", "Edge.LEFT", True),
    ("Gtk4LayerShell", "Edge.RIGHT", True),
    ("Gtk4LayerShell", "Edge.TOP", True),
    ("Gtk4LayerShell", "Edge.BOTTOM", True),
    ("Gtk4LayerShell", "set_monitor", False),
    ("Gtk4LayerShell", "get_major_version", False),
    ("Gtk4LayerShell", "get_minor_version", False),

    # Pango, for the font fallback chain.
    ("Pango", "FontDescription", False),
    ("Pango", "FontDescription.set_family", False),
    ("Pango", "FontDescription.get_family", False),
]

def _resolve(root, dotted: str):
    obj = root
    for part in dotted.split("."):
        obj = getattr(obj, part)
    return obj


def check(gi_root) -> tuple[list[str], list[str]]:
    """Return (missing_required, missing_optional) as dotted strings."""
    import gi

    missing_required: list[str] = []
    missing_optional: list[str] = []

    for spec in REQUIRED:
        module_name, dotted, required = spec
        try:
            module = getattr(gi_root, module_name)
        except AttributeError:
            (missing_required if required else missing_optional).append(
                f"{module_name} (whole namespace)"
            )
            continue
        try:
            _resolve(module, dotted)
        except AttributeError:
            (missing_required if required else missing_optional).append(
                f"{module_name}.{dotted}"
            )

    return missing_required, missing_optional


def report() -> bool:
    """Print the probe result.  True if secretsway can start."""
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Gdk", "4.0")
        gi.require_version("Gtk4LayerShell", "1.0")
        import gi.repository  # noqa: F401  -- populates the namespace
        root = gi.repository
    except Exception as exc:
        print(f"  ! could not import the GI stack: {exc}")
        return False

    # pycairo is not a GI symbol, but without it the draw callback cannot be
    # marshalled and the panel silently never paints.
    try:
        gi.require_foreign("cairo")
        import cairo  # noqa: F401
        print("  cairo: ok (pycairo present)")
    except Exception as exc:
        print(f"  ! pycairo unusable ({exc}); the panel would render blank.")
        print("    Install it with:  sudo apt install python3-cairo")
        return False

    missing_required, missing_optional = check(root)

    print(f"  probed {len(REQUIRED)} symbols")
    if missing_required:
        print(f"  ! {len(missing_required)} REQUIRED symbol(s) missing:")
        for name in missing_required:
            print(f"      {name}")
        print("    secretsway cannot start until these are resolved.")
        return False
    if missing_optional:
        print(f"  - {len(missing_optional)} optional symbol(s) absent "
              "(features degrade, launcher still runs):")
        for name in missing_optional:
            print(f"      {name}")

    print("  all required symbols present")
    return True
