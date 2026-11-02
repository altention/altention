"""What a picture was made from (C-91): a program that draws a picture can say so, and the bot gives it back when
the owner taps 📝 on the message (dcbot/bot/sources.py).

One object, the same on both ways in:
    {"kind": "公式", "lang": "latex", "tool": "KaTeX", "source": "…"}
- `altention dc post --file x.png`: a file next to it named `x.png.source.json` (SIDECAR) holds it.
- a reply rewritten before it is posted (dcbot/bot/rewrite.py): a message's "sources", {picture path: object}.
kind and tool: a few words each, shown as they are ("<kind>，<tool> 做的"); lang: the code block's language, also the
file's extension when it is attached; source: the text, at most MAX_SOURCE bytes. All four are needed: anything else
and the picture is posted without one (checked here, for both). Standard library only, Python 3.8."""
import json
import os
import re

SIDECAR = ".source.json"
MAX_SOURCE = 64 * 1024        # bytes of UTF-8
MAX_WORDS = 40                # characters of kind and of tool
LANG = re.compile(r"[A-Za-z0-9+-]{1,20}")


def check(obj):
    """(the object as kept, None) or (None, why)."""
    if not isinstance(obj, dict):
        return None, "不是一個物件"
    out = {}
    for key in ("kind", "tool"):
        v = obj.get(key)
        if not isinstance(v, str) or not " ".join(v.split()):
            return None, "%s 要是一段字" % key
        if len(" ".join(v.split())) > MAX_WORDS:
            return None, "%s 超過 %d 字" % (key, MAX_WORDS)
        out[key] = " ".join(v.split())
    lang = obj.get("lang")
    if not isinstance(lang, str) or not LANG.fullmatch(lang):
        return None, "lang 只能是英數字、+、-（最多 20 字）"
    out["lang"] = lang
    source = obj.get("source")
    if not isinstance(source, str) or not source.strip():
        return None, "source 要是一段字"
    if len(source.encode("utf-8")) > MAX_SOURCE:
        return None, "source 超過 %d KB" % (MAX_SOURCE // 1024)
    out["source"] = source
    return out, None


def sidecar(path):
    """(object, None) for the file at `path` from its sidecar, (None, None) when it has none, (None, why) when the
    sidecar is not one (the file goes without)."""
    side = path + SIDECAR
    if not os.path.isfile(side):
        return None, None
    try:
        with open(side, "rb") as f:
            data = f.read(MAX_SOURCE * 2 + 4096)  # (JSON escapes: a source within the limit fits in this)
        obj = json.loads(data.decode("utf-8"))
    except (OSError, ValueError) as e:
        return None, "讀不懂 %s（%s）" % (os.path.basename(side), e.__class__.__name__)
    got, why = check(obj)
    return got, (why and "%s：%s" % (os.path.basename(side), why))
