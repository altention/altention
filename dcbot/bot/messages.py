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
    'claude_code.resent':
        'ℹ️ 上一則訊息第一次沒有送進去（按了 Enter 還留在輸入框裡），已經重送。',
    'claude_code.new_conversation':
        '接不回原本的對話（`{session}`），已開始新的對話。',
    'masked': '-# 有 {n} 處像金鑰的字被遮掉了',
    # C-74 (bot/unsent.py): a message not sent for now, sent again
    'resend.done': '✅ 已再送一次',
    'resend.handed': '✅ 對話框關了，已補送',
    'resend.expired': '這則已經過期或已經送過了，要的話請重打一次。',
    'resend.still': '-# 再送一次也沒送出，見下一則',
    'resend.failed': '⚠️ 補交給 #{to} 的 Claude 時出錯（{why}），這則還留著，下次再試：{text}',
    'resend.recovered': '✅ 剛才補交給 #{to} 的 Claude 出錯的那則，已經交到了：{text}',
    'resend.press_failed': '⚠️ 再送一次時出錯（{why}），內容還留著，可以再按一次。',
    'resend.given_up': '⚠️ 這則一直沒能交給 #{to} 的 Claude（{hours} 小時內那個頻道的終端機都不是一般的輸入框），已經放棄：{text}',
}

GENERAL = "⚠️ 這則**沒有送出**（原因代號 `{code}`{data}）。請用 `/screen` 看一下終端機，處理好之後再重發一次。"
GENERAL_NOTE = "ℹ️ `{code}`{data}"
GROUPS = {"not_sent": NOT_SENT, "unconfirmed": UNCONFIRMED, "notes": NOTES, "far": FAR}


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
loaded = {"lang": alt_text.DEFAULT, "own": {}, "stamp": False, "talk_limit": None, "small_tables": True}


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
    stamp, lang, own, talk, tables = None, alt_text.DEFAULT, {}, None, True
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
    except Exception as e:
        lang, own, talk, tables = alt_text.DEFAULT, {}, None, True
        print("messages: the installation's settings cannot be read (%s: %s); the default words are used" % (type(e).__name__, e),
              flush=True)
    loaded.update(lang=lang, own=own, stamp=stamp, talk_limit=talk, small_tables=tables)
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


def note(code, **data):
    return words("notes", code, data, GENERAL_NOTE)


def common(code):
    """The common code a code belongs to: its own when it is one, the table's, else "unknown"."""
    return code if code in COMMON else NOT_SENT.get(code, ("unknown",))[0]


def retry(code):
    """TEMPORARY or PERMANENT; a code not known here is not known to be worth sending again."""
    return NOT_SENT[code][1] if code in NOT_SENT else PERMANENT
