#!/usr/bin/env bash
# 安裝總入口（階段 3a.9）：不互動，答案全部來自實例設定，每一步可以重跑。
#   ./install.sh            這台需要的全部（實例、連結、各插件的程式、排程和 hook）
#   ./install.sh --links    只做輕的部分（skill 連結、altention 指令、全域說明、記憶）
#   ./install.sh --token    在自己的終端機輸入機器人的 token 和兩個 ID（只從鍵盤讀，不經過 Claude）
# 每一步一行「狀態 代號 說明」；結束碼 0 成功、1 失敗、2 要你動手（todo 那幾行寫了做什麼）。
# 新機器：git clone <repo> ~/altention/engine（或把壓縮檔解到那裡），再跑 ~/altention/engine/install.sh --token 和 install.sh
# 這裡只找 python3 和 git，其餘都在 ops/install.py。
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="$(command -v python3 2>/dev/null)"
V="$([ -n "$PY" ] && "$PY" -c 'import sys; print("ALT-PY %d %d" % sys.version_info[:2])' 2>/dev/null)"
case "$V" in
  "ALT-PY 3 "[89]|"ALT-PY 3 "[1-9][0-9]) ;;
  *) echo "fail core.python 找不到 Python 3.8 以上（python3 是 ${PY:-沒有}${V:+，回答 $V}）：先安裝 Python 3.8 以上再重跑"; exit 1 ;;
esac
case "$(git --version 2>/dev/null)" in
  "git version "*) ;;
  *) echo "fail core.git 找不到 git：先安裝 git 再重跑"; exit 1 ;;
esac
exec "$PY" "$HERE/ops/install.py" "$@"
