# Changelog

Every release has a `## vX.Y.Z — date` heading, newest first. The release tool publishes the text under a version's
heading as that version's notes.

Each version has these sections:

- **For people**: what changed, for you to read.
- **For people (zh-TW)**: the most important of those points in plain Chinese, the ones that matter most first.
  The update notices in `#update` quote it in an installation whose language (`core.language`) is zh-TW, each
  point cut to its bold lead; a version without it is quoted from For people.
- **For agents**: what an agent updating an installation to this version needs to know, in plain words (settings to
  move, a step to run, something to check afterwards), or `None`. A planned `/update` command (not in this version
  yet) will read it, tell you what it says before doing anything, and never carry it out on its own.

## v0.3.1 — 2026-10-20

### For people

- **A file of yours in the way of an update is renamed aside, once you say so.** When a new version adds a file
  whose name one of your own files already has, 「請 Claude 幫忙」 now lets `#setup`'s Claude rename yours to
  `<name>.mine` beside it (`.mine.2` and on when that is taken; the contents stay) and go on with the update,
  after asking you once. Before, you had to move it yourself first.
- **Ignored files count too.** A file you ignore (`.gitignore`, `.git/info/exclude`) with the name of one the new
  version adds used to be overwritten by the update without a word; now it stops the update like any other file
  of yours in the way.
- **Updates that run on their own check for such files as well.** An update that moves by itself (`core.update`
  set to `release` or `branch`, or `update.py --release`) now stops before the move when one of your files is in
  the way, leaves it as it is, and tells you to say 「引擎更新卡住了，幫我處理」 in `#setup`.
- **`#update`'s other lines are the system's notices too.** The lines the setup steps post there (the
  welcome when `#update` is opened, 「整理頻道」, 「有 N 件事要你決定」, and 「你在 #setup 回答的都設定好了」) now
  look like the update notices, not like a message from one of the Claudes.

### For people (zh-TW)

- **擋到更新的同名檔，問過你就改名留著。** 新版要加的檔跟你自己的檔同名時，按「請 Claude 幫忙」，#setup 的 Claude 問過你一次，就把你的檔改名成「<名字>.mine」放在旁邊（內容不動），接著更新；以前要你自己先搬走。
- **被忽略的檔也會擋下來。** 寫在 .gitignore 裡、跟新版要加的檔同名的檔，以前更新時會被直接蓋掉，現在會先停下來，跟其他同名檔一樣處理。
- **自動更新也會先檢查同名檔。** 自己跑的更新（core.update 是 release 或 branch，或 update.py --release）遇到同名檔會先停下來、不動你的檔，並請你到 #setup 說「引擎更新卡住了，幫我處理」。
- **#update 裡其他幾則也改成系統通知。** 開頻道時的說明、「整理頻道」、「有幾件事要你決定」、「你在 #setup 回答的都設定好了」，現在跟更新通知一樣是系統發的，不再顯示成某個 Claude 傳來的。

### For agents

1. New command `altention update aside <vX.Y.Z>`: it renames the files not tracked or ignored here that have the
   name of one the release adds to `<name>.mine` (never over anything), contents untouched. `#setup`'s allow
   rules gain it; `altention update go` (the 更新 button) writes them through its reconcile, `./install.sh` does
   too. Do not edit that settings file.
2. `altention update check --json` kind `untracked` now also lists ignored files with such a name. Do not move or
   delete those files yourself: send the person to `#setup`, whose update skill asks and then runs `aside`.

## v0.3.0 — 2026-10-20

### For people

- **Setting up happens in Discord, in `#setup`.** The install in the terminal asks nothing any more: it installs
  with safe defaults and ends by sending you to `#setup`. On its first start the bot opens `#setup`, where a Claude
  asks the questions one at a time, in your language, and you can stop and come back; `#chat` opens once that is
  done. Nothing that spends your subscription (progress, reading the usage limits) runs until then.
- **Coming back from your backups is done in `#setup` too**: choose 「從備份還原」, pick the repository, and your
  settings, records, progress and the secretary's plan come back. The fresh install is kept beside it, never
  deleted, and put back if anything fails.
- **A tidier channel list.** A new installation puts the bot's record and guide channels (`#audit`, `#guide`) in a
  「系統」 category at the bottom, and update news in `#update` in the main area. If you move 「系統」 somewhere
  else, the bot leaves it there. Categories whose name starts with 「━」 are taken as dividers, not projects.
- **Updates have their own channel and a button.** News about updating goes to `#update` and lists what changed in
  every release in between, in plain words; an update you asked for with `--release` is announced too, and an
  engine stuck on one fixed version says once that it will not update. On the bot's machine a new release shows
  up as a card with 更新 / 之後再說 / 看要做什麼. Pressing 更新 checks first and, when nothing of yours is in the
  way, updates, installs, fills in what the new version needs and checks the system on its own, each step written
  on the same card. `altention update check` and `altention update go <version>` do the same from a terminal.
  With several machines, only the bot's machine says a new release is out, and pressing 更新 updates the others
  the same way, each a line on the card.
- **Nothing of yours is lost in an update.** Every update leaves a mark where the engine was: say 「引擎回到更新
  前」 to the Claude in `#setup` to go back (your edits are kept, and changes made since are never thrown away).
  When your own edits are in the way, the card says so in plain words and 「請 Claude 幫忙」 hands it to `#setup`,
  where the Claude explains what happened and asks before doing anything: it can keep your edits and update, merge
  your changes with the new version's in a copy aside, show you the result and put it in place only on your yes.
  A file of yours with the name of one the new version adds is left alone and named on the card: rename or move
  it, then press 更新 again. When an update notice says an update is stuck, say what it tells you to the Claude
  in `#setup` (for example 「引擎更新卡住了，幫我處理」). The engine keeps running the version it ran until then.
- **An update cut off halfway is finished.** If the machine restarts or the update is stopped after the engine
  moved, the next 更新 (or asking the Claude in `#setup`) does only the steps left, and the interrupted card says
  where it went on.
- **After an update, what the new version needs is set up or asked.** Anything that costs nothing and adds
  nothing to your channel list is set up by itself (for an older installation: `#update`, and `#audit` and
  `#guide` moved into 「系統」). Anything that would add a channel you see or spend your subscription is asked in
  `#setup`, one question at a time; `#update` names what will be asked. A setting you already have is never asked
  again. A channel you chose to open that was never made, or that was deleted (`#secretary`, `#dashboard`), is
  asked as that, not as a new question. If you never chose how much Claude may do on its own (the permission
  mode), `#setup` asks that too: until you answer, it works as before (it neither asks nor blocks); once you
  answer, the conversations opened after that follow what you chose.
- **`/archive` puts away any channel.** The bot's window for it is closed and the conversation is kept; the
  channel moves to a 「封存」 category, its topic saying where it was. Writing in it later does not bring it back by
  itself: the bot asks how to go on, with 「接回原本的對話」 or 「在這裡開新對話」 for a conversation's channel and
  「恢復這個頻道」 for any other, and the channel goes back to its old category once you press one. A full 「封存」
  (50 channels) gets a 「封存 2」. Channels you moved into 「封存」 by hand are recorded when the bot starts.
- Smaller changes:
  - When Claude Code asks in a new channel whether to read files outside its folder (Claude Code 2.1.303's
    external `CLAUDE.md` imports), the question and its buttons show in the channel instead of the window waiting
    unseen.
  - On a phone or tablet that cannot drag categories, ask Claude to reorder channels and categories
    (`dcctl position`); each change is recorded in `#audit`.
  - `altention dc post --file` with the text piped in now posts the text with the file (it used to post the file
    alone).
  - Progress: a project you renamed keeps its history under the new name (an old name, or one listed in
    `progress.aliases`, joins it instead of starting a new project), and a repository several projects share is
    given the same project every round.

### For people (zh-TW)

- **設定改在 Discord 的 #setup 裡做。** 終端機的安裝不再問問題，裝好後會叫你去 #setup，由 Claude 一題一題問，可以中途停下來，之後再回來接著回答。設定做完才會開 #chat；在那之前，任何會用到訂閱額度的功能都不會開始跑。
- **有新版時，#update 會出現一張卡片，按「更新」就會自己更新。** 卡片會先檢查，沒有東西擋路就更新、安裝、補上新版需要的設定，再檢查一次系統，每一步都寫在同一張卡片上。有好幾台機器時，只有機器人那台會通知，按一次就會連其他台一起更新。
- **更新不會弄丟你的東西。** 每次更新前都會留一個記號，想退回去，跟 #setup 的 Claude 說「引擎回到更新前」。你改過的檔案擋到更新時，卡片會用白話說明；按「請 Claude 幫忙」，Claude 會先解釋，問過你才動手。
- **更新做到一半斷掉，下次會接著做完。** 機器重開或更新被中斷時，再按一次「更新」（或請 #setup 的 Claude），只會做剩下的步驟。
- **頻道列表比較整齊了。** 機器人的紀錄和說明頻道（#audit、#guide）放在最下面的「系統」分類，更新消息放在 #update。你把「系統」移到別處，機器人不會再拉回去；名字用「━」開頭的分類當作分隔線，不算專案。
- **用 /archive 可以把任何頻道收起來。** 頻道會移到「封存」分類，主題寫著它原本在哪裡，對話也會保留。之後在裡面打字，機器人會問你要怎麼繼續：對話的頻道可以按「接回原本的對話」或「在這裡開新對話」，其他頻道按「恢復這個頻道」，按了才會回到原本的分類。
- **可以從備份還原。** 在 #setup 選「從備份還原」、選好倉庫，設定、紀錄、進度和秘書的計畫就會回來。新裝的那份會留在旁邊，不會刪掉，還原失敗時會換回來。
- 其他小改動：
  - 新頻道的資料夾裡，CLAUDE.md 引用了資料夾外面的檔案時，Claude Code 一開始會問要不要允許匯入這些外部的 CLAUDE.md（Allow external CLAUDE.md file imports?）。這個問題和按鈕現在會貼在頻道裡由你回答，不會卡住。
  - 手機或平板拖不動分類時，可以請 Claude 幫你調整頻道和分類的順序，每次調整都記在 #audit。
  - 進度：專案改名後，舊的紀錄會接到新名字下面。

### For agents

1. After updating, run `./install.sh` on each machine, then `python3 ops/doctor.py`. The install writes `#setup`'s
   working folder (`~/.local/state/altention/setup/`): its `.claude/settings.json` allows `ops/setup.py` and
   `altention update check / go / save / apply / undo`, one rule each, and links the `setup` and `update` skills
   there only. Do not edit that settings file: it is rewritten whole.
2. The bot restarts by itself on the new version. On that first start it runs `ops/setup.py reconcile`: on an
   older installation it opens `#update`, moves `#audit` and `#guide` into the 「系統」 category, opens `#guide`
   when there is none, and opens `#setup` when something is to be decided: what adds a channel or spends the
   subscription, and the permission mode (`dcbot.default_permission`) when the instance has none. Until that is
   answered the bot runs as it did with none set; afterwards new conversations use the chosen mode. Tell the
   person they will see `#setup` and why; the questions are answered there, not by you. `python3 ops/setup.py
   reconcile --dry-run` shows what it would do.
3. An installation from before this version has no `setup` entry in its instance and counts as set up: nothing
   is asked again except what the reconcile lists.
4. `ops/apply.py` now updates each machine's engine through that machine's own `altention/update.py`, behind the
   same syntax and load check as any update: `--release` when the instance's `core.update` is notify or release,
   a plain round when it is branch. A refused release stops that machine at the engine step (`--take-refused`
   goes past the check as before). `update.py --release` exits 75 when the git lock was held and nothing was done;
   apply tries again for about two minutes, then says that machine's engine did not move.
5. The bot's machine is the machine list's entry with the `dcbot` role, matched by this machine's name;
   `"ssh": ["local"]` only says how a machine is reached. A machine whose name the list does not know posts no
   update card and leads no other machine.
6. Copies made for merging live in `~/.local/state/altention/setup/update-work/`; an interrupted update's record
   is `~/.local/state/altention/update-go.json` (`python3 ops/setup.py status --json` reports it as
   `update_unfinished`). Leave both to `altention update`.
7. Known limit: in automatic (`release`) mode each machine still posts its own ✅ in words; they are not gathered
   on the bot's card yet.

## v0.2.2 — 2026-10-19

### For people

- A machine that updates the engine by itself (`core.update` is `release` or `branch`) now says so in Discord (the
  notification channel) once per release: "✅ 引擎已更新到 vX.Y.Z", with the gist of each point of that release's
  "For people" notes here (its bold lead, or else its first sentence). It is said whoever moved the engine, and when
  several releases came at once it names the newest and lists the ones between. Nothing is said while the bot's
  supervisor has rolled that release back on the machine. The first round after this update only writes down the
  release the machine runs and says nothing. With `notify` nothing changes.
- `ops/release.py publish --push` now also puts the release tag on the published commit in the repository you
  publish from (its branch's upstream remote, else `origin`), so machines that follow that repository see releases
  too. It refuses before making anything when that remote is missing or already has the tag on another commit.

### For agents

None.

## v0.2.1 — 2026-10-19

### For people

- **When a new version's packages will not install, the bot steps back instead of staying down.** If installing the
  packages of a new version keeps failing for 30 minutes (no network, say), the bot's supervisor goes back to the
  last version that worked, on this machine only: it is never pushed. It keeps trying the new version's packages
  every 10 minutes and moves forward by itself once they install. While a machine is stepped back, `deploy.sh` and
  `bin/home-git push` there refuse and name the one command that takes the step back out; the system check says so
  after a day.
- **Restart the machine once after updating** (or have Claude replace the bot's supervisor as
  `dcbot/SUPERVISOR-RESCUE.md` says): the step back lives in the supervisor, and restarting only the bot leaves the
  old one running. The refusals of `deploy.sh` and `bin/home-git push` work at once.
- The bot's first start under the new supervisor runs `pip install` once, even when its packages did not change: the
  supervisor now records which requirements it installed and checks them before every start.
- **A board is no longer posted twice.** When editing a board message fails for any reason other than the message
  being gone (Discord slow or unreachable, a server error), the bot keeps that message and edits it again next time;
  before, it posted a new copy and left the old one behind on old content. Only a board message deleted by hand is
  posted again. A board that fails three times running is said once as a notification (the board, its channel and
  the error), for a failure that will not go away by itself, such as the bot's permissions in that channel.

### For agents

1. Replace the running supervisor, as in v0.2.0's step 4: restart the machine, or follow
   `dcbot/SUPERVISOR-RESCUE.md` ("換上新的 supervisor（改了 supervisor.py 之後）"). `!restart`, a deploy and the
   updater restart only the bot; until the supervisor is replaced, nothing of the step back runs.
2. The new supervisor's first start installs the bot's packages once (it has no record of them yet). That pip
   failing does not stop the bot: it starts anyway. Only a new version that then fails to come up stays down as
   C-40 does, and steps back after 30 minutes.
3. Never push or revert a step-back commit (subject `dcbot: step back to <hash> while packages install (…)`); the
   supervisor removes it itself when it moves forward. To take one out by hand, run the command the refusal names
   in the engine (`bin/home-git rebase --onto <step>^ <step>`, see `dcbot/SUPERVISOR-RESCUE.md`), then `!restart`.

## v0.2.0 — 2026-10-19

### For people

- **A guided tour.** A new install ends by opening `#tutorial`, where Claude walks you through the bot one step at
  a time (buttons, 📋, a new channel and channels talking to each other, the boards, a reminder, attaching your own
  Claude) and lists what you did not try at the end. `/tour` in any channel starts it again; `/tour 提醒` does one
  part. It asks first, and costs about as much quota as a dozen or two ordinary messages.
- **Connecting Discord is one command.** `./install.sh discord` replaces `./install.sh --token`: paste the bot's
  token (read from the keyboard only); it finds the application, the owner and the server itself, prints the invite
  link, waits for the bot to join, then goes on to the whole install. No Developer Mode, no IDs to copy, no
  permission list to tick. The Chinese guide and messages name each Developer Portal button in Chinese and English
  (新建應用程式（New Application）), since the site may show either.
- **`#dashboard` has content right after the install**: the usage boards first, then the status boards, with what
  is waiting for you at the bottom.
- **Backups.** The install now asks, after making the instance, whether to back up your instance, conversation
  records, progress and the secretary's plan to private repositories (you can say "later"). Every backup address
  is checked first (no password or token in it, not the engine's, not readable without a login), and each
  repository gets a pre-push hook that asks again before every push, so nothing is ever pushed to a public
  repository; a refused address or push shows up in the system check. The sync now also commits and pushes
  progress's and the secretary's repositories, and the instance's `plugins.json`, `machines.json` and `backup/`.
  The system check shows each backup, its last push and commits waiting for more than a day, and fails on a
  repository anyone can read. The secretary's folder is now a git repository; the instance folder is readable by
  you only (700). README: "Backup and restore".
- **Restoring a machine from the backups.** `/install` asks whether this is a new install or a restore. A restore
  joins the instance, clones the records, progress and the secretary's folder back, and can take the old machine's
  name (`ops/instance.py take-name`), so the settings kept by machine name (plugins turned off there, its records'
  folder) apply again. It asks you first to confirm the old machine will not be used again.
- **Schedules can be changed without touching the code.** `plugins.<plugin>.schedules` in the instance's
  `plugins.json` overrides when a plugin's item runs, turns one off, or sets its time zone; `sync.interval` (the
  short way of `core.schedules.sync`) now really sets how often the sync runs (before, nothing read it). The sync
  and the engine update can change their interval but never be turned off. The system check says a run is late
  after two of its scheduled turns in a row were missed, from the schedule as it runs.
- The usage board now reads your subscription limits by default: once an hour, one smallest request on haiku,
  counted against your 5-hour limit. **This also applies to an installation that never set
  `usage.subscription_limits`**: after the update it starts reading them. To keep it off, set
  `"usage": {"subscription_limits": false}` in the instance's `plugins.json`. The install guide now asks your plan
  (`usage.plan`) and says what reading the limits costs.
- The usage boards no longer guess your plan: without `usage.plan` they say it is not set and how to set it (they
  used to say Max 20x). The percentages stay; the "× Pro" lines are left out.
- The secretary's morning brief, evening review and weekly review are now items in its own schedule file
  (`reminders.json`), so you can tell the secretary to change their times or turn one off. The install guide asks
  about the secretary and its times at the start. That file is always read first, and the briefings go only there.
- **Engine updates.**
  - With `core.update: "notify"` (the default), a new release is now announced once in Discord (the notification
    channel), saying how to update; before, only the system check and the status board showed it.
  - After an automatic engine update that changes the bot's packages (`dcbot/requirements.txt`), they are installed
    before the bot restarts into the new version. If that fails (no network, say), the bot keeps running the old
    version, the system check says so, and it is tried again an hour later; a version whose packages will not
    install is not rolled back for failing its start. Every way the engine moves by itself now restarts the bot
    when its code changed.
  - On a machine that follows releases (`core.update` is `release` or `notify`), a rollback by the bot's supervisor
    now stays on that machine: it no longer pulls the unreleased branch or tries to push. The next release you
    update to replaces it, and the system check says not to push it. A machine that cannot see its instance's
    settings and never read them now says that it does not update by itself.
  - The usage board no longer updates the engine on the machines it reads: it used to run `home-pull` there every
    hour over SSH, which took a new release without the load check `update.py` does. It now only reads usage there.
- **The engine is no longer pushed for you.** The sync and `bin/home-pull` pull the engine but never push it (a fork
  of a public engine is public, and a commit made on a machine can hold private values). If you change the engine
  yourself, push with `bin/home-git push`; the system check says how many commits are not pushed.
- The engine's git lock is renamed (`altention-git.lock`). **After updating, restart the machine once** (or have
  Claude replace the bot's supervisor as `dcbot/SUPERVISOR-RESCUE.md` says): restarting only the bot leaves the
  supervisor running v0.1.0's code with the old lock.
- The install now checks that `~/.local/bin` (where the `altention` command and Claude Code go) is on your shell's
  `PATH`, as a new terminal starts it. When it is not, it says which file to add which line to; it never edits
  your shell's files itself. An install that `ops/apply.py` runs on another machine skips this check.
- Skills: a skill that a new version brings is linked by the sync, without running the install again. A skill link
  you delete comes back: turn its plugin off instead. When the install cannot read the instance's settings (or
  cannot tell which machine a one-machine plugin runs on, as before Discord is set up), a plugin's skill is neither
  linked nor taken away, and the install says which ones wait and why; run it again once that is fixed.
- Moving the engine to another folder: `python3 ops/apply.py <machine> --moved-from <old folder>` replaces the old
  folder's cron lines and hooks; the system check lists any left and the command to run. Two engines on one machine
  no longer take each other's cron lines.
- `bin/home-git -C FOLDER` works only at the top of a plugin's own data repository, never inside the engine or the
  instance.
- Fixes:
  - Updating the bot's hooks no longer removes other hooks in the same group (conversation logs' own hook, or one
    you added with `/hooks`).
  - The system check counts only the last 24 hours of alerts, so old ones leave the dashboard.
  - The system check also checks `~/.local/bin/altention` (`core.entry`) and every skill of the engine, on every
    machine; a machine that does not answer over SSH is said as such, not as an error of the check.
  - A backup whose repository names no branch (made with `git init --bare`) is checked out from its real branch
    when cloned back.
  - The bot says "系統檢查" (the system check) instead of "doctor"; `/doctor` in Discord is Claude Code's own check.
  - Several attachments with the same name in one message (Discord calls every pasted screenshot `image.png`) are
    now saved as separate files; before, only the last one was kept and Claude got the same path for each.
  - Claude Code 2.1.302: in auto mode, a permission question that counts down to an automatic denial (about two
    minutes, for example a dangerous `rm -rf`) could not be answered from Discord: every button said the menu had
    changed. The buttons now work, the message says when Claude Code will deny it by itself, and a click after
    that presses nothing. The README lists 2.1.302 among the checked versions.

### For agents

This update is carried out by v0.1.0's own updater, which does not yet do what this version's updater does
(install the bot's packages, leave rollbacks behind, restart the bot on every path). Do these in this order.

1. Before updating an installation whose instance `plugins.json` has no `usage.subscription_limits` and has the
   usage plugin on: tell the user that the usage board will start reading the subscription limits (one haiku
   request an hour, counted against the 5-hour limit). If they do not want it, write
   `"usage": {"subscription_limits": false}` before the update. If they want it and `usage.plan` is not set, ask
   their plan and write it.
2. Before updating, look at the engine's commits not on its upstream (`git log --oneline @{u}..` in the engine).
   v0.1.0's updater carries every one of them onto the new release, the supervisor's rollbacks too (subject
   `dcbot: roll back … to <hash> (…)`), which would put old code back on top of it. If there are rollbacks, show the
   user the list and, with their OK, drop the rollbacks and keep their own commits before updating.
3. Update, then run `./install.sh`, even when the engine updated itself (`core.update`). It also makes the instance
   folder 700, checks the conversation records repository's remote and puts in its pre-push hook, and checks that
   `~/.local/bin` is on the shell's `PATH` (when it says how to add it, tell the user; do not edit their shell
   files). The progress repository gets the remote check on progress's next round, the secretary's folder (made a
   repository) when `remind.py --secretary` runs in step 5. A remote that fails the check is left as it is and
   written to the alerts: tell the user, and do not set it again by hand.
4. Replace the running supervisor, not only the bot: `!restart`, a deploy and v0.1.0's updater restart only the
   bot, and `./install.sh` leaves a running supervisor in place, so it keeps v0.1.0's code until the machine
   restarts: the old lock name (not exclusive with the sync, deploys and `bin/home-git`), v0.1.0's rollbacks (which
   try to push in release mode too), and no handling of packages pending. Restart the machine, or follow
   `dcbot/SUPERVISOR-RESCUE.md` ("換上新的 supervisor"): wait until the old supervisor has recorded the new version
   as good, then `state/stop` and `restart-requested`, wait for it to end, and start the new one. Never kill the
   supervisor alone: the bot, its child, stays and a second one starts.
5. Installation with planner on: the three wake-ups (morning 08:00, evening 22:00, weekly Sundays 21:00) are no
   longer in `planner/plugin.json`; they are items of planner's own schedule file `{data}/planner/reminders.json`
   (the first line `python3 planner/remind.py --where` prints). The update does not take their old cron lines out
   (apply keeps a line of a plugin that is on even when it no longer asks for it, and the system check then warns
   about them), so they keep coming at the old times. To move them, right after the update: back up the crontab
   (`crontab -l > ~/crontab.bak`), delete the three lines ending `# altention:planner/morning`,
   `# altention:planner/evening` and `# altention:planner/weekly` (`crontab -e`), then run
   `python3 planner/remind.py --secretary`: it adds `morning`, `evening` and `weekly` at the old times (or those in
   `planner.briefings`), with the same messages; one whose time today has just passed (the old line sent it) is
   marked sent, so it does not come again. The old lines went by the machine's time zone, the items go by
   `core.timezone`: when the two differ, convert the old times to `core.timezone` and write them into
   `planner.briefings` before running it. Do both: items added with the old lines still there send each one
   twice; lines deleted without the items, none. The secretary's own `CLAUDE.md` is not rewritten: the section on
   changing the times is in `planner/CLAUDE.template.md`.
   - `reminders.json` is now always read, and first, even when `planner.schedule_files` lists other files, and
     `--secretary` writes only into it. If that setting is set and `morning`, `evening` and `weekly` are in another
     file it lists (the old `--secretary` wrote them into the first one), move those three items into
     `reminders.json`; left there, a later `--secretary` adds them again and each comes twice.
6. Run `python3 ops/doctor.py` and go through what it says:
   - `sync.engine-ahead`: the machine has engine commits that are not pushed (the sync used to push them). Show the
     user what they are before anyone pushes with `bin/home-git push`; the engine's remote may be public. When the
     fix says 「不要直接推」, rollbacks are among them: never push those.
   - The backup lines: an installation from before this version has none. Ask the user whether to set them up
     (README "Backup and restore"); every address goes through `ops/remote.py set`, which checks it first.
   - If `sync.interval` was set in the instance, the sync now runs at that interval once `./install.sh` has run.
7. From the next update on (this version's updater): an update to a release leaves behind rollback commits the
   supervisor made on that machine. When the update stops with 「不猜哪些要留」, that machine has a rollback and
   other commits of its own on top of the release: show the user `git log --oneline <the release tag>..HEAD` in the
   engine, keep their own commits and drop the rollbacks, then update again. Never put a rollback on a new release.

## v0.1.0 — 2026-10-15

### For people

First public release.

- Discord bot for Claude Code: one long-lived conversation per channel, running in tmux on your own machine; attach
  a conversation you started yourself with `/attach`; questions, plans and permission prompts as buttons; subagents and
  workflows as threads; terminal channels and a file browser; channels that post to and talk with each other
  (`altention dc post|read|notify`); the bot changes and redeploys itself, with automatic rollback.
- Install with one script and a guided `/install` skill; `doctor` checks every machine and says how to fix each
  problem.
- Bundled plugins, each optional: chatlog (conversation logs), progress (project progress), usage, status, planner
  (reminders), council (a decision skill).
- Your settings and data live in a private instance repository, apart from the engine; see `examples/instance/`.
- Security: the bot obeys one owner; the token stays in a mode-600 file that Claude Code is kept from reading; the
  engine does not update itself unless you set `core.update`. See `SECURITY.md`.
- Tested with Claude Code 2.1.288 and 2.1.296 (2.1.299: screens checked, in daily use). Several machines over SSH
  work but are marked advanced: not yet verified from a clean install. See the README's known limitations and roadmap.

### For agents

None.
