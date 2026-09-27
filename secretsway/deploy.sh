#!/usr/bin/env bash
# Deploy secretsway to ~/.config/secretsway, kill the stale instance, and show the
# result.  Verifies each step rather than assuming it worked.
#
#   ./deploy.sh            deploy and preview
#   ./deploy.sh --src DIR  deploy from a specific source tree
#   ./deploy.sh --check    just report what is installed vs running

set -uo pipefail

src=""
check_only=0
while [ $# -gt 0 ]; do
  case "$1" in
    --src)   src="$2"; shift 2 ;;
    --check) check_only=1 ;;
    -h|--help) sed -n '2,7p' "$0" | sed 's/^# \?//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

dest="$HOME/.config/secretsway"
cfg="$dest/secretsway.toml"
wrapper="$HOME/.local/bin/secretsway"

# The running instance is `python3 <dest>`; match exactly that.
#
# A bare `pkill -f secretsway` kills this script, whose own argv contains the
# word.  Widening it to `pkill -f "$dest"` is not enough either: anything whose
# command line merely mentions the path -- an editor, a `tail -f`, the shell
# that invoked this -- matches too.  Anchoring on the interpreter and the exact
# argument is what makes the pattern specific.
instance_re="python3 $dest\$"

stop_instance() {
  local pids
  pids="$(pgrep -f "$instance_re" 2>/dev/null)"
  [ -n "$pids" ] || return 0
  kill $pids 2>/dev/null
}

# A line that only exists in the current tree, used to detect a stale source.
MARKER='target = content_w - right_w'

report() {
  echo "source:    $src"
  echo "installed: $dest"
  echo "installed build:"
  python3 "$dest" --check 2>&1 | head -2 | sed 's/^/    /'
  echo -n "absolute-placement fix present: "
  if grep -qF "$MARKER" "$dest/secretsway/render.py" 2>/dev/null; then echo "yes"; else echo "NO"; fi
  echo "resident secretsway process:"
  pgrep -af "$instance_re" | sed 's/^/    /' || echo "    none (good)"
}

if [ "$check_only" = 1 ]; then
  report
  exit 0
fi

# --- locate a usable source tree -----------------------------------------
# $PWD covers running this from a checkout; --src DIR covers everything else.
# No hardcoded developer paths -- they only ever matched one person's machine.
if [ -z "$src" ]; then
  for candidate in "$PWD"; do
    if [ -f "$candidate/secretsway/render.py" ]; then src="$candidate"; break; fi
  done
fi
[ -f "$src/secretsway/render.py" ] || { echo "no source tree found; run from the checkout or pass --src DIR" >&2; exit 1; }

echo "==> source: $src"
if ! grep -qF "$MARKER" "$src/secretsway/render.py"; then
  echo "!!! the source tree is STALE -- it has no absolute-placement fix."
  echo "!!! $src/secretsway/render.py does not contain: $MARKER"
  echo "!!! Point --src at the tree you are editing, or sync this one first."
  exit 1
fi
echo "    source contains the absolute-placement fix"

# --- stop the old code ----------------------------------------------------
if pgrep -f "$instance_re" >/dev/null 2>&1; then
  echo "==> killing resident instance (it is running the old code)"
  stop_instance
  sleep 0.5
fi
rm -f "${XDG_RUNTIME_DIR:-/tmp}"/secretsway-*.sock

# --- fix the config -------------------------------------------------------
if [ -f "$cfg" ]; then
  if grep -qE '^[[:space:]]*show_(right|exec)[[:space:]]*=' "$cfg"; then
    echo "==> removing legacy show_right/show_exec from $cfg"
    sed -i -E '/^[[:space:]]*show_(right|exec)[[:space:]]*=/d' "$cfg"
  fi
  if grep -qE '^right_column' "$cfg"; then
    echo "!!! $cfg has a right_column at top level (column 0), which is in the"
    echo "!!! last TOML section -- likely [behaviour]. Delete it; the default"
    echo "!!! is already \"dots\":   sed -i '/^right_column/d' $cfg"
    exit 1
  fi
fi

# --- deploy ---------------------------------------------------------------
echo "==> deploying"
mkdir -p "$dest"
cp "$src"/secretsway/*.py "$dest/secretsway/"
cp "$src"/__main__.py "$dest/"
find "$dest" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
if [ ! -f "$cfg" ] && [ -f "$src/secretsway.toml" ]; then
  cp "$src/secretsway.toml" "$cfg"
fi

echo
report
echo
echo "==> panel preview (no compositor involved)"
python3 "$dest" --preview --width "${WIDTH:-70}" || exit 1
echo
echo "Now press Mod+d."
