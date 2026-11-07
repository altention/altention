# plugin.json 的欄位

每個欄位引擎拿來做什麼，由 `altention/plugin.py` 的 `check()` 檢查格式。沒寫的欄位用預設值，不認得的欄位忽略。
這份只列欄位；插件能用和不能碰的介面另外寫（C-96）。

## 在用

- 頂層：`format`、`name`、`title`、`description`、`machines`、`order`、`pitch`、`keep`、`lose`、`engine`、`test`、`schedule_files`、`schedule_files_default`
- `hooks[]`：`event`、`matcher`、`command`、`timeout`
- `schedules[]`：`name`、`scheduler`；`when` 的 `every`、`offset`、`at`、`days`、`once`；`do` 的 `run`、`message`、`channel`、`env`、`log`、`output`、`timeout`、`trace`（`file`、`key`、`within`）
- `setup[]`：`name`、`run`、`optional`、`today`、`lazy`
- `data[]`：`name`、`path`、`private`、`sync`
- `skills[]`：`name`、`path`
- `needs[]`：`name`、`check`、`hint`、`why`、`install`（`{作業系統: 指令}`，作業系統是 `debian`、`fedora`、`arch`、`macos`；以 `sudo ` 開頭的由使用者自己跑）、`size`、`optional`、`for`、`ask`
- `guide[]`（要使用者到別處做的步驟，照順序）：`id`、`title`、`why`、`url`、`do`、`check`（或 null，要寫 `ask`）、`ask`、`run`（插件代做的指令）、`optional`、`secret`（`name` 要是 `secrets[]` 裡的一筆、`label`）、`after`、`gate`（這一步的檢查說還沒好時，先不跑的 `setup[]` 項目和 `schedules[]` 的名字；這一步好了之後就放行，之後就算又變成沒好也不再擋。檢查不了的步驟（check 是 null 或回 3）不擋。沒寫 gate 就什麼都不擋）
- `needs[]` 和 `guide[]` 的 `check` 結束碼怎麼算：見 [checks.md](checks.md)
- `offer`：`cost`（會花什麼，沒寫就照 `needs[]` 和 `guide[]` 自動寫）、`when`（`install`：新安裝時 #setup 一起問，預設；`asked`：只有在別的程式跑 `setup.py offer <插件>` 時才問，例如某個條件第一次成立）。問的那句話用頂層的 `pitch`。只對官方清單上、實例還沒寫的插件問；答案記在實例 plugins.json 的 `offers`（「先不要」不再問，「之後再說」最多三次）。`install` 的插件從沒被問過的（安裝比這一題舊，或問的時候官方清單還沒抓到），引擎下次更新後在 #setup 補問一次。新安裝時安裝程式會先看一次官方清單（有時間上限，連不上就照舊）。新上官方清單的插件不會因此被問（#update 只說一次）。C-64
- `secrets[]`：`name`、`label`（C-77 G3）。值存在 `~/.config/altention/secrets/<插件>/<name>`（資料夾 700、檔案 600），使用者用 `altention secret set <插件> <name>` 在自己的終端機存，或按 Claude 用 `altention dc post <頻道> --secret <插件>/<name>` 貼的「填入」按鈕在 Discord 的輸入框存（`label` 是按鈕和輸入框上的字，見 discord.md）；插件自己的程式用這個路徑讀，不要印出值。秘密守門員擋住整個資料夾：Claude 要讀、要寫、在指令裡寫到它都會被擋（盡力而為，不是隔離：這個帳號跑的程式都讀得到）。不跟著備份。
- `requires[]`：`plugin`
- `channels[]`：`name`、`kind`、`setting`、`name_setting`、`category`
- `asks[]`：`key`、`question`、`unless`
- `rewrite`：`command`、`timeout`、`when`（輸入輸出見 `docs/plugins/discord.md`）
- `activity.pages[]`：`id`、`title`、`access`
- `interfaces`：`{"<介面>": {"version", "what", "paths", "also"}}`，插件自己提供給別的插件用的介面，格式跟 `altention/interfaces.json` 一樣。這台開著時才算數；名字不能跟引擎的重複。
- `uses`：`{"<介面>": <版本>}`，插件用到引擎的哪些介面（C-94）。介面和版本列在 `altention/interfaces.json`；這版引擎沒有其中一個、或版本對不上，插件在這台就不啟用（blocked：跟關掉一樣拿掉它的 hook、排程和 skill），`plugins.py list`、doctor 和更新卡片會說是哪一個、怎麼修。沒寫 `uses` 照常啟用，外部插件會被提醒補上。能用哪些、不能碰哪些：`docs/plugins/interfaces.md`。
- 只有核心（`altention/core.plugin.json`）能寫：`cron[]`（`name`、`on`、`when`、`run`、`scheduler`）、`settings[]`（`file`、`key`、`at_least`）

## 保留（檢查格式，引擎現在不讀）

- `provides`：一句給 Claude 看的話，說要找這個插件的東西去哪裡讀。之後要組進全域說明。
- 核心 `cron[]` 的 `why`：註解，給人看的。

## 拿掉了（C-95）

插件還寫著也照常啟用，`plugins.py list` 和 `release.py check-plugin` 會提醒作者刪掉。

- `commands[]`：沒有程式讀。
- `requires[].data`：只看 `plugin`。
- `secrets[].path`：路徑固定了（C-77 G3），見上面的 `secrets[]`。
- 核心的 `links[]`：連結由同步管，見 `docs/architecture.md` 的「知識、記憶、全域說明」。
