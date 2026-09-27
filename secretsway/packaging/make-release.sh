#!/usr/bin/env bash
# Build a source release tarball for SecretSway.
#
#   packaging/make-release.sh            build dist/secretsway-<version>.tar.gz
#   packaging/make-release.sh --check    list contents, do not build
#
# There is nothing to compile: the launcher is pure Python and stdlib, so the
# "binary" people install is this directory plus a small sh wrapper.  One
# tarball therefore works on every distribution that has GTK4.
#
# What is NOT in the tarball is gtk4-layer-shell: that is compiled C, is
# per-architecture, and cannot ship here.  install.sh builds it on the target
# machine when the distribution does not package it.

set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/.." && pwd)"
name=secretsway
version="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$root/$name/__init__.py")"
dist="$root/dist"
tarball="$dist/${name}-${version}.tar.gz"

check_only=0
[ "${1:-}" = "--check" ] && check_only=1

echo "==> $name $version"

if [ "$check_only" = 1 ]; then
  [ -f "$tarball" ] || { echo "not built: $tarball" >&2; exit 1; }
  tar -tzf "$tarball"
  exit 0
fi

mkdir -p "$dist"
staging="$(mktemp -d)"
trap 'rm -rf "$staging"' EXIT

top="$staging/${name}-${version}"
mkdir -p "$top"

# Exactly what belongs in a release: no tests, no dev scripts, no build
# artefacts, no local state.
# README.md and LICENSE live at the repository root, one level above this
# project dir, so that GitHub shows the README without a second copy here
# drifting out of sync with it.
cp -r "$root/$name" "$top/"
cp "$root/__main__.py" "$root/install.sh" "$root/secretsway.toml" \
      "$root/../README.md" "$root/../LICENSE" "$top/" 2>/dev/null \
   || cp "$root/__main__.py" "$root/install.sh" "$root/secretsway.toml" \
         "$root/../README.md" "$top/"
mkdir -p "$top/packaging"
cp -r "$root/packaging/debian" "$root/packaging/arch" \
      "$root/packaging/make-deb.sh" "$top/packaging/"

# Debian/Ubuntu maintainers want it under packaging/debian; Arch under
# packaging/arch.  Both are there already.

find "$top" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$top" -name '*.pyc' -delete 2>/dev/null || true
chmod +x "$top/install.sh" "$top/packaging/make-deb.sh" \
         "$top/packaging/debian/"*.sh 2>/dev/null || true

# Deterministic-ish: fixed owner, sorted, no timestamps from the build host.
tar --sort=name --owner=0 --group=0 --numeric-owner \
    --mtime="@$(git -C "$root" log -1 --format=%ct 2>/dev/null || echo 0)" \
    -czf "$tarball" -C "$staging" "${name}-${version}"

echo "==> built $tarball"
echo
echo "Attach both of these to a GitHub release:"
echo "  $tarball"
( cd "$dist" && sha256sum "${name}-${version}.tar.gz" ) > "$dist/${name}-${version}.tar.gz.sha256"
echo "  $dist/${name}-${version}.tar.gz.sha256"
echo
echo "Users then:"
echo "  tar xzf ${name}-${version}.tar.gz"
echo "  cd ${name}-${version} && ./install.sh"
