# plugin.json 的欄位

每個欄位引擎拿來做什麼，由 `altention/plugin.py` 的 `check()` 檢查格式。沒寫的欄位用預設值，不認得的欄位忽略。
這份只列欄位；插件能用和不能碰的介面另外寫（C-96）。

## 在用

- 頂層：`format`、`name`、`title`、`description`、`machines`、`standby`、`order`、`pitch`、`keep`、`lose`、`engine`、`test`、`schedule_files`、`schedule_files_default`
- `standby`：`true` 只配 `"machines": "one"`：實例沒寫 `on` 時，插件除了機器人那台，也在機器清單裡有 `dcbot-standby` 角色的機器上跑。插件自己要判斷這台現在是不是機器人（例如看板只在當下是機器人的那台貼）。更早的引擎不認得這個欄位，照舊只在機器人那台跑。用到這個欄位，`uses` 要加 `"plugin.format": 4`（check-plugin 會擋），例如 `"machines": "one", "standby": true, "uses": {"plugin.format": 4}`。寫了 `"standby": true` 但不是 `"machines": "one"`，plugins.py list 和 check-plugin 都會說這個 plugin.json 寫錯了。
- `hooks[]`：`event`、`matcher`、`command`、`timeout`
- `schedules[]`：`name`、`scheduler`；`when` 的 `every`、`offset`、`at`、`days`、`once`；`do` 的 `run`、`message`、`channel`、`env`、`log`、`output`、`timeout`、`trace`（`file`、`key`、`within`）
- `setup[]`：`name`、`run`、`optional`、`today`、`lazy`
- `setup[]`、`secrets[]` 的每一筆都可以寫 `on`，說它在哪幾台：`all`（沒寫就是它）、`one`（插件跑的那台）、`bot`（機器人那台）、`sources`、`standby`（機器清單裡有 `dcbot-standby` 角色的機器，C-163）或機器名；寫成清單是其中任一台。用到 `standby` 的插件，uses 要加 `"plugin.format": 3`（更早的引擎會把它當成機器名，那一步永遠不跑；check-plugin 會擋）。
- `data[]`：`name`、`path`、`private`、`sync`
- `skills[]`：`name`、`path`
- `needs[]`：`name`、`check`、`hint`、`why`、`install`（`{作業系統: 指令}`，作業系統是 `debian`、`fedora`、`arch`、`macos`；以 `sudo ` 開頭的由使用者自己跑。指令裡可以用 `{plugin}`、`{python}` 等代換符號，跟 check 一樣（見 interfaces.md 的 tokens），使用者看到的裝法是展開後的完整路徑；要用的話 `uses` 寫 `"tokens": 2`。跟 check 一樣不會替你加引號：路徑可能有空白，請自己加，例如 `"{plugin}/bin/display" --install chrome`）、`size`、`optional`、`for`、`ask`
- `guide[]`（要使用者到別處做的步驟，照順序）：`id`、`title`、`why`、`url`、`do`、`check`（或 null，要寫 `ask`）、`ask`、`run`（插件代做的指令）、`optional`、`secret`（`name` 要是 `secrets[]` 裡的一筆、`label`）、`after`、`gate`（這一步的檢查說還沒好時，先不跑的 `setup[]` 項目和 `schedules[]` 的名字；這一步好了之後就放行，之後就算又變成沒好也不再擋。檢查不了的步驟（check 是 null 或回 3）不擋。沒寫 gate 就什麼都不擋）
- `needs[]` 和 `guide[]` 的 `check` 結束碼怎麼算：見 [checks.md](checks.md)
- `offer`：`cost`（會花什麼，沒寫就照 `needs[]` 和 `guide[]` 自動寫）、`when`（`install`：新安裝時 #setup 一起問，預設；`asked`：只有在別的程式跑 `setup.py offer <插件>` 時才問，例如某個條件第一次成立）。問的那句話用頂層的 `pitch`。只對官方清單上、實例還沒寫的插件問；答案記在實例 plugins.json 的 `offers`（「先不要」不再問，「之後再說」最多三次）。`install` 的插件從沒被問過的（安裝比這一題舊，或問的時候官方清單還沒抓到），引擎下次更新後在 #setup 補問一次。新安裝時安裝程式會先看一次官方清單（有時間上限，連不上就照舊）。新上官方清單的插件不會因此被問（#update 只說一次）。C-64
- `secrets[]`：`name`、`label`（C-77 G3）。值存在 `~/.config/altention/secrets/<插件>/<name>`（資料夾 700、檔案 600），使用者用 `altention secret set <插件> <name>` 在自己的終端機存，或按 Claude 用 `altention dc post <頻道> --secret <插件>/<name>` 貼的「填入」按鈕在 Discord 的輸入框存（`label` 是按鈕和輸入框上的字，見 discord.md）；插件自己的程式用這個路徑讀，不要印出值。秘密守門員擋住整個資料夾：Claude 要讀、要寫、在指令裡寫到它都會被擋（盡力而為，不是隔離：這個帳號跑的程式都讀得到）。不跟著備份。
- `requires[]`：`plugin`
- `channels[]`：`name`、`kind`、`setting`、`name_setting`、`category`、`folder`、`folder_setting`。`folder`、`folder_setting`：只有 `kind: "claude"` 能寫；Claude 工作的資料夾，相對於 `{data}/<插件名>`（沒寫就是它本身；不能是絕對路徑、`~` 開頭或含 `..`；建好之後真實路徑也要還在那底下，符號連結指出去就不開），不存在會先建；`folder_setting` 是設定鍵，使用者在那裡寫的路徑（任何路徑）優先。#setup 會問要不要開這個頻道（「開」就開好；「先不要」之後不再問，使用者在 #setup 說「開 #<頻道名>」時開）。已經開好的頻道不動。用到的話 `uses` 要寫 `"plugin.format": 5`；更早的引擎會擋下整個插件並說要先更新引擎。插件沒有那個頻道也能用的話，寫成 `{"v": 5, "optional": true}`：更早的引擎就不擋，只是不開、也不問那個頻道。例如 `"channels": [{"name": "notes", "kind": "claude", "setting": "notes.channel", "folder": "work", "folder_setting": "notes.folder"}], "uses": {"plugin.format": 5}`
- `asks[]`：`key`、`question`、`unless`
- `rewrite`：`command`、`timeout`、`when`（輸入輸出見 `docs/plugins/discord.md`）
- `activity.pages[]`：`id`、`title`、`access`、`opens_files`、`file_button`。`"opens_files": true` 的頁（插件的 `uses` 要寫 `"activity.pages": 2`；寫 1 照常能用，只是沒有這些按鈕）：Claude 用 `altention dc post --file` 貼檔案時，每個檔案都附一顆打開這一頁的按鈕，按鈕上的字是 `file_button`（80 字以內；沒寫就用 `title`），一則有好幾個檔案時後面接「：<檔名>」。按下去，機器人寫給插件的 `args` 是 `{"machine": <機器清單裡的名字>, "path": <絕對路徑>}`；這兩項的 JSON 超過 2048 bytes 的檔案不附按鈕。一則最多 5 顆（Discord 一列），先照檔案的順序、再照頁面排，超過的不附。
- `interfaces`：`{"<介面>": {"version", "what", "paths", "also"}}`，插件自己提供給別的插件用的介面，格式跟 `altention/interfaces.json` 一樣。這台開著時才算數；名字不能跟引擎的重複。
- `uses`：`{"<介面>": <版本>}`，插件用到引擎的哪些介面（C-94）。介面和版本列在 `altention/interfaces.json`；這版引擎沒有其中一個、或版本對不上，插件在這台就不啟用（blocked：跟關掉一樣拿掉它的 hook、排程和 skill），`plugins.py list`、doctor 和更新卡片會說是哪一個、怎麼修。沒寫 `uses` 照常啟用，外部插件會被提醒補上。能用哪些、不能碰哪些：`docs/plugins/interfaces.md`。也可以寫成 `{"v": 1, "optional": true}`：那個介面不能用時（提供它的插件沒開、引擎沒有、版本不對）不擋整個插件，只是那一項不能用；插件執行時用 `altention plugins uses <插件> --json` 查哪些能用。用到這種寫法的插件，uses 要加 `"plugin.format": 2`。例如 `"uses": {"plugin.format": 2, "rig.spend": {"v": 1, "optional": true}}`；`altention plugins uses demo --json` 印 `{"plugin": "demo", "where": "on", "uses": {"plugin.format": {"ok": true, "optional": false}, "rig.spend": {"ok": false, "optional": true, "why": "rig.spend 由插件 rigp 提供，但 rigp 在這台沒開"}}}`（結束碼 0；沒有這個插件是 1）。
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
