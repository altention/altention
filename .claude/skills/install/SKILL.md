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

- **It prints a folder**: this is not a first install. Do not ask the questions of step 2 and do not run step 3:
  they would write over the settings that are there. Run `./install.sh` and `python3 ops/doctor.py --fix` and go
  through what they say (steps 6 and 7). That is all.
- **It prints nothing**: ask (AskUserQuestion), header 「安裝」, question 「這台要怎麼裝？」, options (label,
  description) 「新安裝」「第一次用 Altention，或想從頭開始」 and 「從備份還原」「換了電腦，或舊的壞了：從之前備份的
  private repo 拿回記憶、設定和紀錄」. 「新安裝」: go on with step 2. 「從備份還原」: follow "Restoring from the
  backups" below instead of steps 2 to 4.

### Restoring from the backups

Nothing is asked again and nothing in the instance is written: its settings (time zone, plugins, permission mode,
the boards' and the secretary's channels, the backup remotes) come back with it.

1. **This machine can read their private repositories.** The same ways as step 3b: `gh auth status >/dev/null
   2>&1; echo $?` (0: run `gh auth setup-git` once), else an SSH key (`ssh -T -o BatchMode=yes git@github.com`) or
   an HTTPS token stored in their own terminal, as described in step 3b B.2. Without it the clone below fails.
2. **The instance's URL**: ask them (its repository was `altention-instance` unless they named it otherwise; `gh
   repo list --json name,url` lists theirs when gh is in). Then, in `ENGINE`:

   ```bash
   python3 ops/instance.py join <URL>
   ```

   It says 「放好 pre-push hook」 and 「這台已接上」. A ⚠ line says why the hook did not go in, with the command.
3. **Which machine this one replaces.** The instance keeps some settings by machine name: which plugins are turned
   off on a machine, where plugins run, and each machine's folder of conversation records. Run
   `python3 ops/instance.py names --json` and ask (AskUserQuestion), header 「接替哪台」, question 「這台要接替哪一台
   舊機器？照機器名字記的設定（哪台關掉了哪些功能、對話紀錄寫進哪個資料夾）會變成這台的。」, one option per name
   (label: the name; description: where it is named, from `where`; when `only_in_settings` is true, say instead
   「只出現在設定裡：可能是打錯的，或是一台沒寫進機器清單的機器」), and last 「當成新的一台」「用這台自己的名字：照
   舊名字記的設定不會套到這台，之後要自己重設」. Ask even when there is only one name.
   - A name: before anything is written, ask (AskUserQuestion), header 「確認」, question 「<名字> 那台之後不會再
     開來用了嗎？兩台用同一個名字，對話紀錄會寫進同一個資料夾。連不上不代表它沒了（例如筆電只是沒開）。」, options
     「不會再用了」「它不會再用了；這台沿用它的名字」 and 「之後還會開」「這台用自己的名字」. Only on 「不會再用了」 run
     `python3 ops/instance.py take-name <名字>`. Exit status 1 and a line with 「沒有改」 give the reason:
     - that machine still answers over SSH: it is on, and must be stopped first;
     - the name is only in the settings: ask once more (AskUserQuestion) whether that really is the old machine;
       only on yes run it again with `--only-in-settings`;
     - this machine has a name already (「這台已經叫 X」, usually because `install.sh` ran before this step and
       wrote this machine's own name). If they are sure it replaces the old one, first check that this machine
       has written no conversation records under X: in `ENGINE`, `python3 -c "import sys; sys.path.insert(0,
       'ops'); import remote; print(remote.folder_of('logs'))"` gives the records' folder; nothing must be in its
       `X/` (no such folder, and `git -C <that folder> log --oneline -1 -- X/` prints nothing). Then keep the old
       name file, never delete it: `mv ~/.config/altention/host ~/.config/altention/host.bak`, and run
       `take-name` again, then `./install.sh` again. If there are records under X already, do not change it: say
       that this machine goes on under its own name.
     When the instance has a machine list (`machines.json`, several machines), its entry for that name still has
     the old machine's SSH aliases: change them to this machine's (`"local"` when this is the machine the others
     are managed from), commit and push the instance, or doctor and apply keep trying the old address. On 「之後還會開」, or a refusal they do not want to fix, go on as a new machine and tell them in one
     sentence what that means (the settings kept under the old name do not apply here).
4. **Discord**: the same application as before. Step 5 (`install.sh discord` in their own terminal; on the
   機器人（Bot） page of that application, 重設權杖（Reset Token）). If that application is gone, steps 4 and 5.
   The install it goes on to clones the conversation records, progress and the secretary's folder back from the
   addresses in the instance.
5. **Steps 6 and 7** as written, with two differences: the settings doctor names are already in the instance
   (write nothing it does not ask for), and the bot's channel state is not in the backups: the bot makes new
   `#audit` and `#chat`, and channels opened before need setting up again from their cards. Run
   `python3 planner/remind.py --secretary` once if they had the secretary: it uses the folder that came back.
6. If the install said `todo core.git-user`: `git config --global user.name …` and `git config --global
   user.email …` (their own commits fail without it).
7. Tell them what came back and what did not (the README's 「備份與還原」 lists it). The tour (step 8) only if they
   ask.

## 2. Ask the questions version 1 needs

First say in one sentence that the bot's interface (its messages, cards and boards in Discord) is in Traditional
Chinese for now, and that English is planned (the README says the same). Do not offer a language choice.

Then ask them. Do not write these questions yourself: run (in `ENGINE`)

```bash
python3 ops/plugins.py questions --setup --json
```

It prints the six questions (five, and the secretary's), in order, each with `header`, `question`, `multiSelect`
and `options`, which go into
AskUserQuestion as printed, under "The printed questions" below; it fills in this machine's time zone and says so
itself when the engine was unpacked from an archive and so never updates itself nor says a new release is out. One call holds at most four: ask
1 to 4 in the first call, then 5 and 6 in a second one; then, in a call of their own, the secretary's times (below)
when they chose to open it, then the per-part questions below when they chose to turn some off (at most four per
call; more in the next). If AskUserQuestion is not available, run it without
`--json` and ask in text with those same words.

`key` and `values` are for you, never shown: the setting each answer goes into in step 3, and the value of each
option, in the same order as `options`. They are:

1. `core.timezone`: the zone. An answer typed in "other" is a zone name: check it exists
   (`/usr/share/zoneinfo/<name>`) and ask again if not.
2. `plugins`: `keep` (keep everything) or `some` (turn some off: the per-part questions come next).
3. `core.update`: `notify` (only tells them when a new release is out: one Discord notice per release, and on the status board and in
   doctor) or
   `release` (updates itself to each new release, tags `vX.Y.Z`, after a syntax and load check).
4. `dcbot.default_permission`: `auto`, `manual` or `bypassPermissions` (each channel can be set differently later).
5. `dcbot.talk_limit`: `10`, `30` or `0` (no limit). Each channel can be set differently later with `/talklimit`.
6. `plugins.planner`: `on` (open the secretary, `#secretary`: its times come next) or `off` (the secretary's part,
   planner, turned off in step 3 like a part they turned off, reminders included). Asked here so that step 7
   opens its channel without asking again.

If they chose to open the secretary, ask its three times (in `ENGINE`), the same way, word for word:

```bash
python3 ops/plugins.py questions --times --json
```

Each one's `key` is `planner.briefings.<name>` and each value is the time written as it is (`{"at": "08:00",
"days": "daily"}`; the weekly one `{"at": "21:00", "days": ["sun"]}`). A time typed in "other" is written the same
way: 24-hour `HH:MM`; for the weekly one also the day, as `mon` … `sun` in a list. Ask again if it is not a time.

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
- `planner.briefings` (if they open the secretary): the three times,
  `"planner": {"briefings": {"morning": {…}, "evening": {…}, "weekly": {…}}}`. Read back: `python3 -c "import sys;
  sys.path.insert(0, 'planner'); import remind; print(remind.briefing_times())"` prints the three; an error says which
  one to ask again. Not opening the secretary: no `planner.briefings`, and planner goes into `plugins` below.
- `plugins`: one entry per part they turned off (planner too, if they do not open the secretary), `"<name>": {"disabled_on": ["<this machine>"]}`, where
  `<this machine>` is what `python3 -c "from altention import config; print(config.this_machine())"` prints
  (run it in `ENGINE`). Nothing turned off: leave `plugins` out.

Then read each value back the way the engine reads it (run in `ENGINE`); each must print what they chose:

```bash
python3 -c "from altention import config as c; print(c.setting('core.timezone'), c.setting('core.update'), c.setting('dcbot.default_permission'), repr(c.setting('dcbot.talk_limit')))"
```

Check it reads: `python3 ops/plugins.py show` lists each part as `on` or `disabled` the way they chose. Until
the bot's settings exist (step 5), the parts that run on the bot's machine only show `unknown`: that is expected
here. Commit the change in the instance (`git -C <instance> commit -am "settings from the install"`).

## 3b. Backup (they decide; "later" is fine)

Their memory, settings, conversation records, progress and the secretary's plan live only on this machine until
they have a backup. Ask, the same way as in step 2 (do not write these questions yourself; run in `ENGINE`):

```bash
python3 ops/plugins.py questions --backup --json
```

It prints two questions. Ask the first one (`backup`: `now` or `later`) alone; ask the second (`backup.items`,
multiple choice: the values are the names below) only after `now`. Pass both on as printed, under "The printed
questions" above; in the second, keep the conversation records' description whole (it says they are verbatim). A
part they turned off is not offered. An empty answer to the second is "later".

**Later**: say in one sentence that the system check will keep reminding them that there is no backup, and that
the README's 「備份與還原」 section says how to set it up any time (this step, run again). Go on to step 4.

**Now**: one **private** repository for each one they chose, named `altention-<name>`: `altention-instance`,
`altention-logs`, `altention-progress`, `altention-planner`. Never a public one, never one with anything in it but
an earlier backup of the same kind (`ops/remote.py` checks both and refuses).

First, whether the GitHub command line is there and logged in (only the exit status is read):

```bash
gh auth status >/dev/null 2>&1; echo $?     # 0: logged in
gh api user --jq .login                     # their account, when it is
```

**A. gh logged in.** List the names as `<account>/altention-<name>` and ask (AskUserQuestion) whether to use them
or other names. For each one:

```bash
gh repo view <account>/<repo> >/dev/null 2>&1; echo $?   # 0: one of that name exists already
gh repo create <account>/<repo> --private --disable-issues --disable-wiki
```

One that exists already is never created again, emptied or deleted: ask whether to use it (only an earlier backup
of the same kind passes the checks) or another name. Never `--public`, never `--add-readme`. Then, once:

```bash
gh auth setup-git      # git (and so cron and the hooks) push over HTTPS with gh's login, no ssh-agent needed
```

The URL of each is `https://github.com/<account>/<repo>.git`.

**B. No gh, or not logged in.** Ask (AskUserQuestion) whether they want to install gh and log in
(`gh auth login`, in their own terminal; their system's package manager installs `gh`): that is the shortest
way, and then A. Otherwise they make the repositories by hand:

1. For each one: on GitHub, the **+** menu at the top right → **New repository**, the name `altention-<name>`,
   **Private** (check it, whatever the page shows first), tick nothing (no README, no .gitignore, no license: it
   has to be empty), **Create repository**. They paste each repository's URL back to you.
2. How git logs in (AskUserQuestion, two options):
   - **SSH**: run `ssh -T -o BatchMode=yes git@github.com` and read what it says ("successfully authenticated":
     a key works; its exit status is 1 even then). No key: they run `ssh-keygen -t ed25519` in their own terminal
     and leave the passphrase **empty** (cron cannot type one in), then print the public key themselves (`cat` of the
     `.pub` file it names) and add it on GitHub under Settings → SSH and GPG keys → New SSH key. Never read, print
     or list a key file yourself: the guard refuses any command of yours that names one. The URL form is
     `git@github.com:<account>/<repo>.git`.
   - **HTTPS with a token**: a fine-grained personal access token for these repositories only, permission Contents:
     read and write. They store it in their own terminal, never in this conversation: `git config --global
     credential.helper store`, then `git ls-remote <URL>` typing their account as the user and the token as the
     password (it is kept in `~/.git-credentials`, mode 600).

Not GitHub (GitLab, Gitea, a server of their own with `git init --bare`): take the URLs they give; the same checks
run.

Then, for each one, in `ENGINE`:

```bash
python3 ops/remote.py set <instance|logs|progress|planner> <URL>
```

It checks first (not the engine's repository, nobody reads it without logging in, empty or a backup of the same
kind, reachable the way cron reaches it) and writes nothing when a check fails. Read its lines:

- `✗`: it says which check and why; fix that (made public by mistake: make it private; not empty: a new empty one;
  cron cannot reach it: the SSH key has a passphrase, or the HTTPS login is not stored) and run it again.
- `?`: it could not be told whether the repository is public (offline, or a host reached only over SSH). That does
  not stop it; tell them to make sure it is private.
- `· … 還不是 git repo：安裝那一樣時會照設定接上`: expected for the conversation records, progress and the
  secretary now; the install (step 5) and the secretary's channel (step 7) clone or attach them.
- The instance is pushed at once.

Last, `python3 ops/remote.py status` shows each one, and the instance is committed (the remotes went into its
`plugins.json`): `git -C <instance> commit -am "backup remotes"`. The sync pushes it within 15 minutes.

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
the bot. Then, by the mode they chose in step 2: with `bypassPermissions`, the conversations the bot opens run
commands without asking and nothing is blocked; with `auto`, risky actions are blocked without asking, and only after
several blocks does it ask, as buttons in Discord; with `manual`, every action that needs a permission comes up
as buttons. Buttons are to be read before pressing. Either way, recommend two-factor authentication on that account.

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

## 7. Check

```bash
python3 ops/doctor.py --fix
```

`--fix` prints, under each problem, how to fix it (it changes nothing itself). Go through every problem with them,
using its fix line, until there is none. A `warn` is information (for example `backup.instance` 「沒有備份」 when
they chose "later" in step 3b). A `fail` on a `backup.` line (a repository anyone can read) is never left: its
fix says how to make it private.

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

More than one `#dashboard` listed: ask which. Then fill it **in this order**, so that it has content at once and
what needs them ends up lowest (the usage boards first, then the status boards: a board stays where it was first
posted). Write each ID as a string into `<instance>/plugins.json`, nested like the others (`usage.channel` is
`"usage": {"channel": "<ID>"}`), and read it back (`python3 -c "from altention import config as c;
print(c.setting('usage.channel'))"`). Only the settings doctor named: a part that is off has none.

1. `usage.channel` (if named). Write it and read it back. The usage board also reads their subscription limits,
   on by default (`usage.subscription_limits`). Always ask their plan (AskUserQuestion), header 「方案」, question
   「你的 Claude 訂閱是哪個方案？用量看板會顯示訂閱額度用了多少：為了讀額度，每小時用 haiku 送一個最小的請求，會算進
   5 小時的額度。不想花這個額度就選最後一個。」, options (label, description) 「Pro」「顯示訂閱額度（每小時一個最小的
   haiku 請求）」, 「Max 5x」 and 「Max 20x」 with the same description, and 「不顯示訂閱額度（不花額度）」「看板不顯示額度用了
   多少，也不送讀額度的請求；之後想看再開」. Ask them; never read their Claude login files to find it out. Write `usage.plan` as `[name, how many Pro allowances]`:
   `["Pro", 1]`, `["Max 5x", 5]`, `["Max 20x", 20]`; Team or Enterprise (typed in 「其他」): their plan's name and its
   limit counted in Pro allowances (ask if they do not know; leave `usage.plan` out rather than guess). 「不顯示訂閱
   額度」: write `"subscription_limits": false` and no plan. Read both back
   (`python3 -c "from altention import config as c; print(c.setting('usage.plan'),
   c.setting('usage.subscription_limits'))"`). Then post the usage boards once (in `ENGINE`):

   ```bash
   python3 usage/collect.py
   ```

   It can take a minute or two. It prints nothing when it went through; anything it prints says why not: fix that
   and run it again before going on. Check that they are there: `bin/altention dc read dashboard 12` shows messages
   starting `📌 **訂閱額度**`, `📌 **這個帳號**` and so on (allow it a few seconds).
2. `status.channel` (if named). First run one round of the system check that the boards read (it may send a
   notification for a problem it finds; say so first):

   ```bash
   python3 ops/doctor.py --watch
   ```

   Then write `status.channel`, read it back, and make the status boards. A round makes one new board, the next one
   only once the bot has posted it, so run it again a few seconds apart until all four are there (about six to
   eight runs):

   ```bash
   python3 status/boards.py
   bin/altention dc read dashboard 12   # done when 頻道, 機器和機器人, 需要注意的事 and 在等你的事 are all there
   ```

   Its scheduled round (every two minutes) does the same once the setting is in: it does no harm if it comes in
   between.

Then run doctor once more: the `看板頻道沒設定` lines are gone.

Then the secretary, as they answered in step 2 (do not ask again). Until it is done, doctor says
`對話頻道沒設定：planner.channel` (a part that wakes a Claude channel on a schedule has none).

- They chose to open it: open the secretary's channel now (in `ENGINE`):

  ```bash
  bin/altention dc post secretary --new-claude "$(python3 planner/remind.py --secretary)" "秘書：早報、回顧和提醒"
  python3 dcbot/tools/dcctl channels    # a few seconds later: its ID is the number on the #secretary line
  ```

  The first line makes the secretary's private folder (`<data>/planner`, 700, with its `CLAUDE.md`, which says
  what the briefing and the reviews are, and an empty `plan.md`, the short-term plan), puts the briefing and the
  two reviews into its `reminders.json` at the times they chose (`planner.briefings`; a line from `remind` on the
  screen says what it could not do), and asks the bot for
  `#secretary`, a Claude in the bot's tmux working there (the bot says it in `#audit`). If `dcctl channels` has no
  `#secretary` line yet, run it again every few seconds, for about a minute; still none: read the bot's line in
  `#audit` (`bin/altention dc read audit 5`). If it says `#secretary 已經設定過了` (a channel of that name is set up
  already), ask them (AskUserQuestion) whether to use another name, e.g. `secretary-2`, and run the first line again
  with it. Put `#secretary`'s ID into
  `planner.channel` as a string, nested the same way (`"planner": {"channel": "<ID>"}`), read it back the same way
  and run doctor again. Reminders that name no channel go to `#secretary` too. Check the times
  (`python3 planner/remind.py --check` lists `morning`, `evening` and `weekly` with theirs) and tell them in one
  sentence when each comes, and 「之後跟秘書說就能改時間或關掉」.
- They chose not to: planner was turned off in step 3, so doctor does not say the line. If it does, the entry is
  missing: `"plugins": {"planner": {"disabled_on": ["<this machine>"]}}` (keep any entries already there), check
  `python3 ops/plugins.py show` lists it as `disabled`, and run `./install.sh` again so its schedule line is taken
  out.

Commit the instance after these.

## 8. The tour

Last, a guided tour of the bot, in a channel of its own so that `#chat` stays theirs (in `ENGINE`):

```bash
bin/altention dc post tutorial --new-claude "$(python3 -c 'from altention import config; print(config.instance_dir())')" "導覽：一步步試機器人的主要功能"
python3 dcbot/tools/dcctl channels    # a few seconds later: a #tutorial line
```

If `dcctl channels` has no `#tutorial` line yet, run it again every few seconds, for about a minute; still none,
read the bot's line in `#audit` (`bin/altention dc read audit 5`). If it says `#tutorial 已經設定過了`, use another
name (`tutorial-2`) in both lines. Then start it:

```bash
bin/altention dc post tutorial "安裝完成了。請用 tour skill 帶使用者導覽（從開場那一題開始）。"
```

Tell them to go to `#tutorial`: it opens with a question whether to start; they can choose 「之後再說」, and `/tour`
in any channel brings it back. Otherwise they can just type in `#chat`, where the bot's Claude answers. That is the
end of the install.

## Afterwards (tell them, briefly)

- No backup yet (they chose "later" in step 3b): step 3b can be done any time, and the README's 「備份與還原」
  section says the same. Each backup is a **private** repository, never a public one; `python3 ops/remote.py set`
  checks that, and refuses a public one.
- The usage board reads their subscription limits (one smallest request an hour on haiku, counted against the
  5-hour limit) unless they said no in step 7. To turn it off later: `"usage": {"subscription_limits": false}` (taking
  the key out does not: not set means on). A plan changed later goes into `usage.plan` the same way as in step 7.
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
- If something stops working later, ask Claude in any channel to run the system check (`altention doctor`): it
  says what is wrong and how to fix it. Not Claude Code's `/doctor`, which checks Claude Code itself.
- The tour again: `/tour` in any channel.
