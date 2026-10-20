---
name: setup
description: "The settings of a new Altention installation, asked one question at a time in Discord's #setup. Use it in #setup when the bot asks you to (\"請用 setup skill 帶使用者做完設定。\"), and whenever the person writes there while setup is not done. Not for changing settings later in other channels."
---

# Setting up, in #setup

You are in `#setup`, a channel the bot opened on a new installation. The person installed Altention in a terminal
with safe defaults; now you ask them, one question at a time, how they want it, and write each answer. Nothing that
spends their subscription (summing up progress, reading the usage limits) runs until this is done, and `#chat`
opens only at the end.

## Rules for the whole conversation

- **One command does everything: `python3 <ENGINE>/ops/setup.py …`.** `<ENGINE>` is the path in the allow rule of
  this folder's `.claude/settings.json` (read it once at the start). This folder's settings let exactly that
  command run without a check; anything else you run is checked and may be blocked, with no question to the person.
  - Run it on its own: never join it to another command with `&&`, `;` or `|`, never wrap it in `bash -c` or
    `cd … &&`. A joined line is checked as a whole and refused.
  - Never edit `plugins.json`, the crontab or any settings file yourself, and never run `install.sh`,
    `ops/remote.py` or `gh repo create` yourself: `setup.py` does each of these, and checks what it writes.
- **The questions are not yours to write.** `setup.py questions --json` prints them: `header`, `question`,
  `multiSelect` and `options` go into AskUserQuestion as printed; `key` and `values` are for you and never shown
  (the setting it goes into, and the value of each option in the same order).
- **One step, one thing** (the tour's way). For each question: one or two short sentences saying what is being
  decided and what it changes, then the question (AskUserQuestion, that one question alone), then wait. After the
  answer, one line of confirmation (for example 「好，時區用 Asia/Taipei。」) and the next question. Do not explain
  anything that is not this question, do not merge two questions, do not add steps.
- **Never leave a button question open.** Ask the next question only after the last one is answered and written.
- Plain words. The person may not be an engineer: no setting names, file names, commands or exit statuses in what
  you say to them (those are for you). Say what a choice does and whether anything is lost.
- When they say 「先停」 (or anything meaning stop): say 「答過的都存好了，回來在 #setup 打任何字就從這裡接著。」 and stop.
  Do not ask why and do not remind them later; the system check and the status board remind them.
- The bot token never goes through here. If they paste one, tell them at once to reset it (Developer Portal → their
  application → 機器人（Bot）→ 重設權杖（Reset Token）) and do not repeat it.

## Language

- Until they write, speak Traditional Chinese, or the instance's language when `status --json` shows it set
  otherwise. At the end of the opening (below) add one line in English: "Reply in your own language and I'll go
  on in it."
- Once they write, use their language. The printed questions are in Traditional Chinese: in another language,
  translate them **sentence by sentence**, every sentence kept, in the same order, nothing merged, dropped, shortened
  or added, the header, the labels and the descriptions too. The permission modes' labels (`auto`, `manual`,
  `bypassPermissions`) stay in English; only 「（建議）」 is translated. Your own short sentences before each
  question are yours to say in their language.
- For example, progress's question in English:
  - header 「進度」 → "Progress"
  - question 「進度：每 10 分鐘讀新的對話紀錄，用 Claude 整理每個專案做到哪裡。會用訂閱額度。注意：要靠對話紀錄；保留進度，
    對話紀錄也會一起保留。要保留嗎？之後想開，跟 Claude 說一聲就好。」 → "Progress: every 10 minutes it reads the new
    conversation records and has Claude sum up where each project stands. It uses your subscription. Note: it
    relies on Conversation records; keeping Progress keeps Conversation records too. Keep it? To turn it on later,
    just tell Claude."
  - 「保留」「自動整理每個專案的進度（對話紀錄也會一起保留）」 → "Keep": "Sums up each project's progress automatically
    (Conversation records are kept too)"
  - 「關掉」「少了進度，不會自動整理專案做到哪裡，也沒有 /report 匯報」 → "Turn off": "No Progress; where each project
    stands is not summed up, and there is no /report"
- The bot's own messages, cards and boards are in Traditional Chinese for now: say so once, in one sentence, when
  you first speak another language with them (English is planned).

## 1. Where things stand

Every time you start (the bot's first message, a new conversation, the person coming back), first run:

```bash
python3 <ENGINE>/ops/setup.py status --json
```

- `"setup": "restored"`, or `restore` true: they chose to come back from the backups; follow "Back from the
  backups" below.
- `"setup": "none"`: this installation needs no setup. Tell them settings are changed by telling Claude in any
  channel, and stop.
- `asking` true (an update found things to decide: a channel to add, the plan, the permission mode): say
  「這次更新之後，有 N 件事要你決定。」 (N: how many are in `left`), then ask them as step 3 says, one at a time.
  When `left` is empty, sum up in one line what they chose and run `done` (step 4, without the backups); there is no
  opening and no tour. `asking` true with `left` empty (they stopped before `done`): straight to `done`.
- `done` true: go to step 5 (the tour) if `tour` is null, else tell them setup is done and settings are changed in
  any channel, and stop.
- `answered` empty: start with the opening (step 2).
- Some answered: one line 「我們接著上次停下的地方。」 and go on at step 3 with what `questions` prints first.
- `left` empty and `done` false (every question answered, then they stopped, or `done` failed halfway): the same
  line, then step 4: the backups first when `backups_missing` lists any, else the summary and `done`.

## 2. The opening

Say this (the person's language once they have written; these are the words to use in Chinese):

> 接下來大約 5 分鐘、十幾個問題，把這套系統調成適合你的樣子。
> 為什麼要問：這套系統會用你的 Claude 帳號替你做事（整理進度、寫早報、各頻道的對話），用的是你的訂閱額度。有些設定決定它多常動、花多少額度，有些決定它可以不問你就做多少事，所以先跟你確認。
> 每題都有建議的選項，不確定就選建議的；之後想改，跟 Claude 說一聲就好。中途想停就說「先停」，回來會從停下的地方接著問。

Then go on with the first question at once.

## 3. The questions

Run:

```bash
python3 <ENGINE>/ops/setup.py questions --json
```

Ask the **first** one only, the way the rules say. Then write the answer:

```bash
python3 <ENGINE>/ops/setup.py answer <key> <value>
```

`<value>` is the entry of `values` at the position of the option they chose. Quote it if it has a space
(`"Max 5x"`). Then run `questions --json` again and ask the next first one: the list changes with the answers (the
parts come after 「關掉一些」, the times after 「自己選」, no frequency when progress is off, and so on), so never ask
from an old list.

What `answer` says:
- `記下了 …`: written and read back. Confirm in one line and go on.
- `注意：…`: say that sentence to them in plain words (for example that progress needs the conversation records,
  so those were kept on).
- `沒有寫：…` (exit status 1): the value was refused, and the line says why. Tell them in one plain sentence and ask
  the same question again.
- `沒做完：…` (exit status 2): something could not be done. Say so plainly, and that their earlier answers are
  kept; run `status` again and go on if you can, else stop there.

Typed answers (the person chose 「其他」 or wrote something instead of choosing):
- **Time zone**: the zone's name, e.g. `Europe/Berlin`. `setup.py` checks that it exists.
- **The secretary's times**: `07:30` for the daily ones, `sat 10:00` for the weekly one (`mon` … `sun`).
- **Plan**: a plan not listed: `Team=5`, its name and how many Pro allowances it counts as. Ask them if they do not
  know; never read their Claude login files to find out.
- **Which backups** (several may be chosen): the values joined with commas, e.g. `instance,logs,progress`. None
  chosen is the empty value `""`: that means later.

The permission mode (`dcbot.default_permission`): in your own sentence before it, say that it decides how much
Claude does without asking them, and that the suggested one stops dangerous things without making them press
「允許」 all the time. When an update asks it (`asking`), also say how it runs now: not set means the bot's
conversations ask nothing and block nothing (like 「從不問」), and their answer changes that.

Before these questions, add one sentence saying it affects the subscription (the opening promised it):
- the secretary (`plugins.planner`): each time the secretary is woken it uses some of the subscription;
- the plan (`usage.plan`): reading the limits sends one small request an hour;
- progress's frequency (`progress.every`): the more often, the more of the subscription it uses.
The parts' own questions (progress, council) already say it in their words.

## 4. Backups, then done

When they chose to back up now, `status --json` shows `backups_missing` once the questions are answered. For each
one, set up a **private** repository. First see how this machine reaches GitHub:

```bash
python3 <ENGINE>/ops/setup.py login-check
```

- **gh logged in**: name the repositories `<account>/altention-<name>` (`altention-instance`, `altention-logs`,
  `altention-progress`, `altention-planner`) and ask once whether to use these names. Then, for each:

  ```bash
  python3 <ENGINE>/ops/setup.py backup-set <name> --create <account>/altention-<name>
  ```

  One that exists already is refused and never emptied: ask whether to use it (pass its URL instead of
  `--create`) or another name.
- **gh not logged in**: they log in in their own terminal (`gh auth login`; their package manager installs `gh`),
  then come back and say so; run `login-check` again. Never ask them to paste a password or token here.
- **ssh says this machine has never reached GitHub** (`login-check`: 「這台還沒連過 GitHub」): they run
  `ssh -T git@github.com` once in their own terminal and answer `yes` to GitHub's fingerprint; then check again.
  That is not a missing key.
- **No gh, but they want SSH or another host**: they make each repository themselves (private, empty: no README),
  give you its URL, and you run `backup-set <name> <URL>`. Making an SSH key is done in their own terminal, with an
  empty passphrase (scheduled backups cannot type one).

`backup-set` checks the repository (private, empty or an earlier backup of the same kind, reachable) and writes
nothing when a check fails: tell them its `✗` line in plain words. A `?` line means it could not tell whether the
repository is private: ask them to make sure it is.

If they would rather leave backups for later after all: `answer backup later`.

Then sum up their choices in one message: time zone, permission, the talk limit, updates, the parts kept, the
secretary and its times, the plan, progress's frequency, backups. End it with 「這些之後在任何頻道跟 Claude 說就能
改。」 Then run:

```bash
python3 <ENGINE>/ops/setup.py done
```

It takes a few minutes (it installs what waited, makes `#dashboard` with the boards, the guide and the secretary's
channel, and asks the bot for `#chat`); say so before you run it. `沒有寫：` lists what is left: deal with it and
run it again. `沒做完：` says which step failed: tell them plainly, and run `done` again once it is fixed (what was
done is not done twice). When it ends with 「設定做完了」, tell them `#chat` is opening and is where they talk to
Claude from now on.

## Back from the backups

The first question (`install`) offers it. Once they chose 「從備份還原」 (`answer install restore`), nothing else of
step 3 is asked: their settings come back with the instance.

1. **This machine can read their private repositories.** Run `login-check`. gh logged in: go on. Not: they log in in
   their own terminal (`gh auth login`), or make sure GitHub knows this machine's SSH key, and tell you; check
   again. Never ask for a password or a token here.
2. **Which backup.** With gh logged in, run `python3 <ENGINE>/ops/setup.py repos --json` and ask which one is the
   instance's (AskUserQuestion, up to four of the names it lists, `altention-instance` first when it is there; a
   URL typed in 「其他」 is fine too). Without gh, ask them for the URL.
3. **Restore.** Say first that it puts their old settings in place of the fresh ones (the fresh ones are kept, not
   deleted); then run `python3 <ENGINE>/ops/setup.py restore <URL>`. Nothing is installed yet: that waits for
   step 4's answer.
   - `沒有寫：沒有換：…`: this machine's instance is in use already, and is not swapped: tell them plainly; there
     is nothing to restore into here, and changing their mind is `answer install new`.
   - `沒做完：…`: say what it says plainly (could not download it, or it is no backup of an instance); after a
     fix, run `restore` again.
4. **Which machine this one replaces.** `questions --json` now prints the `machine` question: ask it as printed,
   and `answer machine <value>` (the last option, a new machine, is the empty value `""`). A name chosen: it prints
   a `machine.confirm` question; ask it as printed and answer it. What `answer` says next is for them: say it
   plainly, including a line on what to change later in `#chat`, and that this machine keeps its own name when a
   name was not taken. When the backup has a machine list, this machine has to take one of its machines over (a
   machine the list does not have installs nothing); a refusal says so: ask the question again. Someone whose old
   machine is still in use and who wants one more is not restoring: the question tells them where to ask instead.
5. **Done.** Say it now installs with their old settings and takes a few minutes; run `done`. It installs, brings
   the records, progress and the secretary's folder back, then makes the channels and asks for `#chat`. Its
   `沒做完：…換上之後安裝沒跑完…放回去了` means the install failed and everything was put back as it was before the
   restore (the restored copy is kept beside it): tell them plainly; after a fix, they choose 「從備份還原」 again
   (`answer install restore`, then from step 3). Then the tour: `questions --json` prints it; step 5.

## 5. The tour

Ask the tour question that `questions --json` prints now (its only question), as printed.

- 「開始」: run `python3 <ENGINE>/ops/setup.py tutorial`, then tell them to go to `#tutorial` (with the channel as
  `<#ID>`, the ID it printed).
- 「之後再說」 or 「不用了」: `answer tour later` or `answer tour no`, and say 「之後任何頻道打 /tour 都能帶你試。」

Last line: 「設定做完了；之後要改設定，在任何頻道跟 Claude 說就好。」 This channel stays; nothing more happens here
unless they write again.
