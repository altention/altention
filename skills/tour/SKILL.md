---
name: tour
description: 上手導覽：在 Discord 一步步帶使用者試機器人的主要功能（按鈕提問、📋 看過程、開新頻道、頻道之間傳話、看板、提醒）。使用者打 /tour、說「帶我導覽」「教我怎麼用」，或安裝完成後叫你帶導覽時使用；args 寫了某一項（例如「提醒」）就只帶那一項。
---

# The tour

You are showing someone, in a Discord channel, what the bot can do: by having them do each thing once, not by
explaining it. They have just installed Altention or asked for the tour again. You run in one of the bot's
channels (on a new install, `#tutorial`).

## Rules for the whole tour

- **One step at a time.** Each step is one thing for them to do. Say it in two or three short lines, then wait.
  Do not explain features that are not the step at hand; do not add steps of your own.
- Talk in the language they write in. Until they write, use Traditional Chinese (the bot's own language). The
  quoted sentences below are the wording to use in Chinese; in another language, say the same thing.
- **Never leave a button question open when you stop.** An unanswered question stays on the 「在等你的事」 board
  for up to 12 hours. Every question below is answered within the step, or the tour ends without one.
- They can stop at any moment: 「跳過」「先停」「不用了」, `/stop`, or anything that says so. Then say in one line how
  to get the tour back (below) and stop. Do not ask why, and do not try again later.
- The tour back: 「任何頻道打 `/tour` 都能再帶一次；只想看某一項就在 args 寫那一項，例如 `/tour 提醒`。」
- Only the commands named here. Never delete a channel, a message or a file; never change their settings, except
  the one reminder of step 5, which they ask for themselves.
- With `args` (`/tour 提醒`): do only the step it names (steps 3 to 6; 「看過程」 is step 2, done as it says for
  `args`), then the end (step 7), without the opening question. Before steps 4 and 5 and the end, run
  `altention plugins show` to know what is on (step 1 is where the whole tour learns it).

## 0. Opening

Ask (AskUserQuestion, one question, single choice), header 「導覽」:

- question: 「要我帶你試一下機器人的主要功能嗎？大約十分鐘，每一步你做一件事。會用掉一點訂閱額度，大約跟平常聊十幾二十句
  差不多。」
- options: 「開始」（一步一步試幾個主要功能）、「之後再說」（之後任何頻道打 /tour 再叫我）、「不用了」（不帶導覽）.

「之後再說」 or 「不用了」: one line with the tour back, then stop. 「開始」: say 「這個問題就是 Claude 用按鈕問你的
樣子；選項以外的答案按「其他…」或直接打字都可以。先隨便打一句話給我，例如「你好」。」 and end the turn.

## 1. Typing and the reply

When they write: say one sentence first (「我先看看這台開了哪些功能。」), then run `altention plugins show`, then
reply with:

- what is on (one short line, from that output; remember which of `usage`, `status` and `planner` are on, for the
  steps below);
- 「你的訊息上的 👀 表示已經送進 Claude；剛剛那句引用樣式的話是我中途說的。這則回覆底下的灰字是用了哪些工具、花了幾秒、
  context 用了多少。我在忙的時候你照樣可以打字，急的話開頭加 `!!`。」
- 「現在對我這則回覆按一個 📋 表情符號，再回我「好了」。」

End the turn.

## 2. 📋 and choosing what to try

With `args` (「看過程」, 「📋」 or the like) this step is the 📋 part alone: reply with one line, 「對我這則回覆按一個
📋 表情符號，再回我「好了」。」, and end the turn. When they say it is done, say the 📋 sentence below and go to the end
(step 7). Do not ask what to try.

In the whole tour, when they say it is done: 「📋 開的討論串裡是這一輪的完整過程：我跑了什麼指令、看到什麼。想知道 Claude 剛剛做了什麼，
就對它的回覆按 📋。」

Then ask what to try (AskUserQuestion, **multiSelect**), header 「要試哪些」, question 「接下來想試哪些？（下拉選單可以
選好幾個，選好點選單外面送出）」, options in this order, leaving out those whose part is off:

- 「開新的對話頻道」（自己開一個頻道，再讓兩個頻道的 Claude 互相傳話）
- 「#dashboard 看板」（只在 `status` 或 `usage` 開著時）
- 「提醒」（只在 `planner` 開著時）
- 「接上電腦上的 Claude」（要你人在電腦前）

Do the chosen ones in that order (steps 3 to 6), then the end (step 7). Chose none: go to step 7.

## 3. A new conversation channel, and channels talking to each other

Say:

1. 「在伺服器裡新開一個文字頻道，名字隨你，例如 `試用`。」
2. 「頻道裡會出現設定卡：按「🤖 Claude 對話」→「📁 選工作目錄」，點到一個你自己的專案資料夾（沒有的話就家目錄）→
   「✅ 用這個資料夾」→「🛠️ 交給機器人管」。」
3. 「在新頻道對它說：「跟 #<this channel> 說你好」。」 (your own channel's name; its Claude knows how to send from
   its global instructions, so do not name the command)

End the turn. What comes next is a card here, 「📨 來自 #<their channel>」, from that channel's Claude: say 「這張卡片
就是另一個頻道的 Claude 傳來的。叫 Claude「貼到 #某頻道」或「去看 #某頻道」都可以；每個頻道是一個獨立的對話，
這個新頻道之後就當你的第一個專案頻道吧。」 and go on.

If instead they write here that it is done (or that something is stuck), run `altention dc read --map` and look for
their channel: not there or not set up yet, say which button of the card is next; set up, ask them to send the
sentence of point 3 in it. Do not suggest deleting the channel: deleting it does not close its Claude.

## 4. #dashboard

Only when `status` or `usage` is on; asked for with `args` while both are off, say 「看板（status、usage）在這台關著，跟
Claude 說「打開狀態看板」或「打開用量看板」就會有。」 and go to the end (step 7). Run `altention dc read --map`: no channel holding the boards (`#dashboard` on a
new install), say 「看板頻道還沒設定，裝好後跟 Claude 說「幫我設定看板」就好。」 and go on.

Say: 「打開 #dashboard，從上往下看：上面是用量和圖表，下面是各頻道在做什麼、各機器的狀況、需要注意的事，最下面是
在等你回答的事。等一下最下面那塊會出現我現在要問你的這一題。」 (the last sentence only when `status` is on).

Then ask (AskUserQuestion), header 「看板」, question 「看完 #dashboard 了嗎？」, options 「看完了」、「沒看到看板」. When
answered: 「最下面那塊就是剛剛這一題：哪個頻道的 Claude 在等你，會列在那裡。」 (only when `status` is on). 「沒看到看板」:
run `altention dc read <the boards' channel> 12`; nothing there yet, say they appear within a few minutes of the
install and the usage ones on the hour, and go on.

If `usage` is on, say one line about the subscription board: 「「訂閱額度」那塊是整個 Claude 帳號這 5 小時和這週用了
多少，每小時用 haiku 送一個最小的請求去讀；不要的話跟 Claude 說「關掉訂閱額度看板」。」 (If that board says it is
turned off, say instead how to turn it on: 「打開訂閱額度看板」.)

## 5. A reminder

Only when `planner` is on; asked for with `args` while it is off, say 「提醒要靠秘書提醒（planner），它在這台關著；跟
Claude 說「打開秘書提醒」就能用。」 and go to the end (step 7). Say 「試試設一個提醒：對我說「10 分鐘後提醒我站起來動一動」。」 and end the turn.

When they ask for it: set it as the global instructions say for reminders (the item 「使用者要你設提醒」), with this
channel as its `channel`, and check it with `--check` as they say. Then: 「設好了，10 分鐘內會出現在這個頻道（提醒每 5
分鐘看一次）。我們先繼續。」 and go on with the next step without waiting.

If `planner.channel` is set (`#secretary` on a new install), add, with that channel's actual name: 「#<頻道名> 的秘書每天會寫早報、做晚間回顧，每週做一次
每週回顧，每一次都會用一點額度；要改時間或關掉，跟秘書說一聲就好。」（要說時間的話，照 `python3 ~/altention/engine/planner/remind.py --check`
裡 `morning`、`evening`、`weekly` 那三行說。）

When the reminder arrives later (a message here with the text of that reminder): one line, 「這就是剛剛設的提醒。」
Nothing else.

## 6. Your own Claude on the computer

Only when they chose it. First say what this step is, in one line: 「接下來試試把你自己在電腦上開的 Claude 對話接到
Discord：在終端機開一個 Claude，再從這裡接上它。」 Then ask (AskUserQuestion), header 「電腦」, question
「你現在在跑機器人的那台電腦前嗎？」, options 「在」、「不在，之後再試」. 「不在」: 「之後在電腦前打 `/tour 接上` 就好。」 and go on. 「在」:

1. 「在電腦的終端機跑 `tmux new -s demo`，在裡面跑 `claude`。」
2. 「回到 Discord，任何頻道打 `/attach`，點那個對話的按鈕。」

End the turn. When they say it is done: run `altention dc read --map` and point to the new channel (under the
category of its folder's name). 「在那個頻道打字會送進電腦上的那個對話，兩邊同時可用；`/detach` 讓 Discord 不再跟著，
對話照跑。」

## 7. The end

Say, as one short message:

- 「基礎導覽到這裡結束，還有很多功能這次沒試到：」 and a short list, one line each, of what they did not try this
  time: the parts of steps 3 to 6 that were on but not chosen (or skipped, like 「不在」 in step 6), then, for each
  of these that `altention plugins show` listed as on, its line: `chatlog` 「搜尋、回顧以前的對話：問 Claude「我上週
  在做什麼」」; `progress` 「各專案做到哪裡：問 Claude「某專案的進度」」; `council` 「難下的決定：說「召開議會」」;
  `planner` 「#<its channel> 的早報和回顧」 (only when `planner.channel` is set, under that channel's own name, and
  step 5 did not say it). Nothing that is off, nothing they already did. Then: 「想看哪一個直接告訴我，我帶你試。」
  (When the list is empty, this first line is only 「基礎導覽到這裡結束。」; the lines below still follow.) When
  they later name one, do that step (or try that feature with them the same way: one thing to do at a time).
- 「出狀況時：沒反應先 `/screen` 看畫面；方向錯了 `/stop`；對話亂了 `/reset`。」
- 「`!help` 列出機器人的指令，完整說明在 #guide；不懂的直接問任何頻道的 Claude。」
- the tour back (see the rules).

Then, only on a new install's `#tutorial`, ask (AskUserQuestion), header 「這個頻道」, question 「這個導覽頻道要留著
嗎？」, options 「留著」（之後在這裡打 /tour 再帶一次）、「封存」（移到「封存」分類）. 「封存」: 「在這個頻道打 `/detach`
就會移到封存分類，之後在裡面打字可以再接回來。」 You cannot do it yourself. That is the end of the tour.
