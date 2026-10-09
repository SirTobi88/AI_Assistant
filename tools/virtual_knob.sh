#!/bin/bash
# Start the bridge in virtual mode and the simulator window connected to it.
#   tools/virtual_knob.sh            # with the usage poll (one tiny API request per minute)
#   tools/virtual_knob.sh --no-usage # without it (limits show "No data")
# Quit with Esc in the simulator window (or Ctrl-C here).
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SOCK="$HOME/.config/ai-knob/knob.sock"
[ -x "$ROOT/firmware/.pio/build/sim/program" ] || pio run -d "$ROOT/firmware" -e sim
cd "$ROOT"
.venv/bin/python -m bridge.claude_usage_daemon --virtual "$SOCK" "$@" &
BRIDGE=$!
trap 'kill $BRIDGE 2>/dev/null' EXIT
while [ ! -S "$SOCK" ]; do sleep 0.1; done
cd "$ROOT/firmware" && SIM_BRIDGE="$SOCK" .pio/build/sim/program
