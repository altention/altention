#!/usr/bin/env bash
# 停止機器人（搬到別台機器時，由 ops/apply.py 在舊機器上呼叫）。cron 那兩行之後由 plugins.py apply 拿掉；
# 全域 hook 每台都要，不拿。
[ $# = 0 ] || { echo "✗ 不認得 $1（這支不收參數），什麼都沒做"; exit 2; }
trap 'rc=$?; [ "$rc" = 0 ] && echo "ALT-STEP-OK dcbot-uninstall"; exit $rc' EXIT  # ops/apply.py looks for it: this script, not a stand-in, ran
# only this machine's engine may install (a worktree or a second clone would add every line again, start a
# second bot): altention/engine_id.py says so with ALT-ENGINE-OK, anything else (or a python3 that says nothing) stops
EID="$(python3 "$(cd "$(dirname "$0")/.." && pwd)/altention/engine_id.py" 2>&1)" || true
case "$EID" in "ALT-ENGINE-OK "*) ;; *) echo "✗ ${EID:-判斷不了這份是不是這台的引擎}；什麼都沒做"; exit 1 ;; esac
cd "$(dirname "$0")"
# state/stop 裡寫明是搬走時放的：機器人之後搬回來，apply 認得這種 stop 才會刪掉並啟動（人手動放的空檔不動）
mkdir -p state && printf 'uninstalled by apply %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" > state/stop || { echo "✗ 寫不了 state/stop，機器人沒有停"; exit 1; }
for f in state/bot.pid state/supervisor.pid; do [ -f "$f" ] && kill "$(cat "$f")" 2>/dev/null || true; done
echo "✓ dcbot 已停止（state/stop；cron 的兩行接著由 plugins.py apply 拿掉）"
