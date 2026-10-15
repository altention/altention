"""Claude Code's hooks: which ones report (one definition, for the sessions the bot starts through --settings and for
every other session on a machine through ~/.claude/settings.json), and what is added to an event where its
transcript can be read. From the bot's hookspec.py and from tools/dcremote's enrich() as they were (stage 3b step
1.5); what changed in enrich is said in its docstring."""
import re
import time

from . import transcript as cc_transcript

EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "Stop", "Notification", "SessionEnd",
          "SubagentStart", "SubagentStop", "StopFailure")
AFTER_TOOLS = "Agent|Task|Workflow"  # the only tool results the bot wants: what a subagent or workflow was started as
ASK_SECS = 12 * 3600                 # how long Claude may wait for the owner to answer a question
GUARD_SECS = 5  # the secrets guard (dchook --guard, K-03): before every tool call, and it is a file read at most


def hooks(command, timeout=None):
    """The "hooks" section of a settings file. `command` is the dchook command line (already quoted)."""
    def entry(cmd, secs):
        h = {"type": "command", "command": cmd}
        if secs:
            h["timeout"] = secs
        return h
    out = dict((ev, [{"hooks": [entry(command, timeout)]}]) for ev in EVENTS)
    out["PostToolUse"] = [{"matcher": AFTER_TOOLS, "hooks": [entry(command, timeout)]}]
    out["PreToolUse"].append({"matcher": "AskUserQuestion", "hooks": [entry(command + " --ask", ASK_SECS)]})
    out["PreToolUse"].append({"hooks": [entry(command + " --guard", GUARD_SECS)]})  # every tool: no agent reads a secret
    out["PermissionRequest"] = [{"matcher": "ExitPlanMode", "hooks": [entry(command + " --plan", ASK_SECS)]}]
    return out


NORMALIZED = ("dc_kind", "dc_why", "dc_from", "dc_peer", "dc_text", "dc_agent")  # what normalize() adds; the same rule as ADDED

# The kinds an event is of (dc_kind). Open: one who reads them leaves alone, and writes down, a kind it does not
# know. What each means is said in normalize().
READY, INPUT, PROGRESS, PEER_SENT, TURN_END, TURN_FAILED = "ready", "input", "progress", "peer_sent", "turn_end", "turn_failed"
SESSION_CHANGED, SESSION_LEAVING, SESSION_ENDED = "session_changed", "session_leaving", "session_ended"
COMPACTING, COMPACTED, COMPACT_DONE = "compacting", "compacted", "compact_done"
ASK, PLAN, DIALOG, IDLE = "ask", "plan", "dialog", "idle"
AGENT_START, AGENT_PROGRESS, AGENT_STOP, AGENT_LAUNCHED, WORKFLOW_START = \
    "agent_start", "agent_progress", "agent_stop", "agent_launched", "workflow_start"
UNKNOWN = "unknown"
EXITED, CLOSED = "exited", "closed"  # why a session ended (dc_why), as far as Claude Code says; `gone` (no event at
# all: the process was killed) is found by whoever looks whether it is alive, and what the bot did itself (a reset,
# an idle one closed) the bot knows without being told
TYPED, TASK, PEER, NOTICE, SYSTEM = "typed", "task", "peer", "notice", "system"  # who an input is from (dc_from)
_PEER_OPEN, _PEER_CLOSE = "<cross-session-message", "</cross-session-message>"
_NOTICE = re.compile(r"^\[[A-Z][\w -]+\]")  # "[Cross-session idle notice] …"


def peer_message(prompt):
    """(the sender's name, or "" when it gives none; the message) if the prompt is, whole, a message from another
    conversation as Claude Code hands it over (<cross-session-message from-name="…">…</cross-session-message>), else
    None. Read in one pass over the text: the pattern this was (and the bot's own, the same one) took the cube of
    the length for an opening tag followed by a long stretch of blanks and no closing tag (10 s for 4000 blanks),
    and every event of a machine waited meanwhile."""
    text = prompt.strip() if isinstance(prompt, str) else ""
    if not (text.startswith(_PEER_OPEN) and text.endswith(_PEER_CLOSE)):
        return None
    rest = text[len(_PEER_OPEN):]
    if rest[:1].isalnum() or rest[:1] == "_":  # another tag that only begins the same
        return None
    head = rest.find(">")
    end = len(rest) - len(_PEER_CLOSE)
    if head < 0 or head + 1 > end:  # no opening tag ended before the closing one
        return None
    return _attribute(rest[:head], "from-name"), rest[head + 1:end].strip()


def _attribute(attrs, name):
    """The value of name="…" among a tag's attributes, "" when it is not there (or its quote never closes)."""
    at = attrs.find(name + '="')
    if at < 0:
        return ""
    at += len(name) + 2
    stop = attrs.find('"', at)
    return attrs[at:stop] if stop >= 0 else ""


def normalize(ev, anew=False):
    """Say what kind of thing an event is, from the event alone: nothing is read, nothing remembered, so it can be
    done before anyone decides whether the event has to wait (stage 3b step 2.1). Adds `dc_kind`, and with some
    kinds `dc_why`, `dc_from`, `dc_peer`, `dc_text`; an event that has a kind already is left as it is. Never
    raises: what cannot be made out is UNKNOWN.

    `anew`: whatever kind the event came with, it is made out again. For the one who acts on the kind (the bot): the
    kind is worked out by the side that handles the event; what travels with it was worked out ahead by another
    program, perhaps of another version, and is for reference only.

    - READY: a Claude Code has started with a new conversation (SessionStart, source startup).
    - SESSION_CHANGED: the pane now shows the conversation the event names, `dc_why` "clear" or "resume"
      (SessionStart, those sources; also a Claude Code started with --resume or --continue; a source not seen
      before is passed on as it is). It may name the very
      conversation that was there (`/resume <its own id>`): whoever keeps the id sees that nothing changed.
    - SESSION_LEAVING: the SessionEnd that comes 0.02 to 0.07 s before a SESSION_CHANGED (reason clear, resume): the
      old conversation's id; nothing ended.
    - SESSION_ENDED: the Claude Code is ending, `dc_why` EXITED (/exit, Ctrl-D, Ctrl-C twice) or CLOSED (reason
      "other": its pane, window, session or server was closed, it was sent a signal, `claude stop`: Claude Code
      does not say which); a reason not seen before is passed on as it is.
    - COMPACTING, COMPACTED, COMPACT_DONE: PreCompact, the SessionStart with source compact (same conversation, same
      file, no SessionEnd before it), PostCompact (which does not always come).
    - INPUT: something was said to Claude, `dc_from` TYPED (somebody typed it: the owner at the terminal, or whoever
      types for them), TASK (Claude Code's own notice that a background task is done), PEER (another conversation,
      with `dc_peer` its name and `dc_text` the message), NOTICE (a bracketed notice of Claude Code's), SYSTEM
      (anything else in <…>).
    - PROGRESS: a tool is about to be called. PEER_SENT: that tool is a message to another conversation.
    - TURN_END, TURN_FAILED (`dc_why`: the API's error code, "unknown" when it gives none).
    - ASK, PLAN: Claude waits for an answer to a question, to a plan. DIALOG: Claude Code shows something to be
      answered on the screen; IDLE: its notice that it is waiting for input, which is nobody's business.
    - AGENT_START, AGENT_STOP; AGENT_PROGRESS (a tool call from inside a subagent); AGENT_LAUNCHED (the Agent or
      Task tool has returned), WORKFLOW_START (the Workflow tool has returned). These four hooks are the ones seen
      to come from a subagent. Any other event that bears a subagent's id (none has been seen: a dialog, a turn's
      end) keeps the kind it has without the id, and says in `dc_agent` which subagent it bears: it is handled as it
      always was, not swallowed by a kind made up for it.
    - UNKNOWN: an event this version does not know (a hook it has no name for). The bot leaves it alone and
      writes a line.

    `agent_type` on an event says nothing about its kind: a conversation started with an agent (`claude --agent x`, or
    "agent" in the settings) bears it on every event, and is a conversation like any other (measured, both
    versions: hook_payloads/sessions/*/agent.json, agentset.json). A subagent's events are told by `agent_id`."""
    if not isinstance(ev, dict):
        return ev
    if ev.get("dc_kind"):
        if not anew:
            return ev
        for name in NORMALIZED:
            ev.pop(name, None)
    try:
        ev.update(_kind(ev))
        if ev.get("agent_id") and ev["dc_kind"] not in (AGENT_START, AGENT_PROGRESS, AGENT_STOP, AGENT_LAUNCHED, WORKFLOW_START):
            ev["dc_agent"] = str(ev["agent_id"])
    except Exception:
        ev["dc_kind"] = UNKNOWN
    return ev


def _kind(ev):
    name, agent = ev.get("hook_event_name"), ev.get("agent_id")
    if name == "Ask":
        return {"dc_kind": ASK}
    if name == "Plan":
        return {"dc_kind": PLAN}
    if name == "SubagentStart":
        return {"dc_kind": AGENT_START}
    if name == "SubagentStop":
        return {"dc_kind": AGENT_STOP}
    if name == "PostToolUse":
        return {"dc_kind": WORKFLOW_START if ev.get("tool_name") == "Workflow" else AGENT_LAUNCHED}
    if agent and name == "PreToolUse":
        return {"dc_kind": AGENT_PROGRESS}
    if name == "SessionStart":
        source = ev.get("source")
        if source in ("clear", "resume"):
            return {"dc_kind": SESSION_CHANGED, "dc_why": source}
        if source == "compact":
            return {"dc_kind": COMPACTED}
        if source in ("startup", None):
            return {"dc_kind": READY}
        return {"dc_kind": SESSION_CHANGED, "dc_why": str(source)}  # a source not seen before: the pane shows the
        # conversation named, for a reason this version does not know. (Not UNKNOWN: whoever waits for a Claude Code to
        # say its session is there would wait in vain under a version that words its start otherwise.)
    if name == "SessionEnd":
        reason = ev.get("reason")
        if reason in ("clear", "resume"):
            return {"dc_kind": SESSION_LEAVING, "dc_why": reason}
        return {"dc_kind": SESSION_ENDED, "dc_why": {"prompt_input_exit": EXITED, "other": CLOSED, None: CLOSED}.get(reason, str(reason))}
    if name == "UserPromptSubmit":
        prompt = ev.get("prompt") if isinstance(ev.get("prompt"), str) else ""
        text = prompt.strip()
        peer = peer_message(prompt)
        if peer:
            return {"dc_kind": INPUT, "dc_from": PEER, "dc_peer": peer[0], "dc_text": peer[1]}
        if text.startswith("<task-notification>"):
            return {"dc_kind": INPUT, "dc_from": TASK}
        if text.startswith("<"):
            return {"dc_kind": INPUT, "dc_from": SYSTEM}
        return {"dc_kind": INPUT, "dc_from": NOTICE if _NOTICE.match(text) else TYPED}
    if name == "PreToolUse":
        sent = ev.get("tool_name") == "SendMessage" and str(ev.get("send_text") or "").strip()
        return {"dc_kind": PEER_SENT if sent else PROGRESS}
    if name == "Stop":
        return {"dc_kind": TURN_END}
    if name == "StopFailure":
        return {"dc_kind": TURN_FAILED, "dc_why": str(ev.get("error") or "unknown")}
    if name == "Notification":
        idle = ev.get("notification_type") == "idle_prompt" or "waiting for your input" in str(ev.get("message") or "")
        return {"dc_kind": IDLE if idle else DIALOG}
    if name == "PreCompact":
        return {"dc_kind": COMPACTING}
    if name == "PostCompact":
        return {"dc_kind": COMPACT_DONE}
    return {"dc_kind": UNKNOWN}


ADDED = ("texts", "context", "agent_meta", "agent_run", "agent_prompt", "dc_turn_start", "dc_turn_sure")  # what
# enrich() adds to an event. None may be a name Claude Code uses in what it hands a hook, nor one tools/dchook adds:
# the bot's tests/test_hook_fields.py holds them against real payloads. A new one starts with "dc_".
READS = ("PreToolUse", "Stop", "StopFailure", "Ask", "Plan")  # the events with which what Claude said is read
ENDS = ("Stop", "StopFailure")


def settle(rec, transcript, prompt_id, kind):
    """A file that was not there when its first event came (rec["unmet"]: the time of that event's hook, on the
    machine the file is on), and is now: where does the reading stand in it?

    A conversation that begins has nothing older than that event in it. One Claude Code moved to a background
    process has: its file is written later (with the first message after the move; at once when it was moved in
    mid-turn) and begins with a copy of the conversation as it was, each row with the time it was first written.
    Those were read where they first stood, or never will be: the reading stands after them, and no turn begins
    among them. (Until step 2.4d the place stayed at the file's first byte, and every turn before the move was read
    as said in the first turn after it: #code-review-2 on 011f029, three kinds of channel.)

    Time alone does not say where the copy ends, though: Claude Code stamps a message's row a moment BEFORE it
    calls that message's hook (0.012 to 0.052 s in every one of 30 recorded turns; the row is written 0.1 s after
    the hook). When the first event of the file is a UserPromptSubmit (a new conversation whose SessionStart came
    before a restart: every deploy; a Claude Code attached before it said anything), the turn's own first row is
    older than the event by that much; behind the place, the turn's beginning was never found and its words came
    only with its end (#code-review on 3477015). So, by what the rows say and not by a margin:
    - the running turn has an id and its own first row is in the file (find_begin: of the rows that bear the id,
      not a copy): nothing from that row on is old. Rows stand in the order they were written and a copy lies
      before whatever the turn itself says: the place is the end of the rows older than the first event, and
      never past that row. Unless something Claude said stands between that row and the end of the old rows: then
      the row is not the turn's own after all (time and the row disagree: time holds), and the place stays;
    - it has an id and the row is not there yet (the file is written in two goes a tenth of a second apart, the
      message's row in the second): nothing is settled, this is asked again with the next event; what such an
      event reads meanwhile is nothing (read() brings nothing of a turn whose first row it has not found);
    - a turn's end (it can wait no longer), or no id at all (a Stop of a turn moved while it ran has none; a
      version that sends none): by time alone.
    Asked with every event until a row that is not old stands in the file (or the turn's own first row does): until
    then all of the file is old, and more of the copy may still be written."""
    turn = prompt_id or rec.get("id")
    own = cc_transcript.find_begin(transcript, turn) if turn else None
    if turn and own is None and kind not in ENDS:
        return
    old, newer = cc_transcript.split_at(transcript, rec["unmet"])
    if own is not None or newer:
        del rec["unmet"]  # the copy, if there was one, is all there: something lies after it
    # (else it is asked again with the next event: the file is written in more goes than one, and every row in
    # it so far is old; what is written next may be more of the copy. #code-review-2 on 3477015: an attached
    # pane's conversation moved in mid-turn, two of five runs: the fork's SessionStart was handled when the file
    # held its first few rows, which bear no time, and the copy written a moment later was read as new.)
    if own is not None and own < old:
        # The turn's own first row, stamped a moment before its hook... or a row of the copy taken for it (a copied
        # turn that was cut short has no end to tell it by, and rows of before 2.1.284 give no turn number). What
        # lies between that row and the end of the old rows was all stamped before the first event: when Claude
        # said anything there, it was said before, and the place is not moved back over it (rather none than
        # twice: the turn's beginning is then not found, and its words come with its end). A turn's own first row
        # has nothing said after it that is older than its own hook.
        if not any(cc_transcript.said_in(row) for _start, end, row in cc_transcript.rows(transcript, own) if end <= old):
            old = own
    if old > rec["at"]:
        rec["at"] = old
    if old > rec["start"]:
        rec["start"] = old


def place(pos, transcript, prompt_id, kind, call=None, when=None, fork=False):
    """The record kept for a transcript, brought up to this event: which turn is running and where it lies.
    {"at": read up to here, "id": the running turn's prompt_id, "seen": every id met, "start": where the turn
    begins, "sure": False while that is only where the reading stood when the turn's first event came}.

    Claude Code calls a hook before it writes the row the hook is about (measured on 2.1.288 and 2.1.296: 0.1 s
    later for one tool call, 1.5 s and more when a reply holds several), so nothing here goes by the size of the
    file at the time of an event: only by what the rows say (promptId, the id of a tool call)."""
    rec = pos.get(transcript)
    if rec is not None and rec.get("unmet") is not None and cc_transcript.size(transcript):
        settle(rec, transcript, prompt_id, kind)
    if rec is None:  # nothing known of this file (the first event after a restart, a conversation attached midway):
        # read from its end on, not back to where this turn began: what it said was relayed before, or never will
        # be. Where the turn lies (the 📋 of its reply) goes by its id, like any turn's:
        # - its first row is in the file: it began before this event (a turn that was running); there, for sure;
        # - it is not: either the turn begins with this very event and its row is written in a moment (the hook
        #   comes first), or its row lies too far back to be looked for. For now the end of the file, to be mended
        #   when the row is read, as for any new turn; "midway" says that until then it may as well be a turn met in
        #   its middle, whose beginning is not known: nobody is to take the place for the turn's.
        # A file with no ids at all: its end, as it always was.
        # One thing lying before the end is not behind, though (step 2.2g): the reply whose first tool call this
        # very event is about (`call`), when its row is in the file already. Claude Code writes that row a moment
        # after the hook, with what was said ahead of the call; the event of a call comes before any other of its
        # reply, so nobody has read those words. (Until this step they were posted when the event was handled within
        # that moment, about 0.1 s, and never when it was handled after it: #code-review-2, 20 runs of a restart in
        # mid-turn, 13 and 7.) Not for a call that is not its reply's first: an event of an earlier call came before
        # this one, to whoever was reading then, and the words went with that.
        end = cc_transcript.size(transcript)
        ahead = cc_transcript.reply_of_first_call(transcript, end, call) if call and kind in READS and kind not in ENDS else None
        begin = cc_transcript.find_begin(transcript, prompt_id) if prompt_id else end
        if ahead is not None and (begin is None or ahead >= begin):
            end = ahead  # read from that reply on; what lies before it counts as read, as below
        rec = pos[transcript] = {"at": end, "id": prompt_id, "seen": [prompt_id] if prompt_id else [],
                                 "start": end if begin is None else begin, "sure": begin is not None, "midway": begin is None}
        # What lies before the place counts as read, whoever read it: the tool calls whose rows are behind it are
        # remembered like those this process went past. Their events may still come (the other calls of the reply
        # whose first event this is, the second event of a question): they neither wait for a row that is behind
        # nor read on from here (code review of 2158420: with an empty table they waited out the limit and then
        # read the reply that had been written meanwhile, and it was posted twice).
        rec["called"] = cc_transcript.calls_before(transcript, end)[-CALLED_KEPT:]
        if fork or not cc_transcript.split_at(transcript, float("inf"), YOUNG)[0]:
            # Not there yet; or there with no row that bears a time (Claude Code writes a few such rows first:
            # modes, a snapshot; the rest a tenth of a second later); or the event says it is a conversation
            # moved to a background process (`fork`), whose copy may be half written: settle() says where
            # the reading stands once more of it is there.
            rec["unmet"] = when if isinstance(when, (int, float)) and not isinstance(when, bool) else time.time()
    elif prompt_id is None:
        if rec["id"] is None and kind == "UserPromptSubmit":  # a Claude Code that sends no prompt_id: as it was before
            rec["at"] = rec["start"] = cc_transcript.size(transcript)
    elif prompt_id != rec["id"] and prompt_id not in rec["seen"]:  # a turn begins. Its own row is written after the
        # hook: until it is read, the turn begins where the reading stands (what lies between is the last turn's)
        rec["id"], rec["start"], rec["sure"], rec["midway"] = prompt_id, rec["at"], False, False
        rec["called"] = []  # the calls read were the last turn's
        rec["seen"] = (rec["seen"] + [prompt_id])[-200:]
    return rec


def read(rec, transcript, kind, tool_use_id):
    """What Claude said in the running turn that this event may bring, and the record moved on. Up to the row of
    this event's own tool call, or for the end of a turn up to the next turn's first row; never past either, however
    late the event is handled.

    An event of a tool call (PreToolUse, a question, a plan) finds the row of its call in one of three states, told
    by where the row lies and not by who read it:
    - behind the place (its id is among rec["called"]: an event handled before went past it, the other event of the
      same call or, when a reply's calls come out of order, a later call's; or it was in the file already when the
      file was first met and the place put at its end): nothing is read. What was said ahead of it has been
      brought or was never to be, and what lies after the place now is not this call's (tool results, the reply);
    - in the file, ahead of the place: read up to it, and it is the last row read;
    - not in the file yet: read to the end of what is there. Rows are written in order, so all of it lies before
      the call (mostly nothing; the caller has waited for the row if it could, awaits()).
    Every call whose row is read is remembered (rec["called"]), for the turn: place() empties it when a turn begins."""
    if tool_use_id and kind not in ENDS and tool_use_id in (rec.get("called") or []):
        return []
    rows = cc_transcript.rows(transcript, rec["at"])
    if not rec["sure"]:
        mine, found, ended = [], [], False
        for i, (_, _, row) in enumerate(rows):
            if cc_transcript.begins(row) == rec["id"]:
                mine.append(i)
                found.append((cc_transcript.turn_index(row), ended))
                ended = False
            elif cc_transcript.ends_turn(row):
                ended = True
        own = cc_transcript.own_first(found)  # (not a row copied in with the id)
        first = None if own is None else mine[own]
        if first is not None:  # the turn's own first row (a message, not a tool's result, which carries the id too):
            # it begins there, and what was read before it was not its
            rec["at"] = rec["start"] = rows[first][0]
            rec["sure"] = True
            rows = rows[first:]
        elif kind not in ENDS:
            return []  # not told apart yet from the end of the last turn: left where it is, for a later event (at
            # the latest the turn's end, which brings all of it: a turn whose events carry an id that no message
            # in the file has loses nothing, its words only come late)
    stop = None
    for i, (_, _, row) in enumerate(rows):
        if tool_use_id and kind not in ENDS and cc_transcript.calls(row, tool_use_id):
            stop = i + 1  # the call itself is the last row of this event
            break
        other = cc_transcript.asked_as(row)
        if other and rec["id"] and other != rec["id"] and other not in rec["seen"]:
            stop = i  # a turn whose events have not come yet: its rows are not this event's (events piled up)
            break
    taken = rows if stop is None else rows[:stop]
    if taken:
        rec["at"] = taken[-1][1]
        gone = [call for _, _, row in taken for call in cc_transcript.calls_in(row)]
        if gone:
            rec["called"] = ((rec.get("called") or []) + gone)[-CALLED_KEPT:]
    return [t for _, _, row in taken for t in cc_transcript.said_in(row)]


CALLED_KEPT = 500  # tool calls remembered as read, at most, within one turn (it is emptied when the next begins)
YOUNG = 256 * 1024  # how much of a file's end is looked through, when it is first met, for a row that bears a time
HOLD_SECS = 2.0  # how long an event waits for the row of its own tool call (awaits()). Measured: the row is
# written 0.1 s after the hook for one call, and 1.5 s after the first call's hook when a reply holds three (all of
# its rows are written when the reply has streamed to its end; a longer reply takes longer).


END_HOLD_SECS = 0.5  # how long a turn's end (Stop) waits for the reply's own row (awaits()). Measured on the real API
# (2026-10-13, 79 turns on three machines, both versions): the row is there 0.083 to 0.107 s after tools/dchook's
# stamp, never later than 0.2 s; about five times that, paid only when the row does not come.


NOT_THE_LAST = ("tool_use", "max_tokens", "refusal")  # stop_reasons of a reply after which the turn goes on by itself


LATE_WITHIN = 2.0  # seconds past the limit within which an end handled without its row counts as having waited it out


def last_reply(row, said=None):
    """Is this transcript row the reply a turn ends with? An assistant row of the conversation itself (no side
    chain, no notice of a failed request) that bears a usage, and whose stop_reason is end_turn; or, when `said` is
    given (what the turn's Stop says the reply was), whose text is that and whose stop_reason is not one the turn
    goes on after (a reply that ended on a stop sequence: a reply all the same; before, such a turn waited out the
    whole limit at every end). (Measured, both versions: each block of a reply is a row of its own, all written at
    once, each with the reply's usage and stop_reason: any one of them will do. A reply cut by the output limit or
    refused has its own stop_reason, and Claude Code asks again by itself with no Stop between: not the turn's last.)"""
    message = row.get("message") if isinstance(row, dict) else None
    if not (isinstance(message, dict) and row.get("type") == "assistant" and not row.get("isSidechain")
            and not row.get("isApiErrorMessage") and isinstance(message.get("usage"), dict)):
        return False
    reason = message.get("stop_reason")
    if reason == "end_turn":
        return True
    return bool(said) and reason not in NOT_THE_LAST and "".join(cc_transcript.said_in(row)).strip() == said


def end_written(ev, rec, transcript):
    """Is the row of the reply this Stop is about in the transcript, from where the reading stands? A last_reply()
    row that is this turn's: one after the turn's own first row (the message whose id is the event's prompt_id), or
    any when the reading already stands inside this turn; failing both (a turn whose first row is not to be found:
    its events bear an id no message has), one whose text is what the event says the reply was. The event brings
    only the reply's last block of text, its ends trimmed: a row is a block, so the two are held together trimmed."""
    own, said = ev.get("prompt_id"), (ev.get("last_assistant_message") or "").strip()
    inside = bool(rec.get("sure")) and rec.get("id") == own
    for _, _, row in cc_transcript.rows(transcript, rec["at"]):
        began = cc_transcript.begins(row)
        if began:
            inside = began == own
        elif last_reply(row, said) and (inside or (said and "".join(cc_transcript.said_in(row)).strip() == said)):
            return True
    return False


def awaits(ev, pos, now=None, hold=HOLD_SECS, end_hold=END_HOLD_SECS, calls=True):
    """Is this event better left for a moment? True for the end of a turn (Stop) whose reply's row is not in the
    transcript yet (for at most `end_hold` seconds; see below), and, when `calls`, for a tool call (PreToolUse, or the
    question or plan made of one) whose own row is not in the transcript yet: Claude Code calls the hook first and writes the row after, and
    what Claude said ahead of the call is written with it. Handled now, the event brings none of that, and the words
    come one event late (after the tool has run, or never when the turn is interrupted). Left for the next look,
    they come with it.

    Never for longer than `hold` seconds after the hook ran (the event's own "ts", put there by tools/dchook on the
    same machine): a row that is never written (a call that was refused) costs that once, and events that waited
    while nobody looked are not held at all. Nothing of the event is changed here, nor of where the reading stands;
    one thing is written into `pos`: a count of the ends that waited out their limit (the record's "late", and
    "late_for": which turn was counted last; ends_late()). The caller leaves the event where it is, and with it every
    later event of the same conversation (their order is the turn's)."""
    transcript, kind, call = ev.get("transcript_path") or "", ev.get("hook_event_name"), ev.get("tool_use_id")
    end = kind == "Stop"
    if not transcript or ev.get("agent_id") or not ev.get("prompt_id"):
        return False
    if end:
        # The end of a turn (stage 3b step 2.2f): its reply's row is written about 0.1 s after the hook too, and with
        # it the usage the footer's context comes from. Handled before, the footer said the context of the turn
        # before, or none in a conversation's first turn. Not for a Stop that says of no reply (no
        # last_assistant_message: the model answered with nothing, and no row is written for it); not for a failed
        # request (StopFailure: its row comes as late, and its message shows no context).
        if not isinstance(ev.get("last_assistant_message"), str) or not ev["last_assistant_message"].strip():
            return False  # (nor one that says of an empty reply: there is nothing to know its row by)
        hold = end_hold
    elif not calls or kind not in READS or kind in ENDS or not call:
        return False
    try:
        age = (time.time() if now is None else now) - float(ev.get("ts"))
    except (TypeError, ValueError):
        return False  # no telling for how long it has waited: not held
    rec = pos.get(transcript)
    if end:
        if not isinstance(rec, dict) or age < 0:
            return False  # a file not met yet (read from its end anyway)
        if end_written(ev, rec, transcript):
            return False  # there (also when it came before the hook's stamp: a hook slow to start)
        if age < hold:
            return True
        if age < hold + LATE_WITHIN and rec.get("late_for") != ev.get("prompt_id"):  # waited out the limit, and still
            # no row: counted, once for a turn (ends_late()). Not an event first looked at long after its hook (the
            # reader was away): it waited for nobody, and its row may never have been in reach.
            rec["late_for"], rec["late"] = ev.get("prompt_id"), rec.get("late", 0) + 1
        return False
    if not 0 <= age < hold:
        return False  # waited long enough
    if not isinstance(rec, dict):  # a file not met yet (the first event after a restart): waited for like any other,
        # so that place() finds the row and what was said ahead of it (step 2.2g)
        return call not in cc_transcript.calls_before(transcript, cc_transcript.size(transcript))
    if call in (rec.get("called") or []):
        return False  # its row was read
    return not any(cc_transcript.calls(row, call) for _, _, row in cc_transcript.rows(transcript, rec["at"]))


def ends_late(pos):
    """How many turns' ends were handled without their reply's row, the limit having passed (awaits()), among the
    transcripts kept in `pos`. For whoever keeps `pos` to tell (the bot writes it down, once aloud)."""
    return sum(rec.get("late", 0) for rec in pos.values() if isinstance(rec, dict))


def enrich(ev, pos, texts=True):
    """Add what only the machine with the transcript can read: what Claude said since the last event, the context
    use. (A subagent's details: agent(), below.) `pos`: what is kept for each transcript (place()), by the caller, for as long as
    it sees fit: only this module reads or writes what is in it.

    Turns are told apart by the prompt_id of the events, the same on this machine and on another: a message sent
    while a turn runs comes with that turn's id, a turn that ended with no event (an interrupt) is over when
    another id comes. An event of a subagent (agent_id) carries the id of whatever turn the main conversation is
    in then: it neither begins a turn nor reads what the main conversation said.
    `texts`: False brings nothing that was said; the reading itself goes on as ever.
    `dc_turn_start`: where in the transcript the running turn begins, as known now (None: not known, for a turn
    that may have been running when the file was first met, until its first row is read; still None at its end:
    it was, and its beginning is too far back); `dc_turn_sure`: False while
    that is still to be mended by a later event of the turn (the last one received holds)."""
    transcript, kind = ev.get("transcript_path") or "", ev.get("hook_event_name")
    if transcript and not ev.get("agent_id"):
        rec = place(pos, transcript, ev.get("prompt_id"), kind, ev.get("tool_use_id"), ev.get("ts"),
                    fork=kind == "SessionStart" and ev.get("source") == "fork")
        if kind in READS:  # also when the words are not wanted: the place moves on and the turn's beginning is
            said = read(rec, transcript, kind, ev.get("tool_use_id"))  # found all the same (📋 goes by it)
            if texts:
                ev["texts"] = said
        unknown = rec.get("midway") and not rec["sure"]  # met in what may be its middle, and its first row not read yet
        ev["dc_turn_start"], ev["dc_turn_sure"] = None if unknown else rec["start"], rec["sure"]
    if transcript and kind == "Stop":
        ev["context"] = cc_transcript.context(transcript)


AGENT_TRIES, AGENT_GAP = 6, 0.25  # how often, and how far apart, a subagent's files are looked for (agent_waits())


def agent(ev):
    """Look, once and without waiting, for what a subagent that has just started (its SubagentStart) was started as:
    its files appear a moment after the event, and what it was asked with them or a moment after (the hook comes
    before the row: #issues 24, a thread with no task). What is there is put on the event: `agent_meta` (Claude Code's
    own note of it), `agent_run` (the workflow run it belongs to, or None), `agent_prompt` (what it was asked, "" while
    that is not written). True when all is there: the files, and the row that asks it (the row, not words in it: a
    forked agent's first row leaves no text, and is there at once). The one shape of a subagent's start, wherever
    the files are read (stage 3b step 2.2c: the bot read them its own way for its own machine)."""
    found = cc_transcript.locate(ev.get("transcript_path") or "", ev.get("agent_id"))
    if not found:
        return False
    meta, agent_path, run = found
    ev["agent_meta"], ev["agent_run"] = meta, run
    ev["agent_prompt"] = cc_transcript.first_prompt(agent_path)
    return bool(cc_transcript.asked_yet(agent_path))


def agent_waits(ev, tries=AGENT_TRIES, gap=AGENT_GAP):
    """The waits of one who looks for a subagent's details until they are there: after each look that did not find
    them all, the seconds to wait before the next (at most `tries` looks, 1.5 s in all; what was found by the last
    look stays on the event). Nothing sleeps here: the caller waits its own way (tools/dcremote asleep, the bot in
    its event loop).

        for gap in agent_waits(ev):
            time.sleep(gap)"""
    for _ in range(tries):
        if agent(ev):
            return
        yield gap
