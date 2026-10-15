"""What a channel is, said in one place (stage 3b step 2.5, backlog A-03).

A channel's state says it in two fields: `kind` (how the channel is used) and `connector` (which tool's
conversation is behind it: the name of a connector's package, only for a channel with one). `kind` has been there
since the terminal and files channels ("terminal", "files"); this step adds the names for the two that had none,
"conversation" and "record", and the one function that answers. Its values are open: a plugin, or a later version,
may write another, and code that does not know a value says so (UNKNOWN) and does not fail.

Being archived is a state, not a kind: an archived channel is asked about first, and says why it is so
(archived_why).

Until the flags the bot has always gone by are taken out (`plain`, and the keys of a conversation: tmux, pane, home,
session_id; not in stage 3b: a rollback puts the code back and not the state), they alone are what "conversation"
and "record" rest on: stamp() writes the two fields from them whenever the state is saved (Bot.save(), the one
way that file is written), so that whoever reads the file finds them; and channel_kind() asks the flags, not what
was written: a state whose flags were changed and that is not saved yet answers by what it is now, and one with
neither a conversation's keys nor `plain` is UNSET whatever an earlier save left in `kind`. "terminal" and
"files" have no flag: there `kind` alone says it. Standard library only (tools/dcctl reads it under the system Python)."""
CONVERSATION, TERMINAL, FILES, RECORD = "conversation", "terminal", "files", "record"
KINDS = (CONVERSATION, TERMINAL, FILES, RECORD)
ARCHIVED = "archived"   # a state: put away, with what it takes to take the conversation back
UNSET = "unset"         # nothing says what the channel is for yet (never "record": that is chosen, not a leftover)
UNKNOWN = "unknown"     # a `kind` this version does not know
CLAUDE_CODE = "claude_code"   # the connector of every conversation there is so far

DETACHED, MOVED = "detached", "channel_moved"   # why a channel is archived; open as well


def has_conversation(st):
    """Is there a conversation behind the channel (one running, or one to take up again)?"""
    return bool(st.get("tmux") or st.get("pane") or st.get("home") or st.get("session_id"))


def told_by_flags(st):
    """The kind the old flags tell, or None when they tell none."""
    if st.get("plain"):
        return RECORD
    if has_conversation(st):
        return CONVERSATION
    return None


def channel_kind(st):
    """ARCHIVED, one of KINDS, UNSET or UNKNOWN for a channel's state (None or empty: UNSET)."""
    st = st or {}
    if st.get("archived"):
        return ARCHIVED
    kind = st.get("kind")
    if kind is None:
        return told_by_flags(st) or UNSET
    if kind in (TERMINAL, FILES):
        return kind
    if kind in (CONVERSATION, RECORD):
        return told_by_flags(st) or UNSET
    return UNKNOWN


def connector_of(st):
    """The connector of the conversation behind a channel, or None for a channel without one."""
    st = st or {}
    return (st.get("connector") or CLAUDE_CODE) if channel_kind(st) == CONVERSATION else None


def archived_why(st):
    """Why an archived channel is so (DETACHED, MOVED, or whatever was written), or None: not archived, or archived
    before this was written down."""
    rec = (st or {}).get("archived")
    return rec.get("why") if isinstance(rec, dict) else None


def stamp(st):
    """Write `kind` and `connector` as the flags tell them, in a state about to be saved. A kind of its own
    (terminal, files, one not known here) and an archived channel's record are left as they are."""
    if not isinstance(st, dict) or st.get("archived") or st.get("kind") not in (None, CONVERSATION, RECORD):
        return st
    told = told_by_flags(st)
    if told is None:
        st.pop("kind", None)
        st.pop("connector", None)
    else:
        st["kind"] = told
        if told == CONVERSATION:
            st.setdefault("connector", CLAUDE_CODE)
        else:
            st.pop("connector", None)
    return st
