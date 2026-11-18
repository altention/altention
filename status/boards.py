#!/usr/bin/env python3
"""Status boards in the dashboard channel (every 2 minutes from cron on the bot's machine).

  python3 status/boards.py            build the four boards, post the ones whose text changed
  python3 status/boards.py --print    build them, print them instead (nothing is posted or written)

Four boards, posted in this order the first time (the last one ends up lowest in the channel):
  頻道            every conversation channel the bot keeps: busy or idle, how full its context is, when it was last
                  active, which machine its conversation is on; the most recent TOP, then a line with the total
  機器和機器人    each machine: reachable, engine, last sync; the bot: which machine, since when it is up, how many
                  channels it keeps; with a standby (C-163): whether it is ready, or that it is the bot now
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
- with a standby (C-181) this runs on the standby machines too ("standby": true in plugin.json), and a round hands
  over only where the bot is right now, as this machine's own files say (never asking another machine): the machine
  list with bot-host naming this machine, the heartbeat under BEAT_FRESH old, and the bot's last good read of the lease (state/lease-view.json) under VIEW_FRESH
  old, finding this machine the bot and no other machine holding the place too. Anything else, a file missing or
  unreadable among it, hands nothing over: two machines that both think they are the bot both stay quiet, never
  post twice. Not handing over for a reason other than "the bot is not here" is said in #audit once it has lasted
  SAY_AFTER, and once more when it is over (never every round). A bot that found no lease channel runs as without
  a standby and says so in its lease-view: then the heartbeat alone, said in #audit once. On a standby that is the
  bot, doctor's records are its own (core's watch runs where the bot is, C-185); until its first round since it took
  over is done they are replaced by a line saying so;
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
BEAT_FRESH = 60  # with a standby: the heartbeat (written every 20 s while the bot is the bot) this fresh to post
VIEW_FRESH = 90  # with a standby: the bot's last good lease read (state/lease-view.json) this fresh to post
ALIVE = 150  # a machine's lease message edited this recently: it is up (dcbot/bot/lease.py ALIVE_SECS)
SAY_AFTER = 5 * 60  # not posting for a reason other than "the bot is not here" this long: said in #audit
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


def gate_path():
    """Why the last round did not hand anything over (with a standby), and whether #audit was told."""
    return os.path.join(state_dir(), "status-gate.json")


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
def lease_setup():
    """(standby machines, the primary: the list's dcbot machine) from the machine list; (set(), None) without a
    standby or when the list cannot be read."""
    try:
        engine_on_path()
        from altention import config, plugin
        standbys = set(plugin.standby_machines())
        primary = next((n for n, m in (config.inventory().get("machines") or {}).items()
                        if "dcbot" in ((m or {}).get("roles") or [])), None) if standbys else None
    except Exception:
        return set(), None
    return standbys, primary


def bot_here(here):
    """The machine list (with bot-host: config.bot_machine()) says the bot runs on `here`. False when it cannot tell.
    A failback tells every machine before the standby steps down, so this closes that minute (#code-review on
    68d35325); a takeover counts once the new bot has told this machine (announce_host())."""
    try:
        engine_on_path()
        from altention import config
        return bool(here) and config.bot_machine()[0] == here
    except Exception:
        return False


def lease_view(state, here):
    """state/lease-view.json (the bot's last good read of the lease, dcbot/bot/lease.py view()) as {"at", "holder",
    "leaders", "machines"}, or None when it is missing, unreadable, of another shape, or another machine's."""
    got = read_json(os.path.join(state, "lease-view.json"))
    if not isinstance(got, dict) or moment(got.get("at")) is None or got.get("machine") != here or not here:
        return None
    holder, leaders, machines = got.get("holder"), got.get("leaders"), got.get("machines")
    if not (holder is None or isinstance(holder, str)) or not isinstance(leaders, list) or \
            not all(isinstance(n, str) for n in leaders) or not isinstance(machines, dict):
        return None
    lost = got.get("no_lease")  # (the bot found no lease channel and runs as without a standby: its name)
    return {"at": moment(got["at"]), "holder": holder, "leaders": leaders,
            "machines": {n: e for n, e in machines.items() if isinstance(e, dict)},
            "no_lease": lost.strip().lstrip("#") if isinstance(lost, str) and lost.strip() else None}


GAP_DOWN = "%s 沒有回應；系統檢查改由 %s 跑，接手後的第一輪還沒跑完（每 15 分鐘一次）"
GAP_BACK = "%s 已經回來，還沒切回；系統檢查改由 %s 跑，接手後的第一輪還沒跑完（每 15 分鐘一次）"


def standby_gap(state, here, lease, now):
    """On a standby machine that is the bot now (by its bot's lease read): the line said in place of doctor's records
    while none of its own are there since it took over (C-185: its watch runs from then on, every 15 minutes); None
    elsewhere. Whether there are is boards()'s to say."""
    standbys, primary = lease
    view = lease_view(state, here) if here and here in standbys and primary and primary != here else None
    if view is None or view["holder"] != here:
        return None
    name = harmless(one_line(primary, 40))
    return (GAP_BACK if last_seen(view, primary, now)[0] else GAP_DOWN) % (name, harmless(one_line(here, 40)))


def last_seen(view, name, now):
    """(up, last seen) of machine `name` by the bot's lease read `view`: up when its message was edited within ALIVE
    and it is not marked down; last seen a time on this machine's clock (None: not known)."""
    e = (view or {}).get("machines", {}).get(name)
    age = number(e.get("age")) if isinstance(e, dict) else None
    if age is None:
        return False, None
    seen = view["at"] - max(age, 0)
    return now - seen <= ALIVE and not e.get("down"), seen


def took_over_at(state):
    """When this machine last took the bot over (the newest of state/lease-takeovers.json), or None."""
    got = read_json(os.path.join(state, "lease-takeovers.json"))
    times = [moment(t) for t in got] if isinstance(got, list) else []
    times = [t for t in times if t is not None]
    return max(times) if times else None


def bot_line(state, kept, now, here=None, lease=(set(), None)):
    """The bot: which machine, since when it is up (the time its start-up self test passed), how many channels it
    keeps; with a standby (C-181), on a standby machine: since when it is the bot and whether the primary is back."""
    standbys, primary = lease
    name = harmless(one_line(here, 40)) if here else None
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
    view = lease_view(state, here) if name and here in standbys and primary and primary != here else None
    if view is not None and view["holder"] == here:  # (a standby waiting: the plain line, its bot not running)
        up, _ = last_seen(view, primary, now)
        line = "機器人：%s（備援，%s 接手，%s %s）" % (name, clock(took_over_at(state), now), harmless(one_line(primary, 40)),
                                             "已經回來，還沒切回" if up else "沒有回應")
    elif since is None:
        line = "機器人：%s沒有啟動紀錄（還沒通過啟動後的自我檢查，或沒在跑）" % (name + " " if name else "")
    else:
        inner = "%s 起來" % clock(since, now) + ("，記著 %d 個對話頻道" % kept if kept is not None else "")
        line = "機器人：%s（%s）" % (name, inner) if name else "機器人：" + inner
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


def standby_lines(state, now, here, lease):
    """With a standby (C-181), on the primary: each standby machine ready or not answering, by the bot's lease read."""
    standbys, _ = lease
    if not here or here in standbys:
        return []
    view, out = lease_view(state, here), []
    for s in sorted(standbys):
        up, seen = last_seen(view, s, now)
        out.append("備援：%s 待命中" % harmless(one_line(s, 40)) if up else "備援：%s **沒有回應**（%s）" % (
            harmless(one_line(s, 40)), "最後回應 %s" % clock(seen, now) if seen is not None else "查不到最後回應的時間"))
    return out


def machines_board(records, note, state, kept, now, here=None, lease=(set(), None)):
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
    lines.append(bot_line(state, kept, now, here, lease))
    lines += standby_lines(state, now, here, lease)
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
def setup_left():
    """The instance's setup.done is false: #setup has questions left (C-48); False when that cannot be read."""
    try:
        engine_on_path()
        from altention import config
        return config.setting("setup.done", True) is False
    except Exception:
        return False


HELD = {"usage_limit": "用量上限", "login": "登入失效", "api_error": "API 錯誤"}  # (C-101; a kind not here: as written)


def blocked_rows(channels, now):
    """(rows for 在等你的事, rows for 卡住的頻道) from the channels' `blocked` (dcbot/bot/blocked.py): a dialog waits
    for the owner and goes with the questions; the rest hold a channel up. Each row (since, key, text)."""
    waiting, held = [], []
    for cid, st in (channels.items() if isinstance(channels, dict) else ()):
        b = st.get("blocked") if isinstance(st, dict) else None
        if not isinstance(b, dict) or not isinstance(b.get("kind"), str):
            continue
        kind, since = b["kind"], moment(b.get("since"))
        machine = one_line(str(b.get("machine") or ""), 30)
        if kind == "dialog":
            waiting.append((since or 0, cid, "- %s · 終端機有對話框等你按 · 從 %s 起" % (mention(cid), clock(since, now))))
            continue
        if kind == "usage_limit" and moment(b.get("until")):
            what = "用量上限，<t:%d:t> 自己接著做" % int(b["until"])
        elif kind == "login":
            what = "%s登入失效，等你登入" % (machine + " " if machine else "")
        elif kind == "api_error":
            what = "%s，等你按再試一次" % harmless(one_line(str(b.get("detail") or "API 錯誤"), 60))  # (the bot's words for it)
        else:
            what = HELD.get(kind) or harmless(one_line(kind, 30))
        if machine and kind != "login":
            what += "（%s）" % machine
        held.append((since or 0, cid, "- %s · %s · 從 %s 起" % (mention(cid), what, clock(since, now))))
    return waiting, held


def paused_rows(channels, now):
    """Rows for 暫停中的頻道 from the channels' `paused` (C-184, dcbot/bot/pauses.py), each (until, key, text): until
    when, who paused it and why, the posts kept for it; one past its time (the bot was down) says it is owed."""
    rows = []
    for cid, st in (channels.items() if isinstance(channels, dict) else ()):
        p = st.get("paused") if isinstance(st, dict) else None
        until = moment(p.get("until")) if isinstance(p, dict) else None
        if not until or p.get("resumed"):
            continue
        when = "<t:%d:%s>" % (int(until), "t" if until - now < 86400 else "f")
        if until <= now:
            rows.append((until, cid, "- %s · 應該在 %s 恢復，機器人上線後會補叫" % (mention(cid), when)))
            continue
        held = p.get("held") if isinstance(p.get("held"), list) else []
        who = harmless(one_line("%s：%s" % (p.get("by") or "?", p.get("why") or ""), 80))
        rows.append((until, cid, "- %s · 到 %s 恢復 · %s%s" % (mention(cid), when, who, " · 留著 %d 則" % len(held) if held else "")))
    return rows


def waiting_board(state, now, setup=False, channels=None):
    folder, rows = os.path.join(state, "asks"), []
    if setup:  # first: nothing else runs as chosen until it is done
        rows.append((-1, "", "- 設定還沒做完：到 #setup 接著回答（在那裡打任何一句，Claude 會從還沒答的題目接著問）"))
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
    dialogs, held = blocked_rows(channels, now)
    text = "\n".join(r[2] for r in sorted(rows + dialogs)) or "目前沒有"
    if held:
        text += "\n\n**卡住的頻道**\n" + "\n".join(r[2] for r in sorted(held))
    paused = paused_rows(channels, now)
    if paused:
        text += "\n\n**暫停中的頻道**\n" + "\n".join(r[2] for r in sorted(paused))
    return text


# ------------------------------------------------------------------------------------------------ the round
def boards(now=None, here=None):
    """[(name, text)] of the four boards, in posting order."""
    now = time.time() if now is None else now
    state = bot_state()
    channels = read_json(os.path.join(state, "channels.json"))
    kept = len(conversations(channels, here)) if isinstance(channels, dict) else None
    records, at, note = doctor_records(records_path(), now)
    lease = lease_setup()
    gap = standby_gap(state, here, lease, now)
    took = took_over_at(state) if gap else None
    if gap and (note or at is None or (took is not None and at < took)):  # (a standby that is the bot: records of a
        records, note = [], gap  # round since it took over, else none: old ones are never shown as if nothing were wrong)
    return [(ORDER[0], channels_board(channels, here, now)),
            (ORDER[1], machines_board(records, note, state, kept, now, here, lease)),
            (ORDER[2], attention_board(records, note, state, now)),
            (ORDER[3], waiting_board(state, now, setup_left(), channels))]


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


VIEW_SAY = "看板暫停：還不確定這台是不是正在當機器人（找不到機器人最近留下的紀錄 dcbot/state/lease-view.json），確定前先不貼"
TWO_SAY = "看板暫停：%s 也認為自己是機器人，為了不重複，兩台都先不貼"
BACK_SAY = "看板恢復，照常貼了"
NO_LEASE_SAY = "設了備援，但找不到 #%s（兩台用來確認誰在當機器人的頻道）：看板先照沒有備援的方式貼，只看這台的機器人在不在。要補回這個頻道，在機器人那台跑 %s（跑完會重啟機器人一次；原本設成只通知的，auto 換成 notify）"


def heartbeat(state):
    try:
        with open(os.path.join(state, "heartbeat"), encoding="utf-8") as f:
            return moment(float(f.read().strip()))
    except (OSError, ValueError):
        return None


def may_post(state, here, now, lease):
    """(hand over now?, what to say in #audit: None when nothing is wrong, or the bot is plainly not here; while
    handing over, a note said once). Without a standby: the bot's heartbeat fresh (bot_alive()). With one (C-181):
    see the module's note; a bot that found no lease channel (lease-view's no_lease) goes by its heartbeat."""
    standbys, _ = lease
    if not standbys:
        return bot_alive(state, now), None
    beat = heartbeat(state)
    if beat is None or abs(now - beat) > BEAT_FRESH or not bot_here(here):
        return False, None  # the bot is not the bot here (a standby waiting, a primary whose bot stepped down, a
        # standby handing back after a failback told every machine)
    view = lease_view(state, here)
    if view is not None and view["no_lease"]:  # the bot runs as without a standby (no lease channel): as then
        return True, NO_LEASE_SAY % (harmless(one_line(view["no_lease"], 40)), "、".join(
            "altention standby setup %s auto" % harmless(one_line(s, 40)) for s in sorted(standbys)))
    if view is None or abs(now - view["at"]) > VIEW_FRESH:
        return False, VIEW_SAY
    if view["holder"] != here:
        return False, None  # another machine is the bot: its own boards post (this bot is stepping down)
    others = [n for n in view["leaders"] if n != here]
    if others:
        return False, TWO_SAY % "、".join(harmless(one_line(n, 40)) for n in others)
    return True, None


def say_audit(line):
    """One line in #audit (@audit: the bot's own, by the ID it keeps, C-201 6.2) as a notice of this machine's; not
    said is not a failure."""
    try:
        subprocess.run([os.path.join(ENGINE, "bin", "altention"), "dc", "notify", "-c", "@audit", line],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
    except Exception as e:
        print("status：#audit 說不出去（%s）" % e)


def told(posting, why, now):
    """#audit only when the state changes (C-181): `why` (may_post()) said once it has lasted SAY_AFTER, and
    BACK_SAY once posting again after it was said. Kept in gate_path()."""
    was = read_json(gate_path())
    was = was if isinstance(was, dict) else {}
    if posting and why is not None:  # posting all the same, with a note (no lease channel): said once, at once
        if was.get("why") == why:
            return
        say_audit(why)
        new = {"why": why, "since": now, "said": True, "note": True}
    elif why is None:
        if not was:
            return
        if posting and was.get("said") is True and was.get("note") is not True:
            say_audit(BACK_SAY)
        new = {}
    elif was.get("why") != why or moment(was.get("since")) is None:
        new = {"why": why, "since": now, "said": False}
    elif was.get("said") is not True and now - moment(was["since"]) >= SAY_AFTER:
        say_audit(why)
        new = dict(was, said=True)
    else:
        return
    path = gate_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(new, f, ensure_ascii=False)
        os.replace(path + ".tmp", path)
    except OSError as e:
        print("status：記不下這輪為什麼沒貼（%s）" % e)


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


def says(shown, text, entry=None):
    """Does a board's message as read back (title, "更新於", then the text) begin with `text`'s beginning? `entry`:
    the bot's ledger entry of that message (C-172a P1a, ledger_entry()): a board's part with its heading on record is
    told by that, not by a first line of 📌 (so the boards' looks may change later); without one, as before."""
    lines = shown.split("\n")
    if isinstance(entry, dict) and entry.get("intent") == "board":
        head = "board_head" in (entry.get("framed") or ())
        body = lines[2:] if head and len(lines) >= 2 else lines
        return "\n".join(body).strip().startswith(text.strip()[:HEAD].rstrip())
    if len(lines) < 2 or not lines[0].startswith("📌") or "更新於" not in lines[1]:
        return False
    return "\n".join(lines[2:]).strip().startswith(text.strip()[:HEAD].rstrip())


def ledger_entry(state, message_id):
    """The bot's ledger entry of a message (dcbot/state/ledger.json, dcbot/bot/ledger.py; read only), or None."""
    got = read_json(os.path.join(state, "ledger.json"))
    for rows in ((got or {}).get("channels") or {}).values() if isinstance(got, dict) else ():
        for row in rows if isinstance(rows, list) else ():
            if isinstance(row, list) and len(row) == 2 and str(row[0]) == str(message_id) and isinstance(row[1], dict):
                return row[1]
    return None


def round_(channel, here, now):
    """One round: hand over the boards that need it. Returns how many were handed over."""
    state = bot_state()
    posting, why = may_post(state, here, now, lease_setup())
    told(posting, why, now)
    if not posting:
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
                if says(shown.get(there[name], ""), text, ledger_entry(state, there[name])):
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
    if "-h" in argv or "--help" in argv:  # spec §11.1 (C-150): says how, does nothing
        print(__doc__)
        return 0
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
