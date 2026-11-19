# 斜線指令

從 `dcbot/CLAUDE.md` 的「架構」搬來，原文沒改（2026-11-19）。路徑相對於 `dcbot/`；接頭指 `altention/connectors/claude_code/`。

- `bot/slash.py`：註冊在 Discord 的 `/` 選單（只在這個伺服器）。內建指令走 `Bot.command()`，跟 `!` 指令是同一份程式；`/usage`、`/context`、`/compact` 則是打進 tmux，跟文字訊息 `/usage` 一樣。註冊過的指令是「互動」不是訊息，3 秒內要回應，所以一律先 defer 再用 follow-up 回覆。指令內容有變才會重新向 Discord 註冊（`state/slash-synced` 記著上次的內容）。沒註冊的 `/` 指令照舊當文字送。加新的內建指令時，`!` 版本一定要有，`/` 版本看需要。
- `bot/catalog.py`：Claude Code 有哪些 `/` 指令和 skill。用 `claude -p --output-format stream-json` 的第一行（init）取得，拿到就把行程砍掉，不會真的發請求；說明文字讀 SKILL.md。結果快取在 `state/catalog.json`，每天更新。`slash.add_catalog()` 把它們註冊進 Discord 選單（skill 加上 `catalog.BUILTIN` 列的內建指令；`catalog.HIDDEN` 是不適合由使用者啟動的參考型 skill），其餘的走 `/cc`。機器人自己的指令名稱優先。
- **不能從 Discord 直接打的斜線指令**（3b 2.1b）：哪些指令、哪一種害處（代號）由接頭說（`commands.REFUSED`、`commands.CONFIRM`、`command_of(text)`）；每種害處的字在 `tmux_bridge.HARM`，`BLOCKED`／`CONFIRM` 兩張表由 `harm_tables()` 組出來（內容跟原本寫死的逐字相同，測試釘住）。Claude Code 多一個危險指令時改接頭那兩張表；代號沒有對應的字時照代號顯示，仍然會擋或先問。
