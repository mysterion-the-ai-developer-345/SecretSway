#!/usr/bin/env bash
# Build secretsway_<version>_all.deb from this checkout.
#
#   packaging/make-deb.sh
#
# Produces a dependency-free-of-root installable .deb.  The gtk4-layer-shell
# typelib is NOT bundled -- it is per-architecture C, so it is built by the
# postinst on releases that do not package it.

set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/.." && pwd)"
pkg=secretsway
version="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$root/secretsway/__init__.py")"
arch=all
build="$root/.build/$pkg"
out="$root/dist"

command -v dpkg-deb >/dev/null || { echo "dpkg-deb not found" >&2; exit 1; }

echo "==> cleaning"
rm -rf "$root/.build"
mkdir -p "$out"

stage() {
  install -d "$build/$1"
  shift
}

echo "==> staging $pkg $version"
rm -rf "$build"; mkdir -p "$build"

# Payload
install -d "$build/usr/lib/$pkg"
cp -r "$root/secretsway" "$build/usr/lib/$pkg/secretsway"
cp "$root/__main__.py" "$build/usr/lib/$pkg/"
install -m 644 "$root/secretsway.toml" "$build/usr/lib/$pkg/"
find "$build/usr/lib/$pkg" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

install -d "$build/usr/bin"
cat > "$build/usr/bin/secretsway" <<'WRAPPER'
#!/bin/sh
# SecretSway launcher.
#
# Resolves its own install location rather than hardcoding one, so the same
# wrapper works from /usr/bin and from an unpacked .deb.
self=$(readlink -f "$0")
libdir=$(dirname "$self")/../lib/secretsway

# gtk4-layer-shell must be loaded before libwayland-client, or the layer-shell
# negotiation fails silently and the strip appears as an ordinary window.
for d in /usr/lib/*/; do
    if [ -f "$d/libgtk4-layer-shell.so" ]; then
        LD_LIBRARY_PATH="$d${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
        GI_TYPELIB_PATH="${d}girepository-1.0${GI_TYPELIB_PATH:+:$GI_TYPELIB_PATH}"
        LD_PRELOAD="$d/libgtk4-layer-shell.so${LD_PRELOAD:+:$LD_PRELOAD}"
        export LD_LIBRARY_PATH GI_TYPELIB_PATH LD_PRELOAD
        break
    fi
done

exec python3 "$libdir" "$@"
WRAPPER
chmod 755 "$build/usr/bin/secretsway"

install -d "$build/usr/lib/$pkg"
install -m 755 "$root/packaging/debian/install-layer-shell.sh" \
  "$build/usr/lib/$pkg/install-layer-shell.sh"

install -d "$build/usr/share/doc/$pkg"
# The README lives at the repository root, one level above this project dir, so
# that GitHub shows it without a second copy here drifting out of sync with it.
install -m 644 "$root/../README.md" "$build/usr/share/doc/$pkg/"
install -m 644 "$root/../README.md" "$build/usr/share/doc/$pkg/README"

# Control
install -d "$build/DEBIAN"
# The Maintainer line in packaging/debian/control is the source of truth and
# ships as written.  Override it at build time with either a full
#     MAINTAINER='You <you@example.com>'
# or a bare name plus a separate address:
#     MAINTAINER=You MAINTAINER_EMAIL=you@example.com
#
# $MAINTAINER_EMAIL never appends to a $MAINTAINER that already carries its own
# <...>; it only supplies the address when one is missing.  Only if nothing is
# set anywhere *and* the control file still carries the old __MAINTAINER__
# placeholder do we fall back to git config -- so a checkout with no user.name
# configured can never silently ship somebody else's address.
control_maintainer="$(sed -n 's/^Maintainer: //p' "$root/packaging/debian/control" | head -1)"

# Split "Name <addr>" into its parts; a bare string becomes the name.
split_maintainer() {
  case "$1" in
    *'<'*'>')
      __m_name="${1%% <*}"; __m_mail="${1##*<}"; __m_mail="${__m_mail%>}" ;;
    *)
      __m_name="$1"; __m_mail="" ;;
  esac
}

# 1. An explicit $MAINTAINER wins outright.
if [ -n "${MAINTAINER:-}" ]; then
  split_maintainer "$MAINTAINER"
  maintainer="$__m_name"
  email="${MAINTAINER_EMAIL:-$__m_mail}"
else
  # 2. Otherwise the control file, unless it is still the unfilled placeholder.
  case "$control_maintainer" in
    ""|__MAINTAINER__|__MAINTAINER__\<*)
      # 3. Fall back to git config, then to a visible placeholder.
      maintainer="$(git -C "$root" config user.name 2>/dev/null || true)"
      email="${MAINTAINER_EMAIL:-$(git -C "$root" config user.email 2>/dev/null || true)}"
      if [ -z "$maintainer" ]; then
        maintainer="secretsway maintainers"
        email="nobody@example.com"
        echo "    note: set MAINTAINER='Name <email>' to fill in the real one" >&2
      fi
      ;;
    *)
      split_maintainer "$control_maintainer"
      maintainer="$__m_name"
      email="${MAINTAINER_EMAIL:-$__m_mail}" ;;
  esac
fi
[ -n "$email" ] || email="nobody@example.com"
sed -e "s/^Version: .*/Version: $version/" \
    -e "s|^Maintainer: .*|Maintainer: $maintainer <$email>|" \
    "$root/packaging/debian/control" > "$build/DEBIAN/control"
install -m 755 "$root/packaging/debian/postinst" "$build/DEBIAN/postinst"

fakeroot dpkg-deb --build --root-owner-group "$build" \
  "$out/${pkg}_${version}_${arch}.deb"

echo
echo "==> built $out/${pkg}_${version}_${arch}.deb"
echo "    lint:  lintian $out/${pkg}_${version}_${arch}.deb   (optional)"
echo "    test:  sudo dpkg -i $out/${pkg}_${version}_${arch}.deb"
