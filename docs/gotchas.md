# 踩過的坑

跟平台和工具有關、換一套環境也會遇到的事。只跟你自己的機器有關的（哪台的 PATH 怪、哪台要走哪個 SSH 別名）寫在實例的 `knowledge/gotchas.md`。

## 機器和平台

- **macOS 的 hostname 會隨網路改變。** 用 hostname 當機器名的話，對話紀錄的資料夾會分裂成好幾個。所以機器名固定寫在 `~/.config/altention/host`。
- **會睡眠的機器**（闔上螢幕的筆電）：睡著時 cron 不跑，醒來後下一次同步會補上。睡眠中短暫醒來時網路不穩，`git push` 之類可能卡住，自動化的指令要加逾時。
- **非互動 shell 的 PATH 比較短。** cron、hook、非互動 SSH 的 PATH 通常沒有 `~/.local/bin` 和 Homebrew 的路徑，找不到 `claude` 和 macOS 上的 `tmux`。程式裡要用完整路徑，或自己把這些路徑補進 PATH。
- **`env` 可能被蓋掉。** 有些安裝工具會在 `~/.local/bin` 放一個叫 `env` 的檔案，蓋掉系統的。腳本裡用 `/usr/bin/env`。
- **Python 3.8。** 每台機器上都會跑的程式（hook、同步、`dcremote`、`bin/` 的工具）要相容 3.8，而且只用標準函式庫，因為它們用的是那台的系統 Python。
- **NFS 家目錄上 `umask 077` 不一定生效。** 寫秘密檔案之後要 `chmod 600`，並確認權限真的改了。
- **NFS 上的檔案鎖**（`flock`）在我們測過的環境是可靠的：一台拿著鎖時，另一台從同一個 NFS 拿不到。換一種 NFS 設定要重測。

## git

- **多個程序同時 pull 同一個工作樹會全部失敗**（`Cannot rebase onto multiple branches`），一方失敗時的 `rebase --abort` 還可能中止另一方正在進行的 rebase。引擎資料夾同時是好幾個對話的工作目錄時特別容易發生。所有自動化的 git 操作共用一把鎖；手動更新用 `bin/home-pull`。
- **寫進 git 的檔案不要放每次都會變的時間戳**，否則每次執行都產生一個沒有意義的 commit。
- **`git checkout <commit> -- <路徑>` 會直接蓋掉原位沒被追蹤的同名檔。** supervisor 退回時只另存「有追蹤、沒提交」的修改；一個檔在 good 裡有、之後被刪掉，而現在原位又有一個沒追蹤的同名檔，退回會把它換成 good 的版本、不另存。很少見，知道就好。
- **`.gitignore` 只管沒被追蹤的檔案。** 已經追蹤的檔案加進 `.gitignore` 之後，修改照樣會被提交；要先 `git rm --cached`。

## 指令陷阱

- `pkill -f`、`pgrep -f` 的樣式如果出現在自己這條 SSH 指令裡，會匹配到自己。先用 `ps` 找 PID，再分開 `kill`。
- zsh 的 `log` 是內建指令；macOS 的系統 log 要用 `/usr/bin/log`。
- zsh 不會把沒加引號的變數依空白拆開。`G="a b"; grep x $G` 在 bash 是兩個參數，在 zsh 是一個。

## Claude Code

- **輸入框的灰字**：Claude Code 在 tmux 裡用 256 色畫輸入框（Mac 上設了 `COLORTERM=truecolor` 也一樣）：空的時候的 `Try "…"` 是 dim（`2`），打進去的斜線指令是 `38;5;153`，一般文字是預設色。機器人打字前只在 `/attach` 接上的窗格用顏色判斷灰字（`screen.input_dim`）；自己的對話有字就清，所以如果 Claude Code 之後在輸入框顯示灰色建議，每則訊息會多按兩三次 Ctrl+U、約半秒（功能不受影響）。
- **預設會刪除 30 天前的對話。** 安裝時把 `cleanupPeriodDays` 設成 36500；機器人的「看完整過程」和 `/resume` 都靠對話檔還在。
- `claude -p` 加 `--system-prompt` 取代預設的系統提示，每次呼叫的輸入可以從約 21K token 降到約 4K；`--no-session-persistence` 讓這次呼叫不留下對話紀錄。
- 由程式啟動的對話，entrypoint 是 `sdk-cli`。進度插件用這個欄位略過它們。
- Claude Code 用工作目錄的路徑當對話和記憶的索引。同一個資料夾從符號連結的路徑開，會被當成另一個專案，記憶會分裂；搬資料夾之後舊對話接不回來。設定、cron、hook 一律記解開連結後的真實路徑。
- 長的貼上會被包成 `<pasted_content>`，所以機器人用逐行打字送輸入，不用貼上。
- **macOS 顯示「Not logged in」。** Claude Code 在 macOS 把登入憑證放在鑰匙圈。那台還沒登入過，或鑰匙圈鎖著時，tmux 裡的 Claude 會顯示「Not logged in · Run /login」並提示 `security unlock-keychain`。登入一次（在那台的 Claude 打 `/login`）而且鑰匙圈解鎖之後，從 SSH 新開的 tmux 裡的 Claude 也讀得到。要完全不依賴鑰匙圈，在那台跑一次 `claude setup-token`，把長期 token 設成 `CLAUDE_CODE_OAUTH_TOKEN`。

## 同步的已知限制（Known limits of the sync）

What `altention/sync.py` (and `bin/home-pull`, `deploy.sh`, which use it) does not do on its own, and what to do then.

### Two machines changed the same lines

Two machines edit the same part of a file that is not merged line by line (only `memory/MEMORY.md` is), or one
deletes a file the other changed. Nothing is lost and no conflict markers are written: each machine keeps its own
version. But the machine that tries to put its commit on top of the other's gives up, and from then on its later
memory and knowledge do not go out either, until someone merges.

- How you notice: doctor reports the sync failing on that machine; after 2 hours a notification is sent once.
- What to do: on that machine, `cd ~/altention/engine` (or the instance) and `git status`; merge by hand
  (`git pull --rebase`, resolve, `git rebase --continue`), then `bin/home-git push`.

### The sync breaks after committing

If a broken version of the sync commits this machine's memory and then fails before pushing, the machine is ahead of
the remote. The plain fast-forward fallback in `altention/cron_sync.py` (independent of `sync.py`) cannot help then, so a fix
or a revert does not reach that machine by itself.

- What to do: from the bot's machine, `ssh <machine>`, look at `git -C ~/altention/engine status`, then
  `git -C ~/altention/engine pull --rebase` (and push).
- A syntax error in `sync.py`, or one that fails before committing, is handled by that fallback.

### Someone's staged changes become unstaged

When the sync moves the branch (`git reset --keep`) while another session has changes staged with `git add`, the
contents stay as they were but the staging is undone: modified files show as unstaged, new files as untracked, a
`git mv` as a delete plus a new file. Nothing is lost; stage again before committing.

### Edits saved while undoing a rebase left halfway

If a rebase was left halfway in the work tree (an old tool or the supervisor's rollback killed in the middle), the
sync copies every changed or new file to `.git/altention-saved/<time>/` before undoing it. The copies are not put
back by themselves: until someone merges them, that memory is not in the memory files and does not sync. doctor
reports them.

### 同步不會自動傳播大量刪除
一台機器上的意外（手滑的 `rm -rf`、搬移做到一半失敗）不能在一輪同步內變成每台機器都少了東西：
- 歸實例的資料夾（memory、knowledge，以及實例 `plugins.json` 的 `sync.owns` 列的）整個不見時，同步不提交刪除，回報失敗（2 小時後通知），等人看。是意外的話，在那個 repo 裡 `git checkout -- <資料夾>` 救回來；真的要從實例拿掉，走 `python3 ops/instance.py move-undo`。
- 一次提交會刪掉這類資料夾裡太多檔時（預設：超過一半或超過 10 個，取較小者；`plugins.json` 的 `sync.mass_delete` 可以改 `fraction`、`max`），那些刪除不提交，其餘照常同步，並回報失敗。確認真的要刪：在引擎資料夾跑 `python3 -m altention.sync --allow-delete <repo 的路徑> <資料夾>`，下一輪同步就會提交（24 小時內有效，用一次）。
- 個別檔案的刪除照常同步。其他路徑（例如 `global-notes-edited/`）整個刪掉也照常同步。

### 測試不碰真的家目錄
引擎的每個測試檔第一件事是 `altention.testing.guard_home()`：整個測試期間 HOME 和 XDG 資料夾指到暫存目錄；結束時真的 `~/.local/state/altention`、`~/.cache/altention`、`~/.config/altention`、`~/altention` 有新增、不見或改過的檔，測試就失敗（`deploy.sh` 因此停下）。新寫的測試檔也要加。


## 搬引擎的位置（3a.9 起）

每台「正式引擎」的判斷依序是：指標檔 `~/.config/altention/engine`（安裝入口寫的）→ 機器清單上這台的 `engine` → `~/altention/engine` → `~/.local/bin/altention` 指到的引擎，取第一個存在的。從其他 checkout（worktree、第二份 clone）安裝、套用排程、啟動 supervisor 都會被擋。所以搬引擎要照這個順序：

1. 把引擎 clone（或搬）到新位置。
2. 改機器清單上這台的 `engine`（如果新位置不是 `~/altention/engine`）。
3. **刪掉指標檔** `rm ~/.config/altention/engine`（它排第一，不刪就一直說「引擎在舊位置」）。
4. 在新位置跑 `./install.sh`：它會重寫指標檔、hook、排程到新位置。
5. 換 supervisor（照 `dcbot/SUPERVISOR-RESCUE.md` 的「換上新的」，先用 `--check-engine` 確認），最後再移走舊資料夾。

被擋時的訊息會寫出結論的依據（指標檔、機器清單或預設位置）。

## 引擎更新擋下上游時（3a.10 起）

`altention/update.py` 在移動分支前做語法檢查和載入檢查。上游沒通過就**一直擋著**：同步不會在 45 分鐘後代為拉進來，兩支啟動檔的保底快轉也不拉那個 hash；doctor 顯示「上游被擋：<hash> <原因>，已經多久」（fail），`doctor.py --watch` 會通知。沒有「擋太久就放行」。

- 出路一：在上游推修正（上游換了 hash 就重新檢查，通過就更新）。修正疊在被擋的 commit 上面時，home-pull 和部署也先不拉，由 update 那行檢查過才進來（最多晚一輪）；update 沒在檢查時，同步代班和保底照拉，但會寫 alert（DESIGN 7.1）。
- 出路二：確定是**檢查本身有 bug 而誤擋**時，從控制端跑 `python3 ops/apply.py <機器>`：它跑 `home-pull --take-refused`，不經過 update.py 的檢查。一般的 `home-pull`（串流、usage、各頻道開場）不帶這個旗標，被擋的不會拉。
- 已知而且必要的取捨：update.py 自己壞掉（載不進來、跑到一半丟例外）時沒有人在檢查，啟動檔照舊用單純的快轉把修正拉進來。

## 驗證「各台會自己用 update 那行更新」時（3a.10 起）

引擎會被三條路帶到各台：核心的 update 那行（`altention/cron_update.py`）、同步那行（update 停掉時代為拉）、還有**機器人重啟時**：它對每台重連事件串流，那條 SSH 指令會先跑 `bin/home-pull`。所以部署一個會重啟機器人的 commit（動到 `dcbot/` 或 `altention/`）之後，各台幾秒內就被串流的 home-pull 拉到新版，update 那行到時只看到「沒有新的」。

- 要觀察 update 那行真的拉進新 commit：挑**不重啟機器人**的 commit（只動文件、`chatlog/`、`ops/` 之類），部署後不要手動 home-pull 或 apply，等各台自己的 update 分鐘。
- 對時間：那台 `git reflog` 快轉到新 commit 的時間，要等於 `cron-update.log` 那一輪的時間；`last_ok-update` 的 head 前進。2026-10-07 第一次以為看到了，其實 reflog 顯示三台都在機器人重啟那一秒被串流拉過去。
- 串流、usage 收集和各頻道開場呼叫的 `home-pull` 也不拉被擋的 hash（2026-10-07 起；之前會，最多一小時就被 usage 帶到四台）。

## 實例的設定被推壞了（machines.json、plugins.json…）

**實例快轉前的設定檢查**（2026-10-06、10-07）：同步要把實例接到上游之前，先看上游的 `machines.json`、`plugins.json`：解析得了（UTF-8 的 JSON）、是物件、`format` 沒寫或是 1 以上的整數（比引擎認得的新也放進來，照 `config.py` 的規則：被退回的引擎要能用新版設定）、各段（`format` 和 `_…` 以外的頂層鍵）和 `machines`、`services`、`plugins` 底下每一項是物件；還有這台有 `machines.json`、上游把它刪了也擋（接上之後 config 會安靜地變成「只有這一台」；`plugins.json` 被刪只是回到預設，不擋）。不判斷內容：機器名打錯照樣進來，修正也一樣進得來。接上去的就是檢查過的那個 commit，不是重讀一次的 `@{u}`。

- 沒通過就**擋著**：實例分支不動，這台自己的記憶、知識照樣在本機提交，只是推不出去（沒接到上游上，推了也會被拒）。alerts 寫一行（種類 instance-held，同一行累計次數），doctor 出 `core.instance-held`（fail，`--watch` 會通知，不常開的機器也通知）：「實例上游被擋：<hash> <原因>（已經多久）；這台的實例有 N 個 commit 還沒推出去，等上游修好」。
- **讀不到**上游那棵樹（git 逾時或失敗）不算被擋：只有這一輪沒接上，alerts 寫 instance-unread，不寫紀錄，下一輪重新檢查。
- 被擋的 hash 記在 `~/.local/state/altention/instance-held`，只有 `sync.instance_held()` 一個讀法（同步、`ops/instance.py`、doctor 都用它；內容不對就當成沒有紀錄）。**不會自動放行**：上游還是那個 hash 就不重新檢查。
- 出路一：在上游推修正。上游換了 hash 就重新檢查，通過就接上去，同一輪把等著的 commit 推出去、清掉紀錄。**誰推得了修正**：其他台都擋著、手上沒有壞的那個 commit，只有推壞的那台（或先用出路二越過的那台）能在它上面推修正。在被擋的那台直接改 `machines.json` 提交是沒用的：推不出去（沒接上上游），之後越過時還會跟上游那個壞的改動衝突。
- 出路二：確定是**檢查誤擋**，或要在某台先拿到壞的再推修正時，從控制端跑 `python3 ops/apply.py <機器>`：它在那台跑 `ops/instance.py pull --take-refused`，不經過檢查，越過時印「越過了被擋的 <hash>（原因）」。手動的 `ops/instance.py pull`（不帶旗標）和 move-prepare、move-apply、move-undo 都照樣被擋。
- 已知、留到之後：`ops/instance.py leave` 或分支沒了上游之後，舊的 instance-held 沒人清（doctor 會一直 fail，刪掉那個檔即可）。
- `CLAUDE.global.md`、`privacy.json` 這類不檢查的檔推壞了：同步不會因此停下，下一次推修正，下一輪各台就會拿到。萬一某台還是卡住，到那台的實例資料夾 `cd ~/altention/instance && git pull --rebase`。

## 排程器的只記錄模式（3a.11 起）

每台多一行每分鐘的 cron（`# altention:core`，跑 `altention/cron_schedule.py`）：只把這台的插件排程「會跑什麼」記進 `~/.local/state/altention/scheduler/`（would-run.*.jsonl 滿 1 MB 輪替），什麼都不執行、不通知。對帳：`python3 altention/cron_schedule.py --compare [小時]`：每項直接標 相符／時間不同／不相符／無法判斷（附原因），最後一行是統計。看的是排程項目 `do.trace`（每次執行都會更新的檔：路徑看 mtime，或 `{"file","key","within"}` 讀 JSON 裡的時間；within 預設 5 分鐘），不看 `log`（沒輸出就不寫，不能當證據）。每次「會跑」都要對得到 trace 的一次更新：每分鐘那一輪記完、寫完記號後，另開一個子行程取樣各 trace（只 stat 或讀那個 JSON 欄位，最多等 20 秒，卡住就丟下；pid 檔指向的還是我們的取樣（`cron_schedule.py --sample`）就不再開，空的、0 以下、已不在、別的行程都當作沒有），有變才記進 `~/.local/state/altention/scheduler/trace-history.<機器>.jsonl`（每段連續取樣的起訖也記，512 KB 輪替成 .1），最新狀態在 trace-seen.<機器>.json。共用痕跡檔的項目（planner.out）各比各的；那段沒有取樣（睡著、取樣卡住）標「無法判斷」，不算不相符；還沒有歷史時退回只比最後一次；沒有紀錄的時段不下結論：早於留下來的痕跡歷史最舊那一行的時點標「無法判斷（歷史已輪替）」，早於留下來的 would-run 最舊一行的更新不算「時間不同」，痕跡檔從來讀不到（路徑寫錯）標「無法判斷（痕跡檔讀不到）」；每一次會跑用它自己那一行 would-run 記的 trace 判（trace 換過、後來才寫上都不會變成假的不相符），那個 trace 自己第一次出現在歷史之前的時點標「那時的 trace 沒被取樣」，視窗內換過 trace 會註明；統計行寫出會跑的紀錄和痕跡的歷史各自涵蓋到哪裡（窗子比紀錄長時看得出來）。手動跑一次會落在「時間不同」，由人看。取樣的問題每小時在 cron-schedule.log 說一次（「痕跡：」開頭）。doctor 的 `core.schedule-record`（最後一輪多久前）和 `core.timezone`（排程用的時區）。
- **trace 要只代表那個排程**：chatlog 的 `last_sync` 每個對話結束（hook 的 `export --sync`）都會更新，拿它當排程的 trace，`--compare` 會把那些當成排程外的執行、一直判「時間不同」（2026-10-09）。排程跑的 `chatlog.py sync`（不帶 `--all`、不帶路徑）另外寫 `state.json` 的 `last_cron_sync`，trace 指的是它；`last_sync`（doctor 看的）照舊。手動跑一次不帶參數的 `chatlog.py sync` 也會更新它（那確實是排程外多跑的一次）。替新的排程選 trace 時先問：還有誰會寫這個檔或這個值？
- **自動套用，先只報告（3a.12）**：每分鐘那一輪每 15 分鐘（各台自己的分鐘）起一個子行程跑 `ops/plugins.py report`（最多 60 秒、同時只有一個），把「`apply --write` 會在這台改什麼」寫進 `~/.local/state/altention/scheduler/apply-report.<機器>.json`（最新一份）和 `.jsonl`（內容變了才加一行，256 KB 輪替）。**不會動手**：`report` 只讀 crontab 和 settings.json，不呼叫任何會寫的函式，每分鐘那一輪裡也沒有寫入的程式；實例設定 `core.auto_apply` 只認 `off`（連算都不算）和 `report`（預設），別的值當成 report 並在報告裡說。doctor 的 `plugins.would-apply`：會補／會拿掉／時間不同 → warn；`refuse`（大量移除：會拿掉 2 項以上、或超過帶記號行數的三分之一、或所有插件都 off／不知道、或實例／機器清單／crontab／settings.json 讀不到）和 core 的項目不對 → fail（core 永遠不自動改，要人處理）；刻意停用只是說明；報告超過 45 分鐘沒更新 → 「沒有套用報告」（不常在線的機器說「不在線」），不當成沒有要改的。另一個 checkout 跑這一輪什麼都不寫。
- **打字的節奏（長訊息不能變成貼上）**：Claude Code 把「一口氣到的一長段字」當成貼上（2.1.295：765 字還是打字、785 字變貼上；緊接著送的兩段算同一口氣）：框裡顯示 `[Pasted text #1]`，模型拿到的是包成貼上內容的字（不當成使用者寫的、裡面的指示不照做），長行後面還有換行時甚至打字打完就自己送出。所以 `dcbot/bot/tmuxio.py` 的 `type_text` 照接頭 `altention/connectors/claude_code/typist.py` 的節奏，每 `TYPE_CHUNK`（500）字停 `TYPE_PAUSE`（0.1 秒）再打下一段，跨行累計；500 字以內的訊息照舊一次打完。只動節奏，不改字。**這兩個數字是量出來的**（2026-10-09，暫存 HOME＋本機假 API＋真的 Claude Code 2.1.295，每種情況重開對話）：段長 500、700，間隔 0.02、0.05、0.1、0.2 秒，訊息 780／900／2000／4000 字、中文 2000 字、900＋短、兩行各 900、40 行、三行各 1500：閒著的機器和 20 個核心都被吃滿時，全部都沒有變成貼上、都只送出一則；不停（1000 字一段、間隔 0）才會。選 0.1 秒是最小有效值的 5 倍。**改版後失效怎麼發現**：機器人打完長訊息後若在框裡看到 `[Pasted text`，日誌印一行 `[paste] …`、寫 `dcbot/state/typed-as-paste.json`，doctor 的 `dcbot.typed-as-paste` 一週內顯示 warn；那時照上面的方式重量、調這兩個常數。標記的比對（`screen.bot_typed`、`screen.paste_marks`）留著，節奏失效時接上的終端機仍認得那是機器人打的。

- 每分鐘那行的任何輸出都有上限：stdout、stderr 在 main 一開始就被接走（連子行程），收到的每一句一小時寫一次進 `cron-schedule.log`（256 KB 輪替）。
- 別的 checkout（worktree、第二份 clone）跑它：什麼都不寫。判斷不了是不是這台的引擎（機器清單讀不出來）：不記，一小時說一次。
- 「最後一輪」的記號寫兩份：state 裡和 `/tmp/altention-<uid>-<HOME 真實路徑的 SHA-256 前 8 碼>/`（`scheduler.beside_default()`；state 在檔案伺服器上可能自己就壞了）。2026-10-09 以前是 `/tmp/altention-<uid>/`，同一個帳號的每個 HOME 共用：忘了設 `ALT_RUNTIME` 的暫存 HOME 曾經把真的記號蓋掉；現在每個 HOME 各自一個資料夾，舊資料夾不搬、不讀，機器重開就沒了。寫的（每分鐘那一輪）和讀的（doctor 在那台跑的探測）都走 `scheduler.beside_dir()`。讀的時候每一份先各自排除時間在未來的（別的 HOME、時鐘快的暫存 HOME 也會寫到 /tmp 那份），再取較晚的。測試由 `altention.testing.guard_home()` 設 `ALT_RUNTIME`，不碰真的 /tmp。
- **退回**：引擎退回到 3a.11 之前時，那行 cron 先測啟動檔在不在，不在就什麼都不做、沒有輸出（supervisor 自動退回也安靜）。要把那行拿掉：在每台、**先**用還認得它的引擎跑 `python3 ops/plugins.py backups`，找 3a.11 apply 那次留下的備份，`python3 ops/plugins.py restore <時間>`（只在那之後 crontab 沒有別的改動時用）；或直接 `crontab -l | grep -v 'altention/cron_schedule.py' | crontab -`。然後才退程式。

## 對話紀錄的原始檔分段（2026-10 起）

超過 8 MB 的 `raw.jsonl`（和子代理的 `subagents/<id>.jsonl`）改存成 `raw-0001.jsonl.gz`、`raw-0002.jsonl.gz`…（寫滿的段，各約 8 MB 未壓縮、切在行尾，之後不再變動）加上最後一段未壓縮的 `raw-000N.jsonl`。`meta.json` 的 `raw`（子代理在 `subagents_raw`）列出各段的檔名、行數、位元組數、sha256（未壓縮內容的），它是唯一的「哪些檔是紀錄」：先寫好並驗證新段，再換 meta.json，最後才拿掉被取代的檔。8 MB 以下的照舊一個 `raw.jsonl`。

- **讀**：一律用 `chatlog.load_raw(資料夾)`／`read_raw`（progress.py 也是），逐段核對。要整份（例如從 GitHub 接回對話）：`python3 chatlog/chatlog.py raw <資料夾> > raw.jsonl`（子代理加 `--sub <id>`）。
  - **擋**（結束碼 1、stdout 不印、progress.log 一行）：缺段、讀不出來、行數或雜湊跟 meta 不符；meta.json 不在或讀不出來而且沒有整份 raw.jsonl（資料夾裡有自己檔名的段時另外說出來）；資料夾根本沒有原始紀錄（重建的對話）。
  - **只說不擋**（2026-10-08）：meta 列的段全部在、都對，就是完整的紀錄；資料夾裡另有 meta 沒列的自己檔名的檔（中途被殺的那一輪留下的）照印、結束碼 0，stderr 和 problems 說「另有 meta 沒列的檔」。這種檔可能被 sync 提交推上去，不影響讀，下次 export 那個對話時收掉。
- **export 用雜湊判斷要不要重寫**：這輪有 export 的對話，滿段和尾段都讀出來比內容，壞了（就算大小沒變）就重寫並記一行 log。這輪沒 export 的對話不巡檢；靜態損壞從紀錄 repo 的 git 歷史拿回（git 物件自己有雜湊），或來源還在時 `chatlog.py sync --all`。
- **沒有頻道的 Claude 對話，機器人只在確定沒人用時才關**（`reap_ownerless`，2026-10-11）：其中一個條件是 Claude Code 行程底下沒有任何子行程（背景指令是它底下的 shell）。**有東西常駐掛在 Claude Code 底下的安裝（例如 stdio 的 MCP server）這個條件永遠不成立**，那些對話不會被自動關，只會列在 doctor 的 `dcbot.ownerless-sessions` 給人決定。這是刻意的（分不出來就不關）；公開版的說明（階段 6）要寫這一點。
- **超過 8 MB 就分段，每台都一樣**（上線時的開關 `chatlog.raw_segments` 四台穩定一天後拿掉，2026-10-09；實例 `plugins.json` 裡留著那一項也沒有作用）。既有的整份 `raw.jsonl` 大檔在那個對話下次 export 時自己轉成分段；不再更新的舊對話用 `chatlog.py split`。
- **原始對話變短或不見**：比 meta 記的行數少就整個不動（段、README 都不動），chatlog.log 記一行。被 Claude Code 清掉的對話不會再 export，段也不會被碰。主對話還在、某個子代理的來源不見了：那個子代理的段照舊列在 meta.json、不動。
- **有段檔但 meta.json 沒有完整的分段清單**（meta 不在、截斷、不是物件；清單在但不完整：某段缺 file／lines／sha256，或整份的 lines、sha256 不見或型別不對，或某段 lines 是負數、整份 lines 不等於各段相加；整份 raw.jsonl 加段檔並存而 meta 有壞掉的清單也算）：這個對話**不 export、不重寫、不刪任何檔**（開關關著也不寫整份去蓋），chatlog.log 一小時最多一行，doctor 出 `chatlog.unsure-segments` warn 列出資料夾，`doctor --watch` 也會通知（同一台照既有的限次）（2026-10-08：讀不出來≠空的，meta 不可信時沒有依據判斷來源夠不夠長）。不會自己修好。手動修法：
  1. 先從 git 拿回**壞掉之前的那一筆** meta.json（sync 會把壞掉的也提交，所以最近一筆通常就是壞的）：`git -C ~/altention/data/logs log --oneline -- <資料夾>/meta.json` 由新到舊一筆一筆試，`git -C ~/altention/data/logs checkout <commit> -- <資料夾>/meta.json` 後跑 `python3 chatlog/chatlog.py raw <資料夾> > /dev/null`，**結束碼 0 的那一筆**就是對的；下一輪 export 照常。
  2. 拿不回來、但原始對話還在（`~/.claude/projects/*/<session id>.jsonl`）：先確認它的行數不少於段檔裡的（`for f in <資料夾>/raw-*.jsonl*; do case $f in *.gz) zcat "$f";; *) cat "$f";; esac; done | wc -l` 對 `wc -l < 原始對話`），夠長才把那個資料夾的 `raw-NNNN.jsonl*`（和子代理的 `subagents/<id>-NNNN.jsonl*`）移到別處，跑 `chatlog.py export <原始對話>` 重建，`chatlog.py raw` 確認後再刪移走的檔（它們也都在 git 歷史裡）。
  3. 原始對話不在也拿不回 meta：段檔都在 git 歷史和磁碟上，照檔名順序 zcat／cat 串起來就是紀錄（未經核對），不要刪。
- 例外（照常 export、收尾）：meta.json 讀得出來而且完好、只是根本沒有那一份（主對話或某個子代理）的清單：那些段檔是沒完成那一輪留下的，不算紀錄（2026-10-08）；或 meta.json 不在、而且從沒被提交過（第一次 export 在寫 meta 前被殺）。一個資料夾的 meta 壞掉不會讓整輪 sync 停下：做索引時跳過、chatlog.log 一小時一行，其他對話照常提交、推送。meta.json 是 `{}`（讀得出來、完好、什麼都沒列）照這個定義也照常 export，來源較短會寫成短的；截斷產生不了 `{}`，不另外處理（2026-10-08）。保護的對象是截斷、沒寫完、被殺、來源變短、損壞這些會自己發生的情況，手改成合法但故意錯的值不在範圍內。後者只看 meta.json 的歷史：段檔就算已被 sync 提交、meta 從沒列過它們，它們就不算紀錄，來源變短時會被重寫成短的；舊內容在紀錄 repo 的 git 歷史（2026-10-08）。
- **原始對話已不在的大檔**：`python3 chatlog/chatlog.py split [資料夾…] [--over MB]`（不給資料夾時只看這台自己的、超過 30 MB 的）。中途被殺可重跑：沒換 meta 前舊檔就是紀錄；換了 meta 沒刪舊檔，重跑會核對相同後才刪。
- **收尾只刪自己的檔名**：`<base>-NNNN.jsonl`、`<base>-NNNN.jsonl.gz` 和它們的 `.chatlog-tmp`，以及被取代的整份 `<base>.jsonl`；其他檔一律不碰。
- doctor 的 `chatlog.big-file`：這台的紀錄裡有檔超過 50 MB 就 warn（GitHub 100 MB 就推不上去）。`chatlog.error` 顯示錯誤訊息的結尾（原因在最後，前面常是 LFS 警告）。
- **退回的限制**：revert 之後，舊程式照舊每輪寫整份 `raw.jsonl`（原始對話還在的會長回來），已分段的段檔留著沒人讀；舊程式的 progress.py 對「原始對話已不在、只剩分段」的對話讀不到 raw（當成沒有），這不是資料遺失：段檔都在，再上新版就讀得到。被分段取代、刪掉的舊 `raw.jsonl` 也都還在 紀錄 repo 的 git 歷史裡拿得回來。
- **tmux 的 socket 路徑在 macOS 有兩個名字（2026-10-12）**：macOS 的 `/tmp` 是指到 `/private/tmp` 的連結。機器人記的是 `/tmp/tmux-<uid>/default`（`tmuxio.socket_dir()` 拼的，路由表的鍵、頻道狀態都是這個寫法），tmux 自己放進 `$TMUX` 的是 `/private/tmp/tmux-<uid>/default`。凡是「`$TMUX` 的路徑」碰到「機器人記的路徑」的地方都不能直接比字串：`tools/dchook` 的 `route_of()` 在那台機器上兩邊取 `os.path.realpath`；機器人這邊認別台傳來的窗格（`dcctl` 的 `sender_channel`、`posts` 的 `--attach`）用 `util.same_socket()`（別台的路徑在這裡解不了連結，只認 `/private` 開頭這個 macOS 的事實）。症狀：訊息打得進去、Claude 也回了，但那個窗格的事件一個都不送（hook 查不到頻道、安靜地結束），機器人貼「訊息可能沒有送進 Claude」；有對話 id 的舊頻道靠 `routed_session()` 那條路看起來正常。
