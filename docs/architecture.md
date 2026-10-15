# 架構

這份說明 Altention 現在怎麼運作：有哪些元件、資料怎麼流、狀態放在哪、怎麼管理。使用者自己的部署（哪台機器跑什麼、頻道怎麼分）不在這裡，在實例的 `knowledge/`。

目前只支援 Claude Code，下面說的「對話」都是 Claude Code 的對話。

## 總覽

| 元件 | 做什麼 | 跑在哪 | 狀態存哪 |
|---|---|---|---|
| **引擎** | 所有程式：機器人、共用設定、機器管理、內建的選用功能 | 每台機器各 clone 一份 | 引擎的 git repo |
| **實例** | 使用者的知識、記憶、機器清單、設定 | 每台機器各 clone 一份 | 使用者的私人 repo |
| **dcbot** | Discord 機器人：每個頻道一個對話；可以修改自己，壞了自動退回 | 一台（角色 `dcbot`） | `dcbot/state/`（不進 git）；token 在那台的 `~/.config/dcbot/env` |
| **chatlog** | 把這台機器的對話同步到對話紀錄 repo | 每台要記錄的機器（角色 `chatlog`） | 對話紀錄 repo |
| **progress（進度）** | 讀對話紀錄，把每段工作歸到專案，寫進度和最新狀態 | 一台（plugins.json 的 `plugins.progress.on`） | 進度的資料 repo（含處理進度） |
| **usage** | 把訂閱額度和各機器的用量更新到 Discord 的看板 | 一台（角色 `usage`） | 樣本檔在那台的 `~/.local/share/usage/` |
| **planner** | 定時提醒：時間到了傳一句話給某個頻道的對話 | 一台（角色 `planner`） | 排程檔在各助理自己的資料夾 |
| **council** | 多視角議會 skill，做決定時用 | 被呼叫時所在的機器 | `skills/council` |
| **ops** | `doctor.py` 檢查、`apply.py` 讓機器符合清單 | 從任何一台手動執行 | 機器清單 |

除了引擎和 dcbot，其他都是選用的。

## 機器人

- `dcbot/supervisor.py` 是看門狗，由 cron 帶起（開機時、每 5 分鐘確認一次），它再啟動機器人。新版本 3 分鐘內沒上線或啟動就當掉，它會把 `dcbot/` 退回上一個正常的版本。
- 每個頻道對應 tmux 裡的一個 Claude Code。行程不屬於機器人，所以機器人重啟不會打斷對話。
  - **輸入**：用 tmux 的 `send-keys` 逐行打字。
  - **輸出**：不抓畫面。Claude Code 的 hook 把每個事件寫成一個檔案，機器人讀出來貼到 Discord。
- 對話有三種來源，回報的路徑不同：
  - 機器人自己開的：啟動時帶專用的 hook 設定和環境變數。
  - 使用者自己在 tmux 開、用 `/attach` 接上的：靠裝在 `~/.claude/settings.json` 的全域 hook，用 tmux 窗格查路由表找到頻道；沒接頻道的對話不受影響。
  - 別台機器上的（那台要有角色 `dcattach`）：機器人經 SSH 打字，那台的全域 hook 寫事件，`dcremote` 用一條 SSH 長連線把事件傳回來。
- 頻道的種類：對話、終端機（一個 shell）、檔案瀏覽器、一般頻道（紀錄或看板，機器人不回應）、封存。
- `!status`、`!reset`、`!stop`、`!restart`、`!rollback` 這些內建指令不經過 Claude，機器人裡的對話壞掉時仍然可用。

模組層級的說明和修改流程在 [dcbot/CLAUDE.md](../dcbot/CLAUDE.md)，使用說明在 [dcbot/GUIDE.md](../dcbot/GUIDE.md)。

## 頻道之間、機器之間的溝通

- `altention dc post <頻道> "內容"`：把東西貼到一個頻道。那個頻道有對話的話，它會收到、知道是誰傳的、怎麼回覆。`--file` 附檔，`--board <名稱>` 是原地更新的看板。腳本和排程用頻道 ID 指定，頻道改名也不受影響。
- `altention dc read <頻道>`：讀頻道的訊息、討論串，或那個頻道背後對話的最近幾輪。
- `altention dc notify "訊息"`：只推播，不交給任何對話。
- 這三支在任何機器都能用：機器人在這台就直接執行 `dcctl`，否則經 SSH 到機器人那台執行，所以其他機器不需要 Discord 的 token。
- `/report` skill：對話輸出一段帶標記的匯報，chatlog 看到標記就立刻同步，進度插件下一次執行時讀到。這是單向的。

## 紀錄：chatlog 和進度

```
各機器的對話 ──chatlog──▶ 對話紀錄 repo ──進度──▶ 進度的資料 repo（每個專案的進度和最新狀態）
```

- **chatlog**
  - Stop hook：每輪結束後把目前的對話匯出到本機的對話紀錄資料夾。
  - SessionEnd hook：對話結束時 commit 並 push。
  - cron 每 15 分鐘：同步所有有變動的對話，接住長時間沒結束的對話。各機器的分鐘數依機器名錯開，避免同時 push。
  - 匯出時會遮蔽看起來像金鑰的字串。
  - Claude Code 預設刪除 30 天前的對話；安裝時會把 `cleanupPeriodDays` 設成 36500。已經被刪掉的舊對話會從 `history.jsonl` 等來源重建，並標示「（重建）」。
  - 同一次 cron 也負責更新引擎的程式碼，以及同步知識和記憶（見下面「同步」）。
- **進度（progress）**
  - 每 10 分鐘執行一次。只處理閒置超過 20 分鐘的對話，或剛 `/report` 過的對話。
  - 先用規則歸類（git remote、已知路徑），對不上才問模型。歸錯了就改進度資料裡那個專案的 `project.json`。
  - 只有進度插件寫它的資料 repo；對話不直接寫，需要正式回報時用 `/report`。
  - 由程式啟動的對話（entrypoint 是 `sdk-cli`）會被略過。

## 知識、記憶、全域說明

- `knowledge/`：整理過的說明（使用者是誰、系統怎麼部署、踩過的坑）。
- `memory/`：Claude Code 的自動記憶。安裝時把 Claude Code 在引擎資料夾的記憶位置連到這裡，所有機器和機器人共用同一份。
- 全域說明：`~/.claude/CLAUDE.md` 連到共用的那一份，每台機器的每個對話都會讀到。
- `skills/*` 連結到 `~/.claude/skills/`。
- 多台機器同時追加的檔案（記憶的索引、skill 的教訓檔）在 `.gitattributes` 設成 union 合併。

## 同步

- 核心的兩行 cron 每 15 分鐘各跑一次（3a.10）：`altention/cron_update.py` 更新引擎；`altention/cron_sync.py` 把這台對知識和記憶的修改 commit 並 push，再 pull 引擎和實例，拿到別台推上來的程式和知識。兩支壞掉時都會退回單純的快轉。chatlog 的 cron 只匯出對話並推到對話紀錄 repo。
- 所以改了程式只要 push，其他機器會在一個週期內自己拉到。需要重新安裝的改動（新的 cron、hook）仍然要跑 `ops/apply.py`。
- 所有自動化的 git 操作共用一把鎖（`bin/home-pull`、`dcbot/deploy.sh`、核心的同步和更新）。多個程序同時 pull 同一個工作樹會互相寫壞而全部失敗。在引擎資料夾裡要更新時用 `bin/home-pull`，不要直接 `git pull`。

## 排程

目前每個元件各自在 crontab 加自己的行：

| 誰 | 頻率 |
|---|---|
| dcbot 的看門狗 | 開機時、每 5 分鐘 |
| chatlog | 每 15 分鐘（分鐘數依機器錯開） |
| 進度 | 每 10 分鐘 |
| usage | 每小時 |
| planner | 每 5 分鐘檢查排程檔 |

排程檔的格式（一個助理一份，助理自己可以改）：

```json
{"channel": "100000000000000010",
 "items": [{"name": "weekly", "enabled": true, "at": "20:30", "days": ["sun"],
            "text": "每週小結：整理這週，寫進進度檔。"}]}
```

每一項一天最多發一次；機器那時關著而錯過超過 3 小時的不補發。

## 「助理」這種用法

一個助理是：一個資料夾（通常有自己的 git repo）、一份 `CLAUDE.md`（它是誰、要做什麼、資料放哪）、它的資料檔，加上一個工作目錄設在那個資料夾的頻道。需要定時做事就加一份排程檔。這是之後「外部插件」的原型。

## 機器與角色

- 機器清單（`machines.json`）是單一事實來源：每台機器的 SSH 別名、家目錄、注意事項、角色。機器資訊只寫在這裡。
- 角色：`dcbot`、`dcattach`；其他插件放在哪台由實例的 `plugins.json` 決定。
- 改機器或搬服務：改清單 → `python3 ops/apply.py <機器>` → `python3 ops/doctor.py`。
- 沒有清單時，系統當成只有這一台機器，不需要 SSH。

## 狀態放在哪

| 位置 | 內容 |
|---|---|
| git repo | 引擎、實例、對話紀錄、進度的資料、各助理的資料夾 |
| `dcbot/state/`（機器人那台，不進 git） | 頻道對應哪個對話、路由表、看板的訊息 ID、還沒回答的提問 |
| `~/.cache/` | chatlog 和進度插件的狀態與鎖、提醒發過沒、各排程的輸出 |
| `~/.local/share/usage/` | 用量樣本、畫圖用的 Python 環境 |
| `~/.config/` | 秘密和每台機器的設定：`dcbot/env`（token）、`altention/instance`（實例在哪）、`altention/host`（機器名） |

機器人那台是單點：`dcbot/state/` 沒有備份。程式可以重裝，但頻道和對話的對應要重建。

## 常用操作

- 檢查全部：`python3 ops/doctor.py`
- 新機器或搬服務：改機器清單的 `roles` → `python3 ops/apply.py <機器>` → `doctor.py`
- 進度插件的狀態：在它那台 `python3 progress/progress.py status`
- chatlog 的狀態：`python3 chatlog/chatlog.py status`；紀錄檔在 `~/.cache/chatlog/chatlog.log`
- 對話紀錄 repo 裡每台機器的 `README.md` 是索引

## 設計原則

- 長期服務只跑在清單指定的機器上；會睡眠的機器不跑。
- 單例服務（機器人、進度、提醒、用量）同時只能有一台；`apply.py` 會先停舊的再啟動新的。
- 狀態盡量放 git repo，服務才能隨時搬到別台。
- 秘密不進 git，只放在使用它的機器的 `~/.config/`；搬家時要重新放一次。
- 機器人只聽擁有者一個人。
- 每台機器上會跑的程式只用標準函式庫、相容 Python 3.8。
