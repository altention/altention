# 設定卡和終端機頻道

從 `dcbot/CLAUDE.md` 的「架構」搬來，原文沒改（2026-11-19）。路徑相對於 `dcbot/`；接頭指 `altention/connectors/claude_code/`。

- `bot/setup.py`：新頻道的設定卡。頻道從沒開過對話時（`setup.configured()` 為假），使用者第一則訊息（或擁有者剛建好頻道時，從稽核紀錄判斷）會先貼卡片，訊息暫存到選好為止。選「開新的 tmux」或「加到現有的 tmux」會在頻道狀態寫 `home`（使用者 tmux 的 socket、session、視窗名稱），`tmux_bridge.ensure_home()` 用 `tmuxio.open_window()` 在使用者的 tmux 開視窗跑 Claude（`--settings` 和環境變數照舊，所以 hook 走機器人自己的那套，不經過路由表）。這種視窗是機器人開的：`close()` 會 `kill-pane`，下次說話再開回來並接回對話；不會因閒置被關掉。使用者自己開的視窗（`/attach` 接上的，沒有 `home`）機器人永遠不關。
- 設定卡的機器選單（只給 Claude 對話）：選別台機器時 `home` 多一個 `host`，`socket` 是那台使用者的 tmux（`dcremote info` 問來的），`cwd` 是那台的絕對路徑。`ensure_home()` 用 SSH 開視窗，argv 不帶 `--settings`（那台沒有這個檔），開好馬上 `routes.bind_any()`，讓那台的全域 hook 照路由表回報，跟 `/attach` 的窗格一樣；`wait_ready()` 對別台機器也接受「畫面出現輸入框」，因為 SessionStart 可能比路由表先到。終端機頻道也一樣（見下面）。
- `/attach` 在還沒設定的頻道（設定卡還在）裡選對話時，`attach_here()` 把那個頻道直接接上（`attach_one(into=ch)`，不搬分類），收掉設定卡、送出暫存的訊息；其他情況照舊在專案分類開新頻道。
- 終端機頻道也可以開在別台機器：`home.host` 時 rc 檔和 done 檔在那台（`dcremote termrc`、`dcremote done`，共用的 `bot/termrc.py` 只用標準函式庫），tmux 操作一律帶 `tmuxio.on(pane)`。
- `bot/terminal.py`：終端機頻道（設定卡選 💻，頻道狀態 `kind: "terminal"`）。視窗裡跑使用者自己的 shell，啟動時用我們的 rc（zsh 用 `ZDOTDIR` 指到 `state/term/<頻道>/`，先載入使用者原本的設定，再加一個 precmd hook；bash 用 `--rcfile`），hook 每次出提示符號就把上一個指令的結束碼寫進 `done` 檔，所以「多一行 = 指令跑完」。使用者的 zsh 可能開了 `noclobber`，所以 hook 要用 `>>|`、檔案要先建好。輸出從指令開始時游標那一行抓到現在（`capture-pane -S`，含捲動歷史）。訊息路由在 `main.on_message`：終端機頻道只攔機器人自己的 `!` 指令（`BUILTINS`），其他都送進 shell。窗格帶 `shell: True`，`tmuxio.alive()` 只確認它還在。
