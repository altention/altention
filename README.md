# Altention

Run your AI coding agent's conversations from Discord. Every channel is backed by a long-lived conversation running
in tmux on your own machine: you type on your phone, and the agent runs commands, reads and writes files, and looks
after your other machines.

The one agent supported today is [Claude Code](https://claude.com/claude-code).

[繁體中文說明](README.zh-TW.md)

> **Experimental (0.x).** Altention drives Claude Code through tmux, Claude Code's hooks and its conversation files,
> none of which Claude Code promises to keep stable. A Claude Code update can break part of it until it is fixed
> (see [Known limitations](#known-limitations)). Interfaces and settings formats may change before 1.0.

## What it does

The core (enough on its own):

- **One conversation per channel.** Conversations live in tmux, so restarting or redeploying the bot does not
  interrupt them, and you can `tmux attach` from a computer to take over.
- **Attach a conversation you started yourself.** A Claude Code already running in your tmux can be attached to a
  channel with `/attach` and released with `/detach`.
- **Questions, plan approvals and permission prompts become buttons.** Subagents and workflows show up as threads.
- **Terminal channels** (a shell) and a **file browser**.
- **Channels talk to each other.** A conversation can post a report to another channel, talk to the conversation
  behind it, or keep a "board" message updated in place (`altention dc post`, `altention dc read`,
  `altention dc notify`).
- **The bot can change itself.** In its development channel you describe a feature; it edits its code, runs the
  tests and redeploys. A supervisor rolls back to the last version that started cleanly.

Optional plugins (all bundled, each can be turned off):

- **chatlog**: copies every machine's Claude Code conversations into a git repository as readable Markdown.
- **progress**: reads the conversation logs on a schedule, sorts the work into projects and keeps each
  project's latest status.
- **usage**: keeps a board with your subscription limits and each machine's usage.
- **status**: a board with the state of every machine and channel.
- **planner**: timed reminders that wake a channel's conversation with a message.
- **council**: a multi-perspective "council" skill for decisions.

How often or at what time each plugin runs can be changed in the instance's settings (`plugins.<plugin>.schedules`),
and a single schedule can be turned off; just tell Claude.

The engine's skills are linked into `~/.claude/skills`, and the sync makes a missing link again within 15 minutes. To
go without a plugin's skill, turn that plugin off (`disabled_on`); a deleted link comes back.

The usage and status boards are posted to the channel whose ID is in `usage.channel` and `status.channel` in the
instance's `plugins.json`; the install guide makes `#dashboard` and writes its ID there. To move them, put another
channel's ID in those settings. Without one, the board is not posted and the system check says so.

The install guide asks at the start whether you want planner's daily briefs, and when: a morning brief (08:00 unless
you pick another time), an evening review (22:00) and a weekly review (Sundays at 21:00). They are items in the
secretary's own schedule file (`reminders.json`), so later you just tell the secretary to change a time or turn one
off. Each wakes the Claude in `#secretary` (the channel ID in `planner.channel`), whose folder holds its instructions and the short-term plan, and
uses your subscription. If you do not want them, turn planner off (`disabled_on`); that also stops the reminders in
other channels' schedules.

**Several machines** are optional (advanced): one machine runs the bot; conversations on the others can be attached over SSH.

## Requirements

- Linux or macOS
- Python 3.8 or later, git, tmux
- cron (the `crontab` command). Minimal installs and containers may lack it: `sudo apt install cron` (Debian,
  Ubuntu), `sudo dnf install cronie` (Fedora), `sudo pacman -S cronie` (Arch); macOS has it.
- On Debian and Ubuntu, `python3-venv` for the bot's Python environment: `sudo apt install python3-venv`
- `~/.local/bin` in your `PATH` (the installer puts the `altention` command there). Most Linux distributions have
  it; on macOS add `export PATH="$HOME/.local/bin:$PATH"` to `~/.zshrc` and open a new terminal.
- Claude Code 2.1.296 or later, logged in
- A Discord server of your own and a Discord application you create (the bot token)

One machine is enough. No SSH and no remote repository are needed for a single-machine setup; a private remote
repository is recommended as its backup (see [Backup and restore](#backup-and-restore)).

## Install

1. Get the engine:

   ```bash
   git clone https://github.com/q12we34rt5/altention.git ~/altention/engine
   ```

   (Over SSH: `git clone git@github.com:q12we34rt5/altention.git ~/altention/engine`.)

   Or from an archive downloaded from the [releases page](https://github.com/q12we34rt5/altention/releases) (an archive install never
   updates itself); put the archive's file name in place of `<archive>`:

   ```bash
   mkdir -p ~/altention/engine && tar xzf <archive> -C ~/altention/engine --strip-components=1
   ```

2. Start Claude Code in it and let it guide you:

   ```bash
   cd ~/altention/engine && claude
   ```

   Then say "help me install", or type `/install`. The guide asks a few questions: your time zone, which optional
   plugins, whether the engine updates itself (`core.update`), the permission mode of the bot's conversations
   (`auto`, recommended; `manual`; or `bypassPermissions`), and how many hand-overs between the bot's Claudes
   without you before the bot pauses and asks (`dcbot.talk_limit`: 10, recommended; 30; or 0 for no limit). It
   walks you through
   creating the Discord application and inviting the bot, and runs the installer and the checks with you.

   **The bot token never goes through the conversation.** When the guide gets to Discord, run this in your own
   terminal:

   ```bash
   ~/altention/engine/install.sh discord
   ```

   It asks for the token without echoing it, finds the rest itself (see below), and then installs everything.

Without the guide:

1. <https://discord.com/developers/applications> → **New Application**, any name.
2. **Bot** page: under **Privileged Gateway Intents**, turn on **Message Content Intent**; turn **Public Bot** off
   (it is on by default: anyone who has the application's ID could add your bot to their own server); **Save
   Changes**. If Discord refuses with "Private application cannot have a default authorization link", set
   **Installation → Install Link** to **None**, save, and come back.
3. In your own terminal, run `~/altention/engine/install.sh discord`. When it asks, press **Reset Token** on the
   **Bot** page and paste the new token straight in. With the token it reads the application's ID and its owner
   (you; if a team owns it, you pick yourself from the team), says what is still to change on the Bot page, and
   prints a link that adds the bot with the scopes and the 14 permissions it needs. Open the link and pick your
   server (a new, empty one is best). It waits until the bot is in (it asks which server if there are several) and
   writes the token, your user ID and the server's ID to `~/.config/dcbot/env` (mode 600). Then it goes straight on
   to install everything this machine needs (the same as `./install.sh`); wait until its last line says it is done.

Then:

```bash
cd ~/altention/engine
./install.sh             # everything this machine needs (install.sh discord already ran it; safe to run again)
python3 ops/doctor.py --fix   # the system check; --fix prints how to fix each problem (it changes nothing)
python3 dcbot/tools/dcctl guide   # once the bot is online: post the user guide to #guide
```

`install.sh` exits 0 when done, 1 when something failed, 2 when it needs you to do something (the `todo` lines say
what; run it again afterwards). It is safe to run again at any time.

The system check is `ops/doctor.py` (also `altention doctor`). It is not Claude Code's own `/doctor`, which checks
Claude Code itself. In Discord, ask Claude in any channel to run the system check.

**The first time the bot comes online**, it creates `#audit` and `#chat`. `#audit` gets its online message, and later
deploy results and roll-back notices. `#chat` is a ready conversation whose working folder is the instance: type
there to start. A channel you open yourself gets the settings card first. For a channel where the bot works on its
own code, open one with any name (for example `#bot-dev`) and, on its card, pick 🤖 Claude conversation, then
📁 選工作目錄 (working folder) → the engine folder → ✅ 用這個資料夾, then 🛠️ 交給機器人管 (or a new tmux) for where it
runs. The three channels' names can be changed in the instance's `plugins.json`:
`{"dcbot": {"channels": {"audit": "…", "chat": "…", "guide": "…"}}}`.

The bot's user guide is [dcbot/GUIDE.md](dcbot/GUIDE.md). The install guide posts it to `#guide` once the bot is
online; without the guide, run `python3 dcbot/tools/dcctl guide` yourself. Run it again after an update to bring the
channel in line.

**A guided tour.** At the end, the install guide opens `#tutorial`, where Claude walks you through the main features
one at a time: buttons, 📋, a new channel, channels talking to each other, the boards and a reminder. It asks first
and you can put it off. `/tour` in any channel starts it again. It uses a little of your subscription.

## Words used here

- **engine**: this repository. The core and the bundled plugins; no personal data.
- **core**: the part that is always there: the bot, shared settings, machine management.
- **plugin**: a feature you can leave out. Bundled ones are in the engine; external ones are your own repositories;
  local ones live in your instance.
- **instance**: your private settings and data (knowledge, memory, machine list, plugin settings). A git
  repository, best kept private. See [examples/instance](examples/instance/) for a complete one with made-up values.

Everything lives under one root by default (`ALT_ROOT` moves it):

```
~/altention/
  engine/      the engine (this repository)
  instance/    your instance
  plugins/     external plugins
  data/        plugin data (conversation logs, the progress summaries, …)
```

## Backup and restore

Your memory, knowledge, settings, conversation records, progress and the secretary's plan are kept on your machine.
Each of the four can be backed up to a **private** git repository of its own, pushed about every 15 minutes:

- `instance`: memory, knowledge, settings and the machine list (`altention-instance`)
- `logs`: the conversation records, every conversation word for word with secrets masked: your most private data
  (`altention-logs`)
- `progress`: where each project stands (`altention-progress`)
- `planner`: the secretary's short-term plan, reminders and schedule (`altention-planner`)

The install asks about it after making the instance (you can say "later"). To set it up any time, ask Claude in any
channel to set up the backup, or create the empty private repositories yourself and run, in the engine:

```bash
python3 ops/remote.py set instance git@github.com:you/altention-instance.git   # and logs, progress, planner
python3 ops/remote.py status                                                   # each one, its last push
```

`set` refuses a repository that anyone can read without logging in, the engine's own repository, and one that holds
something other than this kind of data. Every push goes through a pre-push hook that checks the first of these
again; the system check (`altention doctor`) shows each backup, fails on a public one, and says "沒辦法確認" when it
could not tell (offline, or a host reached only over SSH: those are pushed anyway).

Not backed up: the bot's token (`~/.config/dcbot/env`; on a new machine `install.sh discord` asks for a new one), the
bot's channel state (which channel holds which conversation: not yet), the usage board's history
(`~/.local/share/usage`: it starts again from zero) and Claude Code's own conversation files (the conversation
records are their readable copy).

**On a new machine**, clone the engine as in [Install](#install), start Claude Code in it and choose 「從備份還原」
(restore from the backups) when `/install` asks: it does the steps below with you and asks none of the setup
questions again (they would write over the settings that came back). By hand, in the engine:

```bash
python3 ops/instance.py join git@github.com:you/altention-instance.git   # the instance back, with the other three's addresses
python3 ops/instance.py names                                              # the machine names it knows
python3 ops/instance.py take-name old-laptop                               # only if the old machine will not be used again
./install.sh discord                                                       # a new token; then the install clones the records, progress and the secretary's folder back
python3 ops/doctor.py
```

The machine needs read access to your private repositories first (`gh auth setup-git`, or an SSH key). Some settings
are kept by machine name: which plugins are turned off on a machine, where plugins run, each machine's folder of
conversation records. `take-name` gives this machine the old one's name so that they apply here; it refuses while the
old machine still answers over SSH, and once this machine has a name. Use it only when the old machine is gone for
good (a laptop that is merely switched off does not answer either): two machines with one name write into one
folder. With several machines, change that machine's SSH aliases in `machines.json` to the new machine's (and
commit the instance), or doctor and apply keep trying the old address. If `install.sh` says `todo core.git-user`, set
`git config --global user.name` and `user.email`.

Known limit: a repository made public after its last check is still pushed to for up to an hour (the hook keeps a
"private" answer that long).

## Several machines (advanced)

> Multi-machine setups are what the authors use every day, but installing them from scratch has not been
> verified on clean machines for this release. Start with one machine; add others once it works.

List each machine with its SSH aliases and roles in the instance's `machines.json`, then from the bot machine:

```bash
python3 ops/apply.py host-b    # make host-b match the list: install what is missing, remove what is not listed
python3 ops/doctor.py          # the system check, every machine
```

- The bot runs on one machine at a time. Conversations in tmux on the other machines can be attached with
  `/attach`: the bot types over SSH and that machine's hooks send the events back.
- `altention dc post|read|notify` work on every machine; away from the bot machine they go over SSH.
- Put long-running services on machines that are always on. A machine that sleeps (a laptop) catches up when it wakes.

## Layout

| Folder | What it is | How it runs |
|---|---|---|
| `dcbot/` | the Discord bot, its supervisor, `dcctl`, `dchook`, `dcremote` | the supervisor is started by cron (at boot and checked every 5 minutes) |
| `altention/` | the shared library: settings, instance, machine list, scheduler, sync, the Claude Code connector | imported by the rest |
| `bin/` | `altention` (short: `alt`): one entry point (`doctor`, `apply`, `plugins`, `dc post`, `dc read`, `dc notify`) | called by you or by a conversation |
| `ops/` | `install.py`, `apply.py`, `doctor.py`, `plugins.py`, `instance.py` | by hand, or by the installer |
| `chatlog/`, `progress/`, `usage/`, `status/`, `planner/`, `skills/council/` | the bundled plugins, each with a `plugin.json` | hooks and schedules from `plugin.json` |

More: [docs/architecture.md](docs/architecture.md) (how the pieces fit), [docs/gotchas.md](docs/gotchas.md)
(pitfalls), [dcbot/CLAUDE.md](dcbot/CLAUDE.md) (how the bot changes itself).

## Security

Read [SECURITY.md](SECURITY.md) before installing. In short:

- **Whoever can post as you in your Discord server can run commands on your machines.** Turn on two-factor
  authentication and use a server of your own.
- Conversations the bot opens use the permission mode you choose (each channel can choose its own):
  - `auto` (recommended): Claude Code judges each action itself. Safe ones run; risky ones (deleting outside the
    working folder, force-pushing, running a script downloaded from the network, changing startup settings, …) are
    blocked, and Claude takes another way or stops and explains, without asking you first. Only after several blocks
    does it start asking, and then the question is a button in Discord. Discord does not yet show that something was
    blocked.
  - `manual`: every action that needs permission is asked first, as buttons in Discord. Safest, most presses.
  - `bypassPermissions`: never asks and never blocks; your Discord account is as good as a shell on the machine.

  The buttons are read off the screen; see SECURITY.md for what they do not guarantee.
- The token stays in `~/.config/dcbot/env` (mode 600). A guard hook stops every Claude Code on your machines from
  reading, searching or naming it and your private SSH keys; it is best effort, and SECURITY.md says what it does not
  cover. Keys and tokens in what the bot posts are masked first.
- The engine does not update itself by default (`core.update: "notify"`: a notice in Discord, once per release, and
  the system check and the status board tell you of a new release). `"release"` follows release tags on its own; SECURITY.md says what that trusts.
- Plugins are not sandboxed. Install only plugins you trust.

## Known limitations


- **Claude Code only.** Other agents are planned (see the roadmap). The conversation log records Claude Code only.
- **A backup repository made public is still pushed to for up to an hour** after its last check (see
  [Backup and restore](#backup-and-restore)).
- **Tested with Claude Code 2.1.288 and 2.1.296.** 2.1.299: its screens are checked and it is in daily use (its hook
  events and conversation files are not yet in the test samples). 2.1.302: its screens are checked, including the permission
  question that counts down to an automatic denial in auto mode. Other versions may work but have not been checked;
  newer ones may change the screen (see the next item).
- **It reads Claude Code's screen for part of its work** (whether the input box is ready, menus, the startup dialogs).
  A Claude Code update that changes the screen can stop messages from being typed until Altention is fixed. The rule
  throughout is "not recognised = do nothing and say so": the bot refuses and shows you the screen rather than
  guessing. Replies, progress notes, questions, plans and subagents come from hooks and conversation files and are not
  affected by screen changes. A few cases are not yet covered that way (for example a busy conversation can look idle,
  so `!stop` may say there is nothing to stop, and a changed permission dialog could be taken for the input box: see
  SECURITY.md); the roadmap's first item lists them. Before any menu button (a number, Enter, typed text) presses
  a key, the bot reads the screen again and does nothing if the question or its options changed; if the machine does
  not answer, it does not press and the buttons stay. There is no version check or
  repair guide yet: if something stops working right after a Claude Code update, please open an issue with your
  Claude Code version.
- **One owner.** The bot obeys a single Discord user.
- **The interface is in Traditional Chinese** for now. English is planned.
- **tmux is required** for the bot's conversations and for `/attach`.
- **The secret guard is a best-effort string match.** It cannot see a relative path after a `cd` in the same command,
  a search over your whole home folder, or a path put together at run time. A command whose text names a protected
  path in full (`~/…`, `$HOME/…`, an absolute path) is refused in any folder, even when the path is only text to print or post; in a conversation started in your home
  folder, a relative look-alike (a grep pattern, an `echo`) is refused too. Put such text in a file first. The refusal
  says why and tells Claude to ask you.
  Commands that use a key (`ssh -i`, `ssh-add`, `ssh-keygen -y`) are allowed. See SECURITY.md.
- **The usage board reads your subscription limits, and that uses your subscription.** It is on by default
  (`"usage": {"subscription_limits": false}` in the instance's `plugins.json` turns it off); the rest of the usage
  board is updated either way.
  - Once an hour it runs `claude -p` with your subscription and sends the smallest possible request (haiku):
    about 24 a day, each about US$0.001 of API-equivalent usage, counted against the 5-hour limit.
  - That request loads none of your settings, hooks, MCP servers or tools, runs in an empty temporary folder and
    leaves no conversation file.
  - The install guide asks your plan and writes it, for example `"usage": {"plan": ["Pro", 1]}` (or
    `["Max 5x", 5]`, `["Max 20x", 20]`; Team and Enterprise: as many Pro allowances as your limit is worth). Left
    out, the board says the plan is not set and shows the percentages without the Pro multiples.
  - To stop it: set it to `false`, or turn the usage plugin off. Removing the line does not: not set means on.
  - The fields it reads are not documented by Anthropic; they follow what Claude Code 2.1.299 returns. If the format
    changes the board says it could not read them (it never guesses a number), and they may be unreadable while the
    limit is used up.

## Roadmap

The first public version (October 20) runs Claude Code inside tmux and talks to it through Claude Code's hooks plus reading the terminal screen. That is how the system has been used day to day, so it is the most tested path. The items below are planned for after the first release, each with the reason it waits.

### Next

1. **Staying reliable when Claude Code updates.** Claude Code updates often, and some updates change its screen. The first version already refuses to type or press keys on a screen it does not recognize, but a few situations still need work:
   - deciding whether a conversation is busy from hooks first, so a changed screen cannot make it look idle (and `!stop` always works while a turn runs);
   - never typing while a permission dialog is open, known from a hook rather than from the screen;
   - handing an unrecognized startup screen to you (`!screen`, `!key`) instead of closing the window after 90 seconds;
   - not closing an idle conversation whose Claude Code still has child processes running;
   - the system check counting "screen not recognized" refusals, so a breakage is noticed within minutes;
   - a repair guide (`REPAIR.md`) for fixing the connector after an update, and a system check warning when the installed Claude Code version has not been verified.
   - Why it waits: each of these touches the typing path every conversation goes through, so each needs a rehearsal on real Claude Code before it ships. Until then, if something stops working right after a Claude Code update, please open an issue with your Claude Code version.
2. **Multi-machine setups, verified from a clean install.** Running conversations on other machines over SSH works and is used daily, but it has not yet been installed from scratch by following this README. It is marked "advanced" until that is done.
3. **Driving Claude Code without reading the screen** (headless / stream-json mode, or the Agent SDK).
   - Why it matters: it removes the whole class of breakage in item 1 for conversations the bot opens itself.
   - Why it waits: it changes how every conversation runs, and it gives things up: attaching to the same conversation in tmux, the native `/btw`, and keeping conversations alive across bot restarts all have to be redone. We also want to settle how headless use is billed and supported before making it the default.
4. **Plugin extension points**: interactive messages (buttons and menus a plugin creates), plugin-defined `/` and `!` commands, redaction rules declared by plugins, event subscriptions, and `plugins.py deploy`.
   - Why it waits: no real plugin has exercised these interfaces yet, and we do not want to publish an API we would have to break. Until then, a plugin can add commands and scheduled jobs through `plugin.json`, or you can fork.
5. **More for agents talking to each other** (the inter-channel protocol): a structured result for every message sent (delivered, queued, not delivered and why), stable exit codes, a reply command that carries the original message ID, read receipts showing which agents have read a message, and `category/name` shown whenever two channels share a name.
   - Why it waits: it is a larger design that needs the extension points above. Commands that exist in the first version will keep working.
6. **Answering permission prompts through Claude Code's `PermissionRequest` hook** instead of keypresses, and other smaller hardening (a fallback for `/btw`, clearer warnings when a message seems to have started a background conversation).
7. **Finishing the connector boundary**: moving the remaining typing, side-question and status/compaction logic behind the connector interface, and a capability declaration (what an agent can report: started, received, busy, background tasks, resume).
   - Why it waits: no visible change for Claude Code users; it is groundwork for other agents.

### Later

8. **Other agents** (first candidate: Codex CLI). The connector interface is internal and may change until a second agent uses it.
9. **Conversation log and progress summaries for agents other than Claude Code.** In the first version the conversation log (`chatlog`) records Claude Code only.
10. **More bundled plugins** moved to the plugin layout, some as examples to copy.
11. **Learning features**: quizzes and learning channels.
12. **A redesigned file-browsing channel** (UI to be designed first).

## Contributing

[Issues](https://github.com/q12we34rt5/altention/issues) and pull requests are welcome. Before sending code:

- Run the tests the bot runs before it deploys: `dcbot/deploy.sh` lists them (the bot's own suite and those of
  `altention`, `chatlog`, `bin`, `ops`, `tools`, `progress`). Shared code uses the standard library only and must run on
  Python 3.8.
- Run `tools/privacy-scan HEAD`: no machine names, accounts, channel IDs or private URLs in the engine. Personal
  values belong in the instance.

Security problems: please report them privately, as described in [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE)
