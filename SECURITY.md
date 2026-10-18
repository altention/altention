# Security

Altention lets a Discord account drive Claude Code on your machines. Read this page before you install it.

## The one thing to understand

**Whoever can post as the owner in your Discord server can run shell commands on every machine Altention manages.**

- The bot obeys one Discord user only: the `OWNER_ID` in its settings file. Messages, buttons, menus and reactions
  from anyone else are ignored.
- Claude Code conversations the bot opens run with the permission mode you chose at install time (`auto`,
  recommended; `manual`; or `bypassPermissions`), and each channel can choose its own.
  - `auto`: Claude Code judges each action itself. Safe ones run; risky ones (deleting outside the working folder,
    force-pushing, running a script downloaded from the network, changing startup settings, …) are blocked, and
    Claude takes another way or stops and explains, without asking you. Only after several blocks does it start
    asking (a button in Discord). Discord does not yet show that something was blocked.
  - `manual`: every action that needs permission is asked first, as buttons in Discord.
  - `bypassPermissions`: never asks and never blocks; the owner's Discord account is as good as a shell.

  Wherever a question is asked, the owner's Discord account is what approves.
- The bot machine reaches your other machines over SSH (non-interactive, key-based). A compromise of the bot machine
  or of the owner's Discord account reaches them too.
- Not every instruction Claude follows comes from you. A conversation in one channel can send messages to another
  channel's conversation (`altention dc post`), and a web page or file Claude reads can carry instructions of its own
  (prompt injection). In `bypassPermissions` nothing stands between such text and a command. When two of the bot's
  Claudes hand over to each other `dcbot.talk_limit` times in a row without you (10 unless you chose otherwise; 0
  turns it off), the bot pauses them and asks you.

What follows from that:

- Turn on two-factor authentication for the owner's Discord account.
- Use a server of your own. Do not add people you would not give a shell to.
- Install only plugins you trust: a plugin's hooks and schedules run as your user on every machine it is placed on.
- Keep the instance repository (your settings, machine list, memory) private.

## Secrets and where they live

| Secret | Where | Protection |
|---|---|---|
| Discord bot token, `OWNER_ID`, `GUILD_ID` | `~/.config/dcbot/env` on the bot machine | file mode `600`; never in git; see "Keeping Claude away from the token" |
| SSH keys | your usual `~/.ssh` | Altention only adds an `Include` line to `~/.ssh/config` (backed up first) |
| Claude Code login | Claude Code's own files | not read or copied by Altention |

- Enter the token with `./install.sh discord` in your own terminal (it does not echo, does not pass through a Claude
  conversation, and is sent only to Discord's API), or write the file yourself and `chmod 600` it.
- **Never paste the token into a Discord message or a Claude conversation.** Conversations are kept on disk and may be
  synced (see "Conversation logs" below).
- If the token leaks: reset it in the Discord developer portal, write the new one with `./install.sh discord`, and
  restart the bot.

## Keeping Claude away from secrets

Claude Code runs on the same machine and as the same user as the bot, so the operating system alone does not stop it
from reading the token file. Altention adds a guard on top.

**What is protected**: the bot's settings folder (`~/.config/dcbot/`, which holds the token), private SSH keys
(`~/.ssh/id_*`, `~/.ssh/*key*`; public keys `*.pub`, `~/.ssh/config`, `known_hosts` and `authorized_keys` are not),
and any path you add in the instance's `plugins.json`:

```json
"core": {"secret_paths": ["~/.config/my-service/credentials.json"]}
```

(`~` for your home, `*` within a name, `**` at any depth.)

**How**: a `PreToolUse` hook (`dchook --guard`) runs before every tool call of every Claude Code on every machine
Altention manages: the bot's own conversations, the ones you started yourself and attached, and any other session of
your user. It refuses

- Read, Edit, Write and NotebookEdit on a protected file;
- Grep, Glob and LS on a folder that holds one (for example `~/.config` or `~/.ssh`, but not your home folder
  itself);
- a Bash command that names one, read the way the shell reads it: quotes, `~`, `~you`, `$HOME`, `${HOME}`, wildcards
  and `{a,b}` braces, and relative paths resolved from the conversation's folder. Commands that read into folders
  (`grep -r`, `rg`, `find`, `tar`, `zip`, `rsync`, `cp -r`, `ls -R`) are refused on a folder above a secret too.

Using a key is allowed: the `-i` argument of `ssh`, `scp` and `sftp`, keys loaded with `ssh-add`, and
`ssh-keygen -y -f`. When a call is refused, Claude is told which path it was and to ask you if it is really needed.
The bot's own conversations also carry the listed paths without a `*` in their name as Claude Code
`permissions.deny` rules, and the token file is mode 600.

Rehearsed on Claude Code 2.1.296 in `bypassPermissions`: reading the token or a private key, with Read or through a
shell command, from the home folder or a project folder, was refused, and nothing of it reached the API. Ordinary
tool calls went through. Each tool call takes about 35 ms longer.

**What it does not cover** (it is a best-effort match on what Claude asks for, not isolation):

- a relative path after a `cd` in the command (`cd ~/.config && cat dcbot/env`);
- a search over your whole home folder (Grep on `~`, `grep -r x ~`);
- a path put together at run time, or a program written to open the file;
- programs other than Claude Code, and anything that runs before Claude: plugins, cron lines and your own scripts are
  trusted code;
- a machine where the hook is not installed, or where you removed it or turned hooks off;
- Claude changing the guard's own settings: it can edit the instance's `plugins.json`, so in principle it can turn
  the guard off (closing this is planned after the first release).

It can also refuse too much: a command whose text names a protected path in full (`~/…`, `$HOME/…`, an absolute
path) is refused in any folder, even when the path is only text to print or to post to Discord. In a conversation
whose folder is your home, a relative look-alike (`echo .ssh/id_rsa`, a grep pattern) is refused too. Put such text in
a file first and use the file, or reword the command.

If the guard itself fails (it cannot load, it errors), the tool call goes through and the failure is written to a log
(at most once an hour): a broken guard never blocks your work. If its settings cannot be read, it uses the default
list. Turn it off entirely with `"core": {"secret_guard": false}`.

Treat it as protection against accidents and against a confused or misled model, not against someone who already
controls your account. Real isolation (running the bot as a separate system user) is planned after the first
release.

## What the permission buttons do not guarantee

In `manual`, and in `auto` once it has started asking after several blocks, when Claude Code asks for permission,
the bot reads the dialog off the terminal screen and posts its options as buttons.

- Before any menu button presses a key (a number, Enter, typed text; not Esc or the panel's keys), the bot reads the
  screen again and compares the question and its options (for a permission dialog, its title and command or file);
  if the dialog is no longer the one it posted (it was answered in the terminal, or another question replaced it),
  the button does nothing and says why. If the machine does not answer the read, nothing is pressed and the buttons
  stay.
- Whether a conversation is busy, and whether a dialog is waiting, are still read off the screen in the first
  version. If a Claude Code update changes the screen so that a dialog looks like the input box, **a message you send
  from Discord could be typed into the dialog, and Enter would pick its first option** (usually "Yes"). This has not
  happened, and nothing would warn you if it did. Knowing about waiting dialogs from Claude Code's hooks instead of
  the screen is the first item of the README's roadmap. Until then, after updating Claude Code, check that the bot
  still shows permission prompts as buttons before relying on it.

## What reaches Discord

Everything the bot posts lands on Discord's servers and stays in your server's history.

- Before posting, the bot passes Claude's words and its tools' output through the secret filter below: replies and
  in-progress notes, 📋 (the tool-call details of a turn), its expansions and overflow file, conversation histories
  (`/attach`, resuming, recaps), subagent threads, messages from other sessions, and the terminal's input echo and
  working lines. A message where something was masked ends with a short line saying how many places. A key cut off by
  the bot's length limit is masked from its first part.
- Anything not listed above is posted as it is, in particular `!screen`, screenshots (images of the terminal),
  `/btw` answers, and the excerpts of menus and permission dialogs (both read off the screen).
- The file browser refuses folders and files that usually hold keys and tokens (`~/.config`, `~/.ssh`, `~/.gnupg`,
  `~/.aws`, `.env`, `*.pem`, `*.key`, `id_rsa*`, `*credentials*`, `*token*.json`, …). It is a list of names, not a
  sandbox: a secret stored elsewhere under another name is shown.

The secret filter recognises common key formats (API keys of the major providers, GitHub, AWS, Slack and Discord
tokens, Discord webhook URLs, PEM private keys) and leaves git hashes, UUIDs and IDs alone. It is pattern based: a
password, an internal URL or a token in an unknown format is posted as is. A private key without its end line is
masked to the end of the message (more than needed, never less). The conversation logs use the same filter.

## Conversation logs

The `chatlog` plugin copies **every** Claude Code conversation of your user on each machine where it runs, including
ones that have nothing to do with Discord, into a git repository (`~/altention/data/logs`) and, if you set a remote,
pushes it. Turn it off on a machine with `plugins.chatlog.disabled_on`, or do not install it.

- Before writing, it runs the same secret filter. Anything the filter does not recognise is kept.
- Use a **private** remote, or none.

## Hooks installed on every machine

On machines where conversations can be attached or are logged, Altention adds hooks to `~/.claude/settings.json`.
They run for **every** Claude Code session of that user on that machine, including ones that have nothing to do with
Discord. They only record events and route them to the bot when the session is attached; they never send anything to
Discord on their own for a session that is not attached. `python3 ops/doctor.py` lists them.

The secret guard is one of these hooks, so it applies to every session too: a tool call in your own terminal that
touches a protected path (see "Keeping Claude away from secrets") is refused, with the reason. Add paths with
`core.secret_paths`, or turn the guard off with `core.secret_guard`, in the instance's `plugins.json`.

## Engine updates

Set by `core.update` in the instance's `plugins.json`:

- `"notify"` (**the default**, also when the setting is not there): the engine never pulls code by itself. It only
  checks whether a newer release (a `vX.Y.Z` tag) exists; if so, `python3 ops/doctor.py` reports
  `core.update-available` and the status board shows a line. You update when you choose.
- `"release"`: every machine moves to the newest release tag of its upstream (`origin`) on its own, never to an
  untagged commit. Before moving it checks that the new files compile and load; a release that fails is refused and
  reported, and the running version stays.
- `"branch"`: follows the branch you cloned, every new commit (for developers).

An engine installed from an archive (not a git clone) never updates itself; download the new release instead.

**The risk of `"release"` and `"branch"`: whoever can publish to that upstream can run code on all your machines
within a quarter of an hour** (with `"branch"`, whoever can push to the branch). If you cloned this repository
directly, that is this repository. To review updates before they run, point `origin` at a fork you control and tag
releases in it yourself.

## Self-modification

From its development channel the bot can change its own code and redeploy itself (`dcbot/deploy.sh`). A supervisor
rolls back to the last version that started cleanly. This is a feature, not a sandbox: the bot's Claude can change
anything your user can change.

## Reporting a vulnerability

Please report security problems privately through GitHub's "Report a vulnerability"
(<https://github.com/q12we34rt5/altention/security/advisories/new>) rather than in a public issue. Include the version (commit) and the steps to reproduce. We aim to reply
within a week.
