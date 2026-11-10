# Changelog

Every release has a `## vX.Y.Z — date` heading, newest first. The release tool publishes the text under a version's
heading as that version's notes.

Each version has these sections:

- **For people**: what changed, for you to read.
- **For people (zh-TW)**: the most important of those points in plain Chinese, the ones that matter most first.
  The update notices in `#update` quote it in an installation whose language (`core.language`) is zh-TW, each
  point cut to its bold lead; a version without it is quoted from For people. Write each point on one line: the
  card joins a wrapped line with a space, which shows in the middle of Chinese.
- **For agents**: what an agent updating an installation to this version needs to know, in plain words (settings to
  move, a step to run, something to check afterwards), or `None`. A planned `/update` command (not in this version
  yet) will read it, tell you what it says before doing anything, and never carry it out on its own.

## Unreleased

## v0.5.2 — 2026-11-11

### For people

- **Claude started by the bot no longer asks before reading your attachments.** Every conversation the bot opens can
  now read that channel's own attachments folder, on the machine where Claude runs, so reading an attachment no longer
  stops on a "Read outside the working directories" question. Only that channel's folder is opened to it. A Claude
  Code you started yourself and connected with `/attach` is unchanged and may still ask.
- **A message you type after a question was cancelled at the terminal is no longer taken as its answer.** When Claude
  asks with buttons (or shows a plan to approve) and the question is cancelled in its terminal (Esc or Ctrl-C, also
  through `!key`), the bot now closes the buttons and sends your next message to Claude as usual; before, that message
  was swallowed as the answer.
- **altention dc post --attach no longer attaches the wrong pane when the tmux target is not there.** tmux answered for
  the current pane instead of failing when a target named a window or pane that does not exist (cv:7); the post is
  now refused and says what tmux answered.
- **A question Claude Code asks on its own in the background no longer takes your answer.** When Claude Code tidies
  its memory in the background, it can ask a question under the same conversation; the bot used to post it as
  buttons, and your answer went to it instead of to Claude. A question that is not in the conversation's own record
  is now left to the terminal, where Claude Code deals with it.
- **Finishing #setup on a fresh install no longer stops waiting for #secretary.** The last step looked for the new
  channel in a channel list kept for 60 seconds, so it could miss a channel that was already there; running it again as
  told made a #secretary-2 and never got to #chat. It now reads a fresh list, and running it again uses the channel that
  is already there.

### For people (zh-TW)

- **機器人開的 Claude 讀附件不再先問。** 機器人開的每段對話都讀得到這個頻道自己的附件資料夾（在 Claude 所在的那台），讀附件不會再停在「Read outside the working directories」的問題。只開放這個頻道的資料夾。你自己開、用 /attach 接上的 Claude Code 不變，還是可能會問。
- **在終端機取消 Claude 的提問後，下一則訊息不會再被當成回答。** Claude 用按鈕提問（或貼出計畫）時，在終端機按了 Esc 或 Ctrl-C（包括用 !key），機器人現在會關掉按鈕，你接著打的字照常送給 Claude；以前那則會被當成答案吞掉。
- **`--attach` 的 tmux 目標不存在時，不會再接到別的窗格。** 以前 tmux 會改用目前的窗格（例如寫了 `cv:7` 但沒有第 7 個視窗）；現在會拒絕，並說 tmux 回的是哪個。
- **Claude Code 在背景自己問的問題，不再收走你的答案。**它整理記憶時會用同一個對話提問，以前機器人把它貼成按鈕，你的答案就被它收走；現在這種問題不貼，交回終端機由 Claude Code 自己處理。
- **新安裝 #setup 的最後一步，不會再說等不到 #secretary。**以前它讀的是 60 秒前的頻道清單，明明開好了還說等不到；照提示重跑會多開 #secretary-2，#chat 也開不出來。現在每次讀新的清單，重跑也會沿用已經開好的頻道。

### For agents

1. `dchook --ask` posts a question only once its tool call (`tool_use_id`) is in the conversation's file
   (`transcript_path`), waiting up to 60 s for the line. One that never shows there (Claude Code's background memory
   pass runs with no transcript) gets no card: the hook makes no decision, leaving it to the terminal, and writes an
   `AskElsewhere` event (connector kind `ask_elsewhere`), which the bot only notes in #audit. A subagent's question
   (`agent_id`) and an event without a file or id go on as before.

## v0.5.1 — 2026-11-07

### For people

- **Attachments now reach a channel whose Claude works on another machine.** Before, the file was kept only on the
  bot's machine, and the Claude there was given a path it could not open. Files up to 25 MB are now copied there; a
  larger file, or one that cannot be sent, is described with the machine it is on and its original link.
- **A `/**` in text a command only hands over (a code comment in a heredoc, a quoted string) is no longer refused as
  naming a secret.** A wildcard there is not taken as a path; a secret's path written out in full is still refused
  there, and a wildcard the shell expands (`cat /**`) is refused as before. Text handed to a shell to run (`bash -c`,
  `ssh`, `eval`, a heredoc fed to one, `$(…)`) is now read as a command too, so a secret's path inside `bash -c '…'`,
  which slipped through before, is refused.
- **`dcctl` on a machine that does not run the bot hands its command to the bot's machine.** The Discord token is
  needed only there now: from any other machine, the whole command goes over SSH to the bot's machine and its output
  and exit status come back as they are; when that machine cannot be reached it says which one, and never falls back
  on a token left here. doctor now asks Discord whether the bot's token still works, so a dead token shows there rather
  than as a 401 later, and checks that every other machine can reach the bot's.
- **After a reboot, attaching a conversation again puts it back in its own channel.** Resume it in a new tmux window and
  `/attach` it: once the old pane is gone, the same conversation, or the same window and folder, takes the channel it
  had instead of opening a second one. A new conversation started there takes the channel too, and the channel says
  how to go back to the old one. What a channel says when its pane is gone now gives steps that work.
- **A channel you archived is taken back when its Claude is attached again.** /archive puts a note in front of the
  channel's topic, and attaching the same tmux window again did not see past it, so a second channel was opened; the
  archived one now comes back out of the archive, with its history.
- **An attachment keeps the name you gave it.** Discord replaces a file name that is not plain ASCII (「截圖.png」) with
  a string of letters and digits, and Claude was given that; it now gets the file's own name. A very long name is cut
  short, keeping its suffix (.png and the like).
- **The tour no longer mentions a board that is off, and the README says what Discord may ask on the way** (a check
  that you are human, your password, the app opening first).
- **The tour answers the channel that greets it, and an archived channel says to send your message again once it is
  back.**
- **#setup takes any talk limit you type.** A number typed under 「其他」 (3, say) is no longer refused; any whole
  number from 0 up is taken, as `/talklimit` takes it.
- **#setup's plans follow the official pricing: Pro, Max, Ultra.** The ratios are the weekly allowances the pricing
  page gives: Max is 5 times Pro, Ultra 10 times; the page no longer lists Max 20x. If yours is set to Max 20x and your
  plan is now Ultra or Max, set `usage.plan` to `["Ultra", 10]` or `["Max", 5]`.
- **A plugin written for a newer engine now says so.** Before, the official list and the update card called its
  plugin.json unreadable; now they say it needs a newer engine.
- **Posting to Discord from another machine no longer waits forever.** When the command handed to the bot's machine
  over SSH has not ended in 15 minutes, it is stopped and says so; it may have been done there, and it is not sent
  again.
- **The secrets guard now looks at the patterns of a search too.** A Glob pattern or a Grep file filter that could
  match a protected file (`~/.ssh/*`, `**/*` from your home folder) is refused, and so is a Grep over your whole home
  folder or `/`. Searches inside a project are not affected.
- **The guides now say plainly that Claude conversations on one account are not isolated from each other.** Any of them
  can send keys to another, including answering a permission dialog: permission dialogs and approval buttons guard
  against mistakes. For real isolation, use another account, machine or sandbox.
- **`/btw` answers are posted again when Claude Code draws the answer box the newer way.** Some installations of
  Claude Code 2.1.316 draw it without the line of "▔" over it, the question shown above as your message. The bot did
  not see that box: it said after 90 seconds that no answer box came up, the answer was never posted, and the box
  stayed open until `!key Escape`. Both ways are read now.
- **After a restart, commands you sent while the bot was offline are not run again, except ones that only show
  something.** The bot hands the messages it missed to Claude once it is back; commands were run too, so a `!restart`
  sent just before a restart restarted it a second time. Now only plain messages and `!help`, `!status`, `!recap`,
  `!screen` and `/btw` are handed on; for any other command (`!restart`, `!key`, `!cwd`, `/clear`, …) the channel
  says it was not run, one line each, to send again if still wanted. In a terminal channel nothing typed for the shell
  is run again, since the shell runs every message; when many were not run, one message lists the first few and how
  many.
- **Keys on Claude Code's screen are masked before the bot posts it.** A permission dialog, a panel, `/screen` (the
  picture and its text), `!key`, a shell's output in a terminal channel and the screen the bot adds when something did
  not go through all showed the terminal as it was, keys included. They are now masked as Claude's replies already
  were, with the same small line saying so. When a key is on the screen, the `/screen` picture is drawn without
  colours.
- **An installation with no permission mode now has it written down, and is asked to choose.** An installation that
  never had `dcbot.default_permission` written ran its conversations in bypassPermissions: Claude did anything without
  asking. This update writes that mode out, so nothing changes; #audit and #update say so, and #setup asks whether to
  change it (auto is recommended). From now on, with no mode set, a channel opens no conversation and says how to
  choose one.
- **A channel in print mode (`!mode print`) now follows the permission mode too;** before, it always ran in
  bypassPermissions. Print mode cannot show a permission question, so with manual, tools that need permission are
  refused there: switch the channel back with `!mode tmux`.
- **After an update, #setup asks about optional plugins (such as display) you were never asked about.** Each is
  asked once; ones you already answered, or that are already on or turned off, are not asked. A new installation
  is asked the first time #setup opens.
- **#setup no longer says that Claude did not start when the bot restarts before setup is done.** Before, it waited 90
  seconds, said "Claude 沒有啟動成功", and you had to press "再送一次".
- **#setup now always opens during the install.** Before, if the bot came online before the install was done,
  #setup did not open until the bot was restarted.

### For people (zh-TW)

- **沒設權限模式的安裝，這次會寫明並問你要不要改。** 以前沒設 `dcbot.default_permission` 時，機器人默默用 bypassPermissions（Claude 做什麼都不先問）。這次把它寫進設定、行為不變，#setup 會問你要不要改（建議 auto）；以後還沒選的話，頻道會請你先到 #setup 選一個，選好再傳一則訊息就會開。`!mode print` 的頻道也改成照你設的權限模式。
- **貼出 Claude Code 的畫面前會先遮掉金鑰，祕密守門也更準了。** 權限對話框、`/screen`、`!key`、終端機頻道的輸出都會先遮；Glob、Grep 的搜尋條件可能碰到受保護的檔也會擋，`bash -c '…'` 裡的祕密路徑不再漏掉，指令只是交出去的文字裡的 `/**` 不再誤擋。說明也寫清楚了：同一個帳號的 Claude 之間沒有隔離。
- **頻道的 Claude 在別台機器時，附件讀得到，`dcctl` 和貼文也交給機器人那台。** 25 MB 以內的附件會送過去；別台的 `dcctl` 經 ssh 轉給機器人那台，token 只要放那台；轉過去的指令 15 分鐘沒結束就停掉並照實說，不會一直卡住。
- **機器重開或封存後再接上，會回到原本的頻道。** 同一個對話，或同一個 tmux 視窗和資料夾，會接回原本的頻道和紀錄，不會多開一個。
- **#setup 更順了：安裝時一定會開，重啟也不會誤報「Claude 沒有啟動成功」。** 從舊版更新上來會補問沒問過的可選插件（例如 display）；方案改成 Pro、Max、Ultra，來回上限可以打任意次數。
- **機器人重啟後不會再補做離線時收到的指令，只是查看的指令（例如 `!status`、`!screen`）照常做。** 也修好了：`/btw` 新畫法的答案框、中文檔名的附件、需要新版引擎的插件會直接說要先更新引擎。

### For agents

1. `altention dc post`, `read` and `notify` no longer send a command again when the SSH line drops: the way to
   the bot's machine is found first (`ssh <alias> true`, trying the next alias), then the command is sent once; a
   255 while it ran says it may have been done. Check before sending it again.
2. `--attach` of a pane whose conversation, or tmux window and folder, belongs to a channel whose own pane is gone now
   takes that channel; its ID stays the same. Nothing to do when updating.
3. A Grep over the home folder or `/` is refused now: search the project folder, or name a narrower path.
4. `dcbot/deploy.sh -h`/`--help` now prints how to use it and does nothing; before, it deployed with "--help" as the
   commit message (checks, commit, push, restart). An option it does not know is refused with exit 2.
5. An installation without `dcbot.default_permission` gets `bypassPermissions` written by the bot's first reconcile
   after the update; #audit and the instance's commit say so. If #audit shows the reconcile did not finish, new
   conversations will not open: set `dcbot.default_permission` in plugins.json yourself (auto, manual or
   bypassPermissions; ask the person which) and commit it.
6. `setup.py reconcile` now asks every install offer `offers.open_for()` allows (never answered, or 之後再說 fewer
   than three times), not only the laters: an installation older than the question, or one with no official list
   yet when #setup asked, was never asked. Before its plan it looks at the official list
   (`catalog.look_before_asking()`: forced, at most once every 10 minutes, 60 s at most; not reached, the kept
   list is used and the reconcile goes on). install.py's new step `core.catalog` does the same look on the bot's
   machine while setup is not done (`skip` when not reached; it never stops the install).
7. The bot started before the install made #setup's folder now looks for it (its `.claude/settings.json`) every
   5 s for as long as it runs and opens #setup then, said once in #audit; setup finished in the terminal
   meanwhile opens nothing. No restart of the bot is needed.

## v0.5.0 — 2026-11-02

### For people

- **The limits board works again when only the weekly limit comes back.** Claude Code can now report just the week
  (seen on 2.1.312 and 2.1.316), and the board then said the format had changed and stopped recording the week. It
  now shows the week, says the 5-hour window was not provided, and keeps recording.
- **Formulas, tables and diagrams in a reply can be drawn as pictures.** With a plugin that draws them (such as
  display) on the bot's machine, a Claude's final reply that has formulas, tables or mermaid diagrams is handed to
  it before it is posted, and comes out as text and pictures in order. When it takes more than a few seconds the
  channel shows "⏳ 整理排版中…"; when the plugin fails, the reply is posted as before.
- **A plugin from outside the engine gets its new versions from the update card.** When a plugin's source has a newer
  release, the card in `#update` says so with that release's points (from the plugin's own CHANGELOG), even when the
  engine is up to date. 「更新」 moves it on this machine and writes the new version in the instance, and the other
  machines follow in their next round. A release that would do more on this computer (a new schedule, hook, rewrite of
  replies, skill, setup step, data or channel), fails its own test, needs a newer engine, or meets changes of yours in
  the plugin's folder is not moved by the button: 「請 Claude 幫忙」 goes through it with you in `#setup`, and a new hook
  or rewrite of replies (they see your conversations) is asked about on its own. Saying 「引擎回到更新前」 in `#setup` puts the
  plugins of the last change back too. A source that does not answer no longer holds up the update: it is given up
  after about a minute without progress and skipped by later rounds, longer each time (up to 6 hours), until it
  answers again; doctor says since when.
- **Official plugins can be found, and put on by saying so in `#setup`.** Once a day each machine looks at the
  altention organisation on GitHub for its public `plugin-<name>` repositories with a release; `plugins.py list`
  shows them last, under 「可以裝的」. Say 「裝 <name>」 in `#setup`: Claude first tells you what it would do on your
  computers (schedules, hooks, a rewrite of replies, skills, the tools it needs), asks once more about anything that
  sees your conversations, and only then writes it into the instance and installs it here; the other machines that
  use it are named, and brought in line only when you say so. A plugin new on the list is said once in `#update`;
  nothing is installed by itself. When GitHub cannot be reached, the list from last time is used. `/tour` has a step for it too.
- **A plugin's private data can be backed up, and comes back on a new machine.** The backup questions in `#setup`
  now list the data an external plugin says is private, each in a private repository of its own
  (`altention-<plugin>-<data>`). Installing on another machine clones it back before the plugin's setup runs.
- **What a plugin sets to run by itself as Claude works (a hook) is installed once, and taken out when the
  plugin stops.** Each install used to add one more copy of an external plugin's hook. Now one copy stays, and
  all of them go when the plugin is turned off on a machine or becomes a draft. Hooks of your own are left alone,
  even ones that run a file in the plugin's folder; for a week doctor says which hooks were taken out.
- **The project moved to github.com/altention/altention.** Old links and clones keep working: GitHub sends them to
  the new address.
- **A channel attached to a pane stays attached when Claude restarts there.** When Claude Code starts again in the
  same tmux pane and goes on with the same conversation (`claude --resume`, `claude -c`, or the whole pane
  respawned), the channel picks it up again by itself: no `/detach` and `/attach` needed. A new conversation started
  in such a pane is still not followed.
- **Discord can open a plugin's pages.** A plugin may offer pages that open inside Discord (a Discord Activity).
  A Claude posts a message with an 「打開」 button for one of them, and pressing it opens that page right there.
  Pages with anything private on them open for you alone; someone else who presses gets a note only they see.
  Activities have to be set up first, following that plugin's install steps; until then the button says so.
- **Before a plugin is turned off you are told what it costs, and its channels say when it stops.** Claude first
  tells you what you lose (`plugins.py lose <name>`: what it does for you, the plugins that need it, its schedules,
  hooks and channels; its data stays) and asks. Once it is off on a machine, each channel it posts to (a board, its
  Claude's channel) gets one line saying on which machine it stopped and, for a plugin on every machine, which go
  on; turned on again, one more. doctor lists the plugins turned off on purpose, and the lines one turned off left
  in the crontab that apply never touches (commented out, or the old format); install's summary names what it took
  out or put back.
- **With several machines, a setting changed on one now takes effect on the others the next time apply runs
  there.** Before, they could keep the old instance though apply said all went well.
- **Posting by a channel's name is held up by Discord's rate limit far less, and channels opened at once no longer
  make the same category twice.** Each machine reads the server's channel list at most once a minute for every post
  and notification there; one given a channel's ID reads none.
- **Tap 📝 on a drawn picture to get what it was drawn from.** When a plugin attaches the source of a picture it drew
  (a formula's LaTeX, a diagram's mermaid, a chart's code), tapping 📝 on that message makes the bot answer with what
  it is, which tool made it and the source in a code block (as a file when it is long). Add the 📝 reaction yourself:
  the bot does not add it. Sources are kept for 30 days, with anything that looks like a key or token masked first.
- **The bot keeps one short line on what each channel waits on or is doing, for plugins to show in a later
  release.** It is the question a channel waits for you to answer (or the first sentence of what its Claude last
  said), and the first sentence of what a busy Claude last said along the way. Each line is masked for anything that
  looks like a key or token and cut to 80 characters. It is a piece of your conversations, kept on the bot's machine.
- **A message a script sends with `dcctl send` no longer pings anyone by accident, and is never cut short.** A
  `<@…>` or `@everyone` in its text now pings no one unless the script adds `--mentions`. A text over 2000
  characters is not sent at all (before, it was cut to 2000 without a word): use `altention dc post`, which splits
  long texts. If you wrote scripts of your own that use `dcctl send`, look at them when you update.
- **A plugin that needs something this engine does not offer is paused on that machine, and you are told why.** Its
  schedules and hooks stop there, like turning it off, and doctor and the update card say what it needs and how to
  fix it, usually by updating the engine or the plugin. A plugin that does not say what it needs runs as before.
- **A plugin that needs more from you, like a tool to install or an account or key on another site, now tells you
  what is left.** install.sh and doctor say how many things a plugin still needs from you. Say 「幫我裝完 <plugin>」
  in `#setup`, and Claude checks every item again and tells you what is done, what is left and what comes next. A
  step this computer cannot check is asked about once, and your answer is kept. Checking installs nothing. Claude
  then takes you through what is left one thing at a time: once you say yes it installs a tool or does a step the
  plugin can do by itself, gives you the line to paste for anything that needs administrator rights or another
  machine, and checks each step before going on. You can stop any time: say 「接著裝 <plugin>」 later and it goes on
  from what is not done yet. A part of a plugin that needs one of those steps, such as a schedule, waits until the
  step is done and then runs from then on. A new version that would ask more of you (a new tool, step or key) waits
  for your yes on the update card.
- **A key a plugin needs is typed in your own terminal, not in a chat.** When a step asks for a key, `#setup` gives
  you one line to run in your own terminal. What you paste is not shown, is kept only on this computer (and on the
  other machines you say yes to), and is never backed up; any Claude that tries to read it is stopped, as with the
  bot's token.
- **A key can also be typed into a box in Discord.** When a plugin needs a key, Claude may post a 「填入」 button
  instead; only you can press it. What you type goes to the bot's machine and is stored as from the terminal; it is
  not posted in the channel and no Claude sees it. It does pass through Discord, and Discord's box shows what you
  type: to keep a key off Discord, use the terminal line instead.
- **A plugin left out of the instance but installed here before is now mentioned by doctor.** One line says it is a
  draft that does not run here, that its folder and setup record are still there, how to use it again, and what to
  delete if you do not want it.
- **A plugin with no channel of its own now says in the notification channel when it is turned off or on.** A plugin
  that changes every reply, for one, no longer stops or starts on a machine without a word.

### For people (zh-TW)

- **回覆裡的公式、表格、流程圖可以畫成圖，按 📝 看原文。** 機器人那台開著會畫圖的插件（例如 display）時，Claude 正式回覆裡的公式、表格或 mermaid 流程圖，貼出前會交給它畫成圖，跟文字照順序貼出；超過幾秒會先顯示「⏳ 整理排版中…」，插件失敗就照原本的方式貼。畫成的圖按 📝（自己加這個反應），機器人會回它是什麼、用什麼畫的，原文放在程式碼區塊（太長就給檔案）；原文留 30 天，看起來像金鑰或 token 的會先遮掉。
- **外部插件出新版時，更新卡會告訴你，按「更新」就換好。** 插件的來源有新版時，#update 的卡片會列出它的重點（從插件自己的 CHANGELOG 來），引擎已經是最新的也一樣；按「更新」就在這台換好、把實例裡的版本改掉，別台下一輪自己跟上。新版要在這台多做事（多了排程、hook、改寫回覆、skill、安裝步驟、資料或頻道）、自己的測試沒過、要更新的引擎、或插件資料夾裡有你的修改時，按鈕不會換：按「請 Claude 幫忙」在 #setup 跟你一項項看，新的 hook 或改寫回覆（會看到你的對話內容）會另外再問一次；說「引擎回到更新前」時，最後一次換的插件也會退回去。插件的來源連不上時不會卡住更新：大約一分鐘沒有進展就先放棄，之後幾輪先跳過它、隔得越來越久（最多 6 小時），連得上了再照常，doctor 會說它從什麼時候開始連不上。
- **官方插件在 #setup 說一聲就能裝。** 每台每天看一次 GitHub 上 altention 組織公開的 `plugin-<名稱>`，`plugins.py list` 最後「可以裝的」會列出來；在 #setup 說「裝 <名稱>」，Claude 先告訴你它會在電腦上做什麼（排程、hook、改寫回覆、skill、要的工具），會看到對話內容的另外再問一次，你同意了才寫進實例、在這台裝好；別台用得到的會列出來，你說好才裝。清單多了新插件，#update 會說一次，不會自己裝；連不上 GitHub 就用上次的清單。還有事要你做的（裝工具、填金鑰），Claude 會一步一步帶你。
- **外部插件的私人資料可以備份，換電腦時會自己拿回來。** #setup 的備份題目會列出插件寫明是私人的資料，每一樣各用一個 private repo（`altention-<插件>-<資料>`）；在另一台安裝時，會先從備份 clone 回來，再跑插件的安裝步驟。
- **關掉插件之前會先說會少掉什麼；它的 hook 不會重複裝，關掉就拿掉。** 關之前 Claude 會告訴你少掉什麼（`plugins.py lose <名稱>`：它幫你做的事、要靠它的插件、排程、hook、頻道；資料留著），問過才關；它的頻道會說一句在哪台停了，再打開也說一句。hook 跑幾次安裝都只留一份，插件在某台關掉或變成草稿時也會拿掉；你自己加的不動，一週內 doctor 會說拿掉了哪些。
- **只拿到週額度時，額度看板也讀得到了。** Claude Code 現在可能只回報週額度（2.1.312、2.1.316 看過），看板之前會說「格式變了」，週用量也停止記錄；現在照常顯示週額度並繼續記錄，5 小時那段註明這次沒有提供。

### For agents

1. Plugins may declare `rewrite` in plugin.json: `command` (with `{python}`, `{plugin}` and the bot's
   `{out}`), `timeout` (seconds, at most 60), `when` (a regex). Input and output are described in
   `dcbot/bot/rewrite.py`. Nothing to do when updating.
2. An external plugin's new versions are the tags in its source. The card quotes each one's own `CHANGELOG.md`, in
   the sections this file uses. Its `plugin.json` may now say `engine` (`">=X.Y.Z"`: the engine it needs) and
   `test` (a command run in a copy of the new version; it is taken only when the command exits 0).
3. `altention update plugin <name> <vX.Y.Z>` moves one plugin, on the bot's machine, and writes the instance; the
   other machines follow in their update round. `#setup`'s Claude runs it after asking the person (the update
   skill's step 7). Each plugin's source is mirrored in `~/.local/state/altention/plugin-src/`, with the backoff for
   sources that do not answer in `unreachable.json` there; leave them to the update rounds.
4. A plugin's data is backed up as `plugin:<plugin>/<data>` (`python3 ops/remote.py set plugin:<plugin>/<data> <URL>`,
   or `ops/setup.py backup-set`), and only on a machine where that plugin is on.
5. After updating, the first apply on each machine takes out the extra copies of an external plugin's hook in
   `~/.claude/settings.json`, and all of that plugin's hooks where it is off, disabled or a draft. A hook counts as
   the plugin's only when it is exactly the one its `plugin.json` declares (event, matcher, command and timeout); a
   hook the person wrote by hand that is exactly the same counts as the plugin's too.
6. The repository moved to `https://github.com/altention/altention`. `git remote set-url origin
   https://github.com/altention/altention.git` in the engine may be run but is not needed: GitHub sends the old
   address to the new one. A plugin's `source` at an old address works the same way.
7. Plugins may declare Discord Activity pages in plugin.json: `"activity": {"pages": [{"id", "title", "access"}]}`,
   `access` being `owner` (the default: the owner alone) or `guild` (anyone in the server). `altention dc post
   <channel> --activity <plugin>/<page> [--arg k=v …] "text"` posts a button that opens one; on a press the bot
   checks the page's access, writes the intent (user, channel, guild, page, args) to
   `~/.local/state/altention/activity/<plugin>.json` and opens the Activity. `dcctl app --json` gives a plugin the
   bot application's ID, the owner's and server's IDs, and whether its Discord Activities are on. The bot needs
   discord.py 2.6 or newer: its supervisor installs it when it starts the new version (`requirements.txt` changed).
   Nothing to do when updating.
8. `ops/release.py` publishes an external plugin too, the way it publishes the engine: `check-plugin <name>`,
   `publish-plugin <name> <X.Y.Z> [REV] [--push]` (one public commit a release, made from REV's tree in
   `~/altention/plugins/<name>`, its own repository; the tag goes on that repository's remote too) and
   `seen-plugin <name> <X.Y.Z>`. Its public repository is `"release": {"plugins": {"<name>": {"repo": "<git URL>"}}}`
   in the instance's `plugins.json`, the author `release.author`; the privacy scan counts those repositories as
   public. After `--push` it reads the public repository with no login and says when it cannot (not public yet?).
   It never makes or renames a repository. Nothing to do when updating.
9. The official plugin list (`altention/catalog.py`): each machine's update round looks at it once a day (one API
   request with the last ETag, no token; tags by `git ls-remote`, `plugin.json` and `CHANGELOG.md` from
   raw.githubusercontent.com) and keeps it in `~/.local/state/altention/catalog.json`; what `#update` was told is in
   `catalog-told.json` there. `python3 ops/plugins.py list --catalog` also lists the repositories left out and why,
   `--catalog-now` looks now (at most every 10 minutes). doctor: `plugins.catalog` (no fresh list for 7 days) and
   `plugin.<name>.catalog` (a plugin taken from the organisation's public repository the list no longer has).
10. `python3 ops/plugins.py add <name> [vX.Y.Z]` (in `#setup`, `ops/setup.py plugin-add`) says what the plugin would
   do and writes nothing; with `--yes` it writes `"plugins": {"<name>": {"source": "<its https URL>", "version"}}`
   into the instance, commits and pushes it (`setup.py` then runs `./install.sh`); one with hooks or a `rewrite`
   also needs `--hooks-ok`, given only after the person said yes to that question. It never changes an entry the
   instance already has. `ops/setup.py plugin-apply <plugin> <machine>` brings another machine that plugin runs on
   in line (`ops/apply.py` there: everything it has not taken yet), for no other plugin or machine.
11. A plugin's new version that adds a `rewrite` or a skill is now one that does more here (`perms`): the update card
   no longer takes it with one press, and `#setup`'s Claude asks about a new rewrite on its own, as about a hook.
12. `python3 ops/plugins.py lose <name>` prints what turning a plugin off loses: say it and ask before writing its
   `disabled_on`. apply notes each plugin's place per machine
   (`~/.local/state/altention/apply/<machine>/placements.json`) and, when one goes from on to off, disabled or draft
   there (or back), posts one line with `altention dc notify` in each channel its `plugin.json` declares; the first
   apply after updating only notes. doctor: `plugins.disabled`
   (said in its plain output), and `plugins.would-apply` names a turned-off plugin's lines that apply leaves
   (`leftovers` in `plugins.py report`). Nothing to do when updating.
13. `ops/apply.py` now pulls each machine's instance every time, also when the machine list names no
   `instance.repo` (before, those machines kept their instance as it was, though apply said all went well). A
   machine that has none yet is joined to the list's `instance.repo`, else to this machine's instance origin when
   another machine can reach it (a URL or `user@host:path`, not a folder or `file://`); apply then says
   「<machine> 還沒接上實例，已經用 <origin> 接上」 and doctor says `instance.joined-by-apply` for a week. Nothing to
   do when updating.
14. Plugins may post to the reserved targets `@audit` (the bot's own #audit) and `@notify` (the default notification
   channel): `altention dc post @audit "…"`, `altention dc notify -c @notify "…"`. The bot finds them from its own
   settings, so a plugin needs neither name and reads no channel list; posts and notifications only. See
   `docs/plugins/discord.md`. The channel list a name needs is read once a minute per machine and shared
   (`dcbot/state/channel-list.json`). Nothing to do when updating.
15. `altention dc post` and `altention dc notify` take `--wait N` (1 to 600 seconds): they end only once the bot has
   put the message on Discord, exit 0 and print its ID on the last line; 3 means it was still queued when the time
   was up (it is sent later: do not send it again), 4 that the bot did not show it (why on stderr). Without
   `--wait`, 0 still means queued only. `dcctl send <channel ID>` reads no channel list (so a thread's ID works too)
   and prints the message's ID. See `docs/plugins/discord.md`. Nothing to do when updating.
16. A plugin can attach what a picture was drawn from, shown when the owner taps 📝 on the message:
   `{"kind", "lang", "tool", "source"}` (source at most 64 KB) in `<file>.source.json` beside a file given to
   `altention dc post --file`, or in a rewritten reply's message as `"sources": {"<image path>": {…}}`. An older
   bot ignores both. See `docs/plugins/discord.md`. Nothing to do when updating.
17. The bot now keeps two more fields: `text` on each thing a channel waits on the owner for, and `doing` on each
   channel. Each is one line of the conversation (masked, no control characters, at most 80 characters), left out
   when there is none. The files they are kept in are the bot's own and their format may change: plugins should not
   read them, but wait for the read-only commands planned for a later release. Nothing to do when updating.
18. `dcctl send` changed in two ways. Mentions in its text (`<@…>`, `@everyone`, roles) no longer ping anyone:
   a script that means to ping should add `--mentions`. A text over 2000 characters is no longer cut to 2000 but
   refused whole, with exit code 4 and 「沒有送出：超過 2000 字」 on stderr: a script that sent long texts this way
   should use `altention dc post`, which splits them. Look through your own scripts for `dcctl send` when updating.
19. plugin.json: the engine no longer reads `commands[]` or `requires[].data`. A plugin that still has them loads
   as before; `python3 ops/plugins.py list` and `ops/release.py check-plugin` remind its author to delete them.
   `provides`, `secrets[]` and the core's `cron[].why` are reserved: checked, not read. The core's `links[]` is gone
   (the sync's links are listed in `docs/architecture.md`). Every field and what reads it:
   `docs/plugins/plugin-json.md`. Plugin authors: delete the two keys; nothing to do when updating.
20. Plugins now name the engine interfaces they use: plugin.json `"uses": {"<interface>": <version>}`, against
   `altention/interfaces.json` (`altention dc post`/`read`/`notify`, `dcctl`, `dcremote files`, `rewrite`,
   `activity.pages`, the plugin.json tokens, part of `altention.config`, and a few transitional ones). A plugin on a
   machine whose engine does not offer what it uses is blocked there: like turning it off, its schedules, hooks and
   skills are taken away, and `ops/plugins.py list`, doctor (`plugins.blocked`) and the update card say which
   interface and how to fix it. A plugin without `uses` runs as before; `plugins.py list` reminds external plugins
   to add it. `ops/release.py check-plugin` now refuses a plugin that runs or imports engine files no interface
   lists, or uses an interface its `uses` does not name; transitional ones (the bot's state files, the transcript
   reader, `dcbot/bot/filecore.py`; see 17) only get a reminder for now and will be refused once the read-only
   commands that replace them are out. Plugin authors: add `uses` before publishing. Nothing to do when updating.
   A plugin may also offer interfaces of its own in plugin.json `"interfaces"` (the same shape). They count only on a
   machine where that plugin is on; a name the engine already has is refused, and when two plugins on a machine
   offer the same name, the one whose name sorts first counts and the other's does not.
21. plugin.json can now have `guide[]` (steps the owner takes elsewhere) and more fields in `needs[]` (how to install
   a tool on each system, its size, what is missing without it): see `docs/plugins/plugin-json.md`. A plugin that
   uses them should require `"engine": ">=0.5.0"`. Nothing to do when updating.
22. A plugin's secrets live in `~/.config/altention/secrets/<plugin>/<name>` (folder 700, file 600), set with
   `altention secret set <plugin> <name>` and covered by the secrets guard. plugin.json's `secrets[]` needs only
   `name` (and `label`); `path` is retired (`plugins.py list` reminds the author). Nothing to do when updating.
23. `altention dc post <channel> --secret <plugin>/<name> ["text"]` posts a 「填入」 button for a secret the plugin
   declares in `secrets[]` (its `label` names the button); only the owner can press it, and what is typed in
   Discord's box is stored on the bot's machine by `altention secret set … --stdin --via discord`. The value is never
   posted, logged or handed to a Claude. A name the plugin does not declare is not posted (`--wait`: exit 4). See
   `docs/plugins/discord.md`. Nothing to do when updating.
24. plugin.json may say `"offer": {"cost": "...", "when": "install" | "asked"}`; `setup.py offer <plugin>` is the
   "asked" moment. The answers are the instance's `plugins.json` `"offers"`; leave them to #setup. Nothing to do
   when updating.
25. plugin.json `guide[].gate` names the `setup[]` items and `schedules[]` held back while that step's check fails
   (apply adds no line, the scheduler does not run it, install skips the item; a line already there is kept); once
   the step passed, they are let go for good. The gates are kept in `~/.local/state/altention/setup/gate/<plugin>.json`,
   written by every plan. A new version with a new tool to install, a new step, a changed `run` or a new secret counts
   as perms. Nothing to do when updating.

## v0.4.3 — 2026-10-27

### For people

- **Permission questions with a countdown can be answered from Discord when the terminal is narrow.** In a narrow
  pane Claude Code wraps its "will automatically deny this request in 0:56" warning onto three rows; the bot did not
  recognise it as the countdown, so every second the question looked changed and each button press said
  「選單已經變了，沒有送出」. Now the warning is recognised however it wraps.

### For people (zh-TW)

- **終端機視窗窄的時候，有倒數的權限選單也能從 Discord 按了。** 視窗窄時，Claude Code 會把「will automatically deny this request in 0:56」那行警告換成三列，機器人沒認出那是倒數，每過一秒就以為選單變了，按任何按鈕都只說「選單已經變了，沒有送出」。現在不管怎麼換行都認得到。

### For agents

None.

## v0.4.2 — 2026-10-21

### For people

- **Turning chatlog off now stops it: conversations are no longer recorded or pushed.** A machine in chatlog's
  `disabled_on` used to keep its hooks in `~/.claude/settings.json` (only its schedule went), so every conversation
  there was still written down and pushed to your logs repository. Now installing takes chatlog's own two hooks
  (Stop and SessionEnd) out on such a machine, and puts them back when you turn it on again; the same goes for any
  plugin turned off on a machine. The 「更新」 button installs on every machine; updated another way, run
  `./install.sh` there. What was recorded before stays in your logs; hooks of your own are left as they are.

### For people (zh-TW)

- **關掉 chatlog（對話紀錄）之後，對話不再被記錄、推送。** 以前在 chatlog 的 disabled_on 寫了某台之後，只拿掉它的排程，hook 還留在 ~/.claude/settings.json，那台的對話照樣被記下來、推到紀錄 repo。現在安裝時會拿掉 chatlog 自己的兩個 hook（Stop 和 SessionEnd），再打開時會加回來；其他插件在某台關掉也一樣。按「更新」會在每台都跑安裝；用別的方式更新的話，在那台跑 `./install.sh`。之前記下的紀錄留著，你自己加的 hook 不動。

### For agents

1. After updating, `./install.sh` (or `python3 ops/apply.py <machine>`) must run on each machine where a plugin is
   turned off (`disabled_on`, or a `machines: one` plugin placed elsewhere): until then that plugin's hooks stay
   in `~/.claude/settings.json` and keep running. The update card's 「更新」 does it on every machine. Only a hook
   exactly as the plugin declares it (event, matcher, command, timeout) is taken out; others are left.

## v0.4.1 — 2026-10-21

### For people

- **Security fix: file browsing no longer opens anything under /proc, /sys or /dev, and reads ordinary files only.**
  Before, a path into these system folders (from `/files` or a 📂 link under a reply) could show the environment of
  your programs (`/proc/…/environ`), where tokens may be. Everyone on an earlier version should update.

### For people (zh-TW)

- **安全修正：看檔案（/files、📂 連結）不再打開 /proc、/sys、/dev 底下的東西，只讀一般檔案。** 以前透過這些系統資料夾可以看到程式的環境變數（可能有 token）；建議所有人更新。

### For agents

None.

## v0.4.0 — 2026-10-21

### For people

- **A channel set up again after it was archived works again, and taking up an old conversation does not post
  it twice.** A channel set up from the card (or attached to) while it sat in the archive category stays there no
  more: it goes back where it came from. Taking up a conversation the channel already shows posts only its last
  turns (a conversation begun in the channel, or left with `!cwd`, counts too), and `!resume <id> --quiet` (or
  `/resume` with quiet, or `altention dc post --resume <id> --quiet`) posts none of it.
- **A message that could not be handed to a Claude can be sent again.** When the terminal shows a dialog (or
  your half-typed text, or its machine cannot be reached), the "not sent" note now has a 「再送一次」 button that
  sends the words again once you have dealt with it. Messages another Claude sent with `altention dc post` are
  handed over by themselves, in order, once the terminal is back to its input box.
- **Small tables in a Claude's reply can be read on Discord.** Discord shows a Markdown table as rows of `|` and
  dashes. Now a small table (up to 5 columns and 20 rows) in a Claude's final reply is rewritten before it is
  posted: one of plain letters and digits becomes an aligned code block; one with Chinese, or too wide for a phone,
  becomes a list, one item per row. Bigger tables, and tables inside code blocks, stay as they are. To turn this
  off, set `"dcbot": {"small_tables": false}` in your instance's `plugins.json`.
- **#setup and update notices no longer send you the wrong way.** When only the usage board's channel was
  deleted, #setup said #dashboard was gone and "no" turned off both boards; now it asks about that board alone.
  Running setup again after the usage board failed to post no longer treats an older installation as a new one.
  An engine stuck on a fixed version now sends you to #chat, where Claude can put it back, not to #setup.
- **Plugins from outside the engine are installed on every machine that uses them.** Write in the instance where
  one comes from (`source` and `version`) and each machine's install puts it in `~/altention/plugins/<name>` at
  that version, runs the setup steps its `plugin.json` lists (once per version; big ones can wait until first
  use), links its skills, and says which system tools it needs that the machine lacks (it installs none). A step
  that fails twice in a row is not run again by itself: install and doctor say how to run it by hand
  (`python3 ops/plugins.py setup <plugin> <step>`).
- **A plugin folder the instance does not name is only a draft.** `plugins.py list` shows it, but it is not
  installed, scheduled or asked about, so a plugin still being written no longer runs by itself. doctor warns
  when a draft still has schedules, hooks or data on a machine: it has stopped running there.
- **Nothing of yours in a plugin's folder is overwritten.** A folder with changes not committed, another origin,
  or a file of yours where the new version adds one is left as it is and said; a plugin's skill named like one of
  the engine's (`update`, `setup`…) or pointing outside its folder is not linked.
- **Posts that never reached a Claude are not lost without a word.** When handing a post to a channel's Claude
  fails, the channel it came from is told, and doctor shows the posts kept aside (`dcbot.posts-bad`), with where
  to find them and what to do. The bot's "online" line in `#audit` now names the release it runs.

### For people (zh-TW)

- **沒送進去或沒交到 Claude 的訊息不會悄悄不見，可以再送一次。** 終端機開著對話框（或你打到一半、那台連不上）時，「這則沒有送出」下面多一顆「再送一次」按鈕，處理好按下去就重送；別的頻道的 Claude 用 `altention dc post` 傳來的，終端機回到一般輸入框後會照順序自動補交；交給頻道的 Claude 失敗時，會在傳來的頻道說一聲，doctor 也會列出留下來沒交出去的（dcbot.posts-bad），說去哪看、怎麼處理。#audit 的「已上線」也改成寫出正在跑的版本。
- **Claude 回覆裡的小表格在 Discord 上看得懂了。** 以前表格會變成一堆 `|` 和 `-`；現在正式回覆裡的小表格（最多 5 欄、20 列）貼出前會自動改寫：只有英數字的變成對齊的程式碼區塊，有中文或太寬的變成清單，一列一項。更大的表格和程式碼區塊裡的表格照原樣。不想要的話，在實例的 `plugins.json` 寫 `"dcbot": {"small_tables": false}` 就關掉。
- **封存過的頻道重新設定後能用了，接回舊對話也不會重貼一次。** 頻道在「封存」分類裡時用設定卡（或 /attach）設定，現在會搬回原本的分類，打字不會再被當成封存頻道；接回這個頻道已經有的對話只貼最後幾輪，在頻道裡自己開始的、用 `!cwd` 離開過的對話也算；`!resume <id> --quiet`（或 `/resume` 勾 quiet）完全不貼，`altention dc post --resume` 也能加 `--quiet`。
- **#setup 和更新通知不會再帶錯路。** 只有用量看板的頻道被刪時，#setup 以前會說 #dashboard 不在了，選「不要了」還會把兩個看板都關掉，現在只問那一個看板；用量看板貼不上、修好再跑一次設定時，舊安裝不會再被當成新安裝；引擎停在固定版本時，通知改叫你到 #chat 請 Claude 接回去，不再叫你去 #setup。
- **外部插件會自己裝到每台用它的機器，你在插件資料夾裡的東西不會被蓋掉。** 在實例寫好它從哪來（source 和 version），每台安裝時就會把它放到 ~/altention/plugins/<名稱>、切到那一版、跑 plugin.json 寫的安裝步驟（同一版只跑一次，大的可以等用到再裝）、連上它的 skill，缺什麼系統工具也會說（不會自己裝）；同一步連續失敗兩次就不再自動重跑，install 和 doctor 會告訴你怎麼手動跑。插件資料夾裡有還沒 commit 的修改、origin 不一樣、或你的檔跟新版要加的同名時，照原樣留著並告訴你；插件的 skill 跟引擎的同名（update、setup…）或指到資料夾外面時不會連上。
- **實例沒寫的插件資料夾只是草稿。** `plugins.py list` 會列出來，但不裝、不排程、也不問設定，還在開發的插件不會自己跑起來；草稿在某台還留著排程、hook 或資料時，doctor 會提醒你它在那台已經停了。

### For agents

1. New setting `dcbot.small_tables` in the instance's `plugins.json` (default `true`): `false` posts the tables in a
   reply as Claude wrote them. Nothing to do when updating.
2. External plugins (folders under `~/altention/plugins/`) now count only when the instance's `plugins.json`
   declares them: `"plugins": {"<name>": {"source": "<git URL>", "version": "vX.Y.Z"}}`, or `{"local": true}` for a
   folder that is only on the machines that have it and is not cloned. **Before updating an installation that
   already runs external plugins, write those entries**: otherwise they become drafts, and apply takes away their
   cron lines and hooks (their data stays). `python3 ops/plugins.py list` shows the drafts last.
3. After updating, run `./install.sh` on each machine: its new step `core.plugins-external` clones and sets up the
   declared plugins (a private repository uses the machine's own git login). What each setup step did is kept in
   `~/.local/state/altention/plugins/<name>.json`; leave it to install and `plugins.py setup`.
4. `plugin.json` may now say `setup[].lazy` (not run by install) and `needs` (`[{"name", "check", "hint"}]`: a shell
   command that exits 0 when the tool is there, looked for by install and a whole doctor, never installed).

## v0.3.2 — 2026-10-20

### For people

- **Messages to a Claude are no longer lost when several arrive at once.** When you sent two messages in a row,
  or several channels sent messages to the same Claude at the same time, one could be lost without a word. Now
  they go to that Claude one at a time, in order; one that still cannot be handed over is kept aside, and the
  channel that sent it (else `#audit`) is told.
- **A Claude posting into its own channel posts plain messages, in order.** They used to show as a card from
  that channel; now they are ordinary messages (long text split, no mentions), one sender's posts arrive in the
  order sent, and one post can carry up to 10 files.
- **When Discord limits how fast a Claude may post, the Claude is told.** When `altention dc post` is held back
  by Discord's rate limit, it tells the Claude (or script) that posted how long it waits, so the post is not
  sent a second time; nothing about it is said in the channel.

### For people (zh-TW)

- **同時有好幾則訊息要給同一個 Claude 時，不會再漏掉。** 以前連打兩則，或好幾個頻道同時傳訊息給同一個 Claude，可能有一則不見、也沒有任何提示；現在會照順序一則一則交給它，真的交不出去的會留下來，並告訴傳訊息的那個頻道（沒有的話是 #audit）。
- **Claude 貼回自己頻道的內容改成一般訊息，照順序出現。** 以前會顯示成一張卡片；現在是一般訊息（太長會分則、不會 @ 人），同一個來源的訊息照送出的順序出現，一則最多可以附 10 個檔案。
- **Discord 限速時，發文的 Claude 會知道。** Claude 用 `altention dc post` 發文被 Discord 限速時，會告訴它要等多久，它就不會再重送一次；頻道裡不會另外顯示。

### For agents

1. `altention dc post` into your own channel now gives plain messages (no card); they are still never handed
   back to you. `--file` may be given up to 10 times (8 MB together).
2. When Discord's rate limit holds a post, `altention dc post` says on stderr how long it waits and then sends
   it: do not send it again. Only when it says the post did not go, send it again.

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
