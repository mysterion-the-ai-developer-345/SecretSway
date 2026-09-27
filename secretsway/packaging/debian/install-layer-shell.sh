#!/bin/sh
# Build and install gtk4-layer-shell for the current architecture.
#
# Separate from the package build on purpose: the library is compiled C and so
# is per-architecture, which means it cannot be shipped in an Architecture:
# all package.  Releases from Debian trixie / Ubuntu 25.10 onwards package it
# and this script does nothing useful; earlier ones need it.
set -e

PREFIX="${PREFIX:-/usr}"
NAME="gtk4-layer-shell"
URL="https://github.com/wmww/gtk4-layer-shell"

if [ "$(id -u)" != 0 ]; then
    echo "must run as root (it installs into $PREFIX)" >&2
    exit 1
fi

for tool in meson ninja git; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "missing build tool: $tool" >&2
        exit 1
    fi
done

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

git clone --depth 1 "$URL" "$tmp/gls"
meson setup "$tmp/gls/build" "$tmp/gls" --prefix="$PREFIX" \
    -Dvapi=false -Dexamples=false -Ddocs=false -Dtests=false -Dsmoke-tests=false
ninja -C "$tmp/gls/build"
ninja -C "$tmp/gls/build" install
ldconfig 2>/dev/null || true

ls "$PREFIX"/lib/*/girepository-1.0/Gtk4LayerShell-1.0.typelib
