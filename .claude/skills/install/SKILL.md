---
name: install
description: First-time installation of Altention on this machine, for a new user who has just cloned or unpacked the engine and asks to install or set it up ("install Altention", "幫我安裝", "/install"). Not for an installation that already runs (use ops/doctor.py and the repair notes there), not for changing an existing setup, and not for other projects.
---

# Installing Altention

You are helping someone install Altention for the first time: a Discord bot that keeps one Claude Code
conversation per channel, running in tmux on this machine. Talk to them in the language they write in. Keep each
step short, say what you are about to run before you run it, and stop at the first thing that fails: read its
line, fix that, then go on.

Version 1 installs on **one machine**. Several machines work, but setting them up is an advanced topic: point the
user to the README's "Several machines" section after this one works, and do not do it here.

## Rules for the whole session

- **The bot token never goes through this conversation.** The user types it into `install.sh --token` in their own
  terminal. If they paste a token into the chat anyway, tell them at once to reset it (Developer Portal → their
  application → Bot → Reset Token), do not use or repeat it, and continue with the new one typed in the terminal.
- Never read, print or edit `~/.config/dcbot/` (where the token lives), and do not check it yourself either
  (`ls`, `stat`): a guard refuses any command of yours that names that path. `install.sh` and doctor check that
  the file is there; rely on their lines.
- Change nothing in the engine folder (this repository). Everything personal goes into the instance
  (`~/altention/instance` unless `ALT_INSTANCE` says otherwise).
- `install.sh` and `ops/doctor.py` can be run again at any time; prefer rerunning them to working around them.

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

## 2. Ask the five questions version 1 needs

First say in one sentence that the bot's interface (its messages, cards and boards in Discord) is in Traditional
Chinese for now, and that English is planned (the README says the same). Do not offer a language choice.

Then ask them. Do not write these questions yourself: run (in `ENGINE`)

```bash
python3 ops/plugins.py questions --setup --json
```

It prints the five questions, in order, each with `header`, `question`, `multiSelect` and `options`, which go into
AskUserQuestion as printed, under "The printed questions" below; it fills in this machine's time zone and says so
itself when the engine was unpacked from an archive and so never updates itself nor says a new release is out. One call holds at most four: ask
1 to 4 in the first call, then 5 in a second one, together with the per-part questions below when they chose to
turn some off (still at most four per call; more in a third). If AskUserQuestion is not available, run it without
`--json` and ask in text with those same words.

`key` and `values` are for you, never shown: the setting each answer goes into in step 3, and the value of each
option, in the same order as `options`. They are:

1. `core.timezone`: the zone. An answer typed in "other" is a zone name: check it exists
   (`/usr/share/zoneinfo/<name>`) and ask again if not.
2. `plugins`: `keep` (keep everything) or `some` (turn some off: the per-part questions come next).
3. `core.update`: `notify` (only tells them, on the status board and in doctor, when a new release is out) or
   `release` (updates itself to each new release, tags `vX.Y.Z`, after a syntax and load check).
4. `dcbot.default_permission`: `auto`, `manual` or `bypassPermissions` (each channel can be set differently later).
5. `dcbot.talk_limit`: `10`, `30` or `0` (no limit). Each channel can be set differently later with `/talklimit`.

If they chose the second option (關掉一些, turn some off), ask about the parts next. Do not write these questions
yourself: run (in `ENGINE`)

```bash
python3 ops/plugins.py questions --json
```

It prints one question per part (the core is not one), in the order to ask them, each with `header`, `question`,
`multiSelect` and `options`. Put each one's `header`, `question`, `multiSelect` and `options` into AskUserQuestion
as printed, under "The printed questions" below. Do not merge two parts into one question, and do not leave one
out. One call holds at most four; ask the rest in the next call, in the same order. `plugin` is for you: the part
each answer is about. If AskUserQuestion is not available, run it without `--json` and ask in text with those same
words.

**The printed questions** (`questions --setup` and `questions`) are the standard text, in Traditional Chinese.
If they write in Traditional Chinese, pass every word on unchanged. If they write in another language, translate it
into theirs **sentence by sentence**: every sentence stays, in the same order, with the same meaning, the header
and both option labels and descriptions too. Do not merge sentences, drop one, shorten, smooth or add anything; above
all keep the 「注意：要靠…」 sentence and the half-sentences on needs in the options. Parts are named by their
translated title, never by their name (`chatlog`). The permission modes' labels (`auto（建議）`, `manual`,
`bypassPermissions`) stay in English, as Claude Code's own screen shows them; only 「（建議）」 is translated. For example, progress's question in English:

- header 「進度」 → "Progress"
- question 「進度：每 10 分鐘讀新的對話紀錄，用 Claude 整理每個專案做到哪裡。會用訂閱額度。注意：要靠對話紀錄；保留進度，
  對話紀錄也會一起保留。要保留嗎？之後想開，跟 Claude 說一聲就好。」 → "Progress: every 10 minutes it reads the new
  conversation records and has Claude sum up where each project stands. It uses your subscription. Note: it relies
  on Conversation records; keeping Progress keeps Conversation records too. Keep it? To turn it on later, just tell
  Claude."
- 「保留」「自動整理每個專案的進度（對話紀錄也會一起保留）」 → "Keep": "Sums up each project's progress
  automatically (Conversation records are kept too)"
- 「關掉」「少了進度，不會自動整理專案做到哪裡，也沒有 /report 匯報」 → "Turn off": "No Progress; where each project
  stands is not summed up, and there is no /report"

Then check what they kept against what each part needs (`python3 ops/plugins.py list` prints it as
`（需要：…）` under the part): a part they kept that needs one they turned off keeps that one on
too (progress needs chatlog: progress reads chatlog's records). Do not turn it off, and tell them in one sentence,
for example 「進度要靠對話紀錄才能整理，所以對話紀錄我先留著；兩個都不要的話，跟我說把進度也關掉。」 doctor shows a
warning for a part left on without what it needs.

## 3. Make the instance and write the answers

```bash
cd "$ENGINE" && python3 ops/instance.py init   # the instance: a private git repo with no remote, ~/altention/instance
```

Then edit `<instance>/plugins.json` (it is JSON: keep it valid). The settings are **nested objects**, not dotted
keys: `core.timezone` means `"core": {"timezone": …}`. A key written flat (`"dcbot.default_permission"`) or in
the wrong object (under `"plugins"`) is read as not set, and the bot then falls back to never asking. Keep what
`init` wrote and add to it, so that it looks like this (their own values):

```json
{
  "format": 1,
  "core": {"language": "…", "timezone": "Europe/Berlin", "update": "notify"},
  "dcbot": {"default_permission": "auto", "talk_limit": 10},
  "plugins": {"<name>": {"disabled_on": ["<this machine>"]}},
  "sync": {"interval": "15m", "dirs": {}}
}
```

- `core.timezone`: the zone they chose.
- `core.update`: `"notify"` or `"release"`.
- `dcbot.default_permission`: the mode they chose. First run `claude --help` and check that `--permission-mode`
  lists that value (it needs Claude Code 2.1.296 or later). If it is not listed, do not write it: tell them to
  update Claude Code (`claude update`) or choose another mode, then come back to this.
- `dcbot.talk_limit`: `10`, `30` or `0` (no limit), written as a number, not a string.
- `plugins`: one entry per part they turned off, `"<name>": {"disabled_on": ["<this machine>"]}`, where
  `<this machine>` is what `python3 -c "from altention import config; print(config.this_machine())"` prints
  (run it in `ENGINE`). Nothing turned off: leave `plugins` out.

Then read each value back the way the engine reads it (run in `ENGINE`); each must print what they chose:

```bash
python3 -c "from altention import config as c; print(c.setting('core.timezone'), c.setting('core.update'), c.setting('dcbot.default_permission'), repr(c.setting('dcbot.talk_limit')))"
```

Check it reads: `python3 ops/plugins.py show` lists each part as `on` or `disabled` the way they chose. Until
the bot's settings exist (step 5), the parts that run on the bot's machine only show `unknown`: that is expected
here. Commit the change in the instance (`git -C <instance> commit -am "settings from the install"`).

## 4. The Discord application (the user does this; you guide)

Walk them through it one step at a time and wait for each "done":

1. <https://discord.com/developers/applications> → **New Application**, any name.
2. **Bot** page: **Reset Token** and keep the token for step 5 (not in this chat). Under **Privileged Gateway
   Intents**, turn on **Message Content Intent**. Turn **Public Bot** off (it is on by default: anyone who has
   the application's ID could then add this bot to a server of their own). Save. If Discord refuses that because a
   private application cannot have a default authorization link, open the **Installation** page, set **Install
   Link** to **None**, save, and turn Public Bot off again. They can still add the bot themselves in step 3.
3. **OAuth2 → URL Generator**: give them the block below **exactly as it is**, inside a code block. Do not copy
   the names into your own sentence, reorder, translate, shorten or drop any line: the names are what Discord's
   page shows (copied by hand once, Manage Threads and Pin Messages went missing and a name came out mistyped).

   ```text
   SCOPES（勾這 2 個）
   bot
   applications.commands

   BOT PERMISSIONS（勾這 14 個）
   View Channels
   Manage Channels
   View Audit Log
   Send Messages
   Send Messages in Threads
   Create Public Threads
   Manage Threads
   Manage Messages
   Pin Messages
   Embed Links
   Attach Files
   Read Message History
   Add Reactions
   Use Application Commands
   ```

   Under it, add one line: some versions of the page have no Pin Messages box (it is part of Manage Messages
   there), so they skip it if it is not there. For you, to say only if they ask: `dcctl pin` uses Pin Messages for
   the pinned boards; View Audit Log lets the bot see that a new channel was opened by them, so it can greet it at
   once (without it, the channel's first message does that).

   Then they open the generated URL and add the bot to their own server (a new, empty server is best: the bot
   makes its own categories and channels). A box left unticked is not fatal: doctor and the bot say what is
   missing, and it can be granted later in the server's settings.
4. In Discord: **User Settings → Advanced → Developer Mode** on. Right-click their own name → Copy User ID;
   right-click the server → Copy Server ID. They will type both in step 5.

Say plainly what this means: whoever controls their Discord account can run commands on this machine through
the bot. Then, by the mode they chose in step 2: with `bypassPermissions`, the conversations the bot opens run
commands without asking and nothing is blocked; with `auto`, risky actions are blocked without asking, and only after
several blocks does it ask, as buttons in Discord; with `manual`, every action that needs a permission comes up
as buttons. Buttons are to be read before pressing. Either way, recommend two-factor authentication on that account.

## 5. The token, typed in their own terminal

Ask them to open a terminal of their own (not this conversation; `install.sh --token` refuses anything but a
terminal) and run:

```bash
<ENGINE>/install.sh --token
```

It asks for the token (not shown while typed), their user ID and the server ID, checks them, and writes
`~/.config/dcbot/env` (mode 600). It says `changed dcbot.token` when done; wait for them to tell you so.

## 6. Install

```bash
cd "$ENGINE" && ./install.sh
```

Every line is `<status> <id> <text>`; the exit status is 0 (done), 1 (something failed) or 2 (something is left
to the user). For each line that is not `ok`, `changed` or `skip`:

- `todo`: the text says what the user has to do; do it with them, then run `./install.sh` again.
- `fail` / `unknown`: read the text, and the log it names (`~/.local/state/altention/install/last.log`) if the
  text is not enough; fix the cause, then run it again.

`todo dcbot.token … install.sh --token` means step 5 is not done yet.

## 7. Check

```bash
python3 ops/doctor.py --fix
```

`--fix` prints, under each problem, how to fix it (it changes nothing itself). Go through every problem with them,
using its fix line, until there is none. A `warn` is information (for example
`instance.remote`: the instance has no remote yet, which is fine on one machine).

Then in Discord: within a minute or two the bot shows as online and makes two channels: `#audit`, with a line
saying it is online (and its version; deploy results and roll-backs go there too), and `#chat`, a conversation
channel that is ready to use (its working folder is the instance; no setup card). Once it is online, post the
guide channel (in `ENGINE`; run it again if it says the bot or the server cannot be reached yet):

```bash
python3 dcbot/tools/dcctl guide
```

It makes `#guide` with how to use the bot.

Then the boards. Run `python3 ops/doctor.py` again and look for lines `看板頻道沒設定：<setting>`: a part that is
on and keeps boards in Discord (today the status and usage boards) but has no channel for them skips every round
and says nothing else. If there is any such line, make one channel for all of them and put its ID into each
setting named (in `ENGINE`):

```bash
python3 dcbot/tools/dcctl ensure dashboard --topic "系統狀態和用量看板"   # makes #dashboard unless there is one
python3 dcbot/tools/dcctl channels                                       # its ID: the number on the #dashboard line
```

More than one `#dashboard` listed: ask which. Write the ID, as a string, into `<instance>/plugins.json` for every
setting doctor named, nested like the others: `status.channel` is `"status": {"channel": "<ID>"}`. Read each back
(`python3 -c "from altention import config as c; print(c.setting('status.channel'))"`, one per setting named) and
run doctor once more: those lines are gone. The boards show up in `#dashboard` within a few minutes.

Doctor may also say `對話頻道沒設定：<setting>`: a part that wakes a Claude channel on a schedule has none (today
the planner: `planner.channel`). Do not just set it: **ask first** (AskUserQuestion, two options), because each
wake-up is a Claude turn in the secretary's own channel, `#secretary`, that uses their subscription, and none of it
was asked for yet. Read that
part's times from its `plugin.json` (`<engine>/<part>/plugin.json`, the `schedules` that send a `message` to the
channel: `when.at` and `when.days`, in the machine's time zone) and say each one; for the planner today: a
morning briefing every day at 08:00, an evening review every day at 22:00, a weekly review on Sundays at 21:00.
Say too that turning it off also turns off the reminders other channels write into their own schedule files.

- They want it: open the secretary's channel (in `ENGINE`):

  ```bash
  bin/altention dc post secretary --new-claude "$(python3 planner/remind.py --secretary)" "秘書：早報、回顧和提醒"
  python3 dcbot/tools/dcctl channels    # a few seconds later: its ID is the number on the #secretary line
  ```

  The first line makes the secretary's private folder (`<data>/planner`, 700, with its `CLAUDE.md`, which says
  what the briefing and the reviews are, and an empty `plan.md`, the short-term plan) and asks the bot for
  `#secretary`, a Claude in the bot's tmux working there (the bot says it in `#audit`). If `dcctl channels` has no
  `#secretary` line yet, run it again every few seconds, for about a minute; still none: read the bot's line in
  `#audit` (`bin/altention dc read audit 5`). If it says `#secretary 已經設定過了` (a channel of that name is set up
  already), ask them (AskUserQuestion) whether to use another name, e.g. `secretary-2`, and run the first line again
  with it. Put `#secretary`'s ID into
  `planner.channel` as a string, nested the same way (`"planner": {"channel": "<ID>"}`), read it back the same way
  and run doctor again. Reminders that name no channel go to `#secretary` too.
- They do not: turn the part off as in step 3, `"plugins": {"<part>": {"disabled_on": ["<this machine>"]}}`
  (keep any entries already there), check `python3 ops/plugins.py show` lists it as `disabled`, and run
  `./install.sh` again so its schedule lines are taken out. Doctor no longer says the line.

Commit the instance after these. No such lines: nothing to do here.

Then ask them to type a message in `#chat` to try it: the bot's Claude answers there. That is the end of the
install.

## Afterwards (tell them, briefly)

- To keep the instance safe, give it a remote. It holds their memory, knowledge and settings: it must be a
  **private** repository, never a public one:
  `git -C <instance> remote add origin <private repo URL> && git -C <instance> push -u origin main`.
- The usage board's subscription limits are off by default. Turning them on (`"usage":
  {"subscription_limits": true}`) costs one request an hour from their subscription; then also set `usage.plan`
  to their plan, as `[name, how many Pro allowances]`: `["Pro", 1]`, `["Max 5x", 5]`, `["Max 20x", 20]` (Team or
  Enterprise: their plan's name and its limit counted in Pro allowances). Without it the board assumes Max 20x.
- Other machines (servers, a second computer) can be joined to this setup: the README's "Several machines"
  section says how. Version 1 does not do it during the install, so always mention it here.
- A channel for working on Altention itself (a development channel, any name, e.g. `#bot-dev`) is opened only
  when wanted: make a new text channel; on its setup card press 「🤖 Claude 對話」, then 「📁 選工作目錄」, click
  through to the engine folder and press 「✅ 用這個資料夾」, then 「🛠️ 交給機器人管」.
- The boards' channel is the one whose ID is in `status.channel` and `usage.channel`; the briefing, the reviews and
  reminders that name no channel go to `#secretary` (`planner.channel`), whose instructions are the `CLAUDE.md` in
  its folder (theirs to change). To move them, put another channel's ID there.
- The three channels' names can be changed in the instance's `plugins.json`:
  `{"dcbot": {"channels": {"audit": "…", "chat": "…", "guide": "…"}}}`.
- Everything else: the README and `dcbot/GUIDE.md`.
- If something stops working later, `python3 ops/doctor.py` says what and how to fix it.
