#!/usr/bin/env bash
# 套用修改：檢查 → 測試 → commit → push → 請機器人在目前工作結束後重啟。
# 新版本如果啟動失敗，supervisor 會自動退回上一個正常版本。
#
# 兩種用法：
#   ./deploy.sh --commit <hash> [--commit <hash>…] "commit 訊息（英文）" ["給使用者看的更新說明（中文）"]
#       審過的 commit（在別的 worktree 做好的）：先在暫時的 worktree 裡做出候選 commit（HEAD 加上 <hash> 對它的基底改的
#       東西）、在那裡跑完所有檢查，全過才把正式樹快轉過去。main 到那個 hash 之間的每個 commit 都要列出來（疊起來
#       的就給幾個 --commit，每個都要審過）；origin 比這台的 main 新就先 bin/home-pull。之前正式樹一個位元組都不動：cron、hook、別的頻道跑的
#       仍是舊程式。失敗時候選和測試輸出留在 .git/altention-deploy/（下次部署清掉）。cron 或 hook 會直接執行的東西
#       （altention/、chatlog/、progress/、bin/、ops/、tools/、dcbot/tools/、deploy.sh、tools 會 import 的 bot 模組）只能這樣部署。
#   ./deploy.sh "commit 訊息（英文）" ["更新說明"] [dcbot/ 以外要一起提交的路徑…]
#       在正式樹裡原地改好再提交：只給「機器人重啟前沒有人會執行」的檔（dcbot/bot/ 裡 tools 不會 import 的模組、測試、
#       文件）。改到別的會被拒絕，請改用 --commit。
# 只提交 dcbot/ 和明確列出的路徑：同一個引擎資料夾裡別的頻道改到一半的東西不會被帶出去。
# 每次都會把 GUIDE.md 同步到說明頻道；使用者感覺得到的改動請附更新說明。
set -euo pipefail
cd "$(dirname "$0")"
LIVE="$PWD"  # 正式樹的 dcbot/
REVIEWED=""; HASHES=()
while [ "${1:-}" = "--commit" ]; do  # 疊起來的幾個 commit：每一個都要列出來（每一個都審過）
  HASHES+=("${2:?用法：./deploy.sh --commit <hash> [--commit <hash>…] \"commit 訊息\" [\"更新說明\"]}")
  REVIEWED="${REVIEWED:+$REVIEWED }$2"
  shift 2
done
MSG="${1:?用法：./deploy.sh [--commit <hash>] \"commit 訊息\" [\"給使用者看的更新說明\"]}"
NOTE="${2:-}"
EXTRA=("${@:3}")  # 相對於 dcbot/ 的路徑，例如 ../docs/x.md（只有原地改的用法才有）
if [ -n "$REVIEWED" ] && [ "${#EXTRA[@]}" -gt 0 ]; then
  echo "✗ --commit 提交的就是那個 commit 改的檔，不用另外列路徑"; exit 2
fi
sync_guide() {  # 說明頻道跟不上不該擋住部署
  [ -z "${DEPLOY_NO_GUIDE:-}" ] || return 0  # 在試跑用的 clone 裡：不碰 Discord
  echo "== 同步說明頻道"
  if [ -n "$NOTE" ]; then tools/dcctl guide --note "$NOTE"; else tools/dcctl guide; fi || echo "（說明頻道同步失敗，可以之後手動跑 tools/dcctl guide）"
}
PY="$LIVE/.venv/bin/python"  # 正式的 .venv：候選的 worktree 裡沒有（不進 git）
verify() {  # 在目前的資料夾（某棵樹的 dcbot/）跑所有檢查；不寫那棵樹以外的東西

  echo "== 語法檢查"
  $PY -m py_compile bot/*.py supervisor.py deploy_candidate.py tools/dcctl
  echo "== 測試"
  # 各組測試同時跑（各自用暫存資料夾，互不相干），全部跑完才看結果：部署時間取最慢的那組，不是加總
  TL="$(mktemp -d)"
  PIDS=()
  # test_supervisor 約 20 秒（真的起行程、等真的秒數）：只在 supervisor.py 或它的測試有改時跑，平常的部署不用等它
  DCBOT_SUPERVISOR_TESTS=skip $PY -m unittest discover -s tests -q >"$TL/dcbot" 2>&1 & PIDS+=("$!:dcbot")
  if ! git diff --quiet "${DEPLOY_SINCE:-@{u\}}" -- supervisor.py tests/test_supervisor.py 2>/dev/null; then  # committed, not pushed, too
    $PY -m unittest tests.test_supervisor -q >"$TL/supervisor" 2>&1 & PIDS+=("$!:supervisor")
    if ! git diff --quiet "${DEPLOY_SINCE:-@{u\}}" -- supervisor.py 2>/dev/null; then
      echo "⚠⚠⚠ 這次動到 supervisor.py：上線後要換 supervisor 行程（SUPERVISOR-RESCUE.md「換上新的」，先 --check-engine），RESCUE 第 2 步會指到這個 commit"
    fi
  fi
  for t in altention chatlog bin ops tools progress; do  # 引擎共用的程式：用各機器實際會跑的系統 Python 測
    if [ -f "../$t/__init__.py" ]; then top=(-t .); else top=(); fi  # a package is imported from the engine's root
    [ -d "../$t/tests" ] || continue
    (cd .. && /usr/bin/python3 -m unittest discover -q -s "$t/tests" "${top[@]}") >"$TL/$t" 2>&1 & PIDS+=("$!:$t")
  done
  FAILED_TESTS=""
  for p in "${PIDS[@]}"; do
    wait "${p%%:*}" || FAILED_TESTS="$FAILED_TESTS ${p#*:}"
  done
  if [ -n "$FAILED_TESTS" ]; then  # kept first: whatever the report below runs into, the output is there
    mkdir -p "$KEPT" && cp "$TL"/* "$KEPT"/ || true
    echo "（完整輸出在 $KEPT/）"
  fi
  for t in $FAILED_TESTS; do  # unittest's own report first: the tests' prints can push it out of a plain tail
    echo "---- $t 的測試失敗："
    { grep -n -E -A25 '^(FAIL|ERROR):' "$TL/$t" || true; } | head -80  # none (a crash, the home guard): just the tail
    tail -15 "$TL/$t" || true
  done
  rm -rf "$TL"
  [ -z "$FAILED_TESTS" ] || { echo "✗ 測試失敗：$FAILED_TESTS，停止部署"; return 1; }
  if [ -f requirements.txt ] && ! git diff --quiet "${HEAD0:-HEAD}" -- requirements.txt 2>/dev/null; then
    echo "== 安裝新套件"  # 裝進正式的 .venv：跑著的機器人不會去載入沒 import 過的套件，測試要用到
    "$LIVE/.venv/bin/pip" install -q -r requirements.txt
  fi
  echo "== hook 實測"  # 機器人收不到事件＝所有頻道無聲：先確認 hook 真的寫得出事件
  HT="$(mktemp -d)"
  echo '{"hook_event_name": "Stop", "session_id": "deploy-check", "last_assistant_message": "ok"}' | DCBOT_ROOT="$HT" DCBOT_CHANNEL=1 tools/dchook
  if ! ls "$HT"/state/events/*.json >/dev/null 2>&1; then rm -rf "$HT"; echo "✗ tools/dchook 沒有寫出事件，停止部署"; return 1; fi
  rm -rf "$HT"
}

if [ -n "$REVIEWED" ]; then
  KEPT="$(cd .. && git rev-parse --git-common-dir)"; case "$KEPT" in /*) ;; *) KEPT="$LIVE/../$KEPT";; esac
  KEPT="$KEPT/altention-deploy/tests"
  exec 9>>"$(git rev-parse --git-common-dir)/agent-git.lock"
  # 一次只有一個 --commit：候選的資料夾只有一份，第二個會清掉第一個正在驗的候選
  DLOCK="$(git rev-parse --git-common-dir)/altention-deploy.lock"
  exec 8>>"$DLOCK"
  flock -n 8 || { echo "✗ 另一個 ./deploy.sh --commit 正在跑（候選只有一份），等它結束再部署：$(tail -n 1 "$DLOCK" 2>/dev/null)"; exit 1; }
  printf '%s 開始部署 %s（行程 %s%s）\n' "$(date '+%F %T')" "$REVIEWED" "$$" "${DCBOT_CHANNEL:+，頻道 $DCBOT_CHANNEL}" > "$DLOCK"  # 被擋的那個印這一行
  LANDED=0
  for attempt in 1 2 3; do
    echo "== 做候選（$REVIEWED）"
    ASSIGN="$(/usr/bin/python3 "$LIVE/deploy_candidate.py" prepare "$MSG" "${HASHES[@]}" 8>&-)" || exit 1
    eval "$ASSIGN"  # CAND、C、HEAD0
    # 不放在 if 或 || 裡：那樣 set -e 在整個 verify 裡都不作用，中間失敗的檢查會被略過
    # 8>&-：部署鎖只有這支 shell 自己拿著。子行程繼承了它，deploy.sh 被中斷後還沒結束的測試或 git 會讓下一次部署被擋
    set +e; (set -e; cd "$CAND/dcbot"; DEPLOY_SINCE="$HEAD0" verify) 8>&-; RC=$?; set -e
    if [ "$RC" != 0 ]; then
      /usr/bin/python3 "$LIVE/deploy_candidate.py" untouched || true
      exit 1
    fi
    flock -w 120 9 || { echo "✗ 120 秒內等不到 git 鎖（別的同步或部署還在跑），稍後再部署"; /usr/bin/python3 "$LIVE/deploy_candidate.py" untouched || true; exit 1; }
    echo "== 檢查全過，把正式樹換到候選 ${C:0:7}"
    set +e; /usr/bin/python3 "$LIVE/deploy_candidate.py" land "$C" "$HEAD0" 9>&- 8>&-; RC=$?; set -e
    if [ "$RC" = 0 ]; then LANDED=1; break; fi
    flock -u 9
    if [ "$RC" != 3 ]; then /usr/bin/python3 "$LIVE/deploy_candidate.py" untouched || true; exit 1; fi
  done
  if [ "$LANDED" != 1 ]; then
    echo "✗ main 一直有新的 commit 進來，試了 3 次都沒換成，稍後再部署"
    /usr/bin/python3 "$LIVE/deploy_candidate.py" untouched || true
    exit 1
  fi
  /usr/bin/python3 "$LIVE/deploy_candidate.py" done 8>&- || true   # 成功：候選的 worktree 和留下的測試輸出刪掉
  exec 8>&-  # 候選已經換上去、清掉了：之後的 push、同步說明頻道不用再擋著下一個部署
  BOT_CHANGED=0; git diff --quiet "$HEAD0" HEAD -- . ../altention || BOT_CHANGED=1
else
  KEPT="state/deploy-tests"
  # 原地改的用法：只收「機器人重啟前沒有人會執行」的檔
  RUNS="$(/usr/bin/python3 deploy_candidate.py in-place "${EXTRA[@]/#..\//}")"
  if [ -n "$RUNS" ]; then
    echo "✗ 這些檔不等機器人重啟就會被執行（cron、hook、別的頻道、別台機器），不能在正式樹裡改了再測："
    printf '%s\n' "$RUNS" | sed 's/^/    /'
    echo "  請在別的 worktree 提交、送審，再用 ./deploy.sh --commit <hash> \"訊息\" 部署（檢查全過才會換進正式樹）。"
    exit 1
  fi
  set +e; (set -e; verify); RC=$?; set -e
  [ "$RC" = 0 ] || exit 1
fi
/usr/bin/python3 tools/dchook --update   # 這台機器裝過全域 hook 的話，更新到跟程式一致（沒裝過就不動）
# 引擎裡所有自動化的 git（核心的 cron_sync／cron_update、bin/home-pull、這裡）共用一把鎖：同時 pull 會失敗，
# 一個工作的 rebase --abort 也可能中止另一個工作進行到一半的 rebase。pull/push 讓 git 繼承鎖並套 timeout：
# deploy.sh 被中斷時，還在跑的 git 最多再拿著鎖 300 秒，不會變成沒有鎖的孤兒
if [ -z "$REVIEWED" ]; then
  exec 9>>"$(git rev-parse --git-common-dir)/agent-git.lock"
  flock -w 120 9 || { echo "✗ 120 秒內等不到 git 鎖（別的同步或部署還在跑），稍後再部署"; exit 1; }
  echo "== commit（dcbot/${EXTRA:+ 加上 ${EXTRA[*]}}）"
  git add -A -- . 9>&-
  for p in "${EXTRA[@]}"; do  # a path gone, or now an ignored link (stage 2.3b): stage its removal only
    if [ -L "$p" ] || [ ! -e "$p" ]; then git add -u -- "$p" 9>&- 2>/dev/null || true; else git add -A -- "$p" 9>&-; fi
  done
  if git diff --cached --quiet -- . "${EXTRA[@]}"; then
    # 沒有新的修改，但可能有還沒推出去的 commit（例如自己 commit 了的 revert、或上次部署在 commit 之後被中斷）
    UNPUSHED="$(git rev-list --count '@{u}..HEAD' 2>/dev/null || echo 0)"
    # DEPLOY_SINCE: a commit made here under the lock before calling (ops/instance.py): the sync may have pushed it already
    SINCE="${DEPLOY_SINCE:-@{u\}}"
    if [ "$UNPUSHED" = 0 ] && { [ -z "${DEPLOY_SINCE:-}" ] || [ "$(git rev-parse HEAD)" = "$(git rev-parse "$DEPLOY_SINCE")" ]; }; then
      echo "沒有任何修改"; flock -u 9; sync_guide; exit 0
    fi
    echo "== 沒有新的修改，但有 $UNPUSHED 個還沒推出去的 commit（或剛提交的 ${DEPLOY_SINCE:+從 ${DEPLOY_SINCE:0:7} 起的 commit}）：推出去，並照內容決定要不要重啟"
    BOT_CHANGED=0; git diff --quiet "$SINCE" HEAD -- . ../altention || BOT_CHANGED=1
  else
    BOT_CHANGED=0; git diff --cached --quiet -- . ../altention || BOT_CHANGED=1  # decided now: another commit may land on top; the bot imports altention/ too
    git commit -q -m "$MSG" -- . "${EXTRA[@]}" 9>&-
  fi
fi
OTHERS="$(git status --porcelain -- .. ':!.' | head -20)"
[ -n "$OTHERS" ] && [ -z "$REVIEWED" ] && printf '（dcbot/ 以外還有沒提交的修改，這次沒有帶出去；要一起帶就列在第三個參數之後）\n%s\n' "$OTHERS"
# 先直接 push（遠端沒有新 commit 時不需要 pull）；被拒絕才用 altention.sync.update() 接到遠端上：在暫時的 worktree
# 裡 rebase，再用 reset --keep 移過來，所以工作樹裡各頻道改到一半的檔案不會擋住、也不會被動到；不用 autostash。
# 上游是 update 擋下的 hash 就不接上去、停下這次部署（manager 2026-10-07，DESIGN 7.1）：部署不是把別人的壞 commit
# 拉進機器人那台的出路；真的要越過，先 bin/home-pull --take-refused 再部署。
PUSH_ERR="$(mktemp)"
HELD=""
if timeout 300 git push -q 2>"$PUSH_ERR"; then
  :
elif HELD="$(cd .. && /usr/bin/python3 -c 'import sys; sys.path.insert(0, "."); from altention import sync
print(sync.held_back(sync.Repo("."), "先修掉它或由人決定，沒有部署", strict=True))')" || HELD="${HELD:-查不了上游是不是被擋的（檢查本身出錯），沒有部署}"; [ -n "$HELD" ]; then
  echo "✗ $HELD"  # not known is not yes: stopped as well
  echo "  這次的 commit 留在這台、沒有推出去，機器人也不重啟。"
  rm -f "$PUSH_ERR"; flock -u 9; exit 1
elif (cd .. && /usr/bin/python3 -c 'import sys; sys.path.insert(0, "."); from altention import sync
r = sync.Repo("."); r.lock_fd = 9; code, note = sync.update(r); note and print(note); sys.exit(code)') && timeout 300 git push -q; then
  :  # put on top of the upstream the way the sync does: in a throwaway worktree, never a rebase in this one
  :
else
  echo "⚠ 這次的 commit 沒有推出去。第一次 push 的錯誤：$(head -c 300 "$PUSH_ERR")"
  echo "  接著試著接到遠端的新 commit 上也沒成功：可能是兩邊改到同一段、未提交的修改剛好碰到要合進來的檔案，也可能是網路或認證的問題（看上面幾行）。"
  echo "  commit 留在這台，背景同步會再試。**別台機器這次還沒拿到新版**（遠端的 dcremote 要等推出去、機器人下次重啟才換）。"
fi
rm -f "$PUSH_ERR"
flock -u 9
if [ -x ../tools/privacy-scan ]; then  # 個資掃描（DESIGN.md 4.4）：只警告，不擋部署；剛寫下個資的那個 Claude 這一輪就能改
  echo "== 個資掃描（已提交的樹）"
  timeout 120 ../tools/privacy-scan HEAD --summary --top 5 2>&1 | sed 's/^/  /' || true
fi
sync_guide
if [ "$BOT_CHANGED" = 0 ]; then
  echo "== 完成：$(git rev-parse --short HEAD)。這次沒有改到機器人本身，不需要重啟。"
  exit 0
fi
mkdir -p state
printf '{"channel": "%s"}\n' "${DCBOT_CHANNEL:-}" > state/restart-requested
echo "== 完成：$(git rev-parse --short HEAD)。機器人會重啟並回報結果（tmux 模式下這個對話不受影響；print 模式會等這輪結束）。"
