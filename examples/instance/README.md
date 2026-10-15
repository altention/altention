# Example instance

This folder shows what a complete Altention **instance** looks like: the private half of an installation (your
machines, channels, settings, memory). The engine (this repository) is the public half. Every value here is made up
(`host-a`, `alice`, `100000000000000001`, `example.com`).

You do not copy this folder to install. `./install.sh` makes a minimal instance for you at `~/altention/instance`
(a git repository with no remote); the install guide (`/install`) fills in the settings as you answer its questions.
Come back here when you want to know what a file is for, or to add a machine by hand.

The engine finds the instance through `~/.config/altention/instance` (one line: the folder), or the `ALT_INSTANCE`
environment variable, else `~/altention/instance`.

## Files

| File | What it holds | Who writes it |
|---|---|---|
| `machines.json` | Every machine: SSH aliases (`"local"` for the machine itself), home folder, hostname, roles (`dcbot`: runs the bot; `dcattach`: its tmux conversations can be attached from Discord), whether it is always on, free-form notes. | You, then `python3 ops/apply.py <machine>` |
| `plugins.json` | Settings of the core and of each plugin, and where each plugin runs (`plugins.<name>.on`, `disabled_on`). | You, or the install guide (`install.sh --set`) |
| `ssh_config` | SSH aliases used in `machines.json`. Included from `~/.ssh/config`. No keys. | You |
| `privacy.json` | Extra words `tools/privacy-scan` must never find in the engine (your school, company, project names). Only matters if you send changes back upstream. | You |
| `CLAUDE.md` | What every Claude Code conversation opened in the engine folder should know about *this* installation. | You, or Claude |
| `CLAUDE.global.md` | Your global instructions. Combined with the engine's part into `~/.claude/CLAUDE.md` on every machine. | You, or Claude |
| `dcbot/CLAUDE.md` | What the bot's development channel should know about this installation. | You, or Claude |
| `dcbot/guide/channels.md` | The "other channels" part of the guide that `dcbot/tools/dcctl guide` posts to the guide channel. | You, or Claude |
| `knowledge/` | Long-lived notes every machine and every channel reads (how your systems work, pitfalls). | Claude |
| `memory/` | Claude Code's memory, shared by every machine. `MEMORY.md` is the index. | Claude |
| `.gitattributes` | `MEMORY.md` is appended to from several machines: merged, never a conflict. | `install.sh` |

`install.sh` makes only `plugins.json`, `memory/`, `knowledge/`, `README.md` and `.gitattributes`; the other files
here are made when you want them (nothing of the engine needs `CLAUDE.md` or `CLAUDE.global.md` to be there).

## `plugins.json` keys used here

A name like `usage.plan` is a path into nested objects: `{"usage": {"plan": …}}`, as in `plugins.json` above. Do
not write it as one key with a dot in its name (`"usage.plan": …`): such a key is not read.

- `core.language`: the bot's language. Only `zh-TW` exists in the first version.
- `core.timezone`: for schedules ("every day at 08:00") and the dates on the usage board.
- `core.update`: `"notify"` (default: only tell of a new release), `"release"` (update to release tags on its own) or
  `"branch"` (follow the cloned branch). Read `SECURITY.md` before turning updates on.
- `core.secret_paths`: more files no Claude Code may read, search or name in a command, besides the bot's token and
  private SSH keys (`~` home, `*` within a name, `**` any depth). `core.secret_guard: false` turns the guard off. See
  `SECURITY.md`.
- `core.notify_channel`: where `altention dc notify` posts when no channel is given (`category/name` or a channel ID).
- `core.guide`: words used when the bot writes its help text (display names of machines, example channels).
- `dcbot.channels`: names of the bot's own channels, if you want others than `audit`, `chat` and `guide`
  (for example `{"chat": "console"}`).
- `dcbot.talk_limit`: how many times two of the bot's Claudes may hand over to each other without you before the bot
  pauses and asks you (0: no limit; 10 when not set). A channel can set its own with `/talklimit`.
- `dcbot.default_model`, `dcbot.default_permission`: what new channels' conversations are opened with. Each channel
  can choose its own. The install guide offers `auto` (recommended: risky actions are blocked rather than asked
  about), `manual` (every action needing permission is asked as buttons) and `bypassPermissions` (never asks, never
  blocks: read `SECURITY.md` first). The README's security summary says more.
- `sync.interval`: how often the instance and plugin data are committed and pushed.
- `sync.owns`: engine folders that live in the instance instead; the engine's copy becomes a link to it. `memory` is
  always included; a new instance lists `knowledge`.
- `sync.dirs.<name>.remote`: where a plugin's data repository is pushed (`chatlog`: conversation logs; `progress`:
  the progress summaries). Leave it out to keep the data on this machine only: progress then reads chatlog's folder
  on its own machine, so it needs `chatlog`'s remote only when it runs on another one. Use private repositories.
- `usage.channel`, `status.channel`, `planner.channel`: the channel ID each plugin posts to.
- `planner.schedule_files`: the schedule file(s) planner sends reminders from (a path or a list). Leave it out to use
  `<data>/planner/reminders.json`, made on first use; `python3 planner/remind.py --where` prints the one in use.
- `usage.plan`: your subscription as `[name, how many Pro allowances]`, e.g. `["Pro", 1]`, `["Max 5x", 5]`,
  `["Max 20x", 20]` (Team and Enterprise: as many Pro allowances as your limit is worth). Only the subscription
  limits part uses it; left out, it assumes Max 20x.
- `usage.subscription_limits`: `true` to show your subscription limits on the usage board (one cheap request an
  hour, counted against your limits; see the README's known limitations). Off by default.
- `plugins.<name>.on`: the machine a single-machine plugin runs on. `plugins.usage.sources`: machines whose usage
  is collected; leave it out to collect only the machine usage runs on (each other machine you list needs
  `usage/install-node.sh` run there once). `plugins.<name>.disabled_on`: machines where an every-machine plugin is turned off.

## Sharing an instance between machines

An instance is a git repository. To use it on several machines, give it a **private** remote, then on each other
machine run `python3 ops/instance.py join <remote>` followed by `./install.sh`. The sync job commits and pushes
`knowledge/` and `memory/` every `sync.interval`; `machines.json` and `plugins.json` you commit and push yourself.
