# 控制台

在引擎資料夾（這個 repo）開的 Claude Code 對話就是使用者的「控制台」：管理所有機器、服務、專案。Discord 機器人（`dcbot/`）是同一個控制台的行動版，共用知識與記憶。

**這個安裝自己的事**（有哪些機器和專案、「接續」時去哪裡看、使用者的習慣）寫在實例的 `CLAUDE.md`：實例的位置用 `python3 -c "from altention import config; print(config.instance_dir())"` 查，通常是 `~/altention/instance`。有這個檔就開始時先讀它；新安裝沒有，需要記這些事時再建（`examples/instance/CLAUDE.md` 只是範例，不在實例裡）。

## 開始時
- 先跑 `bin/home-pull`，拿到其他機器和 Discord 機器人的最新程式。**不要直接 `git pull`**：很多頻道和自動同步可能共用這個資料夾，同時 pull 會失敗（`Cannot rebase onto multiple branches`），`home-pull` 會先拿共用的 git 鎖。
- 系統怎麼運作：`knowledge/README.md`（指向實例的資料夾）。機器與角色：實例裡的 `machines.json`。
- 不確定各機器現況時：`python3 ops/doctor.py`。

## 做事時
- 改機器或服務：改實例的 `machines.json` → `python3 ops/apply.py <機器>` → `python3 ops/doctor.py`。機器清單改完在實例 repo 裡 commit、push（同步只自動提交 knowledge、memory、docs/refactor、council 的資料和兩份 CLAUDE 說明，不會提交它）。新機器先 `python3 ops/instance.py join <實例的 repo>`。
- 在引擎裡提交或推送用 `bin/home-git commit …`、`bin/home-git push`（拿同一把 git 鎖，免得跟同步、部署同時動分支）；不要直接 `git commit`。
- 改程式碼後 commit（英文訊息）並 push；其他機器透過 `ops/apply.py` 或同步套用。
- 學到長期有效的事：整理好的說明寫進 `knowledge/`，零散的事實寫進記憶（`memory/`）。兩者都在實例，同步每 15 分鐘自動提交並推出去。
- 需要通知使用者：`bin/altention dc notify "訊息"`。
- 秘密（token、金鑰）不進 git。個人的值（機器名、帳號、頻道、私人網址）不進引擎：從 `altention.config` 或實例設定讀；部署時印出的個資掃描結果看到就改。
- 這個資料夾同時是正式環境，也可能是很多頻道的工作目錄。`dcbot/` 只由開發頻道改；其他程式要大改時，先開 git worktree 改好再合併回來，不要在這裡留改到一半的檔案。
