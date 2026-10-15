"""Claude Code's transcript (<project>/<session>.jsonl, one JSON object a line) and the files of its subagents
beside it, read into plain data: what Claude has said since a byte, how full the context window is, a turn between
two bytes, where a subagent's files are and what it was asked. Nothing here writes, and nothing here words
anything for the owner. From the bot's events.py, agents.py and process.py as they were (stage 3b step 1.4);
turn() is the reading half of process.read_turn, whose other half (the Discord pieces) stays there."""
import glob
import datetime
import json
import os


def content_of(row):
    """The content of a transcript row's message, or None: a row whose message is no object (a broken file, a row
    of another program's) has none, and is passed over by every reader here."""
    message = row.get("message") if isinstance(row, dict) else None
    return message.get("content") if isinstance(message, dict) else None


def part(row, key):
    """A part of a row that is an object when Claude Code writes it (`origin`, `attachment`): that object, or an
    empty one for a row that has none or has something else there."""
    value = row.get(key) if isinstance(row, dict) else None
    return value if isinstance(value, dict) else {}


def is_text(block):
    return isinstance(block, dict) and block.get("type") == "text"


def text_of(block):
    """The text of a text block, "" for any other block and for one whose text is no string (passed over)."""
    text = block.get("text") if is_text(block) else None
    return text if isinstance(text, str) else ""


def written(value):
    """A field of a tool's input as text: a string as it is, nothing as "", anything else (a number, a list: not
    what Claude Code writes there) as Python writes it. No shape of an input makes its reader fail."""
    return value if isinstance(value, str) else "" if value is None else str(value)


def size(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def new_texts(path, pos, sidechain=False):
    """What Claude has said in the transcript since byte `pos`: (texts, new_pos). `sidechain`: the file is
    a subagent's own transcript, where every line is marked as a side chain.

    The Stop hook only carries the last message of a turn; anything said before a tool call is only here.
    A line still being written (no newline yet) is left for the next call."""
    try:
        with open(path, "rb") as f:
            f.seek(pos)
            data = f.read()
    except OSError:
        return [], pos
    end = data.rfind(b"\n") + 1
    out = []
    for line in data[:end].split(b"\n"):
        if b'"assistant"' not in line:
            continue
        try:
            d = json.loads(line.decode("utf-8", "replace"))
        except ValueError:
            continue
        if not isinstance(d, dict) or d.get("type") != "assistant" or (d.get("isSidechain") and not sidechain):
            continue
        content = content_of(d)
        for b in content if isinstance(content, list) else []:
            if text_of(b).strip():
                out.append(text_of(b).strip())
    return out, pos + end



def rows(path, at=0):
    """The whole rows of a transcript from byte `at` on: (where the row starts, where the next one starts, the row
    as a dict, or None for a row that is not a JSON object). A row still being written (no newline yet) is left."""
    try:
        with open(path, "rb") as f:
            f.seek(at)
            data = f.read()
    except (OSError, TypeError):
        return []
    out, here = [], at
    for line in data[:data.rfind(b"\n") + 1].split(b"\n")[:-1]:
        there = here + len(line) + 1
        try:
            d = json.loads(line.decode("utf-8", "replace"))
        except ValueError:
            d = None
        out.append((here, there, d if isinstance(d, dict) else None))
        here = there
    return out


def asked_as(row):
    """The promptId of a row that is something said to Claude in the main conversation (the turn it belongs to:
    the owner's message, a tool's result, what Claude Code says to it itself), or None."""
    if row and row.get("type") == "user" and not row.get("isSidechain"):
        return row.get("promptId")
    return None


def begins(row):
    """The promptId of a row that is a message to Claude in the main conversation: what a turn begins with (the
    owner's words, a queued message, Claude Code's own prompt after a background task, a scheduled wake-up or
    another session's message: those last are isMeta rows and count). Not a tool's result: that row carries its
    turn's promptId too, and lies in the middle of the turn."""
    pid = asked_as(row)
    if not pid:
        return None
    content = content_of(row)
    if isinstance(content, list):
        return pid if any(not (isinstance(b, dict) and b.get("type") == "tool_result") for b in content) else None
    return pid if isinstance(content, str) else None


def turn_index(row):
    """Which turn of its conversation a message row opened, as Claude Code numbers them (turnPosition.turnIndex), or
    None for a row that does not say."""
    where = row.get("turnPosition") if isinstance(row, dict) else None
    index = where.get("turnIndex") if isinstance(where, dict) else None
    return index if isinstance(index, int) and not isinstance(index, bool) else None


def ends_turn(row):
    """Does this row say a turn of the conversation itself is over? The reply a turn ends with (an assistant row
    whose stop_reason is end_turn, no side chain), or Claude Code's own note of how long the turn took (a system row
    "turn_duration"). A turn cut short has neither."""
    if not isinstance(row, dict) or row.get("isSidechain"):
        return False
    if row.get("type") == "system":
        return row.get("subtype") == "turn_duration"
    message = row.get("message")
    return row.get("type") == "assistant" and isinstance(message, dict) and message.get("stop_reason") == "end_turn"


def own_first(found):
    """Of the message rows that bear one prompt id, in the order they stand: which is the turn's own first (its
    place in the list), or None for an empty list. `found`: for each, (its turn_index(), whether a turn ended
    between the row before it in the list and this one: ends_turn()).

    As a rule the first: the others are messages added while the turn ran. Not in a conversation Claude Code moved
    to a background process: its file begins with a copy of the conversation as it was, and the copied message
    rows are written with the prompt id of the turn that was current when the file was written (measured on 2.1.288
    and 2.1.296: the left key on an idle conversation; the first turn after it found its beginning in the
    conversation's very first row, and all that was ever said in between was posted again as said in this turn).
    A copied row is of an earlier turn, and either of two things says so:
    - it gives another turn number than the row after it (each keeps its own; rows written before 2.1.284, and
      some since, give none: a row without one says nothing, a turn's rows that give one give the same);
    - a turn ended between it and the row after it (the copy holds every turn whole: its reply, and the note of
      how long it took), and the two do not give the same turn number. (They can: in real conversations a message
      row follows a reply under the same id and number now and then, 5 turns of 1098 looked at; the number is
      what Claude Code itself says, and holds.)
    So the list falls into turns, and the first row of the last of them is the turn's own. (Told by the number
    alone, a conversation whose earlier rows give none had its copy taken for the turn: #code-review on 186a5d4.
    Where a row without a number follows a turn's end under the same id, 3 turns of those 1098, the later row is
    taken: the turn's record begins later than it might, and nothing is read twice.)"""
    if not found:
        return None
    first, number = 0, found[0][0]
    for i, (index, ended) in enumerate(found[1:], 1):
        both = index is not None and number is not None
        if (both and index != number) or (ended and not (both and index == number)):
            first, number = i, index
        elif index is not None:
            number = index
    return first


def before(path, when, tail_bytes=4 * 1024 * 1024):
    """Where the rows of a transcript written before the moment `when` (seconds since the epoch) end: the byte after
    the last row whose own timestamp is earlier, looked for in the last `tail_bytes` of the file; 0 when there is
    none (or nothing can be read). Rows that bear no timestamp, or one that cannot be read, decide nothing."""
    return split_at(path, when, tail_bytes)[0]


def split_at(path, when, tail_bytes=4 * 1024 * 1024):
    """(before(): where the rows older than `when` end, whether a row of that moment or later is in the file)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            here = max(0, f.tell() - tail_bytes)
            f.seek(here)
            data = f.read()
    except (OSError, TypeError):
        return 0, False
    if here:
        cut = data.find(b"\n") + 1
        if not cut:
            return 0, False
        here, data = here + cut, data[cut:]
    found, newer = (here if here else 0), False  # a file longer than what is looked at: what lies before the piece is older still
    for line in data[:data.rfind(b"\n") + 1].split(b"\n")[:-1]:
        here += len(line) + 1
        if b'"timestamp"' not in line:
            continue
        try:
            stamp = json.loads(line.decode("utf-8", "replace")).get("timestamp")
            at = datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
        except (ValueError, AttributeError, TypeError, OverflowError):
            continue
        if at < when:
            found = here
        else:
            newer = True
    return found, newer


def find_begin(path, prompt_id, tail_bytes=4 * 1024 * 1024):
    """Where the turn `prompt_id` begins: the byte of its first message row (own_first(): not a row copied into the
    file with this id), looked for in the last `tail_bytes` of the file (a turn in progress began not long ago; a
    conversation's file can be hundreds of megabytes and this is asked from an event loop). None when it is not
    there."""
    if not prompt_id:
        return None
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            here = max(0, size - tail_bytes)
            f.seek(here)
            data = f.read()
    except (OSError, TypeError):
        return None
    if here:  # the first row of the piece is cut: begin at the next whole one
        cut = data.find(b"\n") + 1
        if not cut:
            return None
        here, data = here + cut, data[cut:]
    mark, found, ended = json.dumps(prompt_id).encode(), [], False
    for line in data[:data.rfind(b"\n") + 1].split(b"\n")[:-1]:
        if mark in line or b'"end_turn"' in line or b'"turn_duration"' in line:
            try:
                d = json.loads(line.decode("utf-8", "replace"))
            except ValueError:
                d = None
            if begins(d if isinstance(d, dict) else None) == prompt_id:
                found.append((here, turn_index(d), ended))
                ended = False
            elif ends_turn(d):
                ended = True
        here += len(line) + 1
    own = own_first([(index, over) for _, index, over in found])
    return None if own is None else found[own][0]


def said_in(row, sidechain=False):
    """The texts Claude said in a row, trimmed (new_texts' rule for one row)."""
    if not row or row.get("type") != "assistant" or (row.get("isSidechain") and not sidechain):
        return []
    content = content_of(row)
    return [text_of(b).strip() for b in content if text_of(b).strip()] if isinstance(content, list) else []


def calls(row, tool_use_id):
    """Is this the row of Claude's tool call with this id?"""
    if not row or row.get("type") != "assistant" or row.get("isSidechain"):
        return False
    content = content_of(row)
    return any(isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id") == tool_use_id
               for b in content if isinstance(content, list)) if isinstance(content, list) else False


def calls_in(row):
    """The ids of the tool calls Claude makes in a row of the main conversation."""
    if not row or row.get("type") != "assistant" or row.get("isSidechain"):
        return []
    content = content_of(row)
    return [b["id"] for b in content if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id")] \
        if isinstance(content, list) else []


def calls_before(path, end, tail_bytes=4 * 1024 * 1024):
    """The ids of the tool calls whose rows lie in the `tail_bytes` before byte `end` of the file, oldest first: what
    one who begins to read at `end` has behind him (the same stretch find_begin looks through)."""
    try:
        with open(path, "rb") as f:
            here = max(0, end - tail_bytes)
            f.seek(here)
            data = f.read(max(0, end - here))
    except (OSError, TypeError):
        return []
    if here:
        data = data[data.find(b"\n") + 1:]
    out = []
    for line in data[:data.rfind(b"\n") + 1].split(b"\n")[:-1]:
        if b'"tool_use"' in line:
            try:
                d = json.loads(line.decode("utf-8", "replace"))
            except ValueError:
                continue
            out += calls_in(d if isinstance(d, dict) else None)
    return out


def reply_of_first_call(path, end, tool_use_id, tail_bytes=4 * 1024 * 1024):
    """Where the reply begins whose FIRST tool call is this one, when the call's row lies in the `tail_bytes` before
    byte `end`: the byte of the first row of that reply (its rows are one after another and bear the reply's id:
    what Claude said ahead of the call, then the call). None when the row is not there, or when the reply makes
    another call before this one (an event of that call came first, and what was said ahead went with it)."""
    here = max(0, end - tail_bytes)
    got = [r for r in rows(path, here) if r[1] <= end]
    if here and got:
        got = got[1:]  # a piece that begins in the middle of a row
    at = next((i for i, (_, _, row) in enumerate(got) if calls(row, tool_use_id)), None)
    if at is None:
        return None
    reply = (got[at][2].get("message") or {}).get("id")
    first = at
    while reply and first > 0:
        before = got[first - 1][2]
        if not before or before.get("type") != "assistant" or before.get("isSidechain") \
                or (before.get("message") or {}).get("id") != reply:
            break
        first -= 1
    if any(calls_in(row) for _, _, row in got[first:at]):
        return None
    block = next((b for b in content_of(got[at][2]) if isinstance(b, dict) and b.get("type") == "tool_use"), None)
    if not (isinstance(block, dict) and block.get("id") == tool_use_id):
        return None  # (a row that holds several calls, and this is not the first of them)
    return got[first][0]


def turn_end(path, start, own=None):
    """Where the turn that begins at byte `start` ends: the first message row of another turn after it (a row that
    begins a turn under another promptId than `own`, the turn's own), or None when the turn runs to the end of
    the file as it is now. Looked for when asked (the 📋 of a reply), not kept: the last rows of a turn are
    written after its Stop hook, and nothing later has to go back and mend a record.

    `own` None (a record that did not keep the turn's id): the first promptId met from `start` on is taken for
    the turn's. That is wrong when `start` lies past the turn's last row under its own id (the next turn's id is
    then the first one met, and the next turn is shown): whoever knows the id passes it."""
    try:
        with open(path, "rb") as f:
            f.seek(start)
            here = start
            for line in f:
                if not line.endswith(b"\n"):
                    break
                if b'"promptId"' in line:
                    try:
                        d = json.loads(line.decode("utf-8", "replace"))
                    except ValueError:
                        d = None
                    d = d if isinstance(d, dict) else None
                    if own is None:
                        own = asked_as(d)
                    elif (begins(d) or own) != own:
                        return here
                here += len(line)
    except (OSError, TypeError):
        pass
    return None


def context(path, tail_bytes=2 * 1024 * 1024):
    """[tokens in the context window now, model] from the last reply in a transcript, or None. Counted as
    Claude Code counts it for its status line: the request's input, cache reads and cache writes."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - tail_bytes))
            lines = f.read().split(b"\n")
    except (OSError, TypeError):
        return None
    for raw in reversed(lines):
        if b'"usage"' not in raw or b'"assistant"' not in raw:
            continue
        try:
            d = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            continue
        msg = d.get("message") if isinstance(d, dict) else None
        if d.get("type") != "assistant" or d.get("isSidechain") or not isinstance(msg, dict):
            continue
        u, model = msg.get("usage"), str(msg.get("model") or "")
        if not isinstance(u, dict):
            continue  # (a row whose usage is no object: passed over, as one with no usage)
        tokens = sum(int(u.get(k)) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
                     if isinstance(u.get(k), (int, float)) and not isinstance(u.get(k), bool)
                     and u.get(k) == u.get(k) and abs(u.get(k)) != float("inf"))  # (a count that is no number: not counted)
        if tokens and not model.startswith("<"):  # "<synthetic>": a placeholder Claude Code writes itself
            return [tokens, model]
    return None


def window(model):
    """The context window Claude Code reports for a model (seen in its status line input, 2.1.289)."""
    return 200000 if "haiku" in model else 1000000


def session_dir(transcript_path):
    """<project>/<session>.jsonl -> <project>/<session>, where Claude Code keeps the session's subagents."""
    return transcript_path[:-len(".jsonl")] if transcript_path.endswith(".jsonl") else transcript_path


def locate(transcript_path, agent_id):
    """(meta, transcript path, workflow run id or None) of a subagent, or None while its files are not there yet."""
    base = os.path.join(session_dir(transcript_path), "subagents")
    hits = glob.glob(os.path.join(base, "agent-%s.meta.json" % agent_id)) + \
        glob.glob(os.path.join(base, "workflows", "*", "agent-%s.meta.json" % agent_id))
    if not hits:
        return None
    try:
        with open(hits[0]) as f:
            meta = json.load(f)
    except (OSError, ValueError):
        meta = {}
    folder = os.path.dirname(hits[0])
    run = os.path.basename(folder) if os.path.basename(os.path.dirname(folder)) == "workflows" else None
    return meta, os.path.join(folder, "agent-%s.jsonl" % agent_id), run


HARNESS = "[Workflow harness"  # how the rows the workflow harness says to an agent begin, and (FOLLOWS) how the one
# that brings its task ends its notice: the wording of Claude Code 2.1.296 (recorded: the tests' turns/2.1.296/w3-F).
# To be looked at with every new version (the connector's __init__ lists what is a version's wording).
FOLLOWS = "task text follows:\n"


def task_text(prompt):
    """A workflow agent's prompt without the harness's notice in front of it (the task itself is indented)."""
    if prompt.startswith(HARNESS) and FOLLOWS in prompt:
        body = prompt.split(FOLLOWS, 1)[1]
        return "\n".join(row[2:] if row.startswith("  ") else row for row in body.split("\n")).strip()
    return prompt


def first_prompt(path):
    """What the subagent was asked: the first thing said to it in its transcript.

    A workflow's agent (Claude Code 2.1.296) is first told two things by the harness: a row that relays the owner's
    request which set the workflow off ("[Workflow harness — user request] …"), the same for every agent of the run,
    and then its own task ("[Workflow harness — computed task] … The computed task text follows:"). Its task is the
    second: the harness's rows are passed over until the one that brings a task. When none does (a version that
    words them otherwise), what the first row said stands, as before."""
    first = None
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if not line.endswith("\n"):
                    break  # a row still being written: not there yet
                if '"type":"user"' not in line:
                    continue
                d = json.loads(line)
                content = content_of(d)
                if isinstance(content, str):
                    text = content.strip()
                else:
                    texts = [text_of(b) for b in content if is_text(b)] if isinstance(content, list) else []
                    text = "\n".join(t for t in texts if t and not t.lstrip().startswith("<")).strip()
                    if not text:
                        continue
                if first is None:
                    first = text
                if text.startswith(HARNESS) and FOLLOWS not in text:
                    continue  # the harness speaking, not yet of the task
                return task_text(text) if text.startswith(HARNESS) else task_text(first)
    except (OSError, ValueError):
        pass
    return task_text(first) if first else ""


def asked_yet(path):
    """Has the subagent been told what to do, as far as its transcript shows? Told by what rows there are, not by
    how a row is worded:
    - nothing said to it yet: no;
    - a first row that is not the harness's relay of the owner's request (a task, or a forked agent's first row, a
      tool's result and a notice in <…> of which first_prompt() leaves no text): yes, it is there;
    - the relay alone (a workflow's agent: "[Workflow harness … " without a task): not yet, its task is the next row;
    - the relay and a second row said to it, whatever that says: yes;
    - or the agent has begun to answer: yes.
    So when a version of Claude Code words the task row otherwise, the worst is that the task shows as the first row
    says (first_prompt()); nothing waits for a wording. Only a relay still known by its beginning and never followed
    by anything is waited for to the limit."""
    said = 0
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if not line.endswith("\n"):
                    break  # a row still being written: not there yet (it is neither said to it nor an answer of its)
                if '"type":"assistant"' in line and said:
                    return True
                if '"type":"user"' not in line:
                    continue
                said += 1
                if said > 1:
                    return True
                d = json.loads(line)
                content = content_of(d)
                if isinstance(content, str):
                    text = content.strip()
                else:
                    texts = [text_of(b) for b in content if is_text(b)] if isinstance(content, list) else []
                    text = "\n".join(t for t in texts if t and not t.lstrip().startswith("<")).strip()
                if not (text.startswith(HARNESS) and FOLLOWS not in text):
                    return True
    except (OSError, ValueError):
        pass
    return False


def turn(path, start, end):
    """What lies in a transcript between two bytes, in the order it was written, or None if the file is gone:
    ("asked", text) what was said to Claude, ("result", block) a tool's result as Claude Code wrote it,
    ("said", text) what Claude said, ("tool", name, input) a tool call. Side chains (subagents) are left out."""
    try:
        with open(path, "rb") as f:
            f.seek(start)
            data = f.read(max(0, end - start) if end else -1)
    except OSError:
        return None
    out = []
    for raw in data.split(b"\n"):
        try:
            d = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            continue
        if not isinstance(d, dict) or d.get("isSidechain"):
            continue
        content = content_of(d)
        if d.get("type") == "user":
            if isinstance(content, str) and content.strip():
                out.append(("asked", content))
            for b in content if isinstance(content, list) else []:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    out.append(("result", b))
                elif text_of(b).strip() and not text_of(b).lstrip().startswith("<"):
                    out.append(("asked", text_of(b)))
        elif d.get("type") == "assistant":
            for b in content if isinstance(content, list) else []:
                if not isinstance(b, dict):
                    continue
                if text_of(b).strip():
                    out.append(("said", text_of(b)))
                elif b.get("type") == "tool_use":
                    out.append(("tool", b.get("name", "?"), b.get("input")))
    return out


SLIM_RESULT = 4000      # characters of a tool's output kept by slim(): more than a reader of the turn shows of one
SLIM_TOTAL = 2000000    # characters of the whole turn as JSON: a turn sent to another machine is not sent beyond this


def result_text(block):
    """The text of a tool's result as Claude Code wrote it (a string, or text blocks among others), ends trimmed."""
    content = block.get("content") if isinstance(block, dict) else None
    if isinstance(content, list):
        content = "\n".join(text_of(b) for b in content if is_text(b))
    return str(content or "").strip()


COMMAND, FILE, SEARCH, AGENT, WEB, OTHER = "command", "file", "search", "agent", "web", "other"  # tool_call()


def tool_call(name, inp):
    """What one tool call of a turn (an item ("tool", name, input) of turn()) is, by the tools Claude Code has, as
    data: (sort, what). The words for it are the reader's (the bot's process.tool_line). Which tool is of which
    sort, and where in its input the thing is, is known here and nowhere else (stage 3b step 2.2d):

      COMMAND  {"command", "note"}                a shell command and its description (Bash)
      FILE     {"path", "old", "new", "chars"}    a file read or changed; `old`, `new`: how long what an Edit took out
                                                  and put in was; `chars`: how long what a Write wrote was (None for
                                                  the tools that have none)
      SEARCH   {"pattern", "path"}                Grep, Glob
      AGENT    {"description", "prompt"}          a subagent started (Agent, formerly Task)
      WEB      {"what"}                           the address fetched or the words searched for
      OTHER    {"input"}                          any other tool (a new one, an MCP server's): its input as it is

    An input that is no dict (a broken row) counts as an empty one; `command`, `prompt` and what is counted are
    text whatever was in the input (written())."""
    inp = inp if isinstance(inp, dict) else {}
    if name == "Bash":
        return COMMAND, {"command": written(inp.get("command")), "note": inp.get("description")}
    if name in ("Read", "Write", "Edit", "NotebookEdit"):
        return FILE, {"path": inp.get("file_path") or inp.get("notebook_path"),
                      "old": len(written(inp.get("old_string"))) if name == "Edit" else None,
                      "new": len(written(inp.get("new_string"))) if name == "Edit" else None,
                      "chars": len(written(inp.get("content"))) if name == "Write" else None}
    if name in ("Grep", "Glob"):
        return SEARCH, {"pattern": inp.get("pattern") or "", "path": inp.get("path")}
    if name in ("Agent", "Task"):
        return AGENT, {"description": inp.get("description"), "prompt": written(inp.get("prompt"))}
    if name in ("WebFetch", "WebSearch"):
        return WEB, {"what": inp.get("url") or inp.get("query") or ""}
    return OTHER, {"input": inp}


def own(path, config_dir):
    """The real path of `path` when it is a transcript of Claude Code's own on this machine (a .jsonl under the
    projects folder of `config_dir`: sessions.config_dir()), else None. For a command that reads a file for
    whoever can run it (tools/dcremote turn): a link out of the folder, `..`, another file are not read."""
    projects = os.path.realpath(os.path.join(config_dir, "projects"))
    real = os.path.realpath(path)
    return real if real.endswith(".jsonl") and real.startswith(projects + os.sep) else None


TEXT, COMMAND_TEXT, PROMPT_TEXT, INPUT_TEXT = "text", "command", "prompt", "input"  # the fields of an item (whole())


def whole(items, n, field):
    """The whole text of one field of the n-th item of a turn (what turn() gave), ends trimmed, or None when the
    turn has no such item or the item no such field. TEXT: what was asked or said, or a tool's output; of a tool
    call COMMAND_TEXT (a shell command), PROMPT_TEXT (what a subagent was asked), INPUT_TEXT (any other tool's
    input, as JSON). A reader that showed only the beginning of a long field asks for the rest by these (the
    bot's 📋 on a message that ends cut short)."""
    if not isinstance(n, int) or isinstance(n, bool) or not 0 <= n < len(items):
        return None
    item = items[n]
    if not isinstance(item, (list, tuple)) or len(item) < 2:
        return None
    if item[0] in ("asked", "said") and field == TEXT:
        return written(item[1]).strip()
    if item[0] == "result" and field == TEXT:
        return result_text(item[1])
    if item[0] == "tool" and len(item) >= 3:
        sort, what = tool_call(item[1], item[2])
        if sort == COMMAND and field == COMMAND_TEXT:
            return what["command"].strip()
        if sort == AGENT and field == PROMPT_TEXT:
            return what["prompt"].strip()
        if sort == OTHER and field == INPUT_TEXT:
            return json.dumps(what["input"], ensure_ascii=False)
    return None


def is_prose(items, n):
    """Is the n-th item's text prose with markup of its own (what was asked or said), where a cut is better made at
    the end of a line and a code fence left open by it has to be closed? (A tool's output, a command, an input:
    shown inside a fence by the reader, cut anywhere.)"""
    return 0 <= n < len(items) and isinstance(items[n], (list, tuple)) and bool(items[n]) and items[n][0] in ("asked", "said")


def fence_at(text, offset, fence=None):
    """The code fence that is open at `offset` of a text with markdown in it: the word after its ``` ("" when it has
    none), or None when no fence is open there. A line that begins with ``` opens one or closes the one open.
    `fence`: the one open where the text begins (a stretch out of the middle of a longer text)."""
    for line in text[:offset].split("\n"):
        if line.strip().startswith("```"):
            fence = None if fence is not None else line.strip()[3:].strip()
    return fence


def line_end(text, at, floor=0):
    """`at`, or the end of the last whole line before it when there is one after `floor`: where a text is cut (the
    newline itself is left for what follows). A cut in the middle of a line splits a word, a number, a path."""
    if at >= len(text):
        return len(text)
    back = text.rfind("\n", floor, at + 1)
    return back if back > floor else at


def cut_at(text, start, limit):
    """Where a stretch of `text` that begins at `start` and holds `limit` characters at most is best ended: at the end
    of its last whole line, when that leaves it at least half as long as it may be; else `limit` characters on (one
    line longer than that, or lines so long that going back would leave little)."""
    end = min(len(text), start + max(0, limit))
    return line_end(text, end, start + max(0, limit) // 2)


def source(items, n):
    """What the n-th item of a turn is, for one who says where a stretch of its text belongs: {"kind": "asked",
    "said", "result" or "tool"; "tool": the tool's name (of a result: the tool call it follows, None when none
    comes before it); "last": it is the last thing said in the turn (the reply itself, not words on the way)}."""
    kind = items[n][0] if 0 <= n < len(items) and isinstance(items[n], (list, tuple)) and items[n] else None
    tool = None
    if kind == "tool" and len(items[n]) >= 2:
        tool = items[n][1]
    elif kind == "result":
        tool = next((it[1] for it in reversed(items[:n]) if isinstance(it, (list, tuple)) and len(it) >= 2 and it[0] == "tool"), None)
    last = kind == "said" and not any(isinstance(it, (list, tuple)) and it and it[0] == "said" for it in items[n + 1:])
    return {"kind": kind, "tool": tool if isinstance(tool, str) else None, "last": last}


def piece(items, n, field, offset, limit):
    """A stretch of one field's whole text (whole()), from `offset`, `limit` characters at most: {"text", "total":
    how long the whole is, "next": where the stretch ends (the next begins there), "prose": is_prose(), "fence":
    the code fence open where the stretch begins (fence_at(); None for what is no prose), "open": the one open
    where it ends, "of": what the item is (source())}. None when there is no such field. Offsets count the
    characters of the whole text (ends trimmed), as Python counts them; a stretch ends at the end of a line when
    that does not make it less than half as long (cut_at())."""
    text = whole(items, n, field)
    if text is None or not isinstance(offset, int) or isinstance(offset, bool) or not isinstance(limit, int) or isinstance(limit, bool):
        return None
    offset = max(0, min(offset, len(text)))
    prose = is_prose(items, n)
    end = cut_at(text, offset, limit)
    return {"text": text[offset:end], "total": len(text), "next": end, "prose": prose, "of": source(items, n),
            "fence": fence_at(text, offset) if prose else None, "open": fence_at(text, end) if prose else None}


def slim(items, result=SLIM_RESULT, total=SLIM_TOTAL):
    """A turn (what turn() gave) made small enough to send to another machine, still as data: each tool's output as
    its text cut to `result` characters, with how long it was ({"content", "is_error", "chars"}); and when the
    whole would pass `total` characters as JSON, the items from there on left out and counted: a last item
    ("more", how many). What was asked, what was said and the tool calls stay as they are."""
    out, used = [], 0
    for n, item in enumerate(items):
        if item[0] == "result":
            text = result_text(item[1])
            item = ("result", {"content": text[:result], "is_error": bool(isinstance(item[1], dict) and item[1].get("is_error")),
                               "chars": len(text)})
        used += len(json.dumps(item, ensure_ascii=False))
        if used > total:
            out.append(("more", len(items) - n))
            break
        out.append(item)
    return out
