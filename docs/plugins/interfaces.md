# 插件能用、不能碰的引擎介面

插件只靠引擎「公開、有版本」的東西。完整清單在 `altention/interfaces.json`，這份說明怎麼用、哪些不能碰、還有哪些情況檢查不到。plugin.json 每個欄位的意思見 `docs/plugins/plugin-json.md`。

## 寫明要用哪些介面：`uses`

plugin.json 寫上插件用到的介面和版本：

```json
"uses": {"dc.post": 1, "dcctl": 1, "py.config": 1}
```

- 版本是一個整數，只在介面改了、舊的用法會壞的時候才加一（拿掉選項、改輸出格式、改意思）。加新的選用欄位或選項不加。
- 引擎可以同時提供一個介面的新版和舊版（interfaces.json 的 `also`），兩個版本都算對得上。
- 有寫 `uses`、但這版引擎沒有其中一個介面、或版本對不上，插件在那台**不啟用**（blocked）：
  - 跟關掉一樣，它的排程、hook 和 skill 都會拿掉，也不會排程；
  - `python3 ops/plugins.py list`、doctor（`plugins.blocked`）和更新卡片會說是哪個介面、怎麼修：更新引擎，或換成這版引擎能用的插件版本；
  - 對上之後再跑一次 apply，全部回來。
- 引擎的介面清單讀不出來時，插件是「不知道」，什麼都不拿掉。
- 沒寫 `uses` 的插件照常啟用，`plugins.py list` 會提醒作者補上。補上以後，引擎改了你用的介面時，插件會先停住並說明原因，而不是裝了才壞掉。
- `"engine": ">=X.Y.Z"` 照舊管「至少要這版引擎」（用到新功能時）；`uses` 管「用到的介面沒被改掉」。兩個都寫最穩。
- 插件也可以提供介面給別的插件用：在自己的 plugin.json 寫 `"interfaces"`，格式跟 interfaces.json 一樣。引擎只把這台開著的插件提供的介面算進清單。提供的插件沒開時，用它的插件不啟用，原因會寫是哪個插件、怎麼修。名字跟引擎的重複就不算，check-plugin 也會擋。

## 能用的（第一版，都是 v1）

指令和 plugin.json：

- `dc.post`、`dc.read`、`dc.notify`：`altention dc post`、`read`、`notify`（`--file`、`--wait`、`--board`、`--activity`、`--create`，保留目標 `@audit`、`@notify`）。怎麼用見 `docs/plugins/discord.md`。用 `--file x.png` 貼圖時，旁邊的 `x.png.source.json`（圖是從什麼畫的）也是 `dc.post` 的一部分，這個檔的格式改了也會升版。
- `rewrite`：plugin.json 的 `rewrite`，機器人把回覆交給插件改寫，輸入輸出見 `docs/plugins/discord.md`。
- `activity.pages`：plugin.json 的 `activity.pages`，和打開頁面時機器人寫給插件的東西。
- `tokens`：plugin.json 裡的代換符號 `{engine}` `{plugin}` `{python}` `{claude}` `{instance}` `{data}` `{root}` `{setting:…}`，以及機器人起的 Claude 拿到的 `$DCBOT_CHANNEL`。

引擎裡的檔，只有這幾個可以用路徑叫：

- `dcctl`：`dcbot/tools/dcctl` 的 `app --json`、`channels`、`send`。
- `dcremote.files`：`dcbot/tools/dcremote files`（JSON 進出，可以用 ssh 在別台跑）。

Python（插件要 import 引擎時）：

- `py.config`：`altention.config` 的 `root`、`instance_dir`、`settings`、`setting`、`machines`、`this_machine`、`engine_on`、`ssh_aliases`。同一個模組的其他函式不公開，之後可能改名或拿掉。

## 過渡介面：現在能用，之後會拿掉

這幾個照常能用，寫進 `uses` 也照常啟用；但 `check-plugin` 會提醒，**之後的版本**有正式的唯讀指令後就會拿掉，到時改用那些指令。v0.5.0 還沒有這些唯讀指令。

- `state.channels`：唯讀 `dcbot/state/channels.json`。
- `state.waiting`：唯讀 `dcbot/state/waiting-on-owner.json`。
- `py.transcript`：`altention.connectors.claude_code.transcript`，讀對話紀錄。
- `files.guard`：`dcbot/bot/filecore.py` 的 `blocked()`，判斷一個路徑是不是放秘密、不能碰的地方。

這兩個狀態檔是機器人自己的，格式可能改（例如欄位變多）：讀的時候把沒看過的欄位當成可有可無，也不要靠它們長期不變。

## 不能碰的

- `dcbot/` 底下上面沒列的檔：機器人的程式、其他狀態檔、`dcbot/state/` 裡其他的檔。不 import，不用路徑載入，也不讀。
- `altention` 裡上面沒列的模組（例如 `altention.sync`、`altention.plugin`），和 `altention.config` 不公開的函式。
- `ops/` 的程式（`apply.py`、`doctor.py`、`setup.py`…）：那是管機器的工具，不是給插件叫的。
- 寫入引擎或機器人的任何狀態檔。插件自己的東西放在它自己的資料夾，或 plugin.json `data` 宣告的地方。
- 機器人的 token 和其他放秘密的地方。插件自己的秘密不要放進 git。

skill 也一樣：插件的 skill 叫 Claude 做事時，也只叫上面能用的指令和檔。

## 發佈前的檢查：`ops/release.py check-plugin`

發佈插件之前，`check-plugin`（`publish-plugin` 也會）會讀要發佈的那一版裡插件會跑的檔（tests/、docs/、test_*.py、Markdown 不算；Python 只看字串，不看註解和 docstring）：

- **拒絕**：
  - 用到 `dcbot/` 底下沒列在 interfaces.json 的檔；
  - import 了 `altention` 裡沒列的模組；
  - 用到某個介面，`uses` 卻沒寫（會說要加哪一項）；
    - 從插件自己寫的內容推得出來的也算：plugin.json 有 `rewrite` 或 `activity.pages`；plugin.json 用了 `{…}` 代換符號，或任何檔、skill 裡有 `$DCBOT_CHANNEL`（要寫 `tokens`）；程式、腳本或 skill 裡有 `altention dc post`、`notify`、`read`（要寫對應的 `dc.*`）。
  - `uses` 在這版引擎對不上。
- **提醒**（不拒絕）：
  - 過渡介面；
  - `altention.config` 不公開的函式；
  - 用變數組出來、看不出是哪個檔的 `dcbot/` 路徑；
  - 只用到公開檔所在的資料夾（例如 `dcbot/state`）；
  - skill 的 Markdown 叫 Claude 跑引擎的 `dcbot/` 或 `ops/` 的檔。
- 不是文字的檔（字型、圖片）跳過。

## 已知限制：檢查抓不到的

`check-plugin` 只讀寫出來的字，不執行插件。下面這些寫法它看不出來，請自己照上面的規則：

- 用字串拼出模組名再 import，例如 `importlib.import_module("altention" + ".sync")`。
- 只寫 `import altention.config`（沒有 `as`）時，不會檢查用到哪些函式，不公開的函式不會被提醒。
- 插件程式裡用路徑叫 `ops/` 的程式：不擋也不提醒（只有 skill 的 Markdown 會提醒）。
- 路徑的每一段都來自變數或設定，只在執行時才知道是哪個檔。
- 先組好一個資料夾，再用變數接檔名：只有檔名寫成字面值時才認得出來。
- 一般的 Markdown（README、docs/），和 skill 以外叫 Claude 做事的文字。
- 「dc post」這幾個字是照字面找的：Python 的字串、和不是 Python 的檔裡的文字（例如 JS 字串裡的一段話），只要出現，就會被當成用到 `dc.post`（Python 的註解和 docstring 不算）。真的沒用到的話，換個說法，或把那段文字搬進 docs/。
- 指令分成兩段組（先寫 `["altention", "dc"]`，之後才接動詞），或動詞是變數（`f"altention dc {verb}"`）：看不出要寫哪個 `dc.*`，請自己寫進 `uses`。
- 插件呼叫的外部程式再去碰引擎的檔。
