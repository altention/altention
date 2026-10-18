#!/usr/bin/env python3
"""Status boards in the dashboard channel (every 2 minutes from cron on the bot's machine).

  python3 status/boards.py            build the four boards, post the ones whose text changed
  python3 status/boards.py --print    build them, print them instead (nothing is posted or written)

Four boards, posted in this order the first time (the last one ends up lowest in the channel):
  頻道            every conversation channel the bot keeps: busy or idle, how full its context is, when it was last
                  active, which machine its conversation is on; the most recent TOP, then a line with the total
  機器和機器人    each machine: reachable, engine, last sync; the bot: since when it is up, how many channels it keeps
  需要注意的事    doctor's warn and fail records, notifications that could not be sent, sessions whose channel is
                  gone, channels Discord does not hand out, turn ends that waited out their limit
  在等你的事      questions and plans not answered yet, and since when

It only reads: the bot's state folder (dcbot/state, written by the bot) and the records `ops/doctor.py --watch`
leaves after each round (~/.local/state/altention/doctor-records.json). Nothing here asks another machine or
Discord for anything. A file that is missing, cut short or of another shape than expected never stops a round: that
part of a board says so or is left out.

A board is posted with `altention dc post <channel> --board <name> <text>` (edited in place by the bot), and only when its
text differs from what was last handed over (kept in ~/.local/state/altention/status-posted.json), so the board's
own "更新於" moves only when something changed. That is why times are written as clock times, never "3 minutes ago".
The channel is the instance's `status.channel`; without it the round is skipped (never a guessed channel).

`altention dc post` taking a board (exit status 0) means it is in the bot's spool, not that it is on Discord. So, reading only:
- nothing is handed over while the bot is not running (no fresh heartbeat), nor while an earlier version of the
  same board still waits in the spool: when the bot is back it gets the latest once, never a pile to race through;
- a board the bot has not made yet (no message of it in the channel's state) is made one at a time, the next only
  once that one is there: this is what fixes the order of the four in the channel;
- every 10 minutes what the boards really say is read back (`altention dc read`, one call) and compared with what was
  handed over; a board that says something else is handed over again. Where reading back does not work, a board
  is handed over again unconditionally after FALLBACK.
Words that come from elsewhere (doctor's messages, machine names) are made harmless before they go on a board:
no code fence, no mention.
Standard library only, Python 3.8.
"""
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.dirname(HERE)
TOP = 15  # channels listed on the first board
STALE = 40 * 60  # seconds: doctor's records older than this (two rounds of --watch missed) are said to be old
OLD_ALERT = 3 * 86400  # seconds: a machine's noted alerts whose latest is older are folded into one line
BEAT_LATE = 180  # seconds without the bot's heartbeat before the board says so
MSG_MAX = 140  # characters of a doctor message shown
ORDER = ("頻道", "機器和機器人", "需要注意的事", "在等你的事")
ITEMS_MAX = 15  # items listed under 需要注意的事; the rest are counted in one line
SETTLE = 150  # seconds the bot is given to put a handed-over board on Discord before it is looked at
MAKING = 6 * 60  # seconds a board handed over for the first time may take to appear before it is handed over again
READ_BACK_EVERY = 10  # minutes: in rounds at these minutes of the hour the boards are read back and compared
READ_BACK = 30  # messages of the channel read back (it holds boards only)
HEAD = 300  # characters of a board's text compared with what the channel shows
# seconds after which a board nobody could read back is handed over again though its text is the same
FALLBACK = {"在等你的事": 30 * 60}
FALLBACK_OTHER = 6 * 3600
# doctor says these two from the same files this reads itself (fresher here): not listed twice
READ_HERE = ("dcbot.posts-failed", "dcbot.ownerless-sessions")


def bot_state():
    """The bot's state folder in this engine."""
    return os.path.join(ENGINE, "dcbot", "state")


def state_dir():
    return os.path.expanduser("~/.local/state/altention")


def records_path():
    """Where `ops/doctor.py --watch` leaves each whole round's records."""
    return os.path.join(state_dir(), "doctor-records.json")


def posted_path():
    return os.path.join(state_dir(), "status-posted.json")


def engine_on_path():
    """The engine's own modules (altention) can be imported from here on."""
    if ENGINE not in sys.path:
        sys.path.insert(0, ENGINE)


def read_json(path):
    """The file's JSON, or None when it is not there or not JSON."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def number(value):
    """`value` as a float if it is a plain, finite number, else None (True, "12", NaN, 1e400 are not)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if value == value and abs(value) != float("inf") else None


def moment(value):
    """`value` as a time in seconds if it can be one (a number, after 1970 and before the year 5000), else None."""
    value = number(value)
    return value if value is not None and 0 < value < 1e11 else None


def clock(ts, now):
    """17:05 for a time today, 10-08 17:05 for another day, 時間不明 for what is not a time."""
    ts = moment(ts)
    if ts is None:  # time.localtime(None) is now: what is not a time must not read as one
        return "時間不明"
    try:
        then, today = time.localtime(ts), time.localtime(now)
    except (TypeError, ValueError, OverflowError, OSError):
        return "時間不明"
    same_day = (then.tm_year, then.tm_yday) == (today.tm_year, today.tm_yday)
    return time.strftime("%H:%M" if same_day else "%m-%d %H:%M", then)


def one_line(text, limit=MSG_MAX):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def harmless(text):
    """`text` so that it changes nothing around it on a board and calls nobody: a code fence becomes ''' (as the
    bot writes one inside its own fences), and @everyone, @here, <@…>, <@&…> get a zero-width space after what
    would start them (still readable, no longer a mention)."""
    text = str(text).replace("```", "'''")
    text = re.sub(r"@(?=everyone|here)", "@\u200b", text)
    return text.replace("<@", "<\u200b@")


def mention(channel_id):
    """<#id> for a channel id (Discord shows its name); anything that is not an id, as harmless words."""
    cid = str(channel_id)
    return "<#%s>" % cid if cid.isdigit() and cid.isascii() else harmless(one_line(cid, 40)).replace("<#", "<\u200b#")


# ------------------------------------------------------------------------------------------------ 頻道
def window(model):
    """The context window of `model`, as the bot's footer counts it; None when that cannot be told."""
    try:
        engine_on_path()
        from altention.connectors.claude_code import transcript
        size = transcript.window(model)
    except Exception:
        return None
    return size if number(size) and size > 0 else None


def context_share(ctx):
    """"context 76%" from a channel's [tokens, model], or "context 不明"."""
    if isinstance(ctx, (list, tuple)) and len(ctx) == 2 and isinstance(ctx[1], str):
        tokens, size = number(ctx[0]), window(ctx[1])
        if tokens is not None and tokens >= 0 and size:
            return "context %d%%" % round(100.0 * tokens / size)
    return "context 不明"


def conversations(channels, here):
    """[(last active or 0, channel id, state dict, machine)] of the conversation channels the bot keeps a session
    or a pane for (not archived), most recently active first."""
    rows = []
    for cid, st in (channels.items() if isinstance(channels, dict) else ()):
        if not isinstance(st, dict) or st.get("kind") != "conversation" or st.get("archived"):
            continue
        pane = st.get("pane") if isinstance(st.get("pane"), dict) else None
        if not (st.get("tmux") or pane):
            continue
        host = pane.get("host") if pane else None
        rows.append((moment(st.get("last_active")) or 0, str(cid), st, host if isinstance(host, str) and host else here))
    rows.sort(key=lambda r: (-r[0], r[1]))
    return rows


def channels_board(channels, here, now, top=TOP):
    if not isinstance(channels, dict):
        return "讀不到機器人的頻道狀態（dcbot/state/channels.json）"
    rows = conversations(channels, here)
    if not rows:
        return "目前沒有對話頻道"
    lines = []
    for last, cid, st, machine in rows[:top]:
        bg = number(st.get("bg"))
        doing = "__**在忙**__" if st.get("busy") else ("背景工作 %d" % bg if bg and bg > 0 else "閒著")
        lines.append("- %s · %s · %s · %s · %s" % (mention(cid), doing, context_share(st.get("context")),
                                                   clock(last, now) if last else "時間不明",
                                                   harmless(one_line(machine, 40)) if machine else "機器不明"))
    busy = sum(1 for r in rows if r[2].get("busy"))
    lines.append("共 %d 個對話頻道，%d 個在忙%s" % (len(rows), busy, "；只列最近 %d 個" % top if len(rows) > top else ""))
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------------ doctor's records
def watch_stale():
    """How old doctor's records may be before the board calls them old: two rounds of --watch missed, as the
    instance may have set its interval (core.schedules.watch, C-37), plus ten minutes; STALE when that cannot be
    worked out."""
    try:
        from altention import config, plugin
        d = plugin.load(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "altention", "core.plugin.json"), core=True)
        items, _ = plugin.effective_schedules("core", d, config.settings())
        when = next(i for i in items if i.get("name") == "watch")
        every = plugin._span_minutes(when["when"]["every"]) if not when.get("unknown") and isinstance(when.get("when"), dict) else None
        return 2 * every * 60 + 600 if every else STALE
    except Exception:
        return STALE


def doctor_records(path, now):
    """(records, at, note): the records of doctor's last whole round, when it was, and a line saying the records
    are old or missing (None while they are fresh)."""
    data = read_json(path)
    recs = data.get("records") if isinstance(data, dict) else None
    at = moment(data.get("at")) if isinstance(data, dict) else None
    if not isinstance(recs, list) or at is None:
        return [], None, "-# 系統檢查還沒跑完第一輪（每 15 分鐘一次），或結果檔讀不懂"
    recs = [r for r in recs if isinstance(r, dict) and isinstance(r.get("id"), str)]
    if now - at > watch_stale():
        return recs, at, "-# 機器的資料是 %s 的，之後沒有新的檢查結果" % clock(at, now)
    return recs, at, None


def by_machine(records):
    """{machine: {check id: record}} in the order machines first appear."""
    out = {}
    for r in records:
        if isinstance(r.get("machine"), str) and r["machine"]:
            out.setdefault(r["machine"], {})[r["id"]] = r
    return out


# ------------------------------------------------------------------------------------------------ 機器和機器人
def bot_line(state, kept, now):
    """The bot: since when it is up (the time its start-up self test passed), how many channels it keeps."""
    try:
        with open(os.path.join(state, "ready"), encoding="utf-8") as f:
            words = f.read().split()
    except (OSError, ValueError):
        words = []
    since = None
    if len(words) >= 2:
        try:
            since = moment(int(words[1]))
        except ValueError:
            pass
    line = "機器人：%s 起來" % clock(since, now) if since is not None else "機器人：沒有啟動紀錄（還沒通過啟動後的自我檢查，或沒在跑）"
    if kept is not None:
        line += "，記著 %d 個對話頻道" % kept
    try:
        with open(os.path.join(state, "heartbeat"), encoding="utf-8") as f:
            beat = moment(float(f.read().strip()))
    except (OSError, ValueError):
        beat = None
    if beat is None:
        line += "；**讀不到心跳**"
    elif now - beat > BEAT_LATE:
        line += "；**心跳停了**（最後 %s）" % clock(beat, now)
    return line


def machines_board(records, note, state, kept, now):
    lines = []
    for name, rs in by_machine(records).items():
        machine = harmless(one_line(name, 40))
        reach = rs.get("core.reachable")
        if reach is None:
            continue  # nothing said of reaching it: not a machine doctor looked at as one
        if reach.get("status") != "ok":
            lines.append("- %s · **連不上**" % machine)
            continue
        engine = (rs.get("core.engine") or {}).get("value")
        sync = (rs.get("core.last-sync-ok") or {}).get("value")
        lines.append("- %s · 連得上 · 引擎 %s · 上次同步 %s" % (
            machine, harmless(one_line(engine, 40)) if isinstance(engine, str) and engine else "不明", clock(sync, now)))
    lines.append(bot_line(state, kept, now))
    if note:
        lines.append(note)
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------------ 需要注意的事
ALERT_TIME = re.compile(r"最新一則（(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{4})")


def alert_time(record):
    """When the latest alert a core.alerts record speaks of was noted, or None when its message does not say."""
    found = ALERT_TIME.search(str(record.get("msg") or ""))
    if not found:
        return None
    try:
        return time.mktime(time.strptime(found.group(1)[:19], "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, OverflowError):
        return None


def count_json(folder):
    """How many records wait in a spool folder; 0 when it is not there."""
    try:
        return len([n for n in os.listdir(folder) if n.endswith(".json") and not n.startswith(".")])
    except OSError:
        return 0


def attention_board(records, note, state, now):
    fails, warns, old, newest_old = [], [], 0, None
    for r in records:
        if r.get("status") not in ("warn", "fail") or r["id"] in READ_HERE:
            continue
        if r["id"] == "core.alerts":
            at = alert_time(r)
            if at is not None and now - at > OLD_ALERT:
                n = number(r.get("value"))
                old += int(n) if n and n > 0 else 1
                newest_old = at if newest_old is None else max(newest_old, at)
                continue
        where = r.get("machine") if isinstance(r.get("machine"), str) and r.get("machine") else "全部機器"
        line = "%s：%s" % (harmless(one_line(where, 40)), harmless(one_line(r.get("msg")) or r["id"]))
        (fails if r["status"] == "fail" else warns).append("- **有問題** " + line if r["status"] == "fail" else "- " + line)
    lines = fails + warns  # what is wrong before what is only worth a look: the cut below takes from the end
    if old:
        lines.append("- 另有 %d 則超過 3 天的舊提醒（最近一則 %s）" % (old, time.strftime("%m-%d", time.localtime(newest_old))))
    failed = count_json(os.path.join(state, "posts-failed"))
    if failed:
        lines.append("- 有 %d 則通知送不出去，機器人會再試" % failed)
    ownerless = read_json(os.path.join(state, "ownerless-sessions.json"))
    kept = ownerless.get("kept") if isinstance(ownerless, dict) else None
    if isinstance(kept, list) and kept:
        lines.append("- 有 %d 個對話的頻道已經不在，機器人沒有關掉它們" % len(kept))
    away = read_json(os.path.join(state, "channels-unreachable.json"))
    away = away.get("channels") if isinstance(away, dict) else None
    if isinstance(away, list) and away:
        ids = [c.get("channel") for c in away if isinstance(c, dict) and c.get("channel")]
        lines.append("- Discord 拿不到 %d 個頻道%s" % (len(away), "：" + "、".join(mention(i) for i in ids[:5]) if ids else ""))
    late = read_json(os.path.join(state, "turn-ends-late.json"))
    count = number(late.get("count")) if isinstance(late, dict) else None
    if count and count > 0:
        lines.append("- 有 %d 次回覆等到上限才收尾（從 %s 算起）" % (count, clock(late.get("since"), now)))
    if len(lines) > ITEMS_MAX:
        lines = lines[:ITEMS_MAX] + ["另有 %d 項沒有列出" % (len(lines) - ITEMS_MAX)]
    if note:
        lines.append(note)
    elif not lines:
        return "目前沒有"
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------------ 在等你的事
def waiting_board(state, now):
    folder, rows = os.path.join(state, "asks"), []
    try:
        names = sorted(n for n in os.listdir(folder) if n.endswith(".json") and not n.startswith("."))
    except OSError:
        names = []
    for name in names:
        ask = read_json(os.path.join(folder, name))
        if not isinstance(ask, dict) or not ask.get("channel"):
            continue
        if "owed" in ask:  # closed already; only its result is still owed to another machine (the bot's ask.load()
            continue       # takes it for not open too): nothing the owner can answer
        if ask.get("kind") == "plan":
            what = "計畫等你核准"
        else:
            asked, answered = ask.get("questions"), ask.get("answers")
            left = (len(asked) if isinstance(asked, list) else 0) - (len(answered) if isinstance(answered, dict) else 0)
            what = "提問還有 %d 題沒回答" % left if left > 0 else "提問還沒回答完"
        since = moment(ask.get("ts"))
        rows.append((since or 0, name, "- %s · %s · 從 %s 等到現在" % (mention(ask["channel"]), what, clock(since, now))))
    return "\n".join(r[2] for r in sorted(rows)) or "目前沒有"


# ------------------------------------------------------------------------------------------------ the round
def boards(now=None, here=None):
    """[(name, text)] of the four boards, in posting order."""
    now = time.time() if now is None else now
    state = bot_state()
    channels = read_json(os.path.join(state, "channels.json"))
    kept = len(conversations(channels, here)) if isinstance(channels, dict) else None
    records, _, note = doctor_records(records_path(), now)
    return [(ORDER[0], channels_board(channels, here, now)),
            (ORDER[1], machines_board(records, note, state, kept, now)),
            (ORDER[2], attention_board(records, note, state, now)),
            (ORDER[3], waiting_board(state, now))]


def settings():
    """(channel, this machine's name) from the instance, or (None, why)."""
    try:
        engine_on_path()
        from altention import config
        channel, here = config.setting("status.channel"), config.this_machine()
    except Exception as e:
        return None, "實例設定讀不出來（%s: %s）" % (type(e).__name__, e)
    if isinstance(channel, bool) or not isinstance(channel, (str, int)) or not str(channel).strip():
        return None, "實例設定沒有 status.channel（看板頻道的 ID）"
    return str(channel).strip(), here if isinstance(here, str) else None


def post(channel, name, text):
    """One board through altention dc post; True when it took it."""
    try:
        r = subprocess.run([os.path.join(ENGINE, "bin", "altention"), "dc", "post", channel, "--board", name, text],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        print("status：「%s」貼不出去（%s）" % (name, e))
        return False
    if r.returncode != 0:
        print("status：「%s」貼不出去（altention dc post 結束碼 %d：%s）" % (name, r.returncode, one_line(r.stdout, 200)))
    return r.returncode == 0


def write_posted(posted):
    path = posted_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(posted, f, ensure_ascii=False)
        os.replace(path + ".tmp", path)
    except OSError as e:
        print("status：記不下這次貼了什麼（%s）；下一輪會再貼一次" % e)


def read_posted(channel):
    """{board: {"text", "at", "seen"}}: what was last handed over for `channel`, when, and when the channel was
    last seen to say it. Anything else in the file (another channel, another shape) counts as nothing handed over."""
    was = read_json(posted_path())
    if not isinstance(was, dict) or was.get("channel") != channel or not isinstance(was.get("boards"), dict):
        return {}
    out = {}
    for name, rec in was["boards"].items():
        if isinstance(rec, dict) and isinstance(rec.get("text"), str) and moment(rec.get("at")) is not None:
            out[name] = {"text": rec["text"], "at": moment(rec["at"]), "seen": moment(rec.get("seen")) or 0}
    return out


def bot_alive(state, now):
    """The bot's heartbeat is fresh: what is handed over now is taken up now."""
    try:
        with open(os.path.join(state, "heartbeat"), encoding="utf-8") as f:
            beat = moment(float(f.read().strip()))
    except (OSError, ValueError):
        return False
    return beat is not None and abs(now - beat) <= BEAT_LATE


def waiting_in_spool(state, channel):
    """The boards of `channel` that still have a version in the bot's spool (handed over, not taken up yet)."""
    folder, names = os.path.join(state, "posts"), set()
    try:
        files = [n for n in os.listdir(folder) if n.endswith(".json") and not n.startswith(".")]
    except OSError:
        return names
    for n in files:
        post = read_json(os.path.join(folder, n))
        if isinstance(post, dict) and isinstance(post.get("board"), str) and str(post.get("channel")).lstrip("#") == channel:
            names.add(post["board"])
    return names


def made(state, channel):
    """{board: id of its first message} for the boards the bot has made in `channel`, from the bot's own state; None
    when that cannot be read (then what was handed over before counts as made)."""
    channels = read_json(os.path.join(state, "channels.json"))
    st = channels.get(channel) if isinstance(channels, dict) else None
    if not isinstance(st, dict):
        return None
    kept = st.get("boards")
    out = {}
    for name, ids in (kept.items() if isinstance(kept, dict) else ()):
        if isinstance(ids, list) and ids and str(ids[0]).isdigit():
            out[name] = str(ids[0])
    return out


SHOWN = re.compile(r"^\[\d\d-\d\d \d\d:\d\d:\d\d\] .* \(id (\d+)\)$")


def read_back(channel):
    """{message id: its text} of the channel's last messages as Discord has them (altention dc read, which only reads), or
    None when that did not work."""
    try:
        r = subprocess.run([os.path.join(ENGINE, "bin", "altention"), "dc", "read", channel, str(READ_BACK)], stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, universal_newlines=True, timeout=45)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if r.returncode != 0:
        return None
    shown, current = {}, None
    for line in (r.stdout or "").split("\n"):
        head = SHOWN.match(line)
        if head:
            current = head.group(1)
            shown[current] = []
        elif current is not None:
            shown[current].append(line)
    return {mid: "\n".join(lines) for mid, lines in shown.items()} if shown else None


def says(shown, text):
    """Does a board's message as read back (title, "更新於", then the text) begin with `text`'s beginning?"""
    lines = shown.split("\n")
    if len(lines) < 2 or not lines[0].startswith("📌") or "更新於" not in lines[1]:
        return False
    return "\n".join(lines[2:]).strip().startswith(text.strip()[:HEAD].rstrip())


def round_(channel, here, now):
    """One round: hand over the boards that need it. Returns how many were handed over."""
    state = bot_state()
    if not bot_alive(state, now):
        return 0  # nothing piles up while it is away; the first round after it is back hands over the latest
    posted = read_posted(channel)
    before = json.dumps(posted, sort_keys=True)
    queued, there = waiting_in_spool(state, channel), made(state, channel)
    reading = time.localtime(now).tm_min % READ_BACK_EVERY == 0
    shown, looked, sent = {}, False, 0
    for name, text in boards(now=now, here=here):
        rec = posted.get(name)
        exists = name in there if there is not None else rec is not None
        if name in queued:
            if not exists:
                break  # being made: the ones after it wait for it
            continue  # an earlier version not taken up yet: no second one behind it
        if not exists:  # made one at a time, in order
            if rec is None or now - rec["at"] > MAKING:
                if post(channel, name, text):
                    posted[name] = {"text": text, "at": now, "seen": 0}
                    sent += 1
            break
        need = rec is None or rec["text"] != text
        if not need and reading and there is not None and now - rec["at"] > SETTLE:
            if not looked:  # one call a round, whatever comes of it
                shown, looked = read_back(channel) or {}, True
            if shown:
                if says(shown.get(there[name], ""), text):
                    rec["seen"] = now
                else:
                    need = True  # the channel says something else (or the message is gone): once more
        if not need and now - max(rec["at"], rec["seen"]) >= FALLBACK.get(name, FALLBACK_OTHER):
            need = True  # nobody could look for this long: once more, to be sure
        if need and post(channel, name, text):
            posted[name] = {"text": text, "at": now, "seen": 0}
            sent += 1
    if json.dumps(posted, sort_keys=True) != before:
        write_posted({"channel": channel, "boards": posted})
    return sent


def main(argv):
    show = "--print" in argv
    if [a for a in argv if a != "--print"]:
        print("status：只認得 --print")
        return 2
    channel, here = settings()
    if show:
        for name, text in boards(here=here if channel else None):
            print("📌 %s\n%s\n" % (name, text))
        return 0
    if channel is None:
        print("status：%s，這一輪跳過" % here)
        return 0
    round_(channel, here, time.time())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
