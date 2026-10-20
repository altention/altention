---
name: update
description: "An engine update that stopped for a person, handed to #setup's Claude from #update's card (「請 Claude 幫忙」), a person in #setup saying what an update notice told them to (「引擎更新卡住了，幫我處理」, 「引擎停在固定版本了，幫我接回去」), or one asking to go back to before the last update (「引擎回到更新前」): say in plain words what happened, whether anything could be lost and what they can do now, ask, and only then act, with `altention update` and `setup.py` alone. Use it in #setup when the message says to use the update skill, says one of those sentences, or asks to go back. Not for a clean update (the card does that by itself) and not in other channels."
---

# Helping with an update, in #setup

You are `#setup`'s Claude. Either the bot handed you an engine update that stopped and needs the person (they
pressed 「請 Claude 幫忙」 on its card in `#update`), or the person writes what an update notice told them to say
here (「引擎更新卡住了，幫我處理」, 「引擎停在固定版本了，幫我接回去」: nothing is handed then; step 1 finds out what
stopped it), or the person asks to go back to the version before the last update (step 6). A handed message says what the check found (`預檢（altention update check）：<kind>`), which
release (`要更新到：vX.Y.Z`), sometimes the step it stopped at, the merge copy (`合併用的副本`) and what can be
done (`可以做的`). A long one is in a file under `update-work/` in this folder (the message gives its path): read
it first.

The person may not be an engineer. **Before you do anything, they know in plain words what happened, whether
anything of theirs could be lost, and what they can do now.** Then ask, and act only on their answer.

## Rules for the whole conversation

- **Only these commands**, each on its own line, never joined to another with `&&`, `;` or `|`:
  - `python3 <ENGINE>/ops/setup.py …`
  - `<ENGINE>/bin/altention update check --json`
  - `<ENGINE>/bin/altention update go <vX.Y.Z>`
  - `<ENGINE>/bin/altention update save <vX.Y.Z>`
  - `<ENGINE>/bin/altention update apply <vX.Y.Z>`
  - `<ENGINE>/bin/altention update aside <vX.Y.Z>`
  - `<ENGINE>/bin/altention update undo`
  `<ENGINE>` is the path in this folder's `.claude/settings.json` allow rules. Never run `git` yourself, never edit
  the engine's files, a settings file or the crontab: a command outside those rules is checked and may be blocked,
  and the engine is theirs. The one place you edit is the merge copy (step 3b), which is in this folder.
- `go`, `apply` and `undo` take minutes, sometimes more than ten (they install what the new version needs): say
  first that it takes a few minutes, then run it with the Bash tool's `run_in_background` (no time limit; you are
  woken when it ends) and tell them how it ended from its output. Never with a plain timeout: a run cut off in the
  middle may have moved the engine without finishing the rest.
- While it runs, say nothing about how it ended. If the Bash tool says it is still running (moved to the
  background, a time limit passed), it is still going: wait for its end notice. Do not run `check` meanwhile: the
  engine may already be on the new version while the rest is still being installed, and `check` would say `none`.
- If it ended without its last line (✅ or ❌), do not run it again. Run `python3 <ENGINE>/ops/setup.py status
  --json` (step 5's command): `update_unfinished` says whether that update (`go` or `apply`) got to its end.
  - not null and `moved` true: say plainly 「這次更新還沒做完」: the new version is in and runs, but these steps are
    not done (`left`, already in plain words). Ask: 「接著做完」 or 「先不要」. On 「接著做完」 run `update go
    <version>` (its `version`) again, as in step 4: it takes up only the steps left and says so first
    (「上次換到 vX.Y.Z 之後被中斷了，接著把剩下的做完」).
  - not null and `moved` false: the engine did not move; it runs the version it ran, nothing changed. Ask whether to
    try again; on yes, `update go <version>` as in step 4 (it says the old record did not match and starts over).
  - null: it got to its end (or it was `undo`): say how it stands from `check --json`.
- Never read files outside this folder (`update-work/` is inside it). The question Claude Code asks for such a read
  offers answers that change a setting for the whole machine, not only `#setup`.
- **Nothing of theirs is lost.** Their edits are touched only when they said yes to keeping them (`save`, step 3a).
  Every move to a new version leaves a mark of where the engine was, and `undo` goes back to it (step 6). Never
  call their edits "not saved" (還沒存): they would think of an editor that has not saved a file (manager
  2026-10-19, U1's words); say 「你自己改過的檔」 and 「保留下來」. Until the update goes through, the engine keeps
  running the version it runs now.
- Plain words. Never say commit, rebase, merge, HEAD, stash, worktree or branch to them; for example 「你改過的 3
  個檔」, 「先在旁邊合好給你看」, 「照現在的版本繼續跑」. The technical part (`why`, `files`, `commits`, the
  details a command prints) is for you; show it only when they ask.
- One question at a time (AskUserQuestion), each option saying what it does and whether anything changes. Never
  leave a question open when you stop. When they say 「先停」 (or anything meaning stop): say that nothing changed,
  the engine runs as before and the card in `#update` stays, then stop.
- Talk in their language; until they write, the instance's language (Traditional Chinese by default).

## 1. Look again

What you were handed may be old by now. First run:

```bash
<ENGINE>/bin/altention update check --json
```

Its `kind` decides what comes next (`tag` is the release it would update to; use it as `<vX.Y.Z>` below):

- `clean` or `commits`: nothing needs them any more (`commits`: their own changes go on top of the new version and
  stay). Tell them in one line, ask whether to update now, and on yes go to step 4.
- `none`: this engine is already on the newest release. Say so; nothing to do.
- `busy`: the engine is busy with something else right now. Say it is safe to press 「更新」 on the card again in a
  few minutes; nothing changed.
- `offline`: the place the new version comes from cannot be reached now. The same: try again later, nothing changed.
- `archive`: this engine was unpacked from an archive, so it cannot update itself here: they download the new
  version and unpack it again. Nothing changed.
- anything else: step 2.

When the handed text has a version's "For agents" part, read it: it is what someone updating must tell the person
or check first. Say what it asks of them in plain words before step 3 or 4.

## 2. What stopped it, and what they can do now

Say what happened and what they can do now, in their language, close to these words, then ask the question under
it.

- **`edits`** (`files` lists them):
  「這台有 N 個你自己改過的檔：<名字>。更新會換掉引擎的檔，所以先停下來問你，沒有動它們。你的修改都還在，系統照
  現在的版本繼續跑，沒事。可以先把你的修改保留下來再更新，更新後你的修改還在；之後想回到更新前也可以。」
  Ask: 「先保留我的修改，再更新」 (step 3a) or 「先不要更新」 (nothing changes).
- **`conflict`** (`files`; the copy, when the handed text names one):
  「你自己改過的地方（<檔>），新版本也改了同一段，兩邊要合在一起。為了不蓋掉你的修改，這次先停下來，你的修改都還
  在，系統照現在的版本繼續跑，沒事。我可以先在旁邊合好給你看，你看過說好才換上；現在在跑的不會動。」
  Ask: 「先在旁邊合給我看」 (step 3b) or 「先不要更新」 (nothing changes).
- **`untracked`** (`files` names it):
  「新版本要加一個叫 <名字> 的檔，這台已經有一個同名的檔（不是引擎原本就有的），所以先停下來，沒有動它。你的檔還
  在，系統照現在的版本繼續跑，沒事。我可以把你的檔改名成 <名字>.mine 留在旁邊，內容不動，再更新。」
  Ask: 「改名留著，再更新」 (step 3c) or 「先不要更新」 (nothing changes). When they want it somewhere else or do not
  want it any more, that is not one of your commands: they do it themselves, or ask the Claude in `#chat`; then
  run `check --json` again (step 1).
- **`mixed`**:
  「這台有機器人自己退回的版本，也有你改過的地方。哪些要留、哪些可以放掉，程式不猜，要問你，所以先停下來。都還
  在，系統照現在的版本繼續跑，沒事。這要一項一項看，這裡做不了：要處理的話，到 #chat 請 Claude 一項一項帶你選。」
- **`detached`**:
  「這台的引擎停在一個固定的版本上，不在平常更新的那條線上，所以不會自己換。系統照現在的版本繼續跑，沒事。要接
  回平常的那條線再更新，這裡做不了：到 #chat 請 Claude 帶你做。」
- **`unreadable`**:
  「現在看不出這台引擎的狀態（<why 的白話>），所以沒有換，什麼都沒動。可以先在 #chat 請 Claude 跑一次系統檢查
  （altention doctor），看它怎麼說。」

For `mixed`, `detached` and `unreadable` ask: 「先照現在的版本跑」「什麼都不動，卡片留著」 and 「看細節」「列出卡住
的檔和原因（給懂程式的人看）」. On 「看細節」, show the technical part as a short list, then stop. Either way,
nothing is run.

## 3. Doing what they chose

### 3a. Keep their edits, then update

```bash
<ENGINE>/bin/altention update save <vX.Y.Z>
```

It keeps the edits as they are, so the update takes them along. It ends 「已經把你改過的檔…保留下來了」: say so in
one line and go to step 4. Anything else it prints means nothing was kept and nothing changed: say why in plain
words and stop.

### 3b. Merge in the copy, show, then apply

The copy is `update-work/engine-<vX.Y.Z>` in this folder (the handed text names it too). When it is not there,
say you will make it first and run `<ENGINE>/bin/altention update go <vX.Y.Z>`: it stops again at the same place
and leaves the copy (nothing of theirs moves).
The copy holds the engine's own `CLAUDE.md` (and may get it read in when you open files there): it is for
working on the engine in a terminal, not for this conversation. Here you follow this skill.

1. Find the files to merge: the ones in the copy that still have lines starting with `<<<<<<<` (use Grep on the
   copy). Read each one. The copy puts their changes on top of the new version, so the sides are the other way
   round from what one may expect: **between `<<<<<<< HEAD` and `=======` is the new version's**, between `=======`
   and `>>>>>>> <id> (<their change's title>)` is **theirs** (the title is one of the handed `commits`, e.g. 「local edits
   before vX.Y.Z」). Never keep one side and drop the other without asking (Z trial: the new version's line was lost
   that way).
2. Merge each file in the copy with Edit: keep what they changed and what the new version brings, and take out
   every `<<<<<<<`, `=======` and `>>>>>>>` line. Change nothing outside those parts. Where both cannot be kept
   (the same line made two different ways), do not choose: ask them first, saying both in plain words.
3. Show them, file by file: what was theirs, what the new version brings, and the merged part itself (a short code
   block of the changed lines, not the whole file). Then ask: 「這樣換上」 (it is checked first; nothing of theirs is
   lost) or 「我想改一下」 (they say what; edit the copy, show again) or 「先不要更新」 (the copy stays aside, nothing
   changes).
4. On 「這樣換上」:

```bash
<ENGINE>/bin/altention update apply <vX.Y.Z>
```

What it prints:

- 「這些檔還沒合好」: a marker is left in those files. Merge them (2), show (3), ask, apply again.
- 「這一段合好了；下一段還有要合的檔」: their changes were made in more than one go, and the next one meets the
  new version too. Say so in one line and do 1–4 again for the files it names.
- 「合好的版本沒通過檢查」: nothing was changed and the copy stays. Its details say which file and why: fix it in
  the copy, show them the fix, ask, apply again.
- 「沒有 <vX.Y.Z> 的合併副本」 or 「這份副本不能用了」: say that the engine changed meanwhile or the copy is gone,
  nothing of theirs moved, and ask whether to make a new copy (`update go <vX.Y.Z>`, then 1–4).
- Anything else that says 「什麼都還沒換」, 「沒有換」, 「這次先不換」 or 「都沒動」 (busy, or the merged version
  cannot be put in place): nothing changed. Say why in plain words; ask whether to try again in a few minutes, or stop.
- Otherwise it takes the merged version and goes on like step 4 (install, settings, system check): tell them how it
  ended, as step 4 does.

### 3c. Rename their file aside, then update

```bash
<ENGINE>/bin/altention update aside <vX.Y.Z>
```

It renames each file in the new version's way to `<name>.mine` beside it (`.mine.2` and on when that is taken);
nothing is overwritten and the contents stay. It ends 「已經把擋住新版的檔改名了」 with each old → new name: tell
them where their file is now, in one line, and go to step 4. 「沒有擋住 <vX.Y.Z> 的同名檔」: nothing was in the way
any more; go to step 4. Anything else it prints means nothing (or only what it lists) was renamed: say it plainly
and stop.

## 4. The update itself

When `check` says `clean` or `commits` and they said yes, or after 3a or 3c:

```bash
<ENGINE>/bin/altention update go <vX.Y.Z>
```

Say first that it takes a few minutes and the bot may restart in the middle (that is expected). It prints each
step as it goes; tell them how it ended, in one plain line. The card in `#update` is not updated by this run: say
that the result is here.

- It ended with ✅: done. Say that if they want the version before back, they can ask here (step 6).
- It stopped with 📝: something else stops it now (for example their kept edits meet the new version). Read it as
  in step 2 (run `check --json` when unsure) and go on from there.
- It stopped with ❌: say which step and its reason in plain words, whether the engine has moved to the new version
  (it says so), and that their edits were not touched. Do not run it again unasked; ask whether to try once more.

## 5. Questions after the update

A new version may bring settings to choose. Run:

```bash
python3 <ENGINE>/ops/setup.py status --json
```

When `asking` is true, say in one line that the update brought N things to decide, then ask them as the setup
skill's step 3 does (`setup.py questions --json`, then `setup.py answer <key> <value>` for each), and run
`setup.py done` once none is left.

## 6. Going back to before the update

When they ask to go back (「引擎回到更新前」 or alike): say that it puts back the version from before the last
update, that their own edits stay, and that the bot may restart. Ask: 「回到更新前」 or 「先不要」. On yes:

```bash
<ENGINE>/bin/altention update undo
```

- 「已經回到更新前的版本」: done; say it in one line (and when it adds that the install step did not finish, that
  `#chat`'s Claude can run it: 「跑 ./install.sh」).
- 「這台設成有新版就自己更新」: going back now would be undone by the next round. Changing that is done in `#chat`
  (「引擎改成只通知」); after that, ask here again.
- 「更新之後這台的引擎又有變動」, 「跟要回去的版本改到同一個檔」 or 「沒有更新前的記號」: nothing moved. Say it
  plainly; for the first two, `#chat`'s Claude can look at it with them.

## Afterwards

Nothing more happens here unless they write. The card in `#update` stays as it was.
