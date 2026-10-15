# Altention

在 Discord 上管理 AI 程式助理的對話。每個頻道背後是一個在你自己機器的 tmux 裡持續進行的對話：你在手機上打字，助理替你執行指令、讀寫檔案、照顧你的其他機器。

目前唯一支援的工具是 [Claude Code](https://claude.com/claude-code)。

[English](README.md)

> **實驗性（0.x）。** Altention 透過 tmux、Claude Code 的 hook 和對話檔驅動 Claude Code，這些都不是 Claude Code 承諾不變的介面。Claude Code 改版後，部分功能可能要等 Altention 修好才能用（見「已知限制」）。介面和設定格式在 1.0 之前不保證相容。

## 它做什麼

核心（只裝這個就能用）：

- **每個頻道一個對話**。對話活在 tmux 裡，機器人重啟或部署不會打斷它；你也可以在電腦上 `tmux attach` 直接接手。
- **把你自己開的對話接上 Discord**：tmux 裡跑著的 Claude Code，用 `/attach` 接到一個頻道，`/detach` 卸下。
- **提問、計畫核准、權限選單變成按鈕**；子代理和 workflow 顯示成討論串。
- **終端機頻道**（一個 shell）和**檔案瀏覽器**。
- **頻道之間傳話**：一個頻道的對話可以把報告貼到另一個頻道、跟那個頻道的對話說話，或維護一則原地更新的「看板」（`altention dc post`、`altention dc read`、`altention dc notify`）。
- **機器人可以修改自己**：在開發頻道說要加什麼功能，它改程式、跑測試、重新部署；新版本起不來，看門狗會退回上一個正常的版本。

選用的插件（都內建，每個都可以關掉）：

- **chatlog**：把每台機器的 Claude Code 對話複製到一個 git repo，轉成可讀的 Markdown。
- **progress**（進度）：定時讀對話紀錄，把工作歸到專案，維護每個專案的最新狀態。
- **usage**：看板，顯示訂閱額度和各機器的用量。
- **status**：看板，顯示每台機器和每個頻道的狀態。
- **planner**：定時提醒，時間到了傳一句話叫醒某個頻道的對話。
- **council**：多視角議會 skill，做決定時用。

用量和狀態看板貼在實例 `plugins.json` 裡 `usage.channel`、`status.channel` 寫的頻道（頻道 ID）；安裝指引會建 `#dashboard` 並把它的 ID 寫進去。要換頻道，把這兩個設定改成別的頻道的 ID。沒設的話看板不會貼，doctor 會提醒。

安裝指引會問要不要 planner 的定時回顧：每天 08:00 早報、22:00 晚間回顧、週日 21:00 每週回顧。每一次都會叫醒秘書頻道 `#secretary` 的 Claude（`planner.channel` 寫的頻道 ID；它的資料夾裡有它的說明和短期計畫），用的是你的訂閱。不要的話把 planner 關掉（`disabled_on`），其他頻道排程檔裡的提醒也會一起停。

**多台機器**是選用的（進階）：一台跑機器人，其他機器上的對話透過 SSH 接上頻道。

## 需求

- Linux 或 macOS
- Python 3.8 以上、git、tmux
- cron（`crontab` 指令）。最小安裝和容器可能沒有：`sudo apt install cron`（Debian、Ubuntu）、`sudo dnf install cronie`（Fedora）、`sudo pacman -S cronie`（Arch）；macOS 內建。
- Debian、Ubuntu 要有 `python3-venv`（機器人的 Python 環境用）：`sudo apt install python3-venv`
- `~/.local/bin` 要在 `PATH` 裡（安裝程式把 `altention` 指令放在那裡）。大多數 Linux 預設就有；macOS 沒有，請在 `~/.zshrc` 加上 `export PATH="$HOME/.local/bin:$PATH"`，再開一個新的終端機。
- Claude Code 2.1.296 以上，已登入
- 一個你自己的 Discord 伺服器，和你自己建的 Discord 應用程式（機器人 token）

一台機器就能完整使用。單機不需要 SSH，也不需要遠端 repo。

## 安裝

1. 取得引擎：

   ```bash
   git clone https://github.com/q12we34rt5/altention.git ~/altention/engine
   ```

   （用 SSH：`git clone git@github.com:q12we34rt5/altention.git ~/altention/engine`。）

   或用從[發佈頁](https://github.com/q12we34rt5/altention/releases)下載的壓縮檔（壓縮檔裝的不會自動更新），`<壓縮檔>` 換成下載的檔名：

   ```bash
   mkdir -p ~/altention/engine && tar xzf <壓縮檔> -C ~/altention/engine --strip-components=1
   ```

2. 在裡面開 Claude Code，讓它帶你裝：

   ```bash
   cd ~/altention/engine && claude
   ```

   接著說「幫我安裝」，或打 `/install`。它會問幾題（時區、要哪些選用插件、引擎要不要自動更新（`core.update`）、機器人對話的權限模式：`auto`（建議）、`manual` 或 `bypassPermissions`、機器人的 Claude 之間沒有你插話時最多來回幾次就先暫停問你（`dcbot.talk_limit`：10（建議）、30，或 0 不限制）），帶你建 Discord 應用程式、邀請機器人，再陪你跑安裝和檢查。

   **機器人的 token 不經過對話。** 問到 token 時，在你自己的終端機跑：

   ```bash
   ~/altention/engine/install.sh --token
   ```

   它會在不顯示輸入的情況下問 token、你的 Discord 使用者 ID（`OWNER_ID`）和伺服器 ID（`GUILD_ID`），寫進 `~/.config/dcbot/env`（權限 600）。也可以自己寫這個檔。

不用安裝指引的話，先建 Discord 應用程式（安裝指引帶你走的也是這幾步）：

1. <https://discord.com/developers/applications> → **New Application**，名字隨意。
2. **Bot** 頁：把 **Public Bot** 關掉（預設開著，別人拿到應用程式的 client ID 就能把你的機器人邀進他的伺服器）。如果 Discord 不讓關、顯示「Private application cannot have a default authorization link」，先到 **Installation** 頁把 **Install Link** 改成 **None**、存檔，再回來關。關掉後你自己還是能用第 3 步產生的網址把機器人加進伺服器。按 **Reset Token**，留著 token 給 `install.sh --token` 用。**Privileged Gateway Intents** 底下打開 **Message Content Intent**，存檔。
3. **OAuth2 → URL Generator**：scopes 勾 `bot` 和 `applications.commands`；bot permissions 勾 View Channels、Manage Channels、View Audit Log、Send Messages、Send Messages in Threads、Create Public Threads、Manage Threads、Manage Messages、Pin Messages、Embed Links、Attach Files、Read Message History、Add Reactions、Use Application Commands。打開產生的網址，把機器人加進你自己的伺服器（最好是新的、空的）。少勾一項不會壞：doctor 和機器人會說缺什麼。
4. 在 Discord 打開 **使用者設定 → 進階 → 開發者模式**；在自己的名字上按右鍵 → 複製使用者 ID，在伺服器上按右鍵 → 複製伺服器 ID。`install.sh --token` 會問這兩個。

然後：

```bash
cd ~/altention/engine
./install.sh --token     # 機器人的設定，在你自己的終端機輸入
./install.sh             # 這台需要的全部
python3 ops/doctor.py --fix   # 檢查；--fix 會在每個問題下印出怎麼修（只印，不動手）
python3 dcbot/tools/dcctl guide   # 機器人上線後：把使用說明貼到 #guide
```

`install.sh` 的結束碼：0 是好了，1 是有地方失敗，2 是要你動手（`todo` 那幾行寫了要做什麼，做完重跑）。任何時候重跑都安全。

**機器人第一次上線時**，會自己建 `#audit` 和 `#chat`。`#audit` 會貼出「已上線」，之後的部署結果和退回通知也在這裡。`#chat` 是已經設定好的一般對話，工作目錄是實例資料夾，到 `#chat` 打字就能開始。自己開的頻道會先出現設定卡。要一個讓機器人改自己程式的頻道，就新開一個、名字自取（例如 `#bot-dev`），在設定卡選 🤖 Claude 對話，按「📁 選工作目錄」點到引擎資料夾、按「✅ 用這個資料夾」，再按「🛠️ 交給機器人管」（或開新的 tmux）選在哪裡跑。三個頻道的名字都可以在實例的 `plugins.json` 改：`{"dcbot": {"channels": {"audit": "…", "chat": "…", "guide": "…"}}}`。

機器人的使用說明是 [dcbot/GUIDE.md](dcbot/GUIDE.md)。安裝指引會在機器人上線後把它貼到 `#guide`；不用指引的話，自己跑 `python3 dcbot/tools/dcctl guide`。更新後再跑一次，頻道就會跟上。

## 名詞

- **引擎（engine）**：這個 repo。核心和內建插件，不含任何個人資料。
- **核心（core）**：一定要有的部分：機器人、共用設定、機器管理。
- **插件（plugin）**：可以不裝的功能。內建的在引擎裡；外部的是你自己的 repo；本地的放在實例裡。
- **實例（instance）**：你自己的私人設定和資料（知識、記憶、機器清單、插件設定）。一個 git repo，建議設成私人。完整的例子（值都是假的）在 [examples/instance](examples/instance/)。

所有東西預設收在一個根目錄底下（`ALT_ROOT` 可以改位置）：

```
~/altention/
  engine/      引擎（這個 repo）
  instance/    你的實例
  plugins/     外部插件
  data/        插件的資料（對話紀錄、進度摘要…）
```

## 多台機器（進階）

> 多機是作者每天在用的做法，但這一版還沒在乾淨的機器上從頭驗證過多機安裝。請先裝好一台，能用之後再加別台。

在實例的 `machines.json` 列出每台機器的 SSH 別名和角色，然後在機器人那台執行：

```bash
python3 ops/apply.py host-b    # 讓 host-b 符合清單：缺的裝上，清單沒有的移除
python3 ops/doctor.py          # 檢查每一台
```

- 機器人同時只跑在一台。其他機器上 tmux 裡的對話可以用 `/attach` 接到頻道：機器人透過 SSH 打字，那台的 hook 把事件傳回來。
- `altention dc post|read|notify` 在每台都能用；不在機器人那台時會自己走 SSH。
- 長期服務只放在常開的機器上。會睡眠的機器（筆電）醒來後會補上。

## 資料夾

| 資料夾 | 是什麼 | 怎麼執行 |
|---|---|---|
| `dcbot/` | Discord 機器人、看門狗、`dcctl`、`dchook`、`dcremote` | 看門狗由 cron 帶起（開機時，之後每 5 分鐘確認） |
| `altention/` | 共用函式庫：設定、實例、機器清單、排程器、同步、Claude Code 接頭 | 被其他部分 import |
| `bin/` | `altention`（短別名 `alt`）：統一入口（`doctor`、`apply`、`plugins`、`dc post`、`dc read`、`dc notify`） | 你或對話直接呼叫 |
| `ops/` | `install.py`、`apply.py`、`doctor.py`、`plugins.py`、`instance.py` | 手動，或由安裝程式呼叫 |
| `chatlog/`、`progress/`、`usage/`、`status/`、`planner/`、`skills/council/` | 內建插件，各有一個 `plugin.json` | `plugin.json` 宣告的 hook 和排程 |

更多：[docs/architecture.md](docs/architecture.md)（各部分怎麼接在一起）、[docs/gotchas.md](docs/gotchas.md)（踩過的坑）、[dcbot/CLAUDE.md](dcbot/CLAUDE.md)（機器人怎麼改自己）。

## 安全

安裝前請讀 [SECURITY.md](SECURITY.md)（英文）。重點：

- **能在你的 Discord 伺服器以你的身分發言的人，就能在你的機器上執行指令。** 請開兩步驟驗證，並使用你自己的伺服器。
- 機器人開的對話用你選的權限模式（每個頻道可以自己選）：
  - `auto`（建議）：Claude Code 自己判斷。安全的直接做；有風險的（刪工作目錄外的東西、force push、執行網路下載的腳本、改開機設定等）會被擋下，Claude 改用別的做法或停下來跟你說明，不會先問你。被擋下好幾次之後，才會改成跳確認（Discord 上是按鈕）。被擋下這件事 Discord 目前不會另外顯示。
  - `manual`：每個需要權限的動作都先問，Discord 上是按鈕。最安全、按最多次。
  - `bypassPermissions`：從不問也不擋；你的 Discord 帳號等於這台機器的 shell。

  按鈕是從畫面讀出來做的，它保證不了什麼，見 SECURITY.md。
- token 只放在 `~/.config/dcbot/env`（權限 600）。一個守門的 hook 擋住你機器上每個 Claude Code 讀、搜尋或在指令裡寫出它和 SSH 私鑰；這是盡力而為，擋不到哪些情況 SECURITY.md 有寫。機器人貼出的內容裡的金鑰和 token 會先遮掉。
- 引擎預設不自動更新（`core.update` 是 `"notify"`：有新版時 doctor 和狀態看板會提醒）。設成 `"release"` 會自動跟發佈的 tag；這代表信任能發佈上游 repo 的人，SECURITY.md 有寫。
- 插件沒有隔離。只裝你信任的插件。

## 已知限制


- **只支援 Claude Code。** 其他工具在路線圖上。對話紀錄也只記 Claude Code。
- **驗證過的是 Claude Code 2.1.288 和 2.1.296。** 2.1.299 的畫面驗證過、日常在用（它的 hook 事件和對話檔還沒收進測試樣本）。其他版本可能可以用，但沒有驗證；較新的版本可能改了畫面（見下一項）。
- **部分功能靠讀 Claude Code 的畫面**（輸入框好了沒、選單、啟動時的對話框）。Claude Code 改版、畫面變了，訊息可能送不進去，直到 Altention 修好。整套的原則是「認不得就不動作並明說」：機器人寧可拒絕並貼出畫面，也不猜。回覆、中途的話、提問、計畫和子代理走 hook 和對話檔，畫面改版不影響。少數情況還沒做到這樣（例如在忙的對話可能被看成閒著，`!stop` 會說沒有東西可停；改版後的權限對話框可能被當成輸入框，見 SECURITY.md），路線圖第 1 項列了。選單按鈕（數字、Enter、輸入文字）按下前都會重讀畫面，題目或選項變了就不按；那台機器沒回應時也不按，按鈕留著。目前還沒有版本檢查和修理說明：如果 Claude Code 一更新就有東西不對，請開 issue 並附上 Claude Code 的版本。
- **只有一個擁有者。** 機器人只聽一個 Discord 使用者。
- **介面目前是繁體中文。** 英文在計畫中。
- **需要 tmux**：機器人的對話和 `/attach` 都靠它。
- **守門 hook 是盡力而為的字串比對。** 同一個指令裡先 `cd` 再用相對路徑、以整個家目錄為範圍的搜尋、執行時才拼出的路徑，它看不到。指令裡寫到受保護路徑的完整寫法（`~/…`、`$HOME/…`、絕對路徑），在任何資料夾都會被擋，就算只是要印出或貼出的文字；在家目錄開的 Claude，連看起來像路徑的相對字（grep 的搜尋字、echo 的文字）也會擋。要寫這種文字，先寫進檔案再用。拒絕訊息會寫原因，並請 Claude 問你。`ssh -i`、`ssh-add`、`ssh-keygen -y` 這類使用鑰匙的指令會放行。見 SECURITY.md。
- **用量看板的「訂閱額度」預設不讀。** 那一塊要在實例的 `plugins.json` 設 `"usage": {"subscription_limits": true}` 才會打開；沒打開時，用量看板的其他部分照常更新。
  - 打開後：每小時用你的訂閱帳號跑一次 `claude -p`，送一句最便宜的請求（haiku），一天約 24 次，每次約 0.001 美元的 API 等值，會算進 5 小時的額度。
  - 隱私：這個請求不載入你的設定、hook、MCP 和工具，在一個空的暫存資料夾跑，不留對話檔。
  - 打開時也要設方案，例如 `"usage": {"subscription_limits": true, "plan": ["Pro", 1]}`（或 `["Max 5x", 5]`、`["Max 20x", 20]`；Team、Enterprise 照額度換算成幾個 Pro）。沒寫時當成 Max 20x。
  - 怎麼關：設成 `false`、拿掉這一行，或不裝 usage 插件。
  - 限制：讀的欄位沒有官方文件，只依 Claude Code 2.1.299 的實測。格式變了時看板會寫「讀不到：格式變了」，不會猜數字；額度用完時可能讀不到。

## 路線圖

第一版（10 月 20 日公開）用的是現在每天在跑的做法：Claude Code 跑在 tmux 裡，靠 Claude Code 的 hook 加上讀終端機畫面跟它互動。下面這些排在第一版之後，各自寫了為什麼要等。

### 接下來

1. **Claude Code 改版時照樣可靠。** Claude Code 更新很頻繁，有些更新會改畫面。第一版遇到認不得的畫面已經是不打字、不按鍵；還要補的：
   - 在不在忙以 hook 為準，畫面變了也不會被當成閒著（對話在跑時 `!stop` 一定停得了）；
   - 權限對話框開著時一律不打字，靠 hook 知道、不靠畫面；
   - 啟動時認不得的畫面交給你處理（`!screen`、`!key`），不在 90 秒後關掉視窗；
   - Claude Code 底下還有子行程在跑時，不回收閒置的對話；
   - `doctor` 統計「畫面認不得」的次數，壞掉幾分鐘內就會被發現；
   - 改版後修接頭的說明（`REPAIR.md`），以及裝的 Claude Code 版本還沒驗證過時 `doctor` 會提醒。
   - 為什麼等：這幾項都動到每個對話都會走的打字那條路，每一項上線前都要在真的 Claude Code 上排練。在那之前，如果 Claude Code 一更新就有東西不對，請開 issue 並附上 Claude Code 的版本。
2. **多台機器，從乾淨的安裝驗證過。** 經 SSH 在別台機器跑對話是能用的、每天在用，但還沒有人照這份 README 從頭裝過。驗證之前標成「進階」。
3. **不靠讀畫面驅動 Claude Code**（headless／stream-json，或 Agent SDK）
   - 為什麼重要：機器人自己開的對話，第 1 項那一整類的壞法就不會再發生。
   - 為什麼等：它改變每個對話的跑法，也會失去一些東西：在 tmux 接進同一個對話、原生的 `/btw`、機器人重啟時對話不中斷，都要重做。headless 用法的計費和支援方式也要先弄清楚，才能當預設。
4. **插件的擴充點**：插件自己做的互動訊息（按鈕、選單）、插件定義的 `/` 和 `!` 指令、插件宣告的遮蔽規則、事件訂閱、`plugins.py deploy`。
   - 為什麼等：還沒有真的插件用過這些介面，不想公開一個之後得改壞的 API。在那之前，插件可以用 `plugin.json` 加指令和排程，或者 fork。
5. **agent 之間傳話的加強**（頻道間的通訊協定）：每則送出的訊息都拿回結構化的結果（送到了、排隊中、沒送到和原因）、固定的結束碼、帶原訊息 ID 的回覆指令、顯示哪些 agent 讀過的已讀標示、頻道同名時一律顯示「分類/名稱」。
   - 為什麼等：這是比較大的設計，要用到上面的擴充點。第一版就有的指令之後照樣能用。
6. **權限對話框改用 Claude Code 的 `PermissionRequest` hook 回答**、不再按鍵；以及其他較小的加強（`/btw` 的退路、訊息看起來開了背景對話時更清楚的提醒）。
7. **把接頭的邊界做完**：剩下的打字、旁支問題、查狀態和壓縮搬到接頭介面後面；能力宣告（一個 agent 能回報什麼：已啟動、收到了、在忙、背景工作、接回）。
   - 為什麼等：對 Claude Code 使用者沒有看得到的差別，是給其他 agent 鋪路。

### 之後

8. **其他 agent**（第一個候選：Codex CLI）。在有第二個 agent 用之前，接頭介面是內部的、可能會變。
9. **Claude Code 以外的 agent 的對話紀錄和進度摘要**。第一版的對話紀錄（`chatlog`）只記 Claude Code。
10. **更多內建插件**搬到插件的格式，有些當範例。
11. **學習功能**：測驗、學習頻道。
12. **重新設計的檔案頻道**（先設計 UI 再做）。

## 參與

歡迎開 [issue](https://github.com/q12we34rt5/altention/issues) 和 pull request。送程式之前：

- 跑機器人部署前會跑的測試：`dcbot/deploy.sh` 裡列了（機器人自己的，加上 `altention`、`chatlog`、`bin`、`ops`、`tools`、`progress`）。共用的程式只用標準函式庫，要能在 Python 3.8 上跑。
- 跑 `tools/privacy-scan HEAD`：引擎裡不能有機器名、帳號、頻道 ID 或私人網址。個人的值放實例。

安全問題請照 [SECURITY.md](SECURITY.md) 私下回報。

## 授權

[MIT](LICENSE)
