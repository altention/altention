# 插件的檢查指令（check）

plugin.json 裡寫了 `check` 的地方，都是「一個指令，跑一次就知道這件事在這台好了沒」：

- `needs[]` 的每一項：這台有沒有那個系統工具，例如 `"check": "command -v pdftoppm"`。
- `guide[]` 的每一步（v0.5.0 起）：使用者到外面做的那一步做好了沒，例如用 token 呼叫一次服務的驗證 API。

## 結束碼

- **0**：好了。
- **3**：這台檢查不了。例如引擎太舊、少了核心的某個檢查指令，或這一步不適用這台。
  - 有 `ask` 的步驟，改用 `ask` 問使用者；沒寫 `ask` 的，問一句「這一步做好了嗎？」，後面接 title（`needs[]` 用 name）。
  - 使用者的回答會記下來，跟 `check` 寫 `null` 的步驟一樣。下次還是先跑 check：回 0 就算好了，還是回 3 才照記錄，不再問。
  - 所以會回 3 的步驟，請寫上 `ask`。
- **其他非 0**：還沒好。#setup 的 Claude 會照 check 印出來的原因跟使用者說，所以失敗時請印一句看得懂的原因（stdout 或 stderr 都可以，最後幾行會被引用）。

逾時、或指令根本跑不起來，都算「還沒好」。

**舊引擎**：v0.5.0 以前的引擎只分 0 和非 0，3 也當成還沒好（`needs[]` 會照 `hint` 說）。插件要給舊引擎用時，回 3 的情況也要印出原因。

## 寫 check 的注意事項

- 只查，不改東西：check 會被重跑很多次（每次 `setup.py plugin-steps`、install、doctor），可能在每台都跑。
- 要快：幾秒內結束（`needs[]` 的 check 超過 30 秒就算還沒好）。要連網路的，自己設時限。
- 不要印秘密：要用 token 的檢查，讀 `~/.config/altention/secrets/<插件>/<名字>`，印出的原因裡不能有 token 的值。還沒存的話，印一句「還沒存」就好（#setup 會叫使用者跑 `altention secret set`）。
- 用 `{plugin}` 代表插件的資料夾，例如 `"check": "\"{plugin}/bin/check\" cf-token"`。
