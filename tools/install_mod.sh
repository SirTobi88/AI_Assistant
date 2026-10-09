#!/bin/bash
# Install the ai-knob mod for every Claude Code session of this user (CLI and,
# per the docs, the desktop app's local Code sessions too), read straight from
# this repository: after editing mod/ai-knob, /reload-plugins picks it up.
#   tools/install_mod.sh             install
#   tools/install_mod.sh --uninstall remove it again
# Run it from a terminal; the desktop app's sessions don't take /plugin commands.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MOD="$ROOT/mod/ai-knob"
if [ "$1" = "--uninstall" ]; then
  claude plugin uninstall ai-knob@ai-knob-local || true
  claude plugin marketplace remove ai-knob-local || true
  exit 0
fi
claude plugin validate "$MOD"
claude plugin marketplace add "$MOD" --scope user
claude plugin install ai-knob@ai-knob-local --scope user
echo "Installed. New sessions load it; running ones after /reload-plugins."
