# 機器人使用說明
不懂的地方直接在任何對話頻道問 agent。
每個頻道是 {{bot_machine}} 上的一個 Claude Code 對話{{#attach_machines}}，也能接上其他機器的{{/attach_machines}}。這個頻道只放說明，機器人不回應；完整說明在引擎的 `dcbot/docs/usage.md`。

## 對話
- **聊天**：在頻道打字就是跟 Claude 說話，👀 表示已送進去。
- **開新對話**：新開一個文字頻道，照設定卡選要做什麼、開在哪裡。
- **插話**：Claude 在忙時照樣可以打字；開頭加 `!!` 立刻送出。
- **附件**：圖片、檔案直接傳。
- **看過程**：對回覆按 📋，討論串裡放這一輪的完整過程。
- **回覆、轉發**：用 Discord 的回覆或轉發，原文會一起交給 Claude。
- **跨頻道**：叫 Claude「貼到 #某頻道」或「去看 #某頻道」。

## Claude 要你回應時
- **提問**：出現按鈕，也可以直接打字回答。
- **計畫**：plan mode 的計畫貼出來，按「核准」或「要修改…」。
- **終端機的選單**：權限確認這類選單變成按鈕。
- **子代理、workflow**：各有一個討論串當紀錄。

## 指令
平常打 `/` 從選單選；選單叫不出來時，有 `!` 版本的直接打成一般訊息也一樣。其他 `!` 開頭的字不會送給 Claude（`!!` 開頭的急件除外）。
- `/status`, `!status`: 機器人版本、這個頻道的模式、工作目錄、對話編號、tmux 名稱。
- `/reset`, `!reset`: 這個頻道開新對話。舊對話不會消失，可以用 `/resume` 接回。
- `/resume`, `!resume`: 列出最近的對話，選一個接回（會把那個對話的紀錄貼進頻道）。`!resume <id 開頭 8 碼>` 可以接回清單外更舊的。
- `/stop`, `!stop`: 中止正在跑的工作，對話保留。
- `/recap`, `!recap`: 顯示這個頻道的對話最近幾輪（預設 3 輪，最多 20）。
- `/screen`, `!screen`: 看 Claude 的終端機畫面（圖片，按 📝 看文字）。
- `/key`, `!key`: 送按鍵到終端機，例如 `Escape`、`Enter`、`Down Enter`。
- `/mode`, `!mode`: 切換 `tmux`（預設，常駐對話）或 `print`（每則訊息跑一次，備用）。
- `/cwd`, `!cwd`: 設定這個頻道的工作目錄，會開新對話。
- `/files`, `!files`: 檔案瀏覽器（見「終端機和檔案」）。
- `/rename`, `!rename`: 頻道和 Claude Code 的對話一起改名。
- `/setup`, `!setup`: 重新叫出這個頻道的設定卡。
- `/attach`, `!attach`: 把 tmux 裡自己開的 Claude Code 接到頻道（見「接上你自己開的 Claude」）。
- `/detach`, `!detach`: Discord 不再跟著這個對話，對話本身不受影響，頻道移到封存。
- `/talklimit`, `!talklimit`: 這個頻道的 Claude 跟別的頻道最多連續來回幾次（`off` 不限制）。
- `/commands`, `!commands`: 列出 Claude Code 的指令和 skill；`refresh` 重新整理選單。
- `/run`: 終端機頻道執行一行指令，可以從跑過的指令裡搜尋（見「終端機和檔案」）。
- `!help`: 列出機器人的指令。
- `!restart`: 重啟機器人。
- `!rollback`: 退回上一個正常版本。

## Claude Code 的指令和 skill
Claude Code 自己的 `/` 指令和 skill 也在選單裡，打 `/` 加幾個字就能搜尋。這些只能用 `/`，會送進 Claude。
- `/btw`：順便問一句，在討論串裡回答，可以追問。
- `/cc`：選單裡沒有的 Claude Code 指令也能執行。

## 接上你自己開的 Claude
- `/attach`：列出 tmux 裡的 Claude Code，點一下就開頻道接上，並貼出之前的對話。
- `/detach`：頻道不再跟著，對話照跑；之後在封存的頻道打字可以接回。
- `/rename`：頻道和對話一起改名。

## 終端機和檔案
- **終端機頻道**：設定卡選 💻，每則訊息是一行指令，輸出貼回來；`/run` 找跑過的指令。
- `/files`：檔案瀏覽器（只能看）；設定卡選 📂 是一直開著的檔案頻道。
- **回覆裡的檔案**：Claude 提到的檔案下面有 📂，點了直接打開。

## 出狀況時
- **沒反應**：`/screen` 看畫面，卡住就 `/key Escape`，再重發。
- **方向錯了**：`/stop`，再說要怎麼改。
- **對話亂了**：`/reset`。
- **tmux 出問題**：`!mode print` 切到備用的 print 模式。
- **新版壞掉**：3 分鐘沒上線會自動退回，也可以 `!rollback`。

## 秘密
- token 和 SSH 私鑰不讓任何 Claude 讀；貼到 Discord 的金鑰、token 會遮成 `[REDACTED]`。

## 其他頻道
{{> channels}}
