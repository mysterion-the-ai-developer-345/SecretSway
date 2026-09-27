"""Configuration: a small TOML file merged over built-in defaults.

Every key has a default, so the launcher runs correctly with no secretsway.toml at
all.  An unrecognised key is a hard error rather than a silent no-op -- a typo
in a colour would otherwise show up as an invisible rendering difference.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field

DEFAULT_CONFIG_PATH = os.path.join(
    os.path.expanduser("~"), ".config", "secretsway", "secretsway.toml"
)

DEFAULTS: dict = {
    "colors": {
        "panel": "#1e1e1e",   # dark grey body of the strip
        "border": "#2f6bff",  # electric cobalt outline
        "text": "#d6d6d6",    # light grey body text
        "dim": "#6b6b6b",     # exec column, prompt prefix, status hints
        "cursor": "#2f6bff",  # the block cursor and match highlight
        "sel_bg": "#d6d6d6",  # selection is an inverted bar, terminal-style
        "sel_fg": "#1e1e1e",
    },
    "ui": {
        "font": "JetBrains Mono, DejaVu Sans Mono, monospace",
        "font_size": 14,
        "max_rows": 15,
        "border_width": 1,
        "pad_x": 2,
        "top_margin": 0,
        "cursor_blink_ms": 530,
        # false: Up at the first entry does nothing.  true: it cycles to the end.
        "wrap": False,
        # "off"   -- nothing; the row is just the app name (the default)
        # "dots"  -- usage frequency, 6 down to 1
        # "exec"  -- the raw Exec line from the .desktop file
        "right_column": "off",
        "show_generic": False,
        "show_index": True,
    },
    "behaviour": {
        # "shell" runs the query through `sh -c` so pipes and redirects work,
        # which is what makes it behave like a prompt.  "argv" tokenises with
        # shlex and execs directly, dropping every shell metacharacter.
        # "off" disables the fallback entirely: only .desktop entries launch.
        "command_fallback": "shell",
        # Empty means: $TERMINAL, then $TERMINAL_EMULATOR, then foot.
        "terminal": "",
        "desktop_file_dirs": [],
    },
}

_HEX_DIGITS = set("0123456789abcdefABCDEF")

# Keys that have been renamed.  An old secretsway.toml using the previous spelling
# keeps working rather than failing validation, so an upgrade never silently
# changes what someone's launcher looks like -- or worse, hard-errors.
#
# The old `show_right` boolean becomes a `right_column` mode: false meant "no
# Exec column", which the dots column now satisfies, and true meant "Exec".
_ALIASES = {
    "ui": {"show_exec": "right_column", "show_right": "right_column"},
}

_BOOLEAN_ALIASES = {
    "right_column": {True: "exec", False: "dots"},
}


def _apply_aliases(data: dict, section: str) -> dict:
    """Rewrite deprecated key names to their canonical ones."""
    mapping = _ALIASES.get(section, {})
    if not mapping:
        return data
    out = {}
    for key, value in data.items():
        canonical = mapping.get(key)
        if canonical is None:
            out[key] = value
            continue
        if canonical in data:
            raise ConfigError(
                f"[{section}] sets both {key!r} and {canonical!r}. "
                f"{canonical!r} is the current name -- drop {key!r}."
            )
        # A renamed key may also have changed type -- a boolean became a mode.
        value = _BOOLEAN_ALIASES.get(canonical, {}).get(value, value)
        out[canonical] = value
    return out


class ConfigError(Exception):
    pass


def _check_color(name: str, value: str) -> str:
    text = str(value).strip()
    if not text.startswith("#") or len(text) != 7:
        raise ConfigError(f"colors.{name} must be #rrggbb, got {value!r}")
    if not set(text[1:]) <= _HEX_DIGITS:
        raise ConfigError(f"colors.{name} has non-hex digits: {value!r}")
    return text.lower()


def _merge(base: dict, override: dict, path: str = "") -> dict:
    """Recursively overlay `override` onto a copy of `base`."""
    result = dict(base)
    for key, value in override.items():
        where = f"{path}.{key}" if path else key
        if isinstance(value, dict):
            if not isinstance(result.get(key), dict):
                raise ConfigError(f"{where} should be a table")
            result[key] = _merge(result[key], value, where)
        else:
            result[key] = value
    return result


def load(path: str | None = None) -> dict:
    """Load and validate the config, falling back to defaults if absent."""
    path = path or os.environ.get("SP_CONFIG") or DEFAULT_CONFIG_PATH
    data: dict = {}
    if os.path.exists(path):
        try:
            with open(path, "rb") as handle:
                data = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"could not read {path}: {exc}") from exc

    known_sections = set(DEFAULTS)
    unknown = set(data) - known_sections
    if unknown:
        raise ConfigError(
            f"unknown section(s) in {path}: {', '.join(sorted(unknown))}. "
            f"Expected any of: {', '.join(sorted(known_sections))}"
        )

    data = {section: _apply_aliases(keys, section) for section, keys in data.items()}

    for section, keys in data.items():
        if not isinstance(keys, dict):
            raise ConfigError(f"[{section}] must be a table")
        bad = set(keys) - set(DEFAULTS[section])
        if bad:
            raise ConfigError(
                f"unknown key(s) in [{section}]: {', '.join(sorted(bad))}. "
                f"Expected any of: {', '.join(sorted(DEFAULTS[section]))}"
            )

    merged = _merge(DEFAULTS, data)

    for name, value in merged["colors"].items():
        merged["colors"][name] = _check_color(name, value)

    mode = merged["behaviour"]["command_fallback"]
    if mode not in ("shell", "argv", "off"):
        raise ConfigError(
            f"behaviour.command_fallback must be shell, argv or off, got {mode!r}"
        )

    if int(merged["ui"]["max_rows"]) < 1:
        raise ConfigError("ui.max_rows must be at least 1")
    if int(merged["ui"]["font_size"]) < 6:
        raise ConfigError("ui.font_size is too small to be legible")

    column = merged["ui"]["right_column"]
    if column not in ("dots", "exec", "off"):
        raise ConfigError(
            f'ui.right_column must be "dots", "exec" or "off", got {column!r}'
        )

    dirs = merged["behaviour"]["desktop_file_dirs"]
    if isinstance(dirs, str):
        raise ConfigError("behaviour.desktop_file_dirs must be a list of paths")
    merged["behaviour"]["desktop_file_dirs"] = [
        os.path.expanduser(str(d)) for d in dirs
    ]

    return merged


def rgb(color: str) -> tuple[float, float, float]:
    """'#2f6bff' -> the (r, g, b) floats Cairo wants."""
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]
