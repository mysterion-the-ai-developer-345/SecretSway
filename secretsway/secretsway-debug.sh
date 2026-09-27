#!/usr/bin/env bash
# One-shot diagnostic for secretsway: start it, wait, then print a compact summary.
#
#   ./secretsway-debug.sh
#
# Press Mod+d a second or two in, then leave it alone. Output appears here.

set -uo pipefail

dest="$HOME/.config/secretsway"
log="$HOME/.cache/secretsway/secretsway.log"

# Match the launcher as `python3 <dest>` and nothing else.
#
# The obvious `pkill -f secretsway` kills this script: its own argv contains
# the word, so it matches itself and dies a line after starting.  But widening
# to `pkill -f "$dest"` is not enough either -- any process whose command line
# merely mentions the path (an editor, a `tail -f`, a shell that has it in its
# argv) gets killed too, including whatever invoked this script.  Anchoring on
# the interpreter and the exact argument is what makes it specific.
instance_re="python3 $dest\$"

stop_instance() {
  local pids
  pids="$(pgrep -f "$instance_re" 2>/dev/null)"
  [ -n "$pids" ] || return 0
  # Never take ourselves down, whatever else matches.
  kill $pids 2>/dev/null
}

stop_instance
sleep 0.4
rm -f "$log"

# mktemp, not a fixed name in /tmp: a predictable path in a world-writable
# directory can be pre-created as a symlink, and the redirect below would then
# write through it with our privileges.
err_file="$(mktemp "${TMPDIR:-/tmp}/secretsway-stderr.XXXXXX")"
trap 'rm -f "$err_file"' EXIT

echo "starting secretsway..."
python3 "$dest" >"$err_file" 2>&1 &
sp_pid=$!

sleep 3
echo "--- press Mod+d now, type 'fire', then leave it ---"
sleep 6

if kill -0 "$sp_pid" 2>/dev/null; then
  echo "process: ALIVE (pid $sp_pid)"
else
  echo "process: EXITED"
fi

echo
echo "=== log (layers/sizes/draws/exceptions) ==="
if [ -f "$log" ]; then
  grep -E 'layer-shell|sizes|draw#|EXCEPTION' "$log" | tail -20 || echo "(no matching lines)"
else
  echo "(no log file written at $log)"
fi

echo
echo "=== stderr from secretsway ==="
tail -15 "$err_file" 2>/dev/null || echo "(none)"

echo
echo "=== surface, from sway ==="
swaymsg -t get_tree 2>/dev/null \
  | jq -c '.. | objects | select(.app_id? != null) | {app_id, rect: .rect, mapped, focused}' \
  | grep -i secretsway || echo "(no secretsway surface found)"

# Do not kill the resident instance by default: a launcher that stays resident
# is the normal state, and killing it here would make the next Mod+d look broken.
if [ "${1:-}" = "--kill" ]; then
  stop_instance
  echo "killed resident instance"
fi
exit 0
