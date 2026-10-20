---
name: install
description: First-time installation of Altention on this machine, for a new user who has just cloned or unpacked the engine and asks to install or set it up ("install Altention", "幫我安裝", "/install"). Not for an installation that already runs (use ops/doctor.py and the repair notes there), not for changing an existing setup, and not for other projects.
---

# Installing Altention

You are helping someone install Altention for the first time: a Discord bot that keeps one Claude Code
conversation per channel, running in tmux on this machine. Talk to them in the language they write in. Keep each
step short, say what you are about to run before you run it, and stop at the first thing that fails: read its
line, fix that, then go on.

Only what has to happen in a terminal happens here: what the machine needs, the Discord application, and
`./install.sh discord`, which installs everything with safe defaults. **Ask no setting here** (time zone,
permission mode, the parts, the secretary, the plan, backups): the bot opens `#setup` in Discord once it is online,
and a Claude there asks them one at a time (C-48). Your part ends when they are in `#setup`.

Version 1 installs on **one machine**. Several machines work, but setting them up is an advanced topic: point the
user to the README's "Several machines" section after this one works, and do not do it here.

## Rules for the whole session

- **The bot token never goes through this conversation.** The user pastes it into `install.sh discord` in their own
  terminal. If they paste a token into the chat anyway, tell them at once to reset it (Developer Portal → their
  application → 機器人（Bot）→ 重設權杖（Reset Token）), do not use or repeat it, and continue with the new one
  typed in the terminal.
- Never read, print or edit `~/.config/dcbot/` (where the token lives), and do not check it yourself either
  (`ls`, `stat`): a guard refuses any command of yours that names that path. `install.sh` and doctor check that
  the file is there; rely on their lines.
- Change nothing in the engine folder (this repository). Everything personal goes into the instance
  (`~/altention/instance` unless `ALT_INSTANCE` says otherwise).
- `install.sh` and `ops/doctor.py` can be run again at any time; prefer rerunning them to working around them.
- When you speak of `ops/doctor.py` to them, call it the system check (系統檢查), with the command the first time
  (`altention doctor`), never just "doctor": Claude Code has its own `/doctor`, which is something else, and new
  users press that one.

`ENGINE` below is the folder this skill is in, two levels up from `.claude/skills/install`: the folder holding
`install.sh`. The README suggests `~/altention/engine`; any folder works.

## 1. Check what is needed

Run these and read the answers (do not install anything yourself; tell the user what is missing and how their
system installs it):

```bash
python3 --version        # 3.8 or later
git --version
tmux -V
claude --version         # Claude Code is installed
command -v crontab       # cron: the schedules go into the crontab
python3 -c "import ensurepip, venv"   # the bot's virtual environment (Debian/Ubuntu: python3-venv)
```

Missing cron: `sudo apt install cron` (Debian/Ubuntu), `sudo dnf install cronie` (Fedora), `sudo pacman -S cronie`
(Arch), and its service running (`systemctl enable --now cron`, or `cronie`; WSL and other systems without
systemd: `sudo service cron start`). The last line failing on Debian or Ubuntu: `sudo apt install python3-venv`.

`--version` does not tell whether Claude Code is logged in: if they have never used it on this machine, ask them
to start `claude` once in their own terminal, log in, and quit. Do not test it with `claude -p`.

## 1b. A new install, or back from the backups

First see whether this machine has an instance already (in `ENGINE`):

```bash
python3 -c "from altention import config; print(config.instance_dir() or '')"
```

- **It prints a folder**: this is not a first install. Write no setting: the ones there are theirs. Run
  `./install.sh` and `python3 ops/doctor.py --fix` and go through what they say (steps 6 and 7). That is all.
- **It prints nothing**: go on with step 4, for a new install and a restore alike: `#setup` asks first which one
  it is (in Discord, once the bot is up).

### Restoring from the backups

Do nothing of it here: go on with step 4 as for a new install. The restore is in Discord: `#setup` asks first
whether this is a new install or one back from the backups, and does the rest there (C-57). Only when they joined
their instance in a terminal before (`python3 ops/instance.py join <URL>`, the advanced way; it stays for that), the
instance is there already: step 1b's first case.

## 4. The Discord application (the user does this; you guide)

Only three things are set by hand on Discord's site; `install.sh discord` (step 5) finds everything else itself
(the application's ID, who owns it, the server) and prints the link that adds the bot. Walk them through it one
step at a time, in this order, and wait for each "done". No ID is copied by hand.

Discord's pages may be in Chinese or in English, so write each button and page as **中文（English）** when you
talk to them in Chinese, as below. A Chinese name marked 待核對 is not yet checked against Discord's Chinese pages:
still write it as 中文（English） to them, but do not say 待核對; if the two differ, the English is what to look for.
**Privileged Gateway Intents** and **Message Content Intent** are in English on the Chinese pages too: write them
in English only.

1. Open <https://discord.com/developers/applications> → **新建應用程式（New Application）**. In the window
   titled 「建立新的應用程式」 in Chinese, fill in **名稱（Name）** with any name, tick the terms, then
   **建立（Create）**.
2. In the left menu, **機器人（Bot）**. Under **Privileged Gateway Intents**, turn on **Message Content Intent**,
   then **儲存變更（Save Changes，待核對）**.
3. On the same page, turn **公開機器人（Public Bot）** off, then **儲存變更（Save Changes，待核對）**. It is on by
   default: anyone who has the application's ID could add this bot to a server of their own. If Discord refuses
   with "Private application cannot have a default authorization link": left menu **安裝（Installation）** →
   **安裝連結（Install Link）** → **無（None）** → **儲存變更（Save Changes，待核對）**, then back to
   **機器人（Bot）** and turn 公開機器人（Public Bot） off again. They can still add the bot themselves: step 5
   prints the link.

Say plainly what this means: whoever controls their Discord account can run commands on this machine through
the bot. How much the bot's conversations may do without asking is one of the questions in `#setup`; until then they
run in `auto` (risky actions are blocked, not asked), or `manual` when this Claude Code is too old for `auto`.
Recommend two-factor authentication on that account.

## 5. Connect Discord and install, in their own terminal

Leave the **機器人（Bot）** page open. Ask them to open a terminal of their own (not this conversation; it refuses anything
but a terminal) and run, copied as it is:

```bash
<ENGINE>/install.sh discord
```

Then tell them what it will do, in this order:

1. It says first that it will ask for the token and that typing it shows nothing.
2. Now, and only now, back on the **機器人（Bot）** page: **重設權杖（Reset Token）** → confirm with **是的，執行！
   （Yes, do it!，待核對）** (Discord may ask for their password or a two-factor code) → **複製（Copy，待核對）**, and
   paste it straight into the terminal, then Enter. Nothing appears while pasting; that is expected. The token is
   shown only once: if they lose it, 重設權杖（Reset Token） again.
3. It asks Discord with the token. If one of the three settings above is not right yet, it lists what to change
   (and also the 需要 OAuth2 代碼授權（Requires OAuth2 Code Grant） switch on the 機器人（Bot） page, or 公會安裝
   （Guild Install） under 安裝（Installation） → 安裝背景（Installation Contexts）, if those were changed); they fix it on the site, then press Enter in the terminal to check again.
4. If the application belongs to a team, it lists the team's members: they pick themselves (the bot listens to
   that one person only). Otherwise nothing is asked: the application's owner is them.
5. It prints a link (`https://discord.com/oauth2/authorize?...`) and waits. They open it in a browser, pick their
   server under **新增至伺服器（Add to Server，待核對）** (a new, empty server is best: the bot makes its own categories
   and channels; they need Manage Server there), **繼續（Continue，待核對）**, leave every permission ticked,
   **授權（Authorize，待核對）**, and pass the check if one comes up. If the bot is already in a server, there is no
   link: it uses that one, or asks which when there are several.
6. As soon as the bot is in, it writes `~/.config/dcbot/env` (mode 600), says `changed dcbot.token`, and goes
   straight on to install everything this machine needs (the same as `./install.sh`). Do not run `./install.sh`
   yourself meanwhile. Ask them to wait until it has finished, that is until the last line says 「安裝跑完了」 or
   「安裝沒跑完」, and to tell you then.

If it waits 10 minutes without the bot coming in, it asks: Enter waits again, `q` stops without writing anything.
Any `fail dcbot.token` line says what was wrong and that nothing was written, and the install is not run: run it
again (with a new 重設權杖（Reset Token） if the old one is gone).

## 6. What the install said

The install's lines were on their screen, not here: read them from its log (`~/.local/state/altention/install/last.log`,
the step lines and what the scripts printed). Every step line is `<status> <id> <text>`, and the last one is
`ok core.done …` (done, or done with things left to the user) or `fail core.done …`. One exception: when their
last line is 「安裝沒跑完：core.lock 沒成功」 (or `core.engine`, `core.instance`), the install stopped before it
began its log, and `last.log` is another run's: do not read it; ask them to paste the line before it (`fail
core.lock …` and so on) and go by that. Otherwise, for each step line in the log that is not `ok`, `changed` or
`skip`:

- `todo`: the text says what the user has to do; do it with them, then run `./install.sh` (in `ENGINE`) again
  yourself: the token is in by now, so nothing is typed.
- `fail` / `unknown`: read the text, and what the log says around it; fix the cause, then run `./install.sh` again.

`todo dcbot.token … install.sh discord` means step 5 is not done yet. When you run `./install.sh` yourself, its
exit status is 0 (done), 1 (something failed) or 2 (something is left to the user).

## 7. On to #setup

```bash
python3 ops/doctor.py --fix
```

`--fix` prints, under each problem, how to fix it (it changes nothing itself). Go through what is about this
machine (something missing, a token line) until none is left. Two are expected now and are not for here:
`core.setup` 「設定還沒做完：到 Discord 的 #setup」, and lines about board or secretary channels not set
(`看板頻道沒設定`, `對話頻道沒設定`): `#setup` makes those channels at its end.

Then in Discord: within a minute or two the bot shows as online and makes `#audit` (a line saying it is online, its
version) and `#setup`, whose Claude greets them and starts asking. Tell them to go to `#setup` and answer there, one
question at a time; they can stop halfway and come back. `#chat`, the channel for talking to Claude, opens when
`#setup` is done. That is the end of the install here.

If `#setup` does not appear within a few minutes: `bin/altention dc read audit 5` shows what the bot said; the
system check says whether it is running.

## Afterwards (tell them, briefly)

- Other machines (servers, a second computer) can be joined to this setup: the README's "Several machines"
  section says how. Version 1 does not do it during the install, so always mention it here.
- If something stops working later, ask Claude in any channel to run the system check (`altention doctor`): it
  says what is wrong and how to fix it. Not Claude Code's `/doctor`, which checks Claude Code itself.
