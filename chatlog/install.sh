#!/usr/bin/env bash
# 安裝 chatlog（由 install.sh 叫）：clone 紀錄 repo、固定機器名稱。hook 和 cron 由 plugins.py apply 照 plugin.json 寫。可重複執行。
set -euo pipefail
[ $# = 0 ] || { echo "✗ 不認得 $1（這支不收參數），什麼都沒做"; exit 2; }
trap 'rc=$?; [ "$rc" = 0 ] && echo "ALT-STEP-OK chatlog"; exit $rc' EXIT  # install.sh looks for it: this script, not a stand-in, ran
# only this machine's engine may install (a worktree or a second clone would add every line again, start a
# second bot): altention/engine_id.py says so with ALT-ENGINE-OK, anything else (or a python3 that says nothing) stops
EID="$(python3 "$(cd "$(dirname "$0")/.." && pwd)/altention/engine_id.py" 2>&1)" || true
case "$EID" in "ALT-ENGINE-OK "*) ;; *) echo "✗ ${EID:-判斷不了這份是不是這台的引擎}；什麼都沒做"; exit 1 ;; esac
HERE="$(cd "$(dirname "$0")" && pwd)"
LOGS="${CHATLOG_REPO:-${ALT_DATA:-${ALT_ROOT:-$HOME/altention}/data}/logs}"  # as {data}/logs in plugin.json
PY="$(command -v python3)"
# 紀錄推到哪：$CHATLOG_REMOTE，否則實例 plugins.json 的 sync.dirs.chatlog.remote。都沒有就只留在本機。
REMOTE="${CHATLOG_REMOTE:-$("$PY" - "$HERE/.." <<'PYEOF'
import sys
sys.path.insert(0, sys.argv[1])
from altention import config
remote = ""
try:
    config.instance_dir() and config.setting("sync")
except Exception as e:  # a broken plugins.json or pointer: one plain line, not a traceback
    sys.exit("✗ 實例的設定讀不出來（%s），沒有安裝 chatlog；修好再重跑" % e)
if config.instance_dir():
    remote = config.setting("sync.dirs.chatlog.remote") or ""
print(remote)
PYEOF
)}"

# 1. 紀錄 repo
if [ ! -d "$LOGS/.git" ]; then
  # 設了遠端就先檢查再 clone（不是公開的、不是引擎的、不是別的資料：ops/remote.py clone）；沒過或 clone 不了就在這台建新的
  if [ -z "$REMOTE" ] || ! "$PY" "$HERE/../ops/remote.py" clone logs "$REMOTE" "$LOGS" </dev/null; then
    mkdir -p "$LOGS"; git -C "$LOGS" init -q  # its origin from step 1a, checked first
  fi
  git -C "$LOGS" checkout -q -B main 2>/dev/null || true
fi
chmod 700 "$LOGS" 2>/dev/null || true  # every conversation, word for word: no other account reads it
echo "✓ 紀錄 repo：$LOGS"

# 1a. 遠端：還沒有 origin 就照設定接上（先檢查：不是公開的、不是引擎的、不是別的資料），並放 pre-push hook
#     （ops/remote.py attach，C-25）。已經有的 origin 跟設定不一樣時不改，只說出來
if [ -n "$REMOTE" ] || git -C "$LOGS" remote get-url origin >/dev/null 2>&1; then
  "$PY" "$HERE/../ops/remote.py" attach logs "$REMOTE" "$LOGS" </dev/null || true
fi

# 1b. 固定機器名稱（macOS 的 hostname 會隨網路改變，紀錄資料夾會跟著分裂）
mkdir -p "$HOME/.config/altention"
[ -s "$HOME/.config/altention/host" ] || hostname | cut -d. -f1 > "$HOME/.config/altention/host"
echo "✓ 機器名稱：$(cat "$HOME/.config/altention/host")（改名請編輯 ~/.config/altention/host）"

# 2. 檢查
git config user.name >/dev/null && git config user.email >/dev/null || echo "⚠ 這台機器沒有設定 git user.name / user.email，commit 會失敗"
ORIGIN="$(git -C "$LOGS" remote get-url origin 2>/dev/null || true)"
if [ -z "$ORIGIN" ] && [ -n "$REMOTE" ]; then  # set, but step 1a did not attach it (said there, and in the alerts)
  echo "⚠ 紀錄的遠端（$REMOTE）沒有接上，紀錄先只留在這台機器（$LOGS）。原因見上面 ⚠ 那一行，或系統檢查的 alerts；改好設定再重跑安裝"
elif [ -z "$ORIGIN" ]; then
  echo "ℹ 沒有設定要推到哪裡，紀錄只留在這台機器（$LOGS）。"
elif ! /usr/bin/env -i HOME="$HOME" PATH=/usr/local/bin:/usr/bin:/bin GIT_TERMINAL_PROMPT=0 \
    GIT_SSH_COMMAND="ssh -o BatchMode=yes -o ConnectTimeout=10" git -C "$LOGS" ls-remote origin </dev/null >/dev/null 2>&1; then  # </dev/null: run from a script fed on stdin (ops/apply.py)
  echo "⚠ cron 環境下連不上紀錄的遠端（$ORIGIN）：金鑰可能有密碼、只存在 ssh-agent 裡，或這台還沒有權限。"
  echo "  hooks 仍會在對話結束時 push；只有定時的背景同步會失敗。"
fi
echo "完成。第一次匯出所有舊對話：$PY \"$HERE/chatlog.py\" sync --all"
