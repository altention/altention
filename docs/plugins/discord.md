# 插件怎麼貼到 Discord

插件（排程、hook、自己的指令）要在 Discord 留東西，用引擎的 `altention dc post` 和 `altention dc notify`，從任何一台機器都能用，不用自己拿 token。

## 貼到哪裡

頻道有三種寫法，越前面越好：

- **保留目標 `@audit`、`@notify`**：機器人自己的頻道，插件不用知道名字。
  - `@audit`：機器人的紀錄頻道（它做的每件事都記在那裡）。適合「做了什麼」的紀錄。
  - `@notify`：預設的通知頻道（實例的 `core.notify_channel`；不存在時機器人會建它）。適合要使用者看到的事。
  - 由機器人用自己的設定找，不讀頻道清單；頻道改名也找得到（`@audit` 是機器人啟動時找到的那個頻道）。
  - 只能用來貼訊息或通知（含 `--file`、`--activity`、`--secret`）；`--new-claude`、`--attach`、`--move-to`、`--resume`、`--history` 和封存不接受，會直接說不行。
- **頻道 ID**：改名也找得到，`dc post`／`dc notify` 完全不讀頻道清單。插件自己建的頻道，記下它的 ID 用。
- **頻道名字**（`notes`、`分類/notes`）：要先用頻道清單把名字對到頻道。

```sh
altention dc post @audit "rigact：今天整理了 3 個檔案"
altention dc notify -c @notify "rigact：同步失敗，請看 ~/.local/state/rigact/log"
echo "長的報告…" | altention dc post @notify
```

## 有沒有真的送到：`--wait`

`dc post` 和 `dc notify` 平常只是把訊息交給機器人的佇列：結束碼 0 代表**進了佇列**，不代表已經在 Discord 上（機器人沒在跑時，它上線後才送）。要確定送到了才做下一步（例如先記一筆再打開某個東西），加 `--wait 秒數`（1 到 600）：

- **0**：機器人已經把它貼上 Discord。stdout 最後一行是那則訊息的 ID（分成好幾則時是最後一則）。通知找不到原本的頻道、由機器人改送到預設通知頻道（開頭註明原本要送哪裡）時也算送到。
- **3**：時間到了還沒送出（機器人沒在跑，或還沒處理到）。訊息**還在佇列裡**，之後會送出，不要重送。stderr 說原因。
- **4**：機器人說沒有貼出（找不到頻道、只寫進了 #audit、送出時出錯）。stderr 說原因。
- **1**：還沒進佇列就失敗了（寫錯參數、找不到頻道名字、連不上機器人那台、Discord 限速一直沒過）。stderr 說原因。

`--wait` 只能用在貼訊息和通知（含 `--file`、`--activity`、`--secret`、保留目標）；看板、開頻道、搬移這類不接受。

```sh
if out=$(altention dc post @notify --wait 30 "rigact：開始整理"); then
  id=$(printf '%s\n' "$out" | tail -1)   # 送到了：最後一行是訊息 ID
fi
```

插件要發 Discord 的任意訊息（embed、Components V2、上傳的圖片）用 `altention dc post <頻道> --json payload.json`（任何機器都能用，經機器人的佇列，C-172a P1d）：payload 原樣送出；發到別人的頻道時最上面加一行 `-# 📨 來自 <#寄件頻道>`（V2 加在最上面的 Text Display），放不下就不送、不截斷；不交給那個頻道的 Claude。規則同 `send-json`：custom_id 要 `user:` 開頭、不能寫 `@everyone`／`@here`、不能冒用保留的開頭（含 `-# 📨 來自`）、@ 只照 `--mention`。`--file`（payload 沒寫 `attachments` 時照 `--file` 的順序補上）、`--edit <訊息 ID>`（只改自己發的，照 ledger）、`--thread-new "名稱"`（印出討論串 ID）、`--reply-to <訊息 ID>`、`--silent`、`--no-embeds`；預設等 30 秒，結束碼 0＋訊息 ID、3 還在佇列、4 不收（原因在 stderr）。`dcctl send-json <頻道>` 是 `altention dc post <頻道> --json -` 的別名。框版看板：`altention dc post <頻道> --board 名稱 --json board.json`（一個 payload 或最多 10 個的陣列，一個一則；原地改，不見了重貼、改不了留舊的、多的刪掉；純文字可以原地改成框，框改回純文字刪掉重貼、釘選跟著移；機器人不加標題和「更新於」）。

`dcctl send <頻道 ID> "…"`（在機器人那台）直接送、不經佇列，給 ID 時也不讀頻道清單，成功時最後一行是訊息 ID。訊息裡的提及預設不 @ 任何人；加 `--mentions` 只讓內容裡擁有者的 `<@ID>` 會響（真的 @ 到時 #audit 多記一行；內容沒寫 `<@ID>` 就不會 @，也不多記），身分組和其他人照樣不 @；內容寫了 `@everyone`、`@here` 整則不送；超過 2000 字整則不送，結束碼 4（不截斷、不拆）。開頭是機器人自己用的格式（`📨 **來自 …**`、`🔔 **[…]**`、`📌 **…**`、`⌨️ **終端機那邊輸入**`）的會拒絕，因為讀回時會被當成別的頻道傳來的、通知、看板或終端機輸入。寄件人是 Claude（`channel:`、`claude:`）而目標不是它自己的頻道（或其中的討論串）時，最上面加 `-# 📨 來自 <#寄件頻道>`、ledger 記 framed source，edit 時插回去；寄件人是程式（`program:`，supervisor、排程、腳本）不加，它的 edit 只看寄件人的鍵。每次送出都記在 #audit（寫是誰送的：照執行它的 Claude Code 自己的環境查出來的，跟 `altention dc post` 一樣；`dcctl whoami` 印出它把你當成誰）。`dcctl edit` 只能改自己發的 post（`dcctl send`／`send-json` 發的，含 `altention dc post` 發到自己頻道的純文字），照機器人的訊息紀錄（ledger）判斷：那則要記著、寄件人跟呼叫者相同（每個頻道留最新 500 則，用 `dcctl pin` 釘選的釘著就一直留著，每個頻道最多 50 則；在 Discord 上手動釘的，要趁它還記著時再跑一次 `dcctl pin`）。機器人自己的卡片、提問、計畫、看板和回覆都改不了；紀錄裡沒有的那則 `dcctl pin` 照樣釘，但會印一句說改不了。別台執行時，寄件人在那台查好、經 ssh 帶過來（`ALTENTION_SENDER`，只在 `ALTENTION_HANDED` 時收；帶不過來就當成機器人那台的程式）。同一個系統帳號底下，這兩個變數都寫得出來：這是防呆，不是安全邊界。被規則拒收的（保留的開頭、`@everyone`／`@here`、custom_id、@ 誰、改不是自己發的訊息）在 `dcctl send`、`send-json`、`edit` 都用結束碼 4（跟太長一樣，重送也沒用）。`dcctl send-json` 的按鈕和選單 custom_id 要以 `user:` 開頭（連結按鈕不用），按下去只會回一句「這個按鈕還沒有接處理」；`--mention owner` 才讓內容裡擁有者的 `<@ID>` 會響，`--mention user:ID`／`role:ID` 要實例的 `dcbot.json_mentions` 設成 `"any"`。

## 附上圖是從什麼畫出來的（按 📝 看）

插件貼出自己畫的圖時，可以一起附上原文。使用者在那則訊息按 📝，機器人就回「<kind>，<tool> 做的」和原文的程式碼區塊（太長的改附成檔案）。原文的格式：

```json
{"kind": "公式", "lang": "latex", "tool": "KaTeX", "source": "e^{i\\pi}+1=0"}
```

- `kind`、`tool`：各一句話（最多 40 字），機器人照貼。
- `lang`：程式碼區塊的語言，也當附檔的副檔名（英數字、`+`、`-`，最多 20 字）。
- `source`：原文，最多 64 KB（UTF-8）。
- 四個都要有；格式不對時那張圖照樣貼出，只是沒有原文（`altention dc post` 會在 stderr 說一句）。

有兩種附法：

- `altention dc post --file x.png`：在圖旁邊放 `x.png.source.json`，內容是上面的物件。dc post 會讀它，sidecar 本身不會貼出去。
- 改寫回覆（plugin.json 的 `rewrite`）：每則訊息的物件可以多一個 `"sources": {"<images 裡的路徑>": {…}}`。

同一則裡好幾張圖的原文完全一樣時（一份長文件切成幾張），機器人只回一次，寫「第 1–3 張：」。原文先過一次秘密遮蔽才存，最多存 30 天（最多 500 則訊息）。原文是對話內容的一部分，不要放秘密。

## 請使用者填金鑰：`--secret`

插件要一個金鑰（plugin.json 的 `secrets[]` 宣告過的名字）時，Claude 可以貼一顆「填入」按鈕，不用叫使用者把金鑰貼進對話：

```sh
altention dc post @notify --secret rigact/api-token "rigact 要一個 API token 才能同步"
```

- 機器人先確認這台的插件宣告了這個名字，沒有就不貼（`--wait` 時結束碼 4，原因在 stderr）。按鈕上的字是宣告裡的 `label`。
- 只有擁有者按得動。按下去 Discord 跳出一個輸入框（最多 4000 字），送出後機器人把值交給核心存（`altention secret set <插件> <名字> --stdin --via discord`），存在機器人那台的 `~/.config/altention/secrets/<插件>/<名字>`（600），#audit 記一行（只有插件和名字），按的人看到「存好了」或「沒有存：原因」。
- 值經過 Discord，輸入時看得到字（Discord 的輸入框不會遮）；值不會貼進頻道、不交給任何 Claude、不寫進機器人的 log 或狀態。不想經過 Discord 的，請使用者在終端機跑 `altention secret set`。
- 機器人不會自己送到別台。這個插件還有別台在用時，「存好了」下面多一行列出是哪幾台；要放過去，由 #setup 的 Claude 問過使用者後用 `setup.py secret-send` 送，只送到使用者說的那台。
- 按鈕記在頻道狀態裡（每頻道最近 20 顆、7 天），機器人重啟後照樣能按；再按一次就覆寫。

## 頻道清單和限速

Discord 讓整個機器人每 60 秒只能讀 10 次頻道清單。用名字貼時要讀它，所以：

- 同一台機器上的 dcctl 共用一份讀過的清單，一分鐘內不再問 Discord；同時好幾個在讀時，只有一個去問，其他的等它。
- 名字在那份清單裡找不到時（可能是剛建的頻道），再讀一次新的；還是找不到才說找不到。
- 頻道剛改名或剛刪掉的一分鐘內，用舊名字貼還可能被收下，由機器人送的時候再說找不到。要確定，用 ID 或保留目標。

所以排程和 hook 裡請用保留目標或 ID，不要用名字。

## 不要做的事

- 不要讀機器人的設定檔或 token 自己找頻道：保留目標就是為了不用碰它們。
- 不要在程式裡寫死這個安裝的頻道名字（每個安裝不一樣）。
