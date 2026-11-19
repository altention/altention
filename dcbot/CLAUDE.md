# dcbot：給「修改機器人自己」的 Claude 看的說明

你就是這個 Discord 機器人，也是 Altention 的核心：使用者在 Discord 上管理 Claude Code 對話的地方，跟使用者其他機器上的 Claude 共用同一份知識和工具。使用者在開發頻道（工作目錄是引擎的頻道，使用者要用時才開，名字隨使用者）跟你說要加什麼功能，你在引擎的 `dcbot/` 裡改自己的程式。

這份說明是公開的，寫的是每個安裝都成立的事。**不要在這裡寫個人內容**（機器名、帳號、頻道名和 ID、對話名、私人 repo 網址、使用者的偏好）；那些寫在實例附錄。範例一律用假值：ID `1000000000000000xx`、機器 `host-a`、帳號 `alice`、頻道 `#project-a`。

- **引擎**：`dcbot/` 的上一層資料夾，下面寫的 `bin/`、`ops/`、`altention/` 都相對於它。
- **實例**：使用者的私人設定和資料。位置：在引擎資料夾執行 `python3 -c "from altention import config; print(config.instance_dir())"`，印出 `None` 表示這台還沒有實例。
- **實例附錄**：`<實例>/dcbot/CLAUDE.md`。這個安裝特有的事（有哪些機器、各台的怪癖、使用者的習慣、踩過的坑）。有這個檔就在開始改之前讀它；學到只屬於這個安裝的事也寫回那裡，不要寫進這份。

## 架構

細節在 `docs/architecture/`，一個主題一個檔（原文從這份搬過去，2026-11-19）。改一個模組之前先讀它的檔；不確定在哪個檔就 `grep -rln 關鍵字 docs/architecture/`。新的架構說明寫進對應的檔，不要寫回這裡。

- `tmux.md` Discord 用戶端和兩種模式：`main.py`、tmux 模式（`tmux_bridge.py`、`tmuxio.Pane`、開對話、輸入、輸出、中途的話、失敗、通知、context 佔用、畫面用在哪）、print 模式（`claude_bridge.py`）
- `degraded.md` Claude Code 不能照常用時：三層（C-162）
- `lease.md` 誰是機器人：租約和備援：租約、備援接手和交還（C-163）
- `slash.md` 斜線指令：`slash.py`、`catalog.py`、不能從 Discord 直接打的斜線指令
- `routes.md` 接上使用者自己開的 Claude：`routes.py`：去程、回程（`dchook`）、背景對話、路由檔、窗格編號、別台機器；那台連不上不是「窗格沒在跑」
- `turns.md` 提問、/btw、一輪的範圍：`ask.py`、`/btw`、一輪是哪一輪、頁尾算誰的
- `lifecycle.md` 頻道和對話的生命週期：頻道被刪、閒置回收、接回失敗留證據、「結束了」、別台 `/resume` 貼紀錄、`sessions.py`
- `archive.md` 封存：`archive.py`、`/archive`、封存後的事件歸屬
- `setup.md` 設定卡和終端機頻道：`setup.py`、機器選單、`/attach` 在沒設定的頻道、別台的終端機、`terminal.py`
- `menus.md` 選單、圖的原文、畫面截圖：`menus.py`、`sources.py`、`screens.py`／`screenshot.py`
- `output.md` 📋 和表格：`process.py`、`tables.py`
- `posts.md` 貼到頻道：`posts.py`（`altention dc post`）、保留字首和轉交引用、`--json`、`--delete`、通知不能無聲消失
- `where.md` 對話在哪和對帳：`where.py`、`reconcile.py`（C-201）
- `waiting.md` 暫停、送不出去、等使用者、卡住：`pauses.py`、`messages.py` 的代號、`awaiting.py`、`unsent.py`、`quota.py`、`blocked.py`
- `files.md` 檔案和附件：`files.py`／`filecore.py`、`attachments.py`
- `peers.md` 對話之間：`peers.py`、轉發、讀別的頻道、回覆
- `connector.md` Claude Code 接頭：`altention/connectors/claude_code/`：事件的種類、`hooks.py`、一輪的範圍、打字（`typist.py`）；`events.py`；按鍵基準
- `agents.md` 子代理和 workflow：`agents.py`
- `delivery.md` 訊息主幹：讀回訊息和 ledger、單一出口 `deliver.py`、`rules.py`、`senders.py`（C-172a）
- `secrets.md` 秘密和遮蔽：秘密不給 agent 讀（K-03）、貼出去的字先遮金鑰（B-12）
- `plugins.md` 插件的按鈕和改寫：`rewrite.py`、`activity.py`、`secretbox.py`
- `ops.md` 工具、重開、上線、看門狗：`util.py`、`tools/dcctl`、機器重開、`catchup.py`、「上線」的定義、`supervisor.py`
- `engine.md` 跟引擎共用的規則：誰能改 `dcbot/`、git 鎖、佇列、`state/`、不在主工作樹 rebase、`config.py`

## 願望放哪裡
使用者許願時，先決定改動落在哪裡，再動手。目標是使用者說一句話就有新功能，同時公開的核心不會越來越大、越來越個人。

1. **調整既有行為、修問題**：直接改核心（`dcbot/`），照下面的修改流程部署。這是大多數的願望，不要為了「乾淨」硬繞成插件。
2. **新的獨立功能**（自己的訊息、按鈕、指令、排程，不必動機器人的內部）：做成本地插件，放在實例的 `plugins/`，程式和個人資料可以混寫；好用了再整理成公開插件。
   - 插件執行層和擴充點還沒上線之前（2026-10-11 還沒：`ops/plugins.py` 已經有了，但它和 `altention/plugin.py` 只做內建插件的宣告、比對和回報；實例的 `plugins/` 還沒有東西會去讀，擴充點也還不存在。上線時這一句會改掉，在那之前不要照第 2 點做本地插件），這類功能先照第 1 點做進核心，並守第 3、4 點。
   - 插件的程式不載入機器人行程：機器人在事件發生時執行插件宣告的指令。需要機器人內部資料（tmux 畫面、對話紀錄）的功能做不成插件，照第 1 點做。
3. **不是人人適用的核心改動**（只合這位使用者的習慣）：做成實例設定的開關（`altention.config.setting("core.…", 預設值)`），**預設維持原行為**。拿不準是不是人人適用就做成開關。
4. **個人的值不進引擎**：機器名、帳號、頻道名和 ID、私人網址、路徑一律從 `altention.config` 或實例設定讀；測試用假值。部署時個資掃描的結果會印在 `deploy.sh` 的輸出裡，它不擋部署，但看到警告就在同一輪改掉（發佈時才擋，累積下來發佈就做不了）。
5. 介面文字（貼給使用者看的字）新寫的放 `altention/text.py`。

## 修改流程（一定要照做）
1. 改程式。新功能盡量做成獨立模組或 `tools/` 下的小工具，不要把 `main.py` 越塞越大。
2. 為新功能補測試（`tests/`），至少確保能 import、純邏輯有單元測試。
   - **要假的 tmux 窗格就用 `tests/fakepane.py`，不要自己 `mock.patch.object(tmuxio, …)`**（2026-10-11）：`with fakepane.use(假物件或 raw_screen=…, keys=…):`；單一替換或常數用 `fakepane.replace("名字", …)`。它只在一個地方記每個函式在哪個模組（`WHERE`），函式搬家改那一行；替換對象不存在、或只是從別處轉手的，直接報錯；`use()` 期間任何程式想開真的 `tmux`／`ssh` 會丟 `fakepane.Reached`（不是 `Exception`，橋接的 `except Exception` 吞不掉）。少了這個，替換沒生效的測試會默默去碰正在跑的機器人的 tmux。
   - 動到「送出 → 打字 → hook 事件 → 貼回覆」這條路，`tests/test_whole_turn.py` 要過：它用真的 `tools/dchook`、真實格式的對話檔（`tests/transcript.py`），本機（spool → `pump_once`）和別台（`dcremote` 的 `relay` → `stream`）各跑一輪，看收件的 👀、中途的話、回覆和頁尾、📋 的位置。開對話那段（`ensure`／`ensure_home`／`wait_ready`）在 `tests/test_open_session.py`。
3. 使用者用得到的功能有變（新指令、行為改變、新的限制）就更新 `docs/usage.md`（完整說明，給 agent 讀：使用者在任何頻道問用法時，Claude 照它回答），有新功能或功能的叫法變了才也改 `GUIDE.md`。`GUIDE.md` 是說明頻道的內容（預設 `#guide`，名字可以用實例 plugins.json 的 `dcbot.channels.guide` 改），是精簡的規格書：第一行請使用者有不懂的直接問 agent，接著每個功能一行（名字、一句話、怎麼叫），不寫細節和已知限制。用 Discord 看得懂的格式（標題、清單、粗體可以，表格不行）；每個 `## ` 段落是一則訊息，別超過 1900 字。`GUIDE.md` 和 `docs/usage.md` 是範本：機器名、頻道名這類因安裝而異的字寫成 `{{變數}}`（有哪些變數、各從實例的哪裡來，見 `GUIDE.variables.md`），不要直接寫出來；「其他頻道」那一段的內容在實例裡。改完用 `tools/dcctl guide --check` 看展開後的樣子。
4. 執行 `./deploy.sh "commit 訊息（英文）" "給使用者看的更新說明（中文，兩三句）"`：語法檢查 → lint（`../tools/lint`：整棵樹跟基準比，新問題、或基準檔比正式樹 HEAD 的多，就停；原地部署只看要提交的路徑底下沒加進 git 的檔。沒有那一版的 ruff 也停：只有改程式、跑 deploy.sh 的人要裝，用的人不用；先在引擎資料夾跑 `tools/lint --install`，裝在自己的家目錄，不需要 sudo）→ 測試 → commit（只有 `dcbot/`，加上第三個參數之後列出的路徑，例如 `../ops`；同一個工作樹裡別的頻道改到一半的東西不會被帶出去，有的話會列出來）→ pull（衝突就取消 rebase、不 push）→ push → 把 `GUIDE.md` 同步到說明頻道（原地編輯；有第二個參數就多貼一則更新紀錄，只留最近 5 則）→ 有改到 `dcbot/` 才重啟。純內部修改可以不給第二個參數。
   - **不等機器人重啟就會被執行的東西，一律 `./deploy.sh --commit <審過的 hash> "訊息" ["更新說明"]`**（2026-10-09 起）：`altention/`、`chatlog/`、`progress/`、`bin/`、`ops/`、`tools/`、`dcbot/tools/`、`deploy.sh`、`deploy_candidate.py`，以及 `dcbot/tools/*` 會 import 的 bot 模組（`deploy_candidate.live_modules()` 從 import 算出來：`events`、`tmuxio`、`config`、`remote`…（接頭在 `altention/` 底下，那邊的檔一律不算可以原地改），不是照資料夾分）。在別的 worktree 做好、提交、送審；`--commit` 會在 `.git/altention-deploy/candidate`（暫時的 worktree）做出候選 commit（HEAD 加上那個 hash 對它的基底改的檔，印出「patch-id 相同」），所有檢查都在那裡跑，全過才拿鎖把正式樹快轉過去；之前正式樹一個位元組都不動，cron 和 hook 跑的還是舊程式。基底之後 main 上有別的 commit 動到同樣的檔、或正式樹裡那些檔有沒提交的修改，就停。失敗時印「正式樹沒有被動過」（比對 HEAD、git status、那些檔的內容），候選和測試輸出留在 `.git/altention-deploy/`（下次部署清掉）；成功就刪掉。`dchook --update` 在換過去之後才跑。main 到那個 hash 之間的每個 commit 都要列出來（疊了幾個就給幾個 `--commit`，每個都審過；沒列到的會被擋，沒審過的不能跟著上線）。做候選前和換上去前都會 fetch：origin 比這台的 main 新就停，先 `bin/home-pull` 接進來再部署（不然換上去之後才接，推出去的是沒驗過的樹）。一次只有一個 `--commit`（`.git/altention-deploy.lock`，第二個直接拒絕並說誰在部署）。**後續的修正從現在的 main 開新的 worktree**，不要疊在已經部署過的那個審過的 hash 上：main 上的是候選 commit，不是那個 hash，疊上去的會因為「基底之後 main 動過同樣的檔」被擋。
   - 原地改再 `./deploy.sh "訊息"` 只給機器人重啟前沒有人會執行的檔（`dcbot/bot/` 裡 tools 不會 import 的模組、測試、文件、`requirements.txt`、`supervisor.py`）；改到別的會被拒絕並列出來。
   注意：`dcbot/` 以外的修改（`altention/`、`ops/`、`bin/`、內建插件、skill）會被所有機器拉去用，自動回滾也救不回來；改之前先跟使用者確認。
5. 回覆使用者改了什麼。重啟後，機器人會在這個頻道回報新版本有沒有上線；失敗會自動退回並說明。

在 tmux 模式下，`deploy.sh` 之後機器人會馬上重啟，你這一輪不受影響，回覆會由新版本貼出。改到 `tmux_bridge.py`／`tmuxio.py`／`dchook` 時，除了單元測試，請另外用假的頻道物件加真的 Claude 跑一次（開一個不同名字的 tmux socket，不要動 `dcbot` 這個 socket）。

不要直接 `kill` 自己或手動重啟；不要 `git push --force`；不要改寫 git 歷史。

## 慣例
- 回覆使用者用實例設定的語言（`core.language`，預設繁體中文）、精簡，先講結論。使用者另有偏好的話寫在實例附錄。
- 破壞性動作（刪頻道、刪檔、強制推送）先問，得到明確同意才做。`dcctl delete` 需要 `--yes`。
- 給使用者看的系統狀態（機器、租約、備援、版本、同步）放 #dashboard 的看板（「機器和機器人」或「需要注意的事」），不要加進回覆的頁尾或回覆本身（使用者 2026-11-11，C-181）。頁尾只留找回覆用的東西（這一輪的 id）。
- 只用標準函式庫或 `requirements.txt` 裡的套件；要加新套件就寫進 `requirements.txt`，`deploy.sh` 會自動安裝。
- 只有開發頻道改 `dcbot/`；其他頻道要改引擎的程式，到另一個 git worktree 改，不要直接改正在跑的那份引擎。
- 引擎資料夾裡的 git 一律拿共用的鎖：提交、推送用 `bin/home-git`，拉新程式用 `bin/home-pull`，不要直接 `git pull`／`git commit`。
- 同步、`home-pull`、`deploy.sh` 都不在主工作樹裡 rebase，自己寫的自動化也不要這樣做。
- `~/.config/altention/secrets/` 和舊的 `~/.config/dcbot/`（token、金鑰）絕對不要讀、印、改。
- 這四條的細節見 `docs/architecture/engine.md`。

## 環境
- 頻道預設的工作目錄：新安裝是實例資料夾（讀得到知識和記憶，又不會在引擎裡留下修改）；改機器人自己時，檔案在引擎的 `dcbot/` 底下。這個安裝實際用哪個資料夾，見實例附錄。
- 機器：`altention.config.machines()` 給機器清單，`bot_machine()` 給機器人在哪台（角色 `dcbot`）。其他機器用 SSH 連線，別名也在清單裡。沒有清單就是只有這一台。
- 裝了哪些插件、各自的資料在哪（例如對話紀錄、專案進度）：見實例設定和實例附錄。別的插件的資料一律唯讀。
