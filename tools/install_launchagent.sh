#!/bin/bash
# Run the bridge as a LaunchAgent: starts at login, restarts after a crash.
#   tools/install_launchagent.sh              install (or update) and start
#   tools/install_launchagent.sh --uninstall  stop and remove
# Logs: ~/Library/Logs/ai-knob-bridge.{out,err}.log
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL=com.user.ai-knob-bridge
DST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ "$1" = "--uninstall" ]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$DST"
  echo "Removed $LABEL."
  exit 0
fi

PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || { echo "No venv: python3 -m venv .venv && .venv/bin/pip install bleak httpx"; exit 1; }
if pgrep -f "bridge.claude_usage_daemon" | grep -v $$ >/dev/null; then
  echo "A bridge is already running (e.g. in a terminal) — stop it first, the knob takes one connection."
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
sed -e "s|__PYTHON_BIN__|$PY|g" -e "s|__REPO_DIR__|$ROOT|g" -e "s|__HOME__|$HOME|g" \
  "$ROOT/tools/$LABEL.plist" > "$DST"
plutil -lint "$DST" >/dev/null
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$DST"
echo "Started $LABEL."
echo "  tail -F ~/Library/Logs/ai-knob-bridge.out.log    # live log"
echo "  launchctl kickstart -k gui/$(id -u)/$LABEL       # restart (after bridge changes)"
echo "  tools/install_launchagent.sh --uninstall         # stop and remove"
