#!/usr/bin/env bash
# Install SecretSway, and gtk4-layer-shell if it is not already available.
#
#   ./install.sh                 install for the current user (~/.local)
#   ./install.sh --system        install system-wide (needs root)
#   ./install.sh --layer-shell   build+install only the layer-shell
#   ./install.sh --deps          list what is missing, install nothing
#   ./install.sh --check         verify
#   ./install.sh --uninstall     remove what this script installed
#
# Works on anything with a GTK4 and a Wayland compositor; the package names
# differ per distribution and are looked up below.  Everything lands under
# ~/.local by default, so the only step needing root is the package install.
# Add --sudo to let this script run the package manager; without it, the
# command is printed and you run it yourself.

set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
use_sudo=0
system=0
action=install
force_build=0

while [ $# -gt 0 ]; do
  case "$1" in
    --sudo)        use_sudo=1 ;;
    --system)      system=1 ;;
    --layer-shell) action=layer-shell ;;
    --deps)        action=deps ;;
    --check)       action=check ;;
    --uninstall)   action=uninstall ;;
    --rebuild)     force_build=1 ;;
    -h|--help)     sed -n '2,14p' "$0" | sed 's/^# \?//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

if [ "$system" = 1 ]; then
  prefix="/usr"
  config_home="/etc"
else
  prefix="$HOME/.local"
  config_home="${XDG_CONFIG_HOME:-$HOME/.config}"
fi
dest="$config_home/secretsway"
bin_home="$prefix/bin"

# ---------------------------------------------------------------- distro

detect_pm() {
  for pm in apt-get dnf zypper pacman apk emerge; do
    if command -v "$pm" >/dev/null 2>&1; then echo "$pm"; return; fi
  done
  echo ""
}

pm="$(detect_pm)"

pkg_for() {
  case "$pm" in
    apt-get)
      case "$1" in
        runtime) echo "python3-gi python3-gi-cairo python3-cairo gir1.2-gtk-4.0 fonts-jetbrains-mono" ;;
        build)   echo "meson ninja-build libgtk-4-dev libwayland-dev wayland-protocols gobject-introspection pkg-config git" ;;
      esac ;;
    dnf)
      case "$1" in
        runtime) echo "python3-gobject gtk4 python3-cairo fontconfig jetbrains-mono-fonts" ;;
        build)   echo "meson ninja-build gtk4-devel wayland-devel wayland-protocols gobject-introspection pkgconf-pkg-config git" ;;
      esac ;;
    zypper)
      case "$1" in
        runtime) echo "python3-gobject gtk4 python3-cairo dejavu-sans-mono-fonts" ;;
        build)   echo "meson ninja gtk4-devel wayland-devel wayland-protocols gobject-introspection pkg-config git" ;;
      esac ;;
    pacman)
      case "$1" in
        runtime) echo "python-gobject gtk4 python-cairo ttf-jetbrains-mono" ;;
        build)   echo "meson ninja gtk4 wayland wayland-protocols gobject-introspection pkgconf git" ;;
      esac ;;
    apk)
      case "$1" in
        runtime) echo "py3-gobject gtk4 py3-cairo font-dejavu" ;;
        build)   echo "meson ninja gtk4-dev wayland-dev wayland-protocols gobject-introspection pkgconfig git" ;;
      esac ;;
    *)
      case "$1" in
        runtime) echo "python3-gi python3-gi-cairo python3-cairo gir1.2-gtk-4.0" ;;
        build)   echo "meson ninja libgtk-4-dev libwayland-dev wayland-protocols gobject-introspection pkg-config git" ;;
      esac ;;
  esac
}

run_pm() {
  case "$pm" in
    apt-get)
      if [ "$use_sudo" = 1 ]; then
        sudo apt-get update && sudo apt-get install -y "$@"
      else
        printf '\n  sudo apt-get update && sudo apt-get install -y %s\n\n' "$*" >&2
        return 1
      fi ;;
    dnf)    ${SUDO:-sudo} dnf install -y "$@" ;;
    zypper) ${SUDO:-sudo} zypper install -y "$@" ;;
    pacman) ${SUDO:-sudo} pacman -S --needed --noconfirm "$@" ;;
    apk)    ${SUDO:-sudo} apk add "$@" ;;
    emerge) ${SUDO:-sudo} emerge "$@" ;;
    *)      echo "no supported package manager found" >&2; return 1 ;;
  esac
}

# ---------------------------------------------------------------- state

find_lib()      { ls "$prefix"/lib/*/libgtk4-layer-shell.so 2>/dev/null | head -1; }
find_typelib()  { ls "$prefix"/lib/*/girepository-1.0/Gtk4LayerShell-1.0.typelib 2>/dev/null | head -1; }
system_lib()     { ls /usr/lib/*/libgtk4-layer-shell.so 2>/dev/null | head -1; }
system_typelib() { ls /usr/lib/*/girepository-1.0/Gtk4LayerShell-1.0.typelib 2>/dev/null | head -1; }

have_layer_shell() {
  [ -n "$(find_typelib)$(system_typelib)" ]
}

have_runtime() {
  python3 - <<'PY' >/dev/null 2>&1
import gi
gi.require_version("Gtk", "4.0")
gi.require_foreign("cairo")
from gi.repository import Gtk
PY
}

have_build_tools() {
  command -v meson >/dev/null && command -v ninja >/dev/null && command -v git >/dev/null
}

# ---------------------------------------------------------------- steps

build_layer_shell() {
  if have_layer_shell && [ "$force_build" != 1 ]; then
    echo "==> gtk4-layer-shell already present"
    return 0
  fi
  if ! have_build_tools; then
    echo "==> build tools missing; need: $(pkg_for build)"
    run_pm $(pkg_for build) || return 1
  fi
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' RETURN
  echo "==> building gtk4-layer-shell into $prefix"
  git clone --depth 1 https://github.com/wmww/gtk4-layer-shell "$tmp/gls"
  # Used from Python via its typelib, so the Vala bindings (which need
  # vapigen), demos, docs and tests are all dead weight here.
  meson setup "$tmp/gls/build" "$tmp/gls" --prefix="$prefix" \
    -Dvapi=false -Dexamples=false -Ddocs=false -Dtests=false -Dsmoke-tests=false
  ninja -C "$tmp/gls/build"
  if [ "$system" = 1 ]; then
    sudo ninja -C "$tmp/gls/build" install
    sudo ldconfig
  else
    ninja -C "$tmp/gls/build" install
  fi
}

write_wrapper() {
  local lib typelib
  lib="$(find_lib)"; [ -n "$lib" ] || lib="$(system_lib)"
  typelib="$(dirname "$(find_typelib)")"
  [ -n "$typelib" ] || typelib="$(dirname "$(system_typelib)")"
  mkdir -p "$bin_home"
  cat > "$bin_home/secretsway" <<EOF
#!/bin/sh
# SecretSway launcher wrapper.
#
# gtk4-layer-shell must be loaded before libwayland-client or the layer-shell
# negotiation fails silently and the strip appears as an ordinary window.
# SecretSway also preloads it via ctypes; this is belt-and-braces.
libdir="\$(dirname "$lib")"
[ -n "\$libdir" ] && LD_LIBRARY_PATH="\$libdir\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}" && export LD_LIBRARY_PATH
[ -n "$typelib" ] && GI_TYPELIB_PATH="$typelib\${GI_TYPELIB_PATH:+:\$GI_TYPELIB_PATH}" && export GI_TYPELIB_PATH
[ -f "\$libdir/libgtk4-layer-shell.so" ] && \\
  LD_PRELOAD="\$libdir/libgtk4-layer-shell.so\${LD_PRELOAD:+:\$LD_PRELOAD}" && export LD_PRELOAD
exec python3 "$dest" "\$@"
EOF
  chmod +x "$bin_home/secretsway"
}

install_app() {
  echo "==> installing SecretSway to $dest"
  mkdir -p "$dest"
  cp -r "$here/secretsway" "$here/__main__.py" "$dest/"
  find "$dest" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
  if [ -f "$dest/secretsway.toml" ]; then
    echo "    kept your existing $dest/secretsway.toml"
  else
    cp "$here/secretsway.toml" "$dest/secretsway.toml"
  fi
  if [ "$system" = 1 ]; then sudo chown -R root:root "$dest"; fi
  write_wrapper
  echo "==> wrapper at $bin_home/secretsway"
}

uninstall() {
  echo "==> removing $dest and $bin_home/secretsway"
  rm -rf "$dest"
  rm -f "$bin_home/secretsway"
  echo "    usage.json and secretsway.log are left alone; remove them too if wanted:"
  echo "      \${XDG_STATE_HOME:-\$HOME/.local/state}/secretsway"
  echo "      \${XDG_CACHE_HOME:-\$HOME/.cache}/secretsway"
}

# ---------------------------------------------------------------- main

case "$action" in
  deps)
    echo "package manager: ${pm:-none found}"
    if have_runtime; then echo "runtime: ok"
    else echo "runtime: MISSING (need: $(pkg_for runtime))"; fi
    if have_layer_shell; then echo "gtk4-layer-shell: installed"
    else
      echo "gtk4-layer-shell: MISSING -- will be built from source"
      if have_build_tools; then echo "build tools: ok"
      else echo "build tools: MISSING (need: $(pkg_for build))"; fi
    fi
    exit 0 ;;

  check)
    [ -d "$dest" ] && echo "installed: $dest" || echo "not installed"
    [ -x "$bin_home/secretsway" ] && echo "wrapper:    $bin_home/secretsway" || echo "no wrapper"
    echo
    [ -x "$bin_home/secretsway" ] && "$bin_home/secretsway" --check
    exit $? ;;

  uninstall) uninstall; exit 0 ;;

  layer-shell)
    build_layer_shell || exit 1
    echo "typelib: $(find_typelib || system_typelib || echo NOT FOUND)"
    exit 0 ;;
esac

if ! have_runtime; then
  echo "==> runtime dependencies missing: $(pkg_for runtime)"
  run_pm $(pkg_for runtime) || exit 1
fi

build_layer_shell || exit 1

if ! have_layer_shell; then
  echo "SecretSway: gtk4-layer-shell produced no typelib" >&2
  exit 1
fi

install_app

cat <<EOF

Done. Point sway at it:
EOF

# Does the bare command resolve in the environment sway will inherit?
# `bindsym ... exec` runs through sh with sway's environment, not your login
# shell's, so this is the only check that predicts the truth.
if command -v secretsway >/dev/null 2>&1; then
  cat <<'EOF'

    -set $menu fuzzel
    +set $menu secretsway

  (secretsway is on PATH, so the short name works)
EOF
else
  cat <<EOF

    -set \$menu fuzzel
    +set \$menu $bin_home/secretsway

  The full path is needed because \`secretsway\` is not on the PATH sway
  inherits.  That is normal for a per-user install under a display manager,
  where ~/.local/bin is added by your login shell but not by sway.  For the
  short name, install system-wide (./install.sh --system) instead.
EOF
fi

cat <<EOF

Then:  swaymsg reload

Check it:   $bin_home/secretsway --check
Preview it: $bin_home/secretsway --preview
EOF
