"""Where a channel's conversation is, one record per channel (C-201 first batch, design instance
docs/refactor/c201-design.md section 2; manager 11-17): the channel's ID is who it is, everything else is where it
is now.

  where: {
    agent:  {kind, conversation, transcript},             # the conversation behind it (its id, its file there)
    place:  {machine, socket, session, window, pane, pid}, # where it runs now; pane and pid only a cache
    folder: <working folder>,
    names:  {channel, session, window},                    # the names it goes by in each place
    follow: [channel, conversation, window(, session)],    # what a rename takes along (owner 11-17, point 1)
    owned:  bot | user,                                    # the bot opened its session or window, or the owner's pane
    seen:   {at, state, known, machine, socket, session, window, pane, pid},  # the last look's (bot/reconcile.py)
  }

This first batch keeps the fields the bot has always gone by (pane, pane_session, home, tmux, session_id, cwd) as the
ones that work: stamp() writes `where` from them whenever the state is saved (Bot.save(), the one way the file is
written; as kinds.stamp()), so it is in channels.json (and on the standby) for /where, doctor and the look. The next
batch makes it the one source and the others made from it (manager 11-17). What the look saw of the same pane (its
session and window now: renamed or moved in tmux) wins over the old fields' copies. Standard library only."""
import os
import re

FOLLOW = ("channel", "conversation", "window")  # owner 11-17: these follow a rename; the session only when it is the
# bot's own and holds this channel alone (a channel on the bot's server: its session is its own)


def transcript(folder, conversation, home="~"):
    """The conversation's file as Claude Code keeps it (the connector's sessions.project_dir(): every character of the
    folder but ASCII letters and digits a -), on the machine it runs on; not looked for (another machine's is not
    here)."""
    if not folder or not conversation:
        return None
    return os.path.join(home, ".claude", "projects", re.sub(r"[^A-Za-z0-9]", "-", folder), "%s.jsonl" % conversation)


def place_of(st, machine, bot_socket):
    """Where the channel's Claude runs, from the old fields: an attached pane (here or another machine's), a window the
    bot opened in a tmux (the setup card), or a session on the bot's own server."""
    pane = st.get("pane") if isinstance(st.get("pane"), dict) else {}
    home = st.get("home") if isinstance(st.get("home"), dict) else {}
    if pane:
        return {"machine": pane.get("host") or home.get("host") or machine, "socket": pane.get("socket"),
                "session": st.get("pane_session") or home.get("session"), "window": home.get("window") or None,
                "pane": pane.get("id"), "pid": pane.get("pid")}
    if home:
        return {"machine": home.get("host") or machine, "socket": home.get("socket"), "session": home.get("session"),
                "window": home.get("window") or None, "pane": None, "pid": None}
    if st.get("tmux"):
        return {"machine": machine, "socket": bot_socket, "session": st["tmux"], "window": None, "pane": None,
                "pid": None}
    return None


def record(st, name, machine, bot_socket):
    """The `where` of a channel's state, or None for a channel with no conversation behind it (a record channel, a
    terminal or files channel, an archived one: its record is in `archived`)."""
    if not isinstance(st, dict) or st.get("archived") or st.get("plain") or st.get("kind") in ("terminal", "files"):
        return None
    place = place_of(st, machine, bot_socket)
    if place is None and not st.get("session_id"):
        return None
    place = place or {"machine": machine, "socket": None, "session": None, "window": None, "pane": None, "pid": None}
    old = st.get("where") if isinstance(st.get("where"), dict) else {}
    seen = old.get("seen") if isinstance(old.get("seen"), dict) else None
    if seen and seen.get("state") == "here" and seen.get("pane") and seen.get("pane") == place.get("pane") \
            and seen.get("machine") == place.get("machine"):
        place = dict(place, session=seen.get("session") or place["session"], window=seen.get("window") or place["window"])
    owned = "user" if st.get("pane") and not st.get("home") and not st.get("tmux") else "bot"
    folder = st.get("cwd")
    pane = st.get("pane") if isinstance(st.get("pane"), dict) else {}
    kind = pane.get("agent") if isinstance(pane.get("agent"), str) and pane.get("agent") else "claude"  # (an adapter's
    # name, as its mark gave it: tmuxio.agent_now(); the bot's own windows run Claude Code)
    out = {"agent": {"kind": kind, "conversation": st.get("session_id"),
                     "transcript": transcript(folder, st.get("session_id"))},
           "place": place, "folder": folder,
           "names": {"channel": name, "session": place.get("session"), "window": place.get("window")},
           "follow": list(FOLLOW) + (["session"] if st.get("tmux") and not st.get("pane") else []),
           "owned": owned}
    if seen:
        out["seen"] = seen
    return out


PLACE_FIELDS = ("pane", "pane_session", "home", "tmux")  # the old fields that say where a channel's Claude runs
context = None  # () -> (this machine's name, the bot's own tmux server's socket): set by the bot (Bot.where_context)


def put(st, name=None, remake=True, ctx=None, **fields):
    """The one way a channel's place is written (C-201 2a; a test holds that nothing else in bot/ writes these fields):
    `fields` among PLACE_FIELDS, None taking one away. The old fields are written as given, for every reader of them
    and for an older bot after a rollback (kept till after v0.9.0: manager 11-17), and the channel's `where` is made
    from them at once (with no context set, as in a test with no bot: at the next save). `name`: the channel's name
    (else the one its record had); `remake=False`: for the state of another machine's bot (the standby's copy for the
    primary): the record's place taken away, made again by its next save there (no place: not said to be written past
    put()), what was seen kept; `ctx`: (machine, bot socket) in place of the
    context's. Its `where`, or None."""
    for k, v in fields.items():
        if k not in PLACE_FIELDS:
            raise KeyError(k)
        if v is None:
            st.pop(k, None)
        else:
            st[k] = v
    ctx = (ctx or (context() if callable(context) else None)) if remake else None
    if not remake and isinstance(st.get("where"), dict):  # (its place made again there, from these fields; what was
        st["where"] = {k: v for k, v in st["where"].items() if k != "place"}  # seen kept: #code-review on 1a4cad50)
    elif ctx:
        old = st.get("where") if isinstance(st.get("where"), dict) else {}
        rec = record(st, name or (old.get("names") or {}).get("channel"), *ctx)
        if rec is None:
            st.pop("where", None)
        else:
            st["where"] = rec
    return st.get("where")


def stamp(channels_state, name_of, machine, bot_socket, say=None):
    """Every channel's `where` made again from its fields (Bot.save()); a channel that has none any more loses it.
    A place that differs from the one put() wrote was written past it: said (`say`, the bot's log), and the fields
    win (what the bot works by)."""
    for cid, st in channels_state.items():
        if not isinstance(st, dict):
            continue
        rec = record(st, name_of(cid), machine, bot_socket)
        old = st.get("where") if isinstance(st.get("where"), dict) else None
        if say is not None and old is not None and rec is not None and "place" in old and old["place"] != rec.get("place"):
            say("[where] %s: its fields say %s, its record said %s (written past where.put())" % (
                cid, rec.get("place"), old.get("place")))
        if rec is None:
            st.pop("where", None)
        else:
            st["where"] = rec


def card(rec, channel, here, bot_socket, title=None, clock=None, say=None):
    """/where's card (its 〔改名…〕 button: bot/rename.py, 2b; the move ones come with the next batches). `here`: this
    machine's name (the folder said with ~ only there); `title`: the conversation's name when it could be read
    (only on this machine); `clock(ts)`: a time as the owner reads it; `say(code, **data)`: the words
    (bot/messages.py where())."""
    if rec is None:
        return say("none", channel=channel)
    place, unknown = rec.get("place") or {}, say("unknown")
    server = say("server_bot" if place.get("socket") == bot_socket else "server_user")
    if place.get("window") or not place.get("pane"):
        line = say("machine", machine=place.get("machine") or unknown, server=server,
                   session=place.get("session") or unknown,
                   window=place.get("window") or ("0" if place.get("socket") == bot_socket else unknown))
    else:
        line = say("machine_pane", machine=place.get("machine") or unknown, server=server,
                   session=place.get("session") or unknown, pane=place.get("pane"))
    lines = [say("title", channel=channel), line]
    conversation, kind = (rec.get("agent") or {}).get("conversation"), (rec.get("agent") or {}).get("kind") or "claude"
    if kind != "claude":  # an adapter (pi, Codex): its own name; no conversation id is not one the next message opens
        lines.append(say("conversation_agent", agent=kind, id=conversation[:8]) if conversation
                     else say("no_conversation_agent", agent=kind))
    else:
        lines.append(say("conversation", id=conversation[:8], title="「%s」" % title if title else "") if conversation
                     else say("no_conversation"))
    folder = rec.get("folder") or unknown
    if place.get("machine") == here and folder.startswith(os.path.expanduser("~") + "/"):
        folder = "~" + folder[len(os.path.expanduser("~")):]
    lines.append(say("folder", folder=folder))
    follow = [n for n in rec.get("follow") or () if not (n == "conversation" and kind != "claude")]  # (an adapter's
    # conversation has no name to follow: rename says ➖ for it; #bot-dev-support on b641f556)
    lines.append(say("follow", names="、".join(say("follow_" + n) for n in follow)))
    seen = rec.get("seen") or {}
    if seen.get("state") in ("here", "gone", "unknown", "unsure", "respawned") and clock:
        lines.append(say("seen_" + seen["state"], at=clock(seen.get("at") or 0)))
    return "\n".join(lines)
