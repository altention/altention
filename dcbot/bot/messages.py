"""What the bot says to the owner, in one place (backlog A-09): from step 2.6 on a sentence is looked up here by a
code, not written where the thing happens. Step 2.6 moves the words of a message that was not sent (the typing
checks of tmux_bridge: may_type, leave_list, resend, clear_leftover, enter, and /btw's); the rest of the bot's
sentences move over as they are touched.

A code has two layers. The few common ones say what kind of thing it is, whatever the tool: "not_ready" (the tool
cannot take it at this moment), "busy_elsewhere", "needs_owner" (somebody has to do something at the terminal),
"refused" (never from here), "unknown". The connector's own ("claude_code.shell_mode") say which case of that tool it
is, and each names its common one. A code this table does not know (a new connector, a dialog a new Claude Code
grew) is said with one general sentence that bears the code and what came with it: nothing in bot/ changes for it.

Each code is also marked: "temporary" (sending again later may well go through: a dialog, the owner's text, a
machine that cannot be reached) or "permanent" (sending the same again changes nothing: a blocked command). The
protocol's "posted, not handed to the agent" carries exactly these two (draft of 2026-10-12, section 7).

The defaults below are entered in the engine's table of text (altention.text.TEXT, "dcbot.<group>.<code>"), where
another language is added; an installation words a sentence its own way in its settings ("dcbot.text"). Taking a
sentence reads memory only (words()); what the installation says is read by load(), off the event loop.
"""
import os

from altention import text as alt_text

TEMPORARY, PERMANENT = "temporary", "permanent"
COMMON = ("not_ready", "busy_elsewhere", "needs_owner", "refused", "unknown")

# code: (its common code, temporary or permanent, the words). Fields in {braces} are the data that comes with it.
NOT_SENT = {
    'claude_code.moving_away': ('not_ready', TEMPORARY,
        '⚠️ 這個窗格的對話正要搬到背景（終端機按了 ←，等手上的工具跑完才搬），所以這則**沒有送出**，也沒有按任何鍵。搬完後窗格會停在背景對話的清單：請在終端機按 Esc 回到對話，再重發一次。'),
    'claude_code.agents_list': ('needs_owner', TEMPORARY,
        '⚠️ 這個窗格在背景對話的清單畫面，請在終端機按 Esc 回到對話。在清單裡送出的字會開一個**新的**背景對話，所以這則**沒有送出**，也沒有按任何鍵；回到對話後再重發一次。'),
    'claude_code.list_has_text': ('needs_owner', TEMPORARY,
        '⚠️ 這個窗格在 Claude Code 的背景對話清單，而且清單的輸入框裡已經有字（可能是你打到一半的）。在那裡送出的字會開一個**新的**背景對話，所以這則**沒有送出**，也沒有按任何鍵。請在終端機處理掉那些字、按 Esc 回到對話，再重發一次。'),
    'claude_code.list_not_left': ('needs_owner', TEMPORARY,
        '⚠️ 這個頻道的 Claude 停在背景對話的清單，按了一次 Esc 之後畫面也沒有回到對話的輸入框，所以這則**沒有送出**。請用 `/screen` 看一下，或 `!reset` 重開這個頻道的對話。'),
    'claude_code.previous_entered': ('not_ready', TEMPORARY,
        '⚠️ 上一則 Discord 訊息按了 Enter 之後還留在終端機的輸入框裡、還沒送進去，所以這則**沒有送出**，也沒有按任何鍵。請用 `/screen` 看一下（`/key Enter` 可以再送一次），再重發這一則。'),
    'claude_code.previous_not_resent': ('not_ready', TEMPORARY,
        '⚠️ 上一則 Discord 訊息沒有送進去，重送一次也還留在輸入框裡，所以這則**沒有送出**。請用 `/screen` 看一下（`/key Enter`、`/key C-u`），再重發。'),
    'claude_code.other_conversation': ('needs_owner', TEMPORARY,
        '⚠️ 這個窗格現在顯示的是另一個背景對話（從清單開的，不是這個頻道的對話），所以這則**沒有送出**，也沒有按任何鍵。請在終端機回到原本的對話（按 ← 回清單，選原本那一列按 Enter），再重發一次。'),
    'claude_code.shell_mode': ('needs_owner', TEMPORARY,
        '⚠️ 終端機的輸入框在 `!`（shell）模式，這時送進去的字會被當成 shell 指令執行，所以這則**沒有送出**，也沒有按任何鍵。請在終端機把那一行處理掉、離開 `!` 模式（清空後按 Backspace），再重發一次。'),
    'claude_code.dialog': ('needs_owner', TEMPORARY,
        '⚠️ 終端機現在不是一般的輸入框：有對話框或選單開著（可能是權限詢問），這時送進去的字和 Enter 會被當成回答，所以這則**沒有送出**，也沒有按任何鍵。請先回答它（頻道裡貼出的選單按鈕、終端機，或用 `/screen` 看、`/key` 操作），再重發一次。'),
    'claude_code.previous_left': ('needs_owner', TEMPORARY,
        '⚠️ 終端機的輸入框裡是上一則 Discord 訊息（`{text}`）：它打進去了但沒有按 Enter、沒有送出，還留在那裡，所以這則也**沒有送出**。用 `/key C-u` 清掉（或在終端機處理），再把兩則重發一次。'),
    'claude_code.owners_text': ('needs_owner', TEMPORARY,
        '⚠️ 終端機的輸入框裡已經有字（`{text}`），可能是你在終端機打到一半的，所以這則**沒有送出**。要清掉就用 `/key C-u`，再重發一次。'),
    'claude_code.scrolling': ('needs_owner', TEMPORARY,
        '⚠️ 終端機的輸入框裡有字（`{text}`），而且終端機正在捲動畫面（tmux 的 copy mode），這時按鍵到不了 Claude Code，所以沒有去分辨它是不是建議字，這則**沒有送出**。請在終端機按 `q` 離開捲動，再重發一次。'),
    'claude_code.changed_while_probing': ('not_ready', TEMPORARY,
        '⚠️ 終端機的輸入框裡有字（`{text}`），想分辨它是不是建議字的時候畫面跟預期的不一樣，所以沒有再按任何鍵，這則**沒有送出**。請用 `/screen` 看一下再重發。'),
    'claude_code.no_colour': ('needs_owner', PERMANENT,
        '⚠️ 終端機的輸入框裡已經有字（`{text}`），這則**沒有送出**。這個終端機沒有顏色，分不出它是你打到一半的字，還是 Claude Code 的灰色建議字。\n- 是建議字：`/key C-u` 清不掉。請在那個對話裡用 `/config` 關掉 Prompt suggestions（改全域設定對已經在跑的對話沒有用），或不帶 `NO_COLOR` 重開 Claude Code。\n- 是你打到一半的字：在終端機處理掉，或用 `/key C-u` 清掉。\n然後再重發一次。'),
    'claude_code.not_cleared': ('needs_owner', TEMPORARY,
        '⚠️ 輸入框裡有清不掉的字（`{text}`），這則沒有送出。用 `/screen` 看、`/key C-u` 清，再重發一次。'),
    'claude_code.box_differs': ('not_ready', TEMPORARY,
        '⚠️ 字打進終端機之後，畫面不是「一般的輸入框裡只有這則訊息」（可能有對話框剛跳出來，或你剛好在終端機打字），所以**沒有按 Enter，這則沒有送出**，也沒有再按任何鍵。請用 `/screen` 看一下、在終端機處理掉，再重發一次。'),
    'claude_code.blocked_command': ('refused', PERMANENT,
        '🚫 `{command}` 不能從 Discord 送：{why}。真的要用，請到終端機自己打。'),
    'claude_code.btw_too_long': ('refused', PERMANENT,
        '這個問題太長：連同帶過去的說明一共 {chars} 字。`/btw` 一次最多約 {limit} 字，再長 Claude Code 會把它當成貼上的內容、當成一般訊息送進主對話。請縮短，或直接在主頻道問。'),
    'claude_code.btw_not_alone': ('needs_owner', TEMPORARY,
        '這則 `/btw` 沒有送出：打進去之後輸入框裡不只是這則 `/btw`（可能你剛好在終端機打字），所以沒有按 Enter，也**沒有清**輸入框。請用 `/screen` 看一下，在終端機把那一行處理掉，再問一次。'),
    'claude_code.btw_pasted': ('refused', PERMANENT,
        '這則 `/btw` 沒有送出：打進去之後輸入框把它當成了貼上的內容，{cleared}。請把問題縮短再問一次。'),
    'claude_code.btw_not_command': ('refused', PERMANENT,
        '這則 `/btw` 沒有送出：打進去之後輸入框的開頭不是 /btw，{cleared}。請把問題縮短再問一次。'),
    "not_started": ("not_ready", TEMPORARY, "出錯了：{error}"),
    # (#issues 29) the pane is on another machine and that machine did not answer: whether the pane still runs is
    # not known, and is not said. {said}: remote.unreachable(), "連不上 <machine>（試過 …）"
    "machine_unreachable": ("not_ready", TEMPORARY,
        "⚠️ {said}，所以不知道這個頻道接的 tmux 窗格還有沒有在跑 Claude Code（{where}）。這則**沒有送出**。"
        "等那台連得上之後再發一次就好；不需要 `!detach`。"),
    # the same, where no message was being sent: the channel was only being given its conversation
    "machine_unreachable_unopened": ("not_ready", TEMPORARY,
        "⚠️ {said}，所以這個頻道的對話還沒有開（{where}）。等那台連得上之後，在這個頻道發一則訊息就會開。"),
}

# What became of the box after a /btw that was not one (the "cleared" of btw_pasted and btw_not_command).
CLEARED = {
    'empty': '已經清掉',
    'unchanged': '按清除鍵之後畫面不再變化，剩下的應該是提示文字；不放心可以用 `/screen` 看',
    'stuck': '**沒能清乾淨，輸入框裡還有字**，用 `/screen` 看、`/key C-u` 清',
}

# Typed and entered, with no word that it was taken (the protocol's "cannot confirm receipt": never sent again unasked).
UNCONFIRMED = {
    'claude_code.stream_down':
        '⚠️ 已經打進去了，但 {host} 的事件連線斷了、正在重連，回覆會晚到。',
    'claude_code.maybe_not_received_moved':
        '⚠️ 訊息可能沒有送進這個頻道的對話：這個窗格的對話已被搬到背景；窗格現在可能在看別的背景對話，或停在清單。請在終端機確認它顯示的是原本的對話。目前的畫面：\n{screen}',
    'claude_code.maybe_not_received':
        '⚠️ 訊息可能沒有送進 Claude。目前的畫面：\n{screen}\n可以用 `!screen` 再看一次、`!key Escape` 關掉面板、`!key Enter` 送按鍵，或 `!reset` 重開這個頻道的對話。',
    'claude_code.btw_still_answering':
        '`/btw` 送出 {seconds} 秒了，答案框還開著、還沒有答案（Claude Code 還在回答，或請求失敗正在重試）。我沒有關它。答案出來後可以用 `/screen` 看；下一則訊息送出前機器人會先把它關掉。目前的畫面：\n{screen}',
    'claude_code.btw_no_answer':
        '`/btw` 送出後 {seconds} 秒內沒有看到答案框（這則可能沒有被當成 /btw）。目前的畫面：\n{screen}',
}

# A command of the owner's, or a button, that could not be done because the channel's pane is on another machine
# which did not answer: said instead of "nothing runs here" (the pane may well run). {said}: remote.unreachable().
FAR = {
    "command": "⚠️ {said}，所以這個指令沒有做：那邊的對話還在不在、在做什麼，現在都不知道。等那台連得上之後再下一次。",
    "status": "那台連不上，不知道 Claude 還在不在這個窗格",
    "menu": "\n-# ⚠️ {said}，讀不到畫面上的選項；連得上之後用 `/screen` 看、`/key` 操作。",
    "button": "⚠️ {said}，這個按鈕沒有送出，按鈕留著。等那台連得上之後再按一次。",
    # a turn is running and its machine has not answered for a while: said once, and nothing is ended
    "turn": "⚠️ {said}，這一輪的狀態暫時不知道。連得上之後會接著貼；這個頻道還算在忙。",
}

# Said along the way: not a failure.
NOTES = {
    # messaging review P0 (C-186): a Claude's post into its own channel that begins with a frame only the bot uses
    "reserved_own": "⚠️ 這則沒有貼出：開頭「{frame}」是機器人自己用的格式，讀回時會被當成別的頻道傳來的、通知、看板或終端機輸入。請換個開頭再貼。",
    "board_files": "看板「{name}」只能附一個檔案，這次沒有更新",
    "board_files_audit": "{sender} 更新 #{channel} 的看板「{name}」時附了不只一個檔案，沒有更新（看板只放一個）",
    # P0-2: the channel a post's own environment names is not the one its Claude Code runs for (sent all the same)
    "held_full": "這則沒有交給 #{channel} 的 Claude，也沒有留住：停在來回上限、等使用者決定的訊息已經有 {most} 則。等使用者處理 #{channel} 那張卡（繼續轉交或停下來）之後，有需要再傳一次。",  # (manager 11-16)
    "claimed": "{who} 傳到 #{channel} 的訊息自稱來自 {claimed}（照查到的寄件人送出）",
    'claude_code.resent':
        'ℹ️ 上一則訊息第一次沒有送進去（按了 Enter 還留在輸入框裡），已經重送。',
    'claude_code.new_conversation':
        '接不回原本的對話（`{session}`），已開始新的對話。',
    'claude_code.no_start':
        '-# 等了 {secs} 秒沒收到 Claude Code 開好的通知，畫面上也找不到輸入框。',
    'claude_code.resume_ended':
        '-# 接回時 Claude Code 自己結束了（{how}），最後的畫面：',
    'masked': '-# 有 {n} 處像金鑰的字被遮掉了',
    # C-74 (bot/unsent.py): a message not sent for now, sent again
    'resend.done': '✅ 已再送一次',
    'resend.handed': '✅ 對話框關了，已補送',
    'resend.expired': '這則已經過期或已經送過了，要的話請重打一次。',
    'resend.still': '-# 再送一次也沒送出，見下一則',
    'resend.failed': '⚠️ 補交給 #{to} 的 Claude 時出錯（{why}），這則還留著，下次再試：{text}',
    'resend.recovered': '✅ 剛才補交給 #{to} 的 Claude 出錯的那則，已經交到了：{text}',
    'resend.press_failed': '⚠️ 再送一次時出錯（{why}），內容還留著，可以再按一次。',
    # C-202: a post another channel's Claude sent, not handed to this channel's Claude: said where it came from
    'post.not_entered': '⚠️ 你傳給 #{to} 的訊息沒有送到它的 Claude：字打進了輸入框，但它的畫面不是在等人打字，所以沒有按 Enter。'
                        '字可能還在它的輸入框裡，視窗關掉就不見了。要的話晚點再送一次（先用 `altention dc read {to}` 看它有沒有收到，免得重複）。',
    'post.refused': '⚠️ 你傳給 #{to} 的訊息沒有送到它的 Claude：{why}這個原因不會自己消失，現在重送也一樣，要等那邊處理好再送。',
    'resend.given_up': '⚠️ 這則一直沒能交給 #{to} 的 Claude（{hours} 小時內那個頻道的終端機都不是一般的輸入框），已經放棄：{text}',
}

GENERAL = "⚠️ 這則**沒有送出**（原因代號 `{code}`{data}）。請用 `/screen` 看一下終端機，處理好之後再重發一次。"
GENERAL_NOTE = "ℹ️ `{code}`{data}"
# C-77 G3b: the 填入 button for a plugin's secret (bot/secretbox.py; #release 10-24)
SECRET = {
    "default": "按下面的按鈕，填入 {plugin} 要的 {label}。",
    "note": "-# 只有擁有者按得動。值會經過 Discord，輸入時看得到字（Discord 的輸入框不會遮）；不想經過 Discord，就在終端機跑 `altention secret set {plugin} {name}`。",
    "button": "填入 {label}",
    "filled": "✅ 已填入 {label}（再按可以換）",
    "box_title": "{label}",
    "box_field": "{label}（貼上後按送出）",
    "box_hint": "只存在機器人那台，不會出現在頻道裡；超過 4000 字請用終端機",
    "owner_only": "只有擁有者能填這個。",
    "expired": "這顆按鈕已經失效了，請 Claude 再貼一次。",
    "slow": "沒有存好：存的程式 {secs} 秒沒跑完，不確定有沒有存進去。看 #audit 有沒有「🔑」那一行；沒有就再按一次。",
    "failed": "沒有存：存的程式出錯了（{why}）。這次的值沒有存下來，可以再按一次。",
    "exit_code": "結束碼 {code}",
    "no_value": "沒有收到輸入框的值",
    "bad_spec": "--secret 要寫成 <插件>/<名字>",
    "unreadable": "讀不到插件的設定",
    "told": "（機器人回覆你的 altention dc post --secret）沒有貼出：{why}。",
}
# C-99a: a usage limit and Claude Code's own wait for its reset (bot/quota.py; #release 10-26)
QUOTA = {
    "armed": "⏸️ **用量達到上限**，Claude Code 會在 {when} 自己接著做。",
    "armed_unknown": "⏸️ **用量達到上限**，Claude Code 會在用量重置後自己接著做。",
    "armed_note": "-# 在這裡打字會先送出你的訊息，等待就取消了（多半也會再撞上限）。",
    "resumed": "▶️ 用量重置了，Claude 接著做。",
    "stale": "⏸️ **用量已經重置**，但 Claude Code 還在等人按 Enter（例如電腦睡著過）：在這裡用 `/key Enter`，或在終端機按 Enter。",
    "stopped": "⚠️ **沒有自動接著做**：{why}",
    "retry_note": "-# 按「再試一次」會請 Claude 從中斷的地方接著做",
    "audit_armed": "#{channel}：用量達到上限，Claude Code 會在 {when} 自己接著做",
    "audit_armed_unknown": "#{channel}：用量達到上限，Claude Code 會在重置後自己接著做",
    "audit_resumed": "#{channel}：用量重置，Claude 接著做",
    "audit_stale": "#{channel}：用量重置了，等人按 Enter",
    "audit_stopped": "#{channel}：沒有自動接著做（{why}）",
}
# C-103 (bot/calls.py): the one line that @-mentions the owner, when something needs them. What is needed comes first
# (a phone shows a notification's first words), the owner's mention last; {gist} is cut to calls.GIST characters, the
# words before it never. Words: #release 2026-11-03.
CALLS = {
    "ask": "🔘 要你選：{gist}",
    "ask_many": "🔘 要你選（共 {n} 題）：{gist}",
    "plan": "📝 要你核准計畫：{gist}",
    "permission": "🔐 要你允許：{gist}",
    "menu": "🔘 要你選：{gist}",
    "talk_limit": "⏸️ 兩個 Claude 來回 {n} 次了，要繼續嗎？",
    "turn_failed": "⚠️ 這一輪中斷了，要你處理：{gist}",
    "not_started": "⚠️ Claude 沒有啟動成功，訊息沒有送出",
    "discuss": "💬 要跟你討論：{gist}",
    # (installation-wide, in the notification channel, once for each time it happens; the lines after it and the
    # line that says it is over do not mention anyone)
    "usage_limit": "⛔ 用量到上限，所有頻道暫停，約 {when} 恢復後自己接著做",
    "usage_limit_unknown": "⛔ 用量到上限，所有頻道暫停，恢復時間不確定",
    "usage_back": "✅ 用量恢復了，各頻道接著做",
    "login": "🔑 {machine} 的 Claude 要重新登入，那台的頻道都停住",
    "login_one": "🔑 Claude 要重新登入，頻道都停住",
    "login_how": "在 {machine} 的終端機打開 claude，輸入 /login 照畫面登入",
    "login_how_one": "在終端機打開 claude，輸入 /login 照畫面登入",
    "login_back": "✅ {machine} 的登入恢復了",
    "login_back_one": "✅ 登入恢復了",
    "rollback": "🛠️ 機器人新版有問題，已退回上一版，照常運作",
    # (a new version too far from the last good one to roll back on its own: supervisor.py hold(), held_down())
    "rollback_held_exit": "啟動失敗（exit {exit}）",
    "rollback_held_events": "試用時把 {n} 個事件標成失敗，被停掉",
    "rollback_held_never_up": "{secs} 秒內沒上線，被停掉",
    "rollback_held": "🛠️ 機器人新版 `{head}` {how}，但它跟上一個正常版本 `{good}` 差了 {commits} 個 commit、"
                     "{lines} 行、{files} 個檔（dcbot/、altention/），太多了，沒有自動退回，照常重試。要退回：在 Discord 打 "
                     "`!rollback`，或在 {machine} 的引擎 `touch dcbot/state/rollback-requested`",
    "rollback_held_again": "🛠️ 新版 `{head}` 又停了（exit {exit}，第 {n} 次），照常重試；要退回打 `!rollback`",
    "restarting": "🛠️ 機器人一直重啟，沒辦法上線",
    "restarting_where": "原因在 {machine} 的 {path}",
    "restarting_back": "✅ 機器人恢復了",
    "machine_down": "📡 {machine} 連不上，那台的 {n} 個頻道停住",  # (the channels whose work it cut off)
    "machine_back": "✅ {machine} 連回來了",
    "machine_idle": "{machine} 連不上，那台的頻道都閒著，沒有 @ 使用者",  # (#audit only)
    "channels": "受影響的頻道：{channels}",
    "done": " ✅",  # added to a mention's line when what it asked for is over (answered, cancelled, cleared)
}
# C-162: Claude Code that the bot cannot use as it is (bot/cc_health.py). Words: #release 11-05 (design section 10);
# down_json, back_json, wait_window, retry_trying: #release 11-07 (second batch).
# retry_*, status_* and mode_usage: #release 11-07 (the level is the machine's: "這台" in retry_free; retry_held gains
# "如果還是認不得畫面，會再退回…" once a failed retry goes back down by itself, the detection step).
CC = {
    "footer_print": "-# 簡易模式",
    "footer_text": "-# 純文字模式",
    "missing": "暫時沒有：按鈕提問（改用文字問）、計畫核准、權限選單（需要允許的會被拒絕）、/btw、背景工作、"
               "Claude 做事時你補充的話（要等這一輪做完才送進去）、定時叫醒 Claude 的排程",
    "retry_held": "已回到一般模式，下一則訊息會重新開 Claude Code，接續原本的對話。",
    "retry_free": "這台現在就是一般模式，不用重試。",
    "retry_held_after": "如果還是認不得畫面，會再退回{mode}並告訴你。",
    "retry_failed": "再試了一次，還是認不得 Claude Code {version} 的畫面（{why}），先留在{mode}。",
    "down_print": "⚠️ 機器人認不得 Claude Code {version} 的畫面，所有頻道改用簡易模式（沒有選單和按鈕），回覆照常",
    "down_json": "⚠️ 這台 Claude Code {version} 的簡易模式不能用了：原本用簡易模式的頻道先改用純文字模式，照常能回，"
                 "只是看不到 Claude 做到哪一步。恢復時會自動換回。",  # (no mention: nothing for the owner to do, C-103)
    "back_json": "✅ 這台 Claude Code {version} 的簡易模式又能用了，那些頻道已經換回簡易模式。",
    "wait_window": "Claude Code 還在做上一件事，做完就會用{mode}回這則，不用重傳。",
    "status_json": "純文字模式（這台 Claude Code {version} 的簡易模式不能用，可以用 `!mode retry` 再試）",  # (#release 11-07)
    # (only `!mode print` meets it: with stream-json alone broken `!mode tmux` simply runs tmux; #release 11-07)
    "mode_held_json": "已記下這個頻道要用簡易模式，但這台 Claude Code {version} 的簡易模式不能用，暫時用純文字模式；"
                      "`!mode retry` 可以再試，或用 `!mode tmux` 回到一般模式。",
    "retry_failed_json": "再試了一次，這台 Claude Code {version} 的簡易模式還是不能用（{why}），先留在純文字模式。",
    "pinned": "⚠️ Claude Code {new} 在機器人這邊不能用，已退回 {old} 繼續用",
    "pin_long": "⏳ 機器人停在 Claude Code {old} 已經 {days} 天，新版一直沒通過檢查。要繼續停著，還是改用新版的簡易模式？",
    "pin_keep_button": "繼續停 {days} 天",
    "pin_new_button": "改用新版的簡易模式",
    "pin_kept": "好，這台繼續用 Claude Code {old}，{days} 天後再問你。",  # (#release 11-07, from here)
    "pin_left": "好，這台改用 Claude Code {new}，先用簡易模式。",
    "pin_by_keep": "由 {by} 回答。要改用新版：在任何頻道打 `!cctest`，新版通過檢查就不再停在舊版。",  # (C-187)
    "pin_by_new": "由 {by} 回答。新版之後又出問題時，機器人會自己再退回留下的舊版。",
    "cctest_start": "🧪 正在檢查 Claude Code {version}：會跑大約 5 次 Haiku 對話（用掉一點用量），要幾分鐘…",
    "cctest_ok": "✅ Claude Code {version} 檢查通過（一般模式、簡易模式、純文字模式都能用），之後新版壞了會退回這一版。",
    "cctest_ok_nokeep": "✅ Claude Code {version} 檢查通過，但沒有留下備份（{why}），所以還不能當作退回用的版本。",
    "cctest_fail": "❌ Claude Code {version} 檢查沒通過：{what}。新版壞了時，還是退回 {good}。",
    "cctest_fail_none": "❌ Claude Code {version} 檢查沒通過：{what}。目前還沒有可以退回的版本。",
    "drilled": "Claude Code {version} 自動檢查：能啟動 {start}、畫面 {screen}、簡易模式 {print}（用測試用的假連線，不花用量）。"
               "要把它記成退回用的版本，請跑 `!cctest`。",  # (#release 11-08; #audit only)
    "retry_trying": "正在重試一般模式，可能要幾分鐘，好了會告訴你。",
    "down_text": "⚠️ 機器人認不得 Claude Code {version} 的畫面，所有頻道改用純文字模式（看不到它做到哪）",
    "back": "✅ 機器人認得 Claude Code {version} 的畫面了，回到一般模式",
    "attached_pane": "認不得這個窗格裡 Claude Code {version} 的畫面，這個頻道暫時沒辦法把訊息送進去。這是你自己開的窗格，"
                     "機器人不會動它：你可以直接在終端機用；或關掉那個窗格後，用 `!mode print` 在這裡接著原本的對話。",
    "status_print": "簡易模式（認不得 Claude Code {version} 的畫面，可以用 `!mode retry` 再試）",
    "status_text": "純文字模式（認不得 Claude Code {version} 的畫面，可以用 `!mode retry` 再試）",
    "mode_usage": "這個頻道目前是{mode}。用法：`!mode tmux`（一般模式）、`!mode print`（簡易模式）、`!mode retry`（再試一次一般模式）",
    "queued": "這台暫時用{mode}，同時最多處理 {most} 則；前面還有 {n} 則在等，輪到時會自動開始，不用重傳。",
    "mode_held": "已記下這個頻道要用 `{want}` 模式，但這台認不得 Claude Code {version} 的畫面，暫時還是用{mode}；"
                 "`!mode retry` 可以再試。",  # (#release 11-07: in place of the plain answer, not after it)
    "mode_tmux": "一般模式",
    "mode_print": "簡易模式",
    "mode_text": "純文字模式",
}
# C-163: the lease (bot/lease.py). Words: #release 11-05 (design section 10).
LEASE = {
    "busy": "機器人正在換機器，請過幾秒再按一次",
    "audit_took_over": "接手：{standby}（第 {epoch} 次換機器），{primary} 最後心跳 {beat}，{ssh}，接手前檢查 {checks}",  # ({ssh}: dclease's ssh_veto
    # words, "ssh 通…" or "ssh 不通": "ssh {ssh}" said "ssh ssh 通", drill 11-09)
    "audit_pane_followed_tag": "#{channel}：窗格 {where} 重開過（程序 {was} → {pid}），換成 {agent}，照窗格上這個頻道的標籤和它自己留的標記接回",
    "pane_followed_tag": "-# 這個頻道的窗格重開過，已經接回同一個窗格",
    "audit_pane_followed": "#{channel}：窗格 {where} 裡的 Claude 重新啟動過（程序 {was} → {pid}），還是同一個對話（{session}），頻道照常接著",
    "gate_setup": "啟動前讀不到 #{channel}（{why}），先照沒有備援的方式啟動。備援的機器也讀不到它，所以這台掛了時不會有別台接手；"
                  "請跑 doctor 看怎麼修。",  # (#release 11-08: #audit is read too, plain words)
    "gate_connection": "啟動前有 {minutes} 分鐘連不上 Discord（{why}），這段時間機器人沒有上線，現在恢復了。",
    "took_over": "⚠️ {primary} 上的機器人停了，{standby} 接手了。{primary} 那台的 {n} 個頻道暫時不能用，別台的頻道照常；"
                 "提醒和用量看板也暫停，等 {primary} 回來才恢復。",  # (#release 11-08)
    "took_over_joined": "⚠️ {primary} 上的機器人停了，{standby} 接手了。所有頻道照常能用（{primary} 那台的也接上了）；"
                        "提醒和用量看板暫停，等 {primary} 回來才恢復。",
    # the takeover that is the DAY_MOST-th in 24 hours: none automatic after it, no failback either (#release 11-10)
    "took_over_last": "⚠️ {primary} 上的機器人停了，{standby} 接手了。{primary} 那台的 {n} 個頻道暫時不能用，別台的頻道照常；"
                      "提醒和用量看板也暫停，等切回 {primary} 才恢復。這是 24 小時內第 3 次換機器：接下來 24 小時內不會再自動"
                      "切換，{primary} 回來也不會自己切回，請看一下 {primary} 怎麼了。",
    "took_over_joined_last": "⚠️ {primary} 上的機器人停了，{standby} 接手了。所有頻道照常能用（{primary} 那台的也接上了）；"
                             "提醒和用量看板暫停，等切回 {primary} 才恢復。這是 24 小時內第 3 次換機器：接下來 24 小時內不會再"
                             "自動切換，{primary} 回來也不會自己切回，請看一下 {primary} 怎麼了。",
    "engines_differ": "（兩台的引擎版本不同：{primary} 是 {theirs}，{standby} 是 {mine}；有空把兩台更新成同一版）",  # (not
    # blocking: manager 11-08; #release 11-08: a release's name when there is one, else 7 characters of the commit)
    "failed_back": "✅ {primary} 回來了，機器人已經切回 {primary}",  # (#release 11-05, design section 10; no mention)
    "failback_ask": "✅ {primary} 回來了，現在由 {standby} 在跑。要切回 {primary} 嗎？",
    "failback_button": "切回 {primary}",
    "failback_pressed": "✅ {primary} 回來了，已經按下切回，現在由 {primary} 在跑。",  # (the ask's message once done: #release 11-10)
    "failback_stale": "這個按鈕已經過期了（之後又換過機器），請按最新那則的按鈕。",  # (#release 11-08)
    "failback_not_now": "現在沒辦法切回 {primary}，細節記在 #audit；過幾分鐘再按一次",
    "failback_draining": "要切回 {primary}，還有 {n} 則傳話在送：新的先排到 {primary}，等這些送完（最多 {mins} 分鐘）再切回。",  # (C-188)
    "failback_drain_late": "切回 {primary}：等了 {mins} 分鐘還有 {n} 則傳話沒送完（{names}），照樣切回；它們留在 {standby} 的佇列，下次 {standby} 當機器人時才會送。",
    "failback_drain_moved": "切回 {primary}：等了 {mins} 分鐘還有 {n} 則傳話沒送完，搬到 {primary} 的佇列，由它接著交。",  # (C-188)
    "failback_moved": "切回 {primary}：交還時 {standby} 的佇列還有 {n} 則傳話，搬到 {primary} 的佇列，由它接著交。",  # (C-193)
    "failback_left": "切回 {primary}：交還時還有 {n} 則傳話（{names}）沒辦法搬到 {primary}，留在 {standby} 的佇列，下次 {standby} 當機器人時才會送。",
    "failback_back": "沒有切回 {primary}：等待期間送到 {primary} 的 {n} 則傳話已經搬回 {standby}，照常交。",
    "failback_back_left": "沒有切回 {primary}：等待期間送到 {primary} 的 {n} 則傳話搬回 {standby} 了，但 {primary} 那邊沒刪掉（{names}），{primary} 下次當機器人時可能會再交一次。",
    "failback_back_failed": "沒有切回 {primary}：等待期間送到 {primary} 的傳話沒辦法搬回 {standby}（{why}），留在 {primary} 的佇列，它下次當機器人時才會送。",
    "failback_push_failed": "切回 {primary} 沒做成：沒辦法把目前的狀態交給 {primary}（{why}），過幾分鐘會自動再試。",
    "replayed_footer": "-# 換機器時補貼 · {tail}",  # (#release 11-08)  # (#release 11-05, design section 10: the footer of a reply posted
    # after a takeover, with the machine and the turn's id as every reply's footer ends with a standby)
    "watch_down": "⛔ {primary} 上的機器人停了超過 5 分鐘（{standby} 發現的；沒有設定自動接手）",  # (#release 11-05, sec 10)
    "proxy_on": "⚠️ 這次連線經過 proxy（{proxy}）：這只給演練用，正式的機器人不該看到這行；請把 DCBOT_PROXY 拿掉",
    "drill_sender_on": "⚠️ 演練用：webhook 發的訊息當成使用者的（DCBOT_DRILL_SENDER）。只在演練的 rig 裡有效；正式的機器人不該看到這行",
    "drill_failback_on": "⚠️ 演練用：主機回來 {secs} 秒就切回（DCBOT_DRILL_FAILBACK_SECS，平常是 30 分鐘）。只在演練的 rig 裡有效；正式的機器人不該看到這行",
    "drill_failback_off": "⚠️ 設了 DCBOT_DRILL_FAILBACK_SECS，但這裡不是演練的 rig（或值不對），已經忽略；請把它拿掉",
    "drill_read_fail_on": "⚠️ 演練用：run/ 裡的檔在的時候，讀 #bot-lease 一律當作失敗（DCBOT_DRILL_LEASE_READ_FAIL）。只在演練的 rig 裡有效；正式的機器人不該看到這行",
    "drill_read_fail_off": "⚠️ 設了 DCBOT_DRILL_LEASE_READ_FAIL，但這裡不是演練的 rig，已經忽略；請把它拿掉",
    "stepped_down_note": "（剛才那則「已上線」是這台發的，它沒有接手）",
    "drill_sender_off": "⚠️ 設了 DCBOT_DRILL_SENDER，但這裡不是演練的 rig，已經忽略；請把它拿掉",
    "watch_no_primary": "⚠️ {primary} 已經 {minutes} 分鐘沒在 #bot-lease 留下紀錄，備援沒辦法判斷它還在不在，所以 {primary} 停了也不會接手。請跑 doctor。",
    "watch_discord": "⚠️ {primary} 上的機器人連不上 Discord 已經 10 分鐘，訊息暫時沒人回",
    "stale_owner": "⚠️ {host} 把現在的機器人當成過時的，拒收它送的東西：它還記得之前有備援時的機器人，但這邊已經沒有在跑"
                   "備援（#bot-lease 被刪了，或拿掉了備援角色）。那台的頻道收不到東西，請跑 doctor，照它說的做。",  # (#release)
    "audit_recovered": "{primary} 重新上線：{standby} 接手後自己也停了，沒有交還。照它 {when} 傳來的頻道狀態接回；"
                       "接手期間在 {standby} 開的對話，留在 {standby}。",  # (#release 11-08)
    "audit_failed_back": "切回：{standby} 交還，{primary} 重新接手（這是第 {epoch} 次換機器）。",
    "checks_ok": "通過",
    "checks_stale": "通過（用的是 {when} 的頻道設定）",
    "cannot_take_over": "⛔ {primary} 上的機器人停了，{standby} 沒辦法接手：{why}。請去看 {primary} 那台。",
    "why_no_state": "還沒收到 {primary} 傳來的狀態",
    "why_protocol": "兩台的引擎差太多，沒辦法互相接手（請把兩台都更新到最新的發佈版本）",
    "why_requirements": "兩台裝的套件不一樣",
    "day_stopped": "⚠️ 機器人 24 小時內換了 3 次機器，先留在 {here}，不再自動切換，請看一下 {primary} 怎麼了",
}
# C-184 (bot/pauses.py): a channel paused until a time by another Claude (altention dc post --pause-until), resumed by
# the bot then. {until}, {at}: clock times (altention/dc/pausetime.shown()); {by}, {who}: "#channel" or "<machine> 上的 Claude".
PAUSE = {
    "paused": "⏸️ 暫停到 {until}（{by}：{why}）。到時間機器人會在這裡叫你恢復；在那之前先不要開始新的工作，"
              "手上這一步做到可以停的地方就停。",
    "resumed": "▶️ 恢復了：{by} 在 {at} 叫你暫停到 {until}（{why}）。照暫停前的進度繼續。{late}",
    "resumed_early": "▶️ 提早恢復（{who}：{early_why}）：原本 {by} 在 {at} 叫你暫停到 {until}（{why}）。照暫停前的進度繼續。",
    "late": "（晚了 {minutes} 分鐘：那時機器人沒在跑）",
    "done_mark": "（已恢復）",
    "held_line": "（暫停期間 {at} 由 {sender} 傳來）",
    "card_held": "暫停中，{until} 恢復時、或使用者在這裡說話時交給 Claude",
    "told_held": "#{channel} 暫停到 {until}（{why}），這則已經留著，屆時交給它；急的話用 --unpause --why 先叫醒它",
    "held_full": "#{channel} 暫停中，留著的訊息已經有 {most} 則，這則沒有留住：請在它恢復後再傳",
    "refused": "#{channel} 暫停到 {until}，要先 --unpause --why",
    "not_paused": "#{channel} 沒有在暫停",
    "not_conversation": "#{channel} 不是有 Claude 的對話頻道，不能暫停",
    "said_paused": "已暫停 #{channel} 到 {until}",
    "said_changed": "已改成暫停 #{channel} 到 {until}（原本到 {old}，{old_by} 停的）",
    "said_unpaused": "已恢復 #{channel}，留著的 {held} 則接著交給它",
    "audit_paused": "{who} 暫停 #{channel} 到 {until}：{why}",
    "audit_changed": "{who} 把 #{channel} 的暫停改成到 {until}（原本到 {old}，{old_by} 停的）：{why}",
    "audit_resumed": "#{channel} 恢復了（{by} 停到 {until}）{late}，留著的 {held} 則接著交給它",
    "audit_unpaused": "{who} 提早恢復 #{channel}：{why}",
    "audit_held_failed": "#{channel} 恢復後，留著的訊息交不進去（{why}），還剩 {left} 則，下一分鐘再試",
    "audit_card_failed": "#{channel} 恢復了，但 ▶️ 卡片沒貼出來（{why}），下一分鐘再貼；那裡的 Claude 照樣會收到",
    "audit_told_failed": "#{channel} 恢復了，但那裡的 Claude 沒收到「恢復了」（{why}），下一分鐘再交一次",
    "resent_mark": "（重送）",
    "audit_held_dropped": "#{channel} 恢復了，但那裡已經沒有 Claude 對話，留著的 {left} 則沒有交（卡片都還在頻道裡）",
    # C-199: the owner speaking in a paused channel takes what was held along; --drop-held
    "audit_woken": "#{channel} 暫停中，使用者說話時把留著的 {n} 則接在後面交給了 Claude（暫停照舊到 {until}{left}）",
    "woken_left": "；還有 {left} 則沒交進去，之後再交",
    "audit_woken_failed": "#{channel} 暫停中，使用者說話後要把留著的訊息接著交，交不進去（{why}），還剩 {left} 則：恢復時或使用者下次說話時再交",
    "drop_refused": "只有 #{channel} 自己的 Claude（查證過的）能清它留著的訊息",
    "drop_none": "#{channel} 沒有留著的訊息",
    "said_dropped": "清掉了 #{channel} 留著的 {n} 則；恢復時不會再交",
    "audit_dropped": "{who} 清掉了 #{channel} 留著的 {n} 則（暫停中，不交了）：{why}",
    "audit_drop_refused": "{who} 想清 #{channel} 留著的訊息，被拒：不是那個頻道自己的 Claude（查證過的）（理由：{why}）",
}
# C-189: a dialog on the screen taken in again (the bot restarted; bot/menus.py look_again())
MENUS = {
    "restored_title": "還開著的對話框（機器人重啟前就在了）",
    "restored": "機器人重啟過，按鈕重新接上了",
}
# C-191: the bot's own permissions (bot/perms.py; why and "works without it": manager 11-17)
PERMS = {
    "card": "🔑 **要不要給機器人「管理員」權限？**\n"
            "**為什麼要**：之後的功能需要它，例如替你把伺服器設成社群伺服器、設定規則和公告頻道。Discord 不讓機器人自己拿權限，要你授權一次。\n"
            "**不給也照常能用**：現在所有功能都只用原本的 14 項權限，不理這張卡什麼都不會壞。\n"
            "**要給的話**：打開下面的連結，伺服器已經選好。畫面會寫「新增至伺服器」，第二頁會列出「管理者」（Discord 畫面上管理員權限叫這個名字），按「授權」就好。"
            "這不是重裝：機器人不會被踢掉，頻道和設定都不變。伺服器開了「管理員要兩步驟驗證」的話，你的帳號要先開。\n"
            "<{grant}>\n"
            "不想用連結：伺服器設定 → 身分組 → 機器人的身分組 → 打開「管理者」。\n"
            "**之後想收回**：用這個連結再授權一次，只會回到原本的 14 項：<{revert}>，或在身分組關掉「管理者」。\n"
            "-# 這張卡只貼這一次；之後系統檢查（doctor）會一直列著，直到給了為止。",
    "granted": "✅ 機器人已經有「管理員」權限了（{when}）。\n"
               "之後想收回：用這個連結再授權一次，只會回到原本的 14 項，不會踢掉機器人：<{revert}>，或在伺服器設定 → 身分組關掉「管理者」。",
    "audit_now": "[權限] 機器人目前的權限：{state}（值 {bits}）",
    "audit_changed": "[權限] 機器人的權限變了：{before} → {after}（值 {was} → {bits}；Discord 的審核日誌不記這件事）",
    "admin": "有管理員",
    "no_admin": "沒有管理員",
    "missing": "少了 {names}",
    "need_admin": "這件事要機器人有「管理員」權限，現在沒有：到 #update 的「管理員權限」卡片按授權連結（不給的話其他功能照常）",
}
# C-201: /where's card (bot/where.py card()); 📍 is where and who a channel is (notice-design.md)
WHERE = {
    "title": "📍 #{channel} 現在在哪裡",
    "machine": "- 機器：{machine} · {server} · session {session} · window {window}",
    "machine_pane": "- 機器：{machine} · {server} · session {session} · 窗格 {pane}",
    "server_bot": "tmux（機器人的）",
    "server_user": "tmux（你的）",
    "unknown": "？",
    "conversation": "- 對話：Claude Code · {id}…{title}",
    "no_conversation": "- 對話：還沒有（下一則訊息會開一個）",
    "conversation_agent": "- 對話：{agent} · {id}…",  # (an adapter's pane: C-198)
    "no_conversation_agent": "- 對話：{agent}（轉接層沒有給對話 id）",
    "folder": "- 資料夾：{folder}",
    "follow": "- 改名時跟著變：{names}",
    "follow_channel": "頻道",
    "follow_conversation": "對話",
    "follow_window": "window",
    "follow_session": "session",
    "seen_here": "- 上次對帳：{at} 在",
    "seen_gone": "- ⚠️ 上次對帳：{at} 窗格不見了",
    "seen_unknown": "- 上次對帳：{at} 那台連不上，不知道",
    "seen_respawned": "- 上次對帳：{at} 窗格重開過，正在確認是不是同一個",
    "seen_unsure": "- 上次對帳：{at} 那個窗格號還在，但紀錄沒有它的程序，不確定是不是同一個（重新 /attach 一次就確定了）",
    "none": "📍 #{channel} 沒有 Claude 對話（不是對話頻道，或已經封存）",
}
# C-201: the look (bot/reconcile.py): a channel's pane not where its record says
RECONCILE = {
    "gone": "⚠️ 這個頻道的窗格不見了（{machine} · {pane}），最後一次看到是 {at}。{what}",
    "never": "（之前沒看到過）",
    "what_attached": "要接回：在那台把 Claude Code 開起來（`claude --resume` 接同一個對話），再用 /attach 接到這個頻道。",
    "what_window": "下一則訊息會在原本的地方重開它。",
    "back": "✅ 這個頻道的窗格回來了（{machine} · {pane}）。",
    "audit_gone": "[對帳] {machine}：{n} 個頻道的窗格不見了：{channels}",
    "audit_first": "[對帳] 第一次對帳：{n} 個頻道的紀錄指向已經不在的窗格：{channels}（只記這一次，沒有在頻道說）",
    "audit_back": "[對帳] #{channel} 的窗格回來了（{machine} · {pane}）",
    "audit_moved": "[對帳] #{channel} 的窗格在 tmux 裡換了位置：{old} → {new}",
    "audit_stale": "[對帳] 拿掉 {n} 個舊的頻道標記（標的頻道現在不接那個窗格）：{panes}",
    "audit_bound": "[對帳] 補上路由：{machine} 的窗格 {pane} → #{channel}（原本：{was}）",
    "audit_unbound": "[對帳] 拿掉路由：{machine} 的窗格 {pane}（原本給 #{channel}；{why}）",
    "unbound_gone": "窗格已經不在",
    "unbound_process": "窗格裡換了別的程式",
    "unbound_tag": "窗格標記的是別的頻道",
    "unbound_unpaned": "那個頻道現在沒有接任何窗格",
    "audit_shared": "[對帳] {machine} 的窗格 {pane} 有好幾個頻道的紀錄都指著它：{channels}。它的路由和標記先不動，請只留一個頻道接著它（其他的用 !detach 卸下）",
    "none": "沒有",
}
# C-201 2b: a rename taken along by the channel, its conversation, its tmux window and session (bot/rename.py);
# marks per #discord-lab 11-18: ✅ done, ❌ tried and not done, ➖ not for this channel (no failure)
RENAME = {
    "usage": "用法：`/rename <新名字>`（例如 `/rename 回測`）",
    "done": "✅ #{old} 改名為 #{new}",
    "partly": "⚠️ #{old} 改名，有一項沒改成",
    "late_head": "⚠️ #{old} 改名，頻道名還不知道改了沒",
    "failed": "❌ #{old} 沒改成名字",
    "row_done": "- ✅ {item}",
    "row_done_as": "- ✅ {item}：{name}",
    "row_failed": "- ❌ {item}：{why}",
    "row_skipped": "- ➖ {item}：{why}",
    "row_late": "- ⏳ {item}：{why}",
    "item_channel": "頻道名",
    "item_conversation": "Claude 對話名",
    "item_agent_conversation": "對話名",  # (an adapter's pane: nothing typed, C-198)
    "adapter_none": "轉接層沒有",
    "item_window": "tmux window",
    "item_session": "tmux session",
    "same_name": "已經是這個名字",
    "rate_limited": "Discord 限制 10 分鐘只能改兩次，頻道名晚點再改",
    "limited_not_done": "Discord 限制 10 分鐘只能改兩次，頻道名先沒改，過幾分鐘再改一次",
    "late": "Discord {secs} 秒內沒有回應，頻道名還不知道改了沒，過幾分鐘看一下",
    "discord_refused": "Discord 沒有接受（{why}）",
    "not_tmux": "這個頻道不是在 tmux 裡跑的 Claude，沒有對話名",
    "not_sent": "沒送進去（{why}）",
    "not_entered": "沒有打進對話框",
    "no_window": "這個頻道的 Claude 現在不在 tmux 裡，沒有 window 可改",
    "window_closed": "視窗現在沒開，下次開的時候用新名字",
    "unreachable": "那台機器現在連不上，{item}名沒改",
    "tmux_refused": "tmux 沒有改（{why}）",
    "not_bots": "不是機器人開的，沒改",
    "users_session": "是你自己的 session，沒改",
    "not_followed": "這個頻道設定改名時不跟著變",
    "by": "-# 由 #{channel} 的 Claude 改名：{why}",
    "audit": "[改名] #{old} → #{new}（{done}{skipped}{failed}）{by}",
    "audit_discord": "[改名] #{old} 在 Discord 改名為 #{new}，跟著改：{done}{skipped}{failed}",
    "audit_tmux": "[改名] #{channel} 的 tmux window 在 tmux 裡改名為 {name}，跟著改：{done}{skipped}{failed}",
    "audit_done": "改了：{items}",
    "audit_none": "沒有",
    "audit_skipped": "；沒改：{items}",
    "audit_failed": "；沒改成：{items}",
    "audit_late": "；還不知道改了沒：{items}",
    "audit_by": " · 由 #{channel} 的 Claude：{why}",
    "button": "改名…",
    "modal_title": "改名：#{channel}",
    "modal_field": "新名字",
    "gone": "這個頻道已經不在了",
}
# C-205: altention dc post --delete (bot/deleting.py)
DELETE = {
    "no_why": "要帶 --why（會記在 #audit）",
    "no_ids": "要給訊息 ID",
    "not_ids": "訊息 ID 要是數字",
    "too_many": "一次最多 {most} 則，請分批",
    "audit": "#audit 是紀錄，不能刪",
    "elsewhere": "這則不在這個頻道（要刪請對它所在的頻道下指令）",
    "unread": "讀不到這則，沒有刪（{why}）",
    "no_read": "機器人看不到這則",
    "no_answer": "Discord {secs} 秒內沒有回應",
    "thread_starter": "這則開了討論串，刪了討論串就沒有開頭；要收掉請處理那個討論串（封存或刪掉討論串）",
    "not_saved": "存不進 #audit，沒有刪",
    "no_manage": "機器人沒有「管理訊息」權限，沒有刪（伺服器設定 → 身分組 → 機器人的身分組 → 打開「管理訊息」；或給它「管理員」權限，看 #update 的權限卡）",
    "late": "Discord {secs} 秒內沒有回應，不知道刪了沒，過一下再看",
    "limited": "Discord 要機器人等一下再刪（速率限制），這次沒有刪，晚點再送一次",
    "refused": "Discord 沒收（{why}）",
    "failed": "出錯了（{why}），沒有刪",
    "saved_head": "🗑️ #{channel} 的訊息 {mid} 要被刪，先存在這裡（{by} 要刪；{words}；理由：{why}）",
    "saved_author": "作者：{name}（{id}）{bot}　時間：{at}",
    "saved_files": "附件：{files}",
    "saved_text": "內容（已遮罩；完整的 embed、元件在附檔，文字也遮罩了）：",
    "saved_no_text": "（沒有文字）",
    "was_board": "看板「{name}」的一部分，看板紀錄拿掉了",
    "was_call": "@ 使用者的「等你」那行（{key}），標成結束",
    "was_asking": "「⏸️ 在等你」的通知（{ask}），紀錄拿掉了",
    "was_menu": "還開著的選單卡（{title}），選單紀錄和「等你」那行收掉了，畫面上還有對話框就重貼一張",
    "was_ask": "提問或計畫的卡片（{ask}），提問已取消，已告訴提問的 Claude 是卡片被刪了",
    "notice": "-# 🗑️ {by} 刪了 {n} 則 <@{author}> 的訊息：{why}",
    "sum_done": "刪了 {n} 則：{ids}",
    "sum_gone": "已經不在 {n} 則：{ids}",
    "sum_refused": "沒有刪 {n} 則：{ids}",
    "sum_left": "時間不夠沒輪到 {n} 則：{ids}",
    "ans_done": "刪掉了：{ids}",
    "ans_gone": "已經不在了（之前就刪掉了）：{ids}",
    "ans_unknown": "這個頻道裡找不到（可能已經刪了，或在別的頻道；在別的頻道就對那個頻道再下一次）：{ids}",
    "ans_refused": "沒有刪 {mid}：{why}",
    "ans_left": "時間不夠，沒輪到：{ids}（沒有動，請再送一次）",
}
# What Claude puts at the start of a final reply that needs the owner (calls.discussed()); in the system note (C-103).
RESET = {  # altention dc post --reset (v0.6.1, manager 11-19): bot/resets.py
    "channel": "🔄 由 {by} 開了新對話：{why}",
    "channel_own": "🔄 這個頻道的 Claude 開了新對話：{why}",
    "audit": "🔄 altention dc post --reset：{by} 讓 #{channel} 開了新對話（{why}）{then}",
    "audit_then": "，接著交給新對話：{then}",
    "done": "#{channel} 開了新對話（舊的可以用 `altention dc post {channel} --resume <id 開頭>` 或 `!resume` 接回）{then}",
    "done_then": "，並把 --then 的話交給了它",
    "later": "這一輪結束後 #{channel} 會開新對話{then}",
    "later_then": "，再把 --then 的話交給它",
    "later_default": "，再叫新對話讀交接檔",
    "own_default": "（新對話）上一個對話由這個頻道的 Claude 結束並開了這個新的：{why}。接續請讀它留下的交接檔"
                   "（舊對話可以用 `!resume` 接回）。",
    "not_claude": "#{channel} 不是有 Claude 對話的頻道，--reset 不適用",
    "attached": "#{channel} 接的是使用者自己在 tmux 開的對話，--reset 不適用（只有使用者能在那裡 `/clear`）",
    "busy": "#{channel} 沒有開新對話：它{what}。等它閒下來再試（--reset 沒有強制的選項）",
    "busy_turn": "正在跑一輪",
    "busy_bg": "有 {n} 個背景工作",
    "busy_subs": "有 {n} 個子代理或 workflow 在跑",
    "busy_queue": "還有 {n} 則訊息排著要給它",
    "paused": "#{channel} 暫停中（到 {until}），先不開新對話；要的話先 --unpause",
    "cancelled": "-# --reset 取消：要求它的那一輪沒有正常結束（中斷或失敗），這裡已經開始了新的一輪，對話照舊",
    "audit_cancelled": "🔄 altention dc post --reset 取消：#{channel} 的那一輪沒有正常結束就開始了新的一輪（{by} 要求的：{why}）",
}
# TODO(v0.7.0, i18n): the marker follows the language, and the bot knows it in every language (#release 11-03).
DISCUSS_MARK = "💬 要跟你討論："
GROUPS = {"not_sent": NOT_SENT, "unconfirmed": UNCONFIRMED, "notes": NOTES, "far": FAR, "secret": SECRET, "quota": QUOTA,
          "calls": CALLS, "cc": CC, "lease": LEASE, "pause": PAUSE, "menus": MENUS,
          "perms": PERMS, "where": WHERE, "reconcile": RECONCILE, "rename": RENAME, "reset": RESET,
          "delete": DELETE}


def enter():
    """The defaults above, entered in the engine's table of text (where they are not there already)."""
    table = alt_text.TEXT[alt_text.DEFAULT]
    for group, rows in GROUPS.items():
        for code, row in rows.items():
            table.setdefault("dcbot.%s.%s" % (group, code), row[-1] if isinstance(row, tuple) else row)
    for how, words in CLEARED.items():
        table.setdefault("dcbot.cleared.%s" % how, words)


enter()


def about(data):
    """What came with a code this table has no words for, short enough for one line."""
    said = "、".join("%s=%s" % (k, str(v)[:80].replace("`", "'")) for k, v in sorted(data.items()))
    return "：" + said if said else ""


# The installation's say in the words: its language (setting "core.language") and its own wording of single
# sentences (setting "dcbot.text": {"<group>.<code>": "…"}, the keys as in GROUPS and "cleared.<how>"). Read by
# load(), never where a sentence is taken: that is the event loop, and the settings file is read from disk, with
# waits when it is broken (step 2.4b: a broken settings file is not waited for in the event loop).
loaded = {"lang": alt_text.DEFAULT, "own": {}, "stamp": False, "talk_limit": None, "small_tables": True,
          "json_mentions": "owner"}


def talk_limit_of(said):
    """The installation's "dcbot.talk_limit" as a limit: a whole number of 0 or more (0 = none), else None."""
    return said if isinstance(said, int) and not isinstance(said, bool) and said >= 0 else None


def small_tables_of(said):
    """The installation's "dcbot.small_tables" (C-71a: small tables in a reply made readable): true unless it says
    false; anything but true or false is the default, and one line in the log."""
    if said is None or isinstance(said, bool):
        return said is not False
    print("messages: dcbot.small_tables is %r, not true or false; the default (true) is used" % (said,), flush=True)
    return True


def load():
    """Read what the installation says of the words into memory. Off the event loop (the bridge runs it in a
    thread at start and once a minute); the settings file is read again only when it has changed. Settings that
    cannot be read: the default words, one line in the log, nothing raised. True when it read."""
    stamp, lang, own, talk, tables, pings = None, alt_text.DEFAULT, {}, None, True, "owner"
    try:
        inst = alt_text.config.instance_dir()
        path = os.path.join(inst, "plugins.json") if inst else None
        if path:
            try:
                st = os.stat(path)
                stamp = (st.st_mtime_ns, st.st_size)
            except OSError:
                stamp = None
        if stamp == loaded["stamp"]:
            return False
        said = alt_text.config.setting("core.language", alt_text.DEFAULT)
        lang = said if isinstance(said, str) and said else alt_text.DEFAULT
        said = alt_text.config.setting("dcbot.text")
        own = dict((k, v) for k, v in said.items() if isinstance(k, str) and isinstance(v, str) and v) if isinstance(said, dict) else {}
        talk = talk_limit_of(alt_text.config.setting("dcbot.talk_limit"))  # read here too: off the event loop, once per change
        tables = small_tables_of(alt_text.config.setting("dcbot.small_tables"))
        pings = "any" if alt_text.config.setting("dcbot.json_mentions") == "any" else "owner"  # (whom a tool's message
        # may ping, bot/rules.py: C-172a P1c)
    except Exception as e:
        lang, own, talk, tables, pings = alt_text.DEFAULT, {}, None, True, "owner"
        print("messages: the installation's settings cannot be read (%s: %s); the default words are used" % (type(e).__name__, e),
              flush=True)
    loaded.update(lang=lang, own=own, stamp=stamp, talk_limit=talk, small_tables=tables, json_mentions=pings)
    return True


def words(group, code, data, general=None):
    """The words for a code, from memory alone: the installation's own, else the engine's table of text in the
    installation's language, else in the default one; with the data put in (a field left out, or a wording that
    cannot take them, is said as it stands: saying it must not fail). `general`: what to say of a code nobody
    has words for."""
    key = "%s.%s" % (group, code)
    said = loaded["own"].get(key) or alt_text.TEXT.get(loaded["lang"], {}).get("dcbot." + key) \
        or alt_text.TEXT[alt_text.DEFAULT].get("dcbot." + key)
    if said is None:
        return general.format(code=code, data=about(data)) if general else key
    try:
        return said.format(**data) if data else said
    except (KeyError, IndexError, ValueError):
        return said


def not_sent(code, **data):
    """The sentence for a message that was not sent, by its code; the general one for a code not known here."""
    if "cleared" in data:  # (a /btw that was not one: what became of the box)
        data = dict(data, cleared=words("cleared", data["cleared"] if data["cleared"] in CLEARED else "stuck", {}))
    return words("not_sent", code, data, GENERAL)


def unconfirmed(code, **data):
    return words("unconfirmed", code, data, GENERAL_NOTE)


def far(code, **data):
    """What is said of a pane on a machine that did not answer, where nothing was being sent (FAR)."""
    return words("far", code, data, GENERAL_NOTE)


def lease(code, **data):
    return words("lease", code, data, GENERAL_NOTE)


def reconcile(code, **data):
    """C-201's words for the look (RECONCILE)."""
    return words("reconcile", code, data, GENERAL_NOTE)


def rename(code, **data):
    """C-201 2b's words (RENAME)."""
    return words("rename", code, data, GENERAL_NOTE)


def where(code, **data):
    """C-201's words (WHERE)."""
    return words("where", code, data, GENERAL_NOTE)


def perms(code, **data):
    """C-191's words (PERMS)."""
    return words("perms", code, data, GENERAL_NOTE)


def cc(code, **data):
    """C-162's words (CC)."""
    return words("cc", code, data, GENERAL_NOTE)


def menus(code, **data):
    """C-189's words (MENUS)."""
    return words("menus", code, data, GENERAL_NOTE)


def pause(code, **data):
    """C-184's words (PAUSE)."""
    return words("pause", code, data, GENERAL_NOTE)


def reset(code, **data):
    """altention dc post --reset's words (RESET)."""
    return words("reset", code, data, GENERAL_NOTE)


def note(code, **data):
    return words("notes", code, data, GENERAL_NOTE)


def call(code, **data):
    """A line of bot/calls.py (CALLS), without the mention."""
    return words("calls", code, data, GENERAL_NOTE)


def common(code):
    """The common code a code belongs to: its own when it is one, the table's, else "unknown"."""
    return code if code in COMMON else NOT_SENT.get(code, ("unknown",))[0]


def retry(code):
    """TEMPORARY or PERMANENT; a code not known here is not known to be worth sending again."""
    return NOT_SENT[code][1] if code in NOT_SENT else PERMANENT
