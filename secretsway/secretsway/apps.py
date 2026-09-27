"""Discover installed applications from freedesktop.org .desktop entries.

This is the same backend fuzzel uses, so the app list should be identical to
what the user sees today.  Pure stdlib and no GTK import, so it is testable
without a compositor.

Desktop entry files are *not* really INI files -- they may repeat keys, they
use bare ``%`` in Exec, and values are unquoted -- so rather than reach for
``configparser`` this scans the handful of keys it needs directly.  That is both
faster and far more tolerant of the malformed files that ship in the wild.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
from dataclasses import dataclass, field

# Keys we pull out of [Desktop Entry].  Everything else is ignored.
_ENTRY_KEYS = {
    "type", "name", "genericname", "exec", "icon", "terminal", "hidden",
    "nodisplay", "tryexec", "onlyshowin", "notshowin", "actions", "path",
}
_LOCALISED = ("name", "genericname")

# Exec field codes, per the Desktop Entry Specification.
#   %f %F %u %U   file / URL arguments      -- we have none, so drop them
#   %d %D %n %N   deprecated directory args -- drop
#   %v %m         deprecated device / main -- drop
#   %i %c %k      icon / name / path        -- substituted
#   %%            a literal percent sign
#
# Matched in one pass so each code is handled exactly once and the substituted
# text is never rescanned for codes of its own.
_FIELD_CODE = re.compile(r"%%|%[fFuUdDnNvvmick]")

# Deprecated argument codes, dropped.  Kept for tests and documentation.
_DROP_CODES = "fFuUdDnNvvm"


@dataclass
class Action:
    """An entry from the [Desktop Action ...] groups, run with Ctrl+Return."""

    name: str
    exec_argv: list[str]


@dataclass
class App:
    name: str
    generic_name: str = ""
    exec_display: str = ""
    exec_argv: list[str] = field(default_factory=list)
    terminal: bool = False
    desktop_id: str = ""
    path: str = ""
    actions: list[Action] = field(default_factory=list)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name) or default


def data_dirs() -> list[str]:
    """XDG data directories, highest priority first."""
    dirs = [_env("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))]
    raw = _env("XDG_DATA_DIRS", "/usr/local/share:/usr/share")
    dirs.extend(part for part in raw.split(":") if part)
    # Preserve priority order while dropping duplicates.
    seen = set()
    ordered = []
    for d in dirs:
        if d and d not in seen:
            seen.add(d)
            ordered.append(d)
    return ordered


def current_desktops() -> set[str]:
    """The desktop names an entry may gate itself on.

    Sway does not reliably export XDG_CURRENT_DESKTOP, so default to "sway" --
    without it, ``OnlyShowIn=GNOME;KDE;`` entries would leak into the list.
    """
    raw = _env("XDG_CURRENT_DESKTOP", "sway")
    return {part.strip().lower() for part in raw.split(":") if part.strip()}


def _locale_candidates() -> list[str]:
    """Locale keys to try for localised names, most specific first.

    For each locale the parent is tried after the child (pt_BR, then pt), and
    the first entry that yields a value wins.  The variables are consulted in
    POSIX-precedence order, with $LANGUAGE included because gettext honours it
    and desktops do set it even though the spec only names $LC_MESSAGES.
    """
    locales: list[str] = []
    for var in ("LC_ALL", "LC_MESSAGES", "LANGUAGE", "LANG"):
        value = _env(var)
        if not value:
            continue
        for part in value.split(":"):
            part = part.strip()
            if not part or part in ("C", "POSIX"):
                continue
            # POSIX order is lang_TERRITORY.codeset@modifier, so the modifier
            # has to come off before the codeset -- splitting on "." first
            # would discard "@euro" along with ".UTF-8".
            base, _, modifier = part.partition("@")
            base = base.split(".", 1)[0]          # drop .UTF-8
            lang = base.replace("-", "_")         # pt-br -> pt_BR
            # Spec order for lang_COUNTRY@MODIFIER is:
            #   lang_COUNTRY@MODIFIER, lang_COUNTRY, lang@MODIFIER, lang
            keys = []
            if "_" in lang:
                head = lang.partition("_")[0]
                if modifier:
                    keys.append(f"{lang}@{modifier}")
                keys.append(lang)
                if modifier:
                    keys.append(f"{head}@{modifier}")
                keys.append(head)
            else:
                if modifier:
                    keys.append(f"{lang}@{modifier}")
                keys.append(lang)
            for key in keys:
                if key not in locales:
                    locales.append(key)
    return locales


def _parse(text: str) -> tuple[dict[str, str], dict[str, dict[str, str]]]:
    """Extract [Desktop Entry] keys and [Desktop Action ...] groups.

    Keys are lowercased.  Localised variants are kept alongside the bare key
    (e.g. both ``name`` and ``name[pt_br]``); picking between them is
    ``_localise``'s job, because that depends on the environment rather than on
    the file.  Groups we do not care about, such as ``[X-Foo]``, are skipped
    wholesale rather than leaking their keys into the entry.
    """
    entry: dict[str, str] = {}
    actions: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line[0] in "#;":
            continue

        if line.startswith("[") and line.endswith("]"):
            group = line[1:-1].strip()
            if group == "Desktop Entry":
                current = entry
            elif group.startswith("Desktop Action") and group != "Desktop Action":
                label = group[len("Desktop Action"):].strip()
                current = actions.setdefault(label, {})
            else:
                current = None
            continue

        if current is None:
            continue
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key, value = key.strip().lower(), value.strip()

        if key.endswith("]"):
            if key.partition("[")[0] in _LOCALISED:
                current[key] = value
            continue
        if key in _ENTRY_KEYS:
            current[key] = value

    return entry, actions


def _localise(fields: dict[str, str], base_key: str) -> str:
    """Pick the best value for `base_key`, honouring Name[lang] fallbacks.

    Keys were lowercased by `_parse`, so the locale is lowercased here too --
    otherwise "pt_BR" never finds the stored "name[pt_br]".
    """
    for loc in _locale_candidates():
        got = fields.get(f"{base_key}[{loc.lower()}]")
        if got:
            return got
    return fields.get(base_key, "")


def expand_exec(exec_string: str, icon: str = "", name: str = "",
                desktop_path: str = "") -> list[str]:
    """Turn an Exec line into an argv list, resolving the field codes.

    ``shlex`` covers the spec's quoting closely enough and errs toward safety:
    it treats ``'`` as a quote, which the spec only reserves.  A file that
    misuses it will be quoted slightly differently, not executed differently.

    Substitution is a single left-to-right pass rather than a sequence of
    ``str.replace`` calls, because sequential replacement is order-dependent
    and the substituted values come from the entry itself.  Replacing ``%c``
    after ``%i`` means an icon called ``ic%con`` has its own ``%c`` eaten by
    the *name* substitution and arrives as ``icon``; a name like ``Save 50%f``
    loses its ``%f`` to the drop pass.  One pass fixes each code exactly once
    and never re-examines text it just inserted.  Nothing here reaches a
    shell -- the result is a plain argv list either way -- but a launcher that
    shows the wrong name is just wrong.
    """
    if not exec_string:
        return []

    icon_arg = "--icon " + shlex.quote(icon) if (
        icon and not os.path.isabs(icon)) else ""

    def substitute(match: "re.Match") -> str:
        token = match.group(0)
        if token == "%%":
            return "\x00"                      # protect a literal percent sign
        if token == "%i":
            return icon_arg
        if token == "%c":
            return shlex.quote(name) if name else ""
        if token == "%k":
            return shlex.quote(desktop_path) if desktop_path else ""
        return ""                              # a deprecated arg we drop

    # Longest match first so "%%" beats a bare "%", and only the codes the spec
    # defines are considered -- a stray "%z" is left alone rather than eaten.
    out = _FIELD_CODE.sub(substitute, exec_string)

    try:
        argv = shlex.split(out.replace("\x00", "%"))
    except ValueError:
        return []
    return argv


def _is_visible(entry: dict[str, str], desktops: set[str]) -> bool:
    """The fuzzel-equivalent filter: should this entry appear in the list?"""
    if entry.get("type", "application").lower() != "application":
        return False
    if _truthy(entry.get("hidden")):
        return False
    if _truthy(entry.get("nodisplay")):
        return False

    tryexec = entry.get("tryexec", "").strip()
    if tryexec and shutil.which(tryexec) is None:
        return False

    only = entry.get("onlyshowin", "")
    if only:
        allowed = {p.strip().lower() for p in only.split(";") if p.strip()}
        if not (allowed & desktops):
            return False

    notonly = entry.get("notshowin", "")
    if notonly:
        denied = {p.strip().lower() for p in notonly.split(";") if p.strip()}
        if denied & desktops:
            return False

    return True


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() == "true"


def _read_desktop(path: str) -> tuple[dict, dict] | None:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return None
    entry, actions = _parse(text)
    if not entry:
        return None
    return entry, actions


def load_apps(extra_dirs: list[str] | None = None) -> list[App]:
    """Enumerate every launchable application visible to the current user."""
    desktops = current_desktops()
    dirs = list(extra_dirs or []) + data_dirs()

    apps: list[App] = []
    seen_ids: set[str] = set()

    for directory in dirs:
        app_dir = os.path.join(directory, "applications")
        try:
            filenames = sorted(os.listdir(app_dir))
        except OSError:
            continue
        for filename in filenames:
            if not filename.endswith(".desktop"):
                continue
            path = os.path.join(app_dir, filename)
            # Keyed on the bare desktop id, not the full path: XDG precedence
            # says ~/.local/share/applications/foo.desktop *shadows*
            # /usr/share/applications/foo.desktop.  data_dirs() returns the
            # home directory first, so the first one to claim an id wins.
            desktop_id = filename[:-len(".desktop")]
            if desktop_id in seen_ids:
                continue

            parsed = _read_desktop(path)
            if parsed is None:
                continue
            entry, action_groups = parsed

            # Claim the id *before* filtering.  A user file that contains only
            # "NoDisplay=true" -- with no Name or Exec of its own -- is a
            # legitimate way to hide a system application, so it has to shadow
            # the system entry rather than be skipped and lose to it.
            seen_ids.add(desktop_id)

            if not _is_visible(entry, desktops):
                continue

            name = _localise(entry, "name")
            if not name:
                continue
            exec_display = entry.get("exec", "")
            icon = entry.get("icon", "")
            argv = expand_exec(exec_display, icon=icon, name=name, desktop_path=path)
            if not argv:
                continue

            actions = []
            for action_name, action_entry in action_groups.items():
                action_argv = expand_exec(
                    action_entry.get("exec", ""),
                    icon=action_entry.get("icon", ""),
                    name=_localise(action_entry, "name") or action_name,
                    desktop_path=path,
                )
                if action_argv:
                    label = _localise(action_entry, "name") or action_name
                    actions.append(Action(name=label, exec_argv=action_argv))

            apps.append(
                App(
                    name=name,
                    generic_name=_localise(entry, "genericname"),
                    exec_display=exec_display,
                    exec_argv=argv,
                    terminal=_truthy(entry.get("terminal")),
                    desktop_id=desktop_id,
                    path=path,
                    actions=actions,
                )
            )

    apps.sort(key=lambda a: a.name.lower())
    return apps
