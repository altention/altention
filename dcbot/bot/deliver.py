"""The one way the bot's own messages go out (C-172a P1b, instance docs/refactor/c172a-design.md).

Every send, edit and delete of a message of the bot's comes through here, in batches (P1b's four): what Discord is
handed stays exactly what it was (the arguments go through to discord.py's send()/edit() as they are; tests/
payloadlog.py holds each batch to a baseline written before it moved); what is added is that each message goes into
the ledger (bot/ledger.py) in one place. What a message is for (`intent`), whose it is (`frm`), what the bot put round
it (`framed`) and a board's or card's `key` are the caller's to say.
Retries and how a refusal is told stay where they were for now (the board's own, a lane's time limit); #audit lines
and P1c's rules come here once every send does.
"""
import io
import types

from . import ledger


def _channel_id(to, msg=None):
    got = getattr(to, "id", None)
    if got is None and msg is not None:
        got = getattr(getattr(msg, "channel", None), "id", None)
    return got


async def deliver(to, *args, intent, edit=None, frm=None, as_=None, key=None, framed=None, keep=True, raw=None, via=None,
                  raw_files=None, **kw):
    """Send to `to` (a channel or thread: `to.send(*args, **kw)`), or with `edit` (a message, or a message id of
    `to`'s) edit that message (`.edit(*args, **kw)`); noted in the ledger, unless `keep` is False: what needs no
    record (a passing message soon deleted, 處理中 and its kind, that changes every few seconds; the bot's own record,
    #audit; a backfill, a few hundred at once: #code-review on ed28927e and on 99392598, manager 11-16); the message,
    as discord.py gives it. An edit that says no `frm`, `as_`, `key` or `framed` keeps what the entry had (one that
    only changes the buttons: #code-review and #code-review-2 on 99392598, manager 11-16); `framed=()` clears it.
    `raw` (P1b batch 4: dcctl send-json and edit): a message payload as JSON, posted (or with `edit` patched) as it is
    through `via`, discord.py's HTTP client (bot.http): what Components V2 holds has no send() argument; `to` needs
    only its id; the message comes back as {id, channel}. `raw_files`: [(name, bytes)] uploaded with it (multipart:
    the payload as payload_json, files[i]; altention dc post --json --file, C-172a P1d)."""
    if raw is not None:
        return await _raw(to, raw, via, edit, intent, frm, as_, key, framed, keep, raw_files)
    if edit is not None:
        target = edit if hasattr(edit, "edit") else to.get_partial_message(edit)
        msg = await target.edit(*args, **kw)
        msg = msg if msg is not None else target
    else:
        msg = await to.send(*args, **kw)
    if keep:
        ledger.note(_channel_id(to, msg), getattr(msg, "id", None), intent, frm=frm, as_=as_, key=key, framed=framed)
    return msg


async def remove(to, message):
    """Delete a message of the bot's (`message`: a message, or a message id of `to`'s)."""
    target = message if hasattr(message, "delete") else to.get_partial_message(message)
    try:
        await target.delete()
    except Exception as e:
        if type(e).__name__ == "NotFound":  # (gone already, deleted by hand: its entry goes too; #code-review-2 on
            ledger.forget(_channel_id(to, target), getattr(target, "id", None))  # 09b745d0) raised as before
        raise
    ledger.forget(_channel_id(to, target), getattr(target, "id", message if not hasattr(message, "delete") else None))


async def _raw(to, body, via, edit, intent, frm, as_, key, framed, keep, files=None):
    from discord.http import Route
    cid = _channel_id(to)
    sent = {"json": body}
    if files:
        import json
        import discord
        uploads = [discord.File(io.BytesIO(data), filename=name) for name, data in files]
        sent = {"files": uploads, "form": [{"name": "payload_json", "value": json.dumps(body, ensure_ascii=False)}] +
                [{"name": "files[%d]" % i, "value": f.fp, "filename": f.filename, "content_type": "application/octet-stream"}
                 for i, f in enumerate(uploads)]}
    if edit is not None:
        mid = getattr(edit, "id", edit)
        got = await via.request(Route("PATCH", "/channels/{channel_id}/messages/{message_id}", channel_id=cid,
                                      message_id=mid), **sent)
    else:
        got = await via.request(Route("POST", "/channels/{channel_id}/messages", channel_id=cid), **sent)
        mid = (got or {}).get("id")
    msg = types.SimpleNamespace(id=int(mid), channel=types.SimpleNamespace(id=cid))
    if keep:
        ledger.note(cid, msg.id, intent, frm=frm, as_=as_, key=key, framed=framed)
    return msg
