# SecretSway

A Sway app launcher styled like a terminal.

Opens as a full-width strip at the top of the screen: dark grey, electric blue
outline, monospace, no icons, no rounded corners. A prompt line and a selection
bar that inverts rather than highlights. Functionally it's close to fuzzel —
same `.desktop` file backend — with a different presentation on top.

> Built with the help of a Chinese open-weights model running in Claude Code.

### Anywhere (this is the one to use)

```sh
git clone https://github.com/mysterion-the-ai-developer-345/SecretSway
cd SecretSway/secretsway
./install.sh
```

`install.sh` detects your package manager and maps dependency names, so it
works unmodified on Debian/Ubuntu/Mint, Fedora, openSUSE, Arch, Alpine, and
Gentoo. It installs to `~/.local` and needs no root access except for the
package install step, which it prints for you to run yourself.

```sh
./install.sh --deps        # show what's missing, install nothing
./install.sh --system      # install system-wide instead
./install.sh --check       # verify the install
./install.sh --uninstall   # remove
```

`gtk4-layer-shell` is built from source on distributions that don't package it
yet (it first appears in Debian trixie / Ubuntu 25.10). On Arch, the AUR
package handles this instead.

### Debian / Ubuntu

```sh
./packaging/make-deb.sh
sudo dpkg -i dist/secretsway_1.2.0_all.deb
```

On releases without a packaged `gtk4-layer-shell`, the package compiles it
during `postinst`.

### Arch

```sh
cd packaging/arch && makepkg -si
```

The PKGBUILD builds `gtk4-layer-shell` as part of the package, since Arch
doesn't ship it. For AUR, copy `packaging/arch/PKGBUILD` into a repository and
point `source` at your release tarball.

### Dependencies

| | Runtime | Build (only if layer-shell is missing) |
|---|---|---|
| Debian/Ubuntu | `python3-gi python3-gi-cairo python3-cairo gir1.2-gtk-4.0` | `meson ninja-build libgtk-4-dev libwayland-dev wayland-protocols gobject-introspection` |
| Fedora | `python3-gobject gtk4 python3-cairo` | `meson ninja-build gtk4-devel wayland-devel wayland-protocols gobject-introspection` |
| Arch | `python-gobject gtk4 python-cairo` | `meson ninja gtk4 wayland wayland-protocols gobject-introspection` |
| openSUSE | `python3-gobject gtk4 python3-cairo` | `meson ninja gtk4-devel wayland-devel wayland-protocols gobject-introspection` |

No PyPI dependencies — stdlib plus PyGObject only.

## Wiring it up

One line in `~/.config/sway/config`, next to your other variable definitions:

```diff
  # Your preferred application launcher
-set $menu fuzzel
+set $menu secretsway
```

Your existing binding picks it up without any edit:

```
bindsym $mod+d exec $menu
```

Then run `swaymsg reload` (or `Mod+Shift+C`).

### When the short name isn't enough

`bindsym ... exec` runs through `sh`, inheriting **Sway's** environment, not
your login shell's. If you installed per-user, `~/.local/bin` is often missing
from that `PATH` — your login shell adds it, Sway doesn't — so a bare
`secretsway` fails with "command not found." Two fixes:

```
# either use the full path
set $menu /home/you/.local/bin/secretsway

# or install system-wide, so it lands in /usr/bin and the short name always works
./install.sh --system
```

`install.sh` checks for this at the end and prints whichever fix applies, so
trust its output rather than guessing.

### Keeping the old launcher

Worth doing on the first install. Leave your existing variable alone and add a
second one:

```
set $menu fuzzel
set $menu2 secretsway

bindsym $mod+d       exec $menu
bindsym $mod+Shift+d exec $menu2
```

Reload, try the second keybind, and switch `Mod+d` over whenever you're ready.

### Verifying it worked

```sh
secretsway --check      # deps, app count, font metrics, GI symbols
secretsway --preview    # the panel rendered as text, no window involved
```

If `Mod+d` does nothing, check for a stray character in the `$menu` line —
`grep menu ~/.config/sway/config` will usually show it.

## Keys

| Key | Action |
|---|---|
| `Enter` | launch the selection |
| `Ctrl+Enter` | launch the entry's first alternate action |
| `↑` `↓`, `Ctrl+P` `Ctrl+N` | move (stops at the ends) |
| `PageUp` / `PageDown` | move a window at a time |
| `Tab` / `Shift+Tab` | move |
| `Esc` | dismiss |

The list shows at most `max_rows` results; the view scrolls with the
selection, so the highlight never leaves the screen.

## Usage memory

SecretSway remembers what you launch. Pressing `Enter` on an app increments a
counter and marks it as most recent, persisted to
`$XDG_STATE_HOME/secretsway/usage.json` (written atomically; a corrupt file is
ignored rather than treated as fatal).

With an empty query, the list is ordered by use: the most recent pick is
pinned to slot 1 and labelled `was last`, and the rest are ordered by
descending launch count, with ties broken alphabetically so the list never
shuffles between runs.

Typing a query switches to pure fuzzy ranking, as expected — usage order stops
overriding what the text matches.

With `right_column = "dots"`, the right-hand column shows frequency instead,
ranked 6 down to 1, with the most recent pick still labelled `was last`.

Anything typed that matches no application is offered as a shell command.

## Configuration

`~/.config/secretsway/secretsway.toml` is entirely optional — every key has a
default, and an unrecognized key is an error rather than a silent no-op, so
typos surface immediately.

```toml
[colors]
panel  = "#1e1e1e"
border = "#2f6bff"
text   = "#d6d6d6"
dim    = "#6b6b6b"
cursor = "#2f6bff"
sel_bg = "#d6d6d6"
sel_fg = "#1e1e1e"

[ui]
font        = "JetBrains Mono, DejaVu Sans Mono, monospace"
max_rows    = 15
wrap        = false
# "dots" = usage frequency, "exec" = the raw command, "off" = nothing
right_column = "off"

[behaviour]
# "shell" runs an unmatched query through `sh -c`, so pipes and redirects work.
# This means anything typed at $mod+d can be executed. Use "argv" to tokenize
# without a shell, or "off" to disable the fallback entirely.
command_fallback = "shell"
```

## Checking it works

```sh
secretsway --check         # config, app count, GTK + layer-shell availability
secretsway --dump-apps     # the parsed app list as JSON
secretsway --dump-apps | jq -r '.[].name' | diff - <(fuzzel --list)
```

`--dump-apps` and `--check` work without GTK installed, so you can confirm the
backend matches fuzzel before binding anything to `$mod+d`.

## Tests

```sh
python3 -m unittest discover -s tests -t .
```

App discovery, `Exec` expansion, locale fallback, the fuzzy matcher, config
validation, launch argv construction, and panel layout are all covered — none
of it requires a compositor. The one thing *not* covered is the GTK/layer-shell
drawing path, which needs a real Sway session to exercise.

## Notes

- **Single instance.** A second `$mod+d` toggles the resident strip rather
  than stacking a new one, via a `0600` Unix socket in `$XDG_RUNTIME_DIR`. The
  socket is created under a `0177` umask, so it's never briefly
  world-connectable between `bind()` and the `chmod`.
- **`command_fallback = "shell"` is the default**, which means anything typed
  at `$mod+d` gets executed by `sh -c` if it matches no application. That's
  the point of a launcher that looks like a terminal, but it does turn a
  compositor-global keybind into an arbitrary-execution surface. Set
  `command_fallback = "argv"` to tokenize without a shell, or `"off"` to
  allow only `.desktop` entries.
- **No `swaymsg exec`.** Going through `swaymsg` would expand the command
  twice more (swaymsg, then Sway, then `sh`), and `swaymsg` splits on commas —
  so a desktop entry with a quoted argument containing `$` could expand into
  something unintended. Since we already run inside Sway and inherit its
  environment, the app is spawned directly with the argv the entry produced.
- **The frame is drawn, not typeset.** Cairo rectangles rather than
  box-drawing glyphs, so the outline is pixel-exact regardless of whether the
  font has `U+2551` or the output width is an exact multiple of the cell width.
- **`gtk4-layer-shell` must be preloaded** before `libwayland-client`, or
  layer-shell negotiation fails silently and the strip comes up as an
  ordinary window. The wrapper script sets `LD_PRELOAD`, and `secretsway`
  also does it itself via `ctypes`.
