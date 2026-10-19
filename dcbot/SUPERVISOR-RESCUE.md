# supervisor：換掉跑著的那個、它壞掉時怎麼救

**機器人掛掉時 Discord 上找不到任何 Claude**：要 SSH 到機器人那台照這份做，或在那台的引擎資料夾開 `claude`，請它照這份做。

supervisor 沒有人替它退回。下面的指令都在機器人那台、用 bash 執行；引擎在別的位置就把 `~/altention/engine` 換掉。

每一段開始前先貼這一行，定義 `mine`：它只列出**這個引擎**的 supervisor 和機器人（看行程的工作目錄），不會列到別的頻道的 shell、測試或演練用的假機器人，所以照它的輸出 kill 不會殺錯：

```
mine() { local d; d="$(cd ~/altention/engine/dcbot && pwd -P)"; for p in $(pgrep -f '^(/usr/bin/python3 supervisor.py|[^ ]*/dcbot/.venv/bin/python -m bot.main)'); do [ "$(readlink /proc/$p/cwd)" = "$d" ] && ps -o pid=,lstart=,cmd= -p "$p"; done; true; }
```

## 換上新的 supervisor（改了 supervisor.py 之後）

部署（`deploy.sh`）只重啟機器人，跑著的 supervisor 行程還是舊程式。**先等部署的那版被舊 supervisor 記成正常**（`supervisor.out` 有 `version … is good`、`state/last_good` 是那個 commit）再換：這樣新 supervisor 起來時不會把它當試用版，萬一剛好出事也不會退回到沒有新 supervisor.py 的版本。**不要直接殺 supervisor**：機器人是它的子行程、會留著，cron 再起一個 supervisor 就有兩個機器人。

```
cd ~/altention/engine/dcbot
touch state/stop                                            # supervisor 下次回到迴圈開頭就結束
printf '{"channel": ""}\n' > state/restart-requested       # 機器人以 42 結束（等手上的工作做完，最多幾分鐘）
while kill -0 "$(cat state/supervisor.pid)" 2>/dev/null; do sleep 2; done
mine                                                        # 應該沒有輸出
rm state/stop
setsid nohup /usr/bin/python3 supervisor.py >> state/supervisor.out 2>&1 < /dev/null &
```

確認：`ps -o pid,lstart,cmd -p "$(cat state/supervisor.pid)"` 是剛剛的時間；`mine` 剛好兩行（一個 supervisor、一個機器人）；`tail state/supervisor.out` 有 `starting bot`。

換之前先確認新的 supervisor 認得這份是這台的引擎（只檢查、不啟動；用 cron 的環境）：
```
cd ~/altention/engine/dcbot && env -i HOME="$HOME" PATH=/usr/bin:/bin SHELL=/bin/sh /usr/bin/python3 supervisor.py --check-engine
```
要印 `ok <引擎的路徑>`、結束碼 0。印 `other` 或 `wait` 就先不要換，回報。

## 新的 supervisor 壞掉時

### 0. 看是哪一種
```
cd ~/altention/engine/dcbot
ps -o pid,lstart,cmd -p "$(cat state/supervisor.pid)"
mine
tail -40 state/supervisor.out
git -C ~/altention/engine log --oneline -8
```
- supervisor 沒在跑、`supervisor.out` 每 5 分鐘一個 Traceback：它一啟動就當掉，機器人也沒起來。
- 在跑，但一直退回（log 有一串 `dcbot: roll back to …`）或把正常的版本殺掉（`set events aside as failed` / `never came up`）。
- 兩個機器人（`mine` 列出兩行 `bot.main`）。

以上任一種都照 1 → 4 做。

### 1. 全部停下來
```
cd ~/altention/engine/dcbot
touch state/stop
kill "$(cat state/supervisor.pid)" 2>/dev/null
kill "$(cat state/bot.pid)" 2>/dev/null
sleep 3; mine                                               # 應該沒有輸出；還有的話 kill 它列出來的 pid
```

### 2. 換回舊的 supervisor
撤掉**最近一個動到 `dcbot/supervisor.py` 的 commit**（每次改 supervisor.py 都適用，不用找訊息）：
```
cd ~/altention/engine
C=$(git log -1 --format=%h -- dcbot/supervisor.py)
git log -1 --format='%h %s' "$C"                            # 先看一眼：是不是剛剛換上的那一版
bin/home-git revert --no-edit "$C" && bin/home-git push
```
`home-git` 失敗（例如 altention 壞了）就改用 `git revert --no-edit "$C" && git push`。

它做錯的退回也要撤掉：`git log --oneline --grep='^dcbot: roll back'` 找出這次的，逐個 `bin/home-git revert --no-edit <hash>`，最後 `bin/home-git push`。退回的 commit 訊息裡列了它退掉的每個 commit（`git log -1 <hash>` 看）。新版退回時另存的、別的頻道沒提交的修改在 `.git/altention-saved/<時間>-rollback/`：另存的是整個檔，要自己把修改合併回去，不要整個複製回去（那等於把退回撤掉）。

套件裝不上時的退路 commit（`dcbot: step back to … while packages install (…)`）不一樣：它只在這台、不能推，也不要 revert（revert 之後推出去就把兩個都推上去了）。要拿掉它（不管上面有沒有疊別的 commit）：`git log --oneline --grep='^dcbot: step back to'` 找出它的 hash，在引擎跑 `bin/home-git rebase --onto <退路>^ <退路>`。其餘交給 supervisor：它的紀錄會自己刪掉，新版啟動前會先裝套件（裝不上就照舊停著等，30 分鐘後再先退回舊版（只在這台））。拿掉之後在 Discord 打 !restart（或等下一次重啟），機器人才會換到新版，換之前會先裝套件。有退路 commit 時，這台的 `deploy.sh`（兩種用法）和 `bin/home-git push` 都會拒絕推，supervisor 的退回也不推；要修 requirements.txt 的部署請在別台做，或先照上面把退路拿掉。

主工作樹卡在 rebase 中途（`ls .git/rebase-merge .git/rebase-apply` 有東西）：`git rebase --abort; rm -f .git/agent-rebase`。

### 3. 重新啟動
```
cd ~/altention/engine/dcbot
rm state/stop
setsid nohup /usr/bin/python3 supervisor.py >> state/supervisor.out 2>&1 < /dev/null &
```
（不手動起的話，cron 5 分鐘內也會起；`state/stop` 一定要先刪，不然它一起來就結束。）
`state/stop` 在的時候，別的頻道對這台跑 `ops/apply.py` 也不會把它叫起來：apply 只刪 `dcbot/uninstall.sh` 在機器人搬走時寫的那種（內容是 `uninstalled by apply …`）；空檔或別的內容一律不刪、不啟動，回報「supervisor 被人停著（state/stop），沒有啟動」，結束碼非 0。手動跑 `dcbot/install.sh`（不帶 --no-cron）照舊會刪掉它並啟動。

### 4. 確認
```
cd ~/altention/engine/dcbot
sleep 20
ps -o pid,lstart,cmd -p "$(cat state/supervisor.pid)"
mine                                                        # 剛好兩行：一個 supervisor、一個機器人
grep -c failed_events_since supervisor.py                   # 0 = 舊版
tail -5 state/supervisor.out
```
再到 Discord 隨便一個頻道說一句，看機器人有沒有回。舊版會把現在的版本當成試用，3 分鐘內上線就會記成正常。
