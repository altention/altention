#!/usr/bin/env bash
# 只在「用量統計」那台機器上執行（由 install.sh 叫）：裝好 node + ccusage 和畫圖用的 matplotlib。cron 由 plugins.py apply 照 plugin.json 寫。
# 沒設 plugins.usage.sources 時只統計這台；在那裡列出的其他機器，也要各在那台跑一次 usage/install-node.sh。
set -euo pipefail
[ $# = 0 ] || { echo "✗ 不認得 $1（這支不收參數），什麼都沒做"; exit 2; }
trap 'rc=$?; [ "$rc" = 0 ] && echo "ALT-STEP-OK usage"; exit $rc' EXIT  # install.sh looks for it: this script, not a stand-in, ran
# only this machine's engine may install (a worktree or a second clone would add every line again, start a
# second bot): altention/engine_id.py says so with ALT-ENGINE-OK, anything else (or a python3 that says nothing) stops
EID="$(python3 "$(cd "$(dirname "$0")/.." && pwd)/altention/engine_id.py" 2>&1)" || true
case "$EID" in "ALT-ENGINE-OK "*) ;; *) echo "✗ ${EID:-判斷不了這份是不是這台的引擎}；什麼都沒做"; exit 1 ;; esac
HERE="$(cd "$(dirname "$0")" && pwd)"
"$HERE/install-node.sh" | sed 's/^/  /'
V="$HOME/.local/share/usage/venv"  # matplotlib for the charts
mkdir -p "$HOME/.local/share/usage" && chmod 700 "$HOME/.local/share/usage"  # private in plugin.json
if [ ! -x "$V/bin/python" ]; then python3 -m venv "$V"; fi
"$V/bin/python" -c "import matplotlib" 2>/dev/null || "$V/bin/pip" install -q matplotlib
mkdir -p "$HOME/.cache"
echo "✓ 用量統計的程式已裝好（cron 由 plugins.py apply 寫）"
