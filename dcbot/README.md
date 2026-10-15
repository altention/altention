# dcbot

只聽擁有者一人的 Discord 機器人，背後是 Claude Code：每個頻道一個持續的對話，可以管理 Discord、操作各台機器，並在開發頻道（工作目錄是引擎的頻道）修改自己。

- 位在引擎的 `dcbot/`；部署與搬家由 `ops/apply.py` 管理（機器清單的角色 `dcbot`）
- 秘密設定：`~/.config/dcbot/env`（`DISCORD_TOKEN`、`OWNER_ID`、`GUILD_ID`），不在 repo 裡
- 自我修改流程與架構：見 [CLAUDE.md](CLAUDE.md)
- 每個頻道的 Claude Code 活在 tmux 裡：`tmux -L dcbot attach -t <名稱>` 可以直接接手（名稱看 `!status`）；備用的單次模式用 `!mode print`
- 改壞了：`supervisor.py` 會自動退回上一個正常版本；Discord 上也可以用 `!rollback`
