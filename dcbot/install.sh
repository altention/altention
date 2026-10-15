#!/usr/bin/env bash
# 在負責跑機器人的機器上執行（由引擎的 install.sh 叫）：機器人的程式和 venv。cron 和 hook 由 plugins.py apply 照
# altention/core.plugin.json 寫。可重複執行。
set -euo pipefail
[ $# = 0 ] || { echo "✗ 不認得 $1（這支不收參數），什麼都沒做"; exit 2; }
trap 'rc=$?; [ "$rc" = 0 ] && echo "ALT-STEP-OK dcbot"; exit $rc' EXIT  # install.sh looks for it: this script, not a stand-in, ran
# only this machine's engine may install (a worktree or a second clone would add every line again, start a
# second bot): altention/engine_id.py says so with ALT-ENGINE-OK, anything else (or a python3 that says nothing) stops
EID="$(python3 "$(cd "$(dirname "$0")/.." && pwd)/altention/engine_id.py" 2>&1)" || true
case "$EID" in "ALT-ENGINE-OK "*) ;; *) echo "✗ ${EID:-判斷不了這份是不是這台的引擎}；什麼都沒做"; exit 1 ;; esac
cd "$(dirname "$0")"
ROOT="$(pwd)"
# venv-make: begin (dcbot/tests/test_install_venv.py runs this block on its own)
# A venv whose pip does not run is made again (--clear): a first try that failed for want of python3-venv leaves
# one with bin/python and no pip, and every later run stopped at it until .venv was removed by hand.
if ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
  python3 -m venv --clear .venv || { echo "✗ 建不了機器人的 Python 環境（dcbot/.venv）。Debian／Ubuntu 請先裝 python3-venv（sudo apt install python3-venv），再重跑安裝"; exit 1; }
fi
# venv-make: end
.venv/bin/python -m pip install -q --upgrade pip >/dev/null
.venv/bin/python -m pip install -q -r requirements.txt
mkdir -p state && chmod 700 state  # what the bot keeps (channels, questions, logs): its owner's only (doctor: core.private-dirs)
# stop-flag: begin (ops/tests/test_apply.py runs this block on its own)
STOPPED=0
if [ -f state/stop ] && case "$(head -c 200 state/stop 2>/dev/null)" in "uninstalled by apply"*) true ;; *) false ;; esac; then
  rm -f state/stop  # left by uninstall.sh when the bot moved away: it has moved back
elif [ -e state/stop ]; then
  STOPPED=1  # someone stopped it (SUPERVISOR-RESCUE.md): never started behind their back
  echo "⚠ supervisor 被人停著（state/stop），沒有啟動；要啟動請照 dcbot/SUPERVISOR-RESCUE.md 刪掉 state/stop"
fi
# stop-flag: end
ENVF="$HOME/.config/dcbot/env"
if [ ! -s "$ENVF" ]; then
  echo "⚠ 還沒有設定檔 $ENVF（需要 DISCORD_TOKEN、OWNER_ID、GUILD_ID），機器人不會啟動"
fi
echo "✓ dcbot 的程式已裝好（cron 和 hook 由 plugins.py apply 寫）"
if [ -n "${ALT_NO_START:-}" ]; then
  echo "（ALT_NO_START：不啟動 supervisor）"
elif [ -s "$ENVF" ] && [ "$STOPPED" = 0 ]; then nohup /usr/bin/python3 supervisor.py >> state/supervisor.out 2>&1 & echo "✓ supervisor 已啟動"; fi
