#!/usr/bin/env bash
# 只在「進度」那台機器上執行（由 install.sh 叫）：確認找得到 claude、準備狀態資料夾。cron 由 plugins.py apply 照 plugin.json 寫。
set -euo pipefail
[ $# = 0 ] || { echo "✗ 不認得 $1（這支不收參數），什麼都沒做"; exit 2; }
trap 'rc=$?; [ "$rc" = 0 ] && echo "ALT-STEP-OK progress"; exit $rc' EXIT  # install.sh looks for it: this script, not a stand-in, ran
# only this machine's engine may install (a worktree or a second clone would add every line again, start a
# second bot): altention/engine_id.py says so with ALT-ENGINE-OK, anything else (or a python3 that says nothing) stops
EID="$(python3 "$(cd "$(dirname "$0")/.." && pwd)/altention/engine_id.py" 2>&1)" || true
case "$EID" in "ALT-ENGINE-OK "*) ;; *) echo "✗ ${EID:-判斷不了這份是不是這台的引擎}；什麼都沒做"; exit 1 ;; esac
CLAUDE_BIN="$(command -v claude || echo "$HOME/.local/bin/claude")"
[ -x "$CLAUDE_BIN" ] || { echo "找不到 claude CLI"; exit 1; }
mkdir -p "$HOME/.cache/progress"
echo "✓ 進度準備好了，claude：$CLAUDE_BIN（cron 由 plugins.py apply 寫）"
