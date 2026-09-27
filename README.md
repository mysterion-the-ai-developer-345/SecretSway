# SecretSway

A sway app launcher that looks like a terminal.

Opens as a full-width strip at the top of the screen: dark grey, electric blue
outline, monospace, no icons, no rounded corners. A prompt line and a selection
that is an inverted bar rather than a rounded highlight. It is similar to fuzzel
 — the app list comes from the same `.desktop` files — with the
presentation swapped out.

### Anywhere (this is the one to use)

```sh
git clone https://github.com/mysterion-the-ai-developer-345/SecretSway
cd SecretSway/secretsway
./install.sh
```

`install.sh` detects your package manager and maps the dependency names, so it
works on Debian/Ubuntu/Mint, Fedora, openSUSE, Arch, Alpine and Gentoo without
editing. It installs to `~/.local` and needs no root except for the package
install, which it will print for you to run.

```sh
./install.sh --deps        # what is missing, install nothing
./install.sh --system      # install system-wide instead
./install.sh --check       # verify
./install.sh --uninstall   # remove
```

`gtk4-layer-shell` is built from source when the distribution does not package
it (it first appears in Debian trixie / Ubuntu 25.10). On Arch this is done by
the AUR package instead.

### Debian / Ubuntu

```sh
./packaging/make-deb.sh
sudo dpkg -i dist/secretsway_1.2.0_all.deb
```

The package installs the app and, on releases without a packaged
gtk4-layer-shell, compiles it in `postinst`.

### Arch

```sh
cd packaging/arch && makepkg -si
```

The PKGBUILD builds gtk4-layer-shell as part of the package, since Arch does
not ship it. For AUR, copy `packaging/arch/PKGBUILD` into a repository and
point `source` at your release tarball.

### Dependencies

| | Runtime | Build (only if layer-shell is missing) |
|---|---|---|
| Debian/Ubuntu | `python3-gi python3-gi-cairo python3-cairo gir1.2-gtk-4.0` | `meson ninja-build libgtk-4-dev libwayland-dev wayland-protocols gobject-introspection` |
| Fedora | `python3-gobject gtk4 python3-cairo` | `meson ninja-build gtk4-devel wayland-devel wayland-protocols gobject-introspection` |
| Arch | `python-gobject gtk4 python-cairo` | `meson ninja gtk4 wayland wayland-protocols gobject-introspection` |
| openSUSE | `python3-gobject gtk4 python3-cairo` | `meson ninja gtk4-devel wayland-devel wayland-protocols gobject-introspection` |

No Python packages from PyPI — stdlib plus PyGObject only.

## Wire it up

One line in `~/.config/sway/config`, next to your other variable definitions:

```diff
  # Your preferred application launcher
-set $menu fuzzel
+set $menu secretsway
```

The binding you already have picks it up with no edit at all:

```
bindsym $mod+d exec $menu
```

Then `swaymsg reload` (or `Mod+Shift+C`).

### When the short name is not enough

`bindsym ... exec` runs through `sh` inheriting **sway's** environment, not
your login shell's. If you installed per-user, `~/.local/bin` is often not on
that `PATH` — your login shell adds it, sway does not — and a bare
`secretsway` fails with "command not found". Two fixes:

```
# either use the full path
set $menu /home/you/.local/bin/secretsway

# or install system-wide, so it lands in /usr/bin and the short name always works
./install.sh --system
```

`install.sh` checks this at the end and prints whichever line applies, so
trust what it says rather than guessing.

### Keeping the old launcher

Worth doing the first time. Leave your existing variable alone and add a
second one:

```
set $menu fuzzel
set $menu2 secretsway

bindsym $mod+d       exec $menu
bindsym $mod+Shift+d exec $menu2
```

`reload`, try the scratch key, and swap which one `Mod+d` uses when you like it.

### Check it worked

```sh
secretsway --check      # deps, app count, font metrics, GI symbols
secretsway --preview    # the panel drawn as text, no window involved
```

If `Mod+d` does nothing, look for a stray character in the `$menu` line —
`grep menu ~/.config/sway/config` will show it.

## Keys

| Key | Action |
|---|---|
| `Enter` | launch the selection |
| `Ctrl+Enter` | launch the entry's first alternate action |
| `↑` `↓`, `Ctrl+P` `Ctrl+N` | move (stops at the ends) |
| `PageUp` / `PageDown` | move a window at a time |
| `Tab` / `Shift+Tab` | move |
| `Esc` | dismiss |

The list shows at most `max_rows` results; the view follows the selection as it
moves, so the highlight never leaves the screen.

## Usage memory

`secretsway` remembers what you launch. Pressing `Enter` on an app bumps a counter and
marks it as most recent, persisted to `$XDG_STATE_HOME/secretsway/usage.json`
(written atomically; a corrupt file is ignored rather than fatal).

With an empty query the list is ordered by use — the most recent pick is pinned
to slot 1 and labelled `was last`, the rest by descending launch count with ties
broken alphabetically so the list never shuffles between runs.

Typing a query switches to pure fuzzy ranking, as you would expect — usage
order stops overriding what the text matches.

With `right_column = "dots"` the right-hand column shows frequency instead,
6 down to 1 by rank, and the most recent pick is labelled `was last`.

Anything typed that matches no application is offered as a shell command.

## Configuration

`~/.config/secretsway/secretsway.toml`, entirely optional — every key has a default, and
an unrecognised key is an error rather than a silent no-op so typos surface
immediately.

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
# This means anything typed at $mod+d can be executed.  Use "argv" to tokenise
# without a shell, or "off" to disable the fallback entirely.
command_fallback = "shell"
```

## Checking it works

```sh
secretsway --check         # config, app count, GTK + layer-shell availability
secretsway --dump-apps     # the parsed app list as JSON
secretsway --dump-apps | jq -r '.[].name' | diff - <(fuzzel --list)
```

`--dump-apps` and `--check` work with no GTK installed, so you can confirm the
backend matches fuzzel before binding anything to `$mod+d`.

## Tests

```sh
python3 -m unittest discover -s tests -t .
```

The app discovery, `Exec` expansion, locale fallback, fuzzy matcher, config
validation, launch argv construction and panel layout are all covered — none of
them need a compositor. What is *not* covered is the GTK/layer-shell drawing
path, which needs a real sway session to exercise.

## Notes

- **Single instance.** A second `$mod+d` toggles the resident strip rather than
  stacking a second one, via a `0600` unix socket in `$XDG_RUNTIME_DIR`. The
  socket is created under a `0177` umask, so it is never briefly
  world-connectable between `bind()` and the `chmod`.
- **`command_fallback = "shell"` is the default**, and that means anything typed
  at `$mod+d` is executed by `sh -c` if it matches no application. That is the
  point of a launcher that looks like a terminal, but it does make a
  compositor-global keybind an arbitrary-execution surface. Set
  `command_fallback = "argv"` to tokenise without a shell, or `"off"` to allow
  only `.desktop` entries.
- **No `swaymsg exec`.** Command expansion would happen twice more (swaymsg,
  then sway, then `sh`) and swaymsg splits on commas, so a desktop entry with a
  quoted argument containing `$` could be expanded into something that runs. We
  already run inside sway and inherit its environment, so the app is spawned
  directly with the argv the entry produced.
- **The frame is drawn, not typeset.** Cairo rectangles rather than box-drawing
  glyphs, so the outline is pixel-exact and does not depend on the font having
  `U+2551` or on the output width being an exact multiple of the cell width.
- **`gtk4-layer-shell` must be preloaded** before `libwayland-client`, or the
  layer-shell negotiation fails silently and the strip comes up as an ordinary
  window. The wrapper sets `LD_PRELOAD` and `secretsway` also does it via `ctypes`.
