"""The official plugin list (C-81 K1, docs/refactor/plugin-catalog.md): the public repositories of the altention
organisation named plugin-<name>, with a version tag and a plugin.json at it, looked for once a day and kept in
~/.local/state/altention/catalog.json; when GitHub cannot be reached, the list from last time stays.

- refresh(deadline=None, force=False): one look (update_go's plugins round calls it every round; it looks at most
  once a day, force at most once every FORCE_GAP). One API request (the organisation's repositories, with the
  ETag of last time: unchanged, only the plugins kept are looked at again, since a tag pushed alone may leave the
  list as it was); each repository's tags by `git ls-remote` and its
  plugin.json and CHANGELOG.md from raw.githubusercontent.com, neither of which counts against the API's limit (60
  an hour without a token). A plugin whose newest tag is the one already kept is not read again. No token is read.
- load(): the list as kept, or None (never fetched, or unreadable).

What is taken (all of it, else the repository is left out, and said in "left_out" when it got as far as its
plugin.json): owner altention, public, not archived or disabled, named plugin-<name> with <name> a plugin's name
(plugin.check_name), not ending in -private, not named like one of the engine's own plugins; a tag vX.Y.Z; that
tag's plugin.json readable (plugin.check) and named <name>. Nothing of a plugin is run: two files are read, as
data, from addresses made here (never from what plugin.json says), and the words in them that are shown to people
(title, pitch, description) are cut short and lose mentions and links.

Not reached (no network, too slow): the list stays, and the next look waits BACKOFF_FIRST, doubled each time, at
most BACKOFF_MAX (as plugin_update's sources). Limited (403 or 429 with no requests left): it waits until GitHub's
reset, and that is not "not reached". Nothing happens at import and HOME is read at call time. Standard library
only, Python 3.8.
"""
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.request

from . import config, plugin, plugin_update

ORG = "altention"
API = "https://api.github.com"
GIT_BASE = "https://github.com"           # where a plugin's repository is cloned from: <GIT_BASE>/<ORG>/plugin-<name>.git
RAW = "https://raw.githubusercontent.com"
DAY = 24 * 3600
FORCE_GAP = 10 * 60      # plugins.py list --catalog-now: at most once in this long
BACKOFF_FIRST = plugin_update.BACKOFF_FIRST
BACKOFF_MAX = plugin_update.BACKOFF_MAX
PAGES = 5                # 100 repositories a page
HTTP_TIMEOUT = 30
FILE_MOST = 256 * 1024   # bytes of a plugin.json or CHANGELOG.md read at most
LOOK_LEAST = 20          # seconds left before the deadline needed to look at one more repository
WORDS = {"title": 40, "pitch": 300, "description": 300}  # characters kept of the words shown to people
FORMAT = 1


class Unreached(Exception):
    """GitHub did not answer (or too slowly): the list stays and the next look backs off."""


class Limited(Exception):
    """GitHub answered that no request is left until `reset` (seconds since the epoch)."""

    def __init__(self, reset):
        super().__init__("limited until %s" % reset)
        self.reset = reset


def path():
    return os.path.join(os.path.expanduser("~/.local/state/altention"), "catalog.json")


def load():
    """The kept list ({"format", "at", "tried", "etag", "plugins": {name: {...}}, "left_out", "wait_until",
    "fails"}), or None when there is none or it cannot be read."""
    try:
        with open(path(), encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and d.get("format") == FORMAT and isinstance(d.get("plugins"), dict) else None


def _save(d):
    p = path()
    os.makedirs(os.path.dirname(p), mode=0o700, exist_ok=True)
    tmp = "%s.tmp-%d" % (p, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, p)


# ------------------------------------------------------------------------------------------ reading GitHub
def _http(url, headers, timeout=HTTP_TIMEOUT):
    """(status, headers, body bytes) of a GET; 304 and 4xx/5xx answers are answers. Unreached when it is not."""
    req = urllib.request.Request(url, headers=dict({"User-Agent": "altention-catalog"}, **headers))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read(FILE_MOST + 1)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), b""
    except (urllib.error.URLError, OSError, ValueError) as e:  # no network, refused, too slow, a bad answer
        raise Unreached(str(getattr(e, "reason", e))[:200])


def _limited(status, headers):
    h = {k.lower(): v for k, v in headers.items()}
    if status in (403, 429) and str(h.get("x-ratelimit-remaining", "")) == "0":
        try:
            return float(h.get("x-ratelimit-reset"))
        except (TypeError, ValueError):
            return time.time() + 3600
    if status == 429:
        return time.time() + 3600
    return None


def _repos(etag):
    """(repositories or None when unchanged since `etag`, etag): the organisation's public repositories."""
    url = "%s/orgs/%s/repos?type=public&per_page=100" % (API, ORG)
    out, first_etag = [], None
    for page in range(PAGES):
        headers = {"Accept": "application/vnd.github+json"}
        if page == 0 and etag:
            headers["If-None-Match"] = etag
        status, h, body = _http(url, headers)
        reset = _limited(status, h)
        if reset is not None:
            raise Limited(reset)
        if status == 304 and page == 0:
            return None, etag
        if status != 200:
            raise Unreached("GitHub 回 %s" % status)
        try:
            got = json.loads(body.decode("utf-8"))
        except ValueError:
            raise Unreached("GitHub 的回應讀不懂")
        if not isinstance(got, list):
            raise Unreached("GitHub 的回應不是清單")
        out += [r for r in got if isinstance(r, dict)]
        if page == 0:
            first_etag = {k.lower(): v for k, v in h.items()}.get("etag")
        nxt = re.search(r'<([^>]+)>;\s*rel="next"', {k.lower(): v for k, v in h.items()}.get("link", ""))
        if not nxt:
            break
        url = nxt.group(1)
    return out, first_etag


def builtin_names():
    """The names of the engine's own plugins (and the core's): a catalog entry may not take one."""
    names = {"core"}
    for p in plugin.candidates():
        if plugin.external(os.path.dirname(p)):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                n = json.load(f).get("name")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(n, str):
            names.add(n)
    return names


def wanted(r, builtin):
    """<name> when repository `r` (one row of the API's list) may be in the list, else None."""
    if not isinstance(r.get("name"), str) or not isinstance(r.get("owner"), dict):
        return None
    if str(r["owner"].get("login", "")).lower() != ORG or r.get("private") is not False:
        return None
    if r.get("archived") or r.get("disabled"):
        return None
    m = re.match(r"^plugin-(.+)$", r["name"])
    if not m or m.group(1).endswith("-private") or not plugin.check_name(m.group(1)) or m.group(1) in builtin:
        return None
    return m.group(1)


def repo_url(name):
    return "%s/%s/plugin-%s.git" % (GIT_BASE, ORG, name)


def tags(name, deadline=None):
    """plugin-<name>'s vX.Y.Z tags by `git ls-remote` (no API request), newest first; [] when it has none or is not
    there; Unreached when it could not be read for the network."""
    r = plugin_update._git(tempfile.gettempdir(), "ls-remote", "--tags", "--refs", repo_url(name),
                           timeout=max(5, plugin_update._left(deadline, plugin_update.FETCH_TIMEOUT)))
    if r.returncode != 0:
        if plugin_update._network(r):
            raise Unreached(plugin_update._said(r))
        return []
    found = [x.split("\t")[1][len("refs/tags/"):] for x in r.stdout.splitlines() if "\trefs/tags/" in x]
    return sorted((t for t in found if plugin_update._tag_key(t)), key=plugin_update._tag_key, reverse=True)


def newest_tag(name, deadline=None):
    """The newest vX.Y.Z of plugin-<name> (tags()), None when it has none."""
    got = tags(name, deadline)
    return got[0] if got else None


def _file(name, tag, rel):
    """The text of `rel` at plugin-<name>'s `tag`, None when it is not there (404) or too big; Unreached for any
    other answer (5xx, 429…: a passing fault, not a plugin to leave out, #code-review of d12afd0)."""
    status, h, body = _http("%s/%s/plugin-%s/%s/%s" % (RAW, ORG, name, tag, rel), {})
    if status == 404:
        return None
    if status != 200:
        raise Unreached("raw.githubusercontent.com 回 %s" % status)
    if len(body) > FILE_MOST:
        return None
    return body.decode("utf-8", "replace")


MENTION = re.compile(r"@(everyone|here)\b|<@[!&]?\d+>|<#\d+>")
LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")


def plain(text, most):
    """Words from a plugin.json made safe to show: one line, no mentions, links as their text, `most` characters."""
    text = " ".join(MENTION.sub("", LINK.sub(r"\1", str(text))).split())
    return text if len(text) <= most else text[:most - 1].rstrip() + "…"


NOT_THERE = "沒有 plugin.json（或太大）"  # left out with nothing read: looked for again next time


def entry(name, tag):
    """({repo, version, plugin, points}, None) for plugin-<name> at `tag`, or (None, why it is left out); `why`
    ends with NOT_THERE when there was none to read (refresh() looks again), else it was read and not taken."""
    text = _file(name, tag, "plugin.json")
    if text is None:
        return None, "%s %s" % (tag, NOT_THERE)
    try:
        d = plugin.check(json.loads(text), "plugin-%s:%s:plugin.json" % (name, tag))
    except (ValueError, plugin.PluginError) as e:
        return None, "%s 的 plugin.json 讀不懂：%s" % (tag, str(e)[:200])
    if d.get("name") != name:
        return None, "%s 的 plugin.json 的 name 是 %r，不是 %r" % (tag, d.get("name"), name)
    for key, most in WORDS.items():
        if isinstance(d.get(key), str):
            d[key] = plain(d[key], most)
    points = []
    notes = _file(name, tag, "CHANGELOG.md")
    if notes:
        from . import update
        got = update.release_notes(None, tag, text=notes, most=3)
        points = [plain(x, 160) for x in got[0]] if got else []
    return {"repo": repo_url(name), "version": tag, "plugin": d, "points": points}, None


# ------------------------------------------------------------------------------------------ one look
def due(d, now, force=False):
    """Whether a look is due: not while backing off or limited; forced, at most once every FORCE_GAP; else once a
    day after the last one that got through."""
    if d and now < float(d.get("wait_until") or 0):
        return False
    if force:
        return not d or now - float(d.get("tried") or 0) >= FORCE_GAP
    return not d or now - float(d.get("at") or 0) >= DAY


def refresh(deadline=None, force=False, now=None):
    """Look once when due (due()): (state, words). state "fresh" (looked), "same" (nothing changed), "kept" (not
    due: the list as it was), "unreached" (kept, backing off), "limited" (kept, until GitHub's reset), "partial" (the
    deadline came: looked at some, the rest next time). Never raises for GitHub's sake."""
    now = time.time() if now is None else now
    old = load()
    if not due(old, now, force):
        return "kept", ""
    d = dict(old or {"format": FORMAT, "plugins": {}, "left_out": []})
    d["tried"] = now
    try:
        repos, etag = _repos(d.get("etag"))
        was_out = dict((x.get("repo"), x) for x in d.get("left_out") or [] if isinstance(x, dict))
        if repos is None:  # the organisation's list is the same; a tag pushed alone may not change it (#code-review)
            names = sorted(set(d["plugins"]) | set(r[len("plugin-"):] for r in was_out if str(r).startswith("plugin-")))
        else:
            builtin = builtin_names()
            names = sorted(n for n in (wanted(r, builtin) for r in repos) if n)
        plugins, left_out, partial = {}, [], False
        for name in names:
            if deadline is not None and deadline - time.time() < LOOK_LEAST:
                partial = True
                if name in d["plugins"]:
                    plugins[name] = d["plugins"][name]
                elif "plugin-%s" % name in was_out:
                    left_out.append(was_out["plugin-%s" % name])
                continue
            tag = newest_tag(name, deadline)
            if tag is None:
                continue
            kept = d["plugins"].get(name)
            if kept and kept.get("version") == tag:
                plugins[name] = kept
                continue
            out_before = was_out.get("plugin-%s" % name)
            if out_before and out_before.get("version") == tag and out_before.get("read"):
                # read and not taken at this tag already: not read again (one not there is looked for again)
                left_out.append(out_before)
                continue
            got, why = entry(name, tag)
            if got:
                plugins[name] = got
            else:
                left_out.append({"repo": "plugin-%s" % name, "version": tag, "why": why,
                                 "read": not why.endswith(NOT_THERE)})
    except Limited as e:
        d["wait_until"] = e.reset
        _save(d)
        return "limited", "GitHub 限流，%s 之後再看官方插件清單" % time.strftime("%H:%M", time.localtime(e.reset))
    except Unreached as e:
        fails = int(d.get("fails") or 0) + 1
        d.update(fails=fails, wait_until=now + min(BACKOFF_MAX, BACKOFF_FIRST * 2 ** (fails - 1)))
        _save(d)
        return "unreached", "連不上 GitHub，官方插件清單用上次的（%s）" % e
    same = repos is None and plugins == d["plugins"] and left_out == list(was_out.values())
    d.update(plugins=plugins, left_out=left_out, fails=0, wait_until=0)
    if same and not partial:
        d.update(at=now)
        _save(d)
        return "same", "官方插件清單沒有變"
    if partial:  # the rest next round: neither the day's mark nor the ETag, so all of it is looked at again
        d.pop("etag", None)
    else:
        d.update(at=now, etag=etag)
    _save(d)
    return ("partial" if partial else "fresh"), "官方插件清單：%d 個%s" % (
        len(plugins), "（時間到了，其他的下一輪看）" if partial else "")


# ------------------------------------------------------------------------------------------ told in #update
def told_path():
    return os.path.join(os.path.expanduser("~/.local/state/altention"), "catalog-told.json")


def _told():
    try:
        with open(told_path(), encoding="utf-8") as f:
            told = json.load(f)
        return set(told) if isinstance(told, list) else None
    except (OSError, ValueError):
        return None


def mark_told(names):
    """`names` written down as told in #update (with those before)."""
    os.makedirs(os.path.dirname(told_path()), mode=0o700, exist_ok=True)
    tmp = "%s.tmp-%d" % (told_path(), os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(sorted(set(names) | (_told() or set())), f)
    os.replace(tmp, told_path())


def news(named=()):
    """[(name, entry)] of the list's plugins not told in #update yet, the instance not naming them (`named`); the
    caller marks them told once said (mark_told). C-81: a new plugin is not asked about, only said once (owner
    2026-10-21). The first time (nothing told before) every plugin there is marked without being said: an
    installation's own list is what #setup offers when it is set up. [] when there is no list."""
    d = load()
    if d is None:
        return []
    told = _told()
    if told is None:
        mark_told(d["plugins"])
        return []
    named = set(named) & (set(d["plugins"]) - told)
    if named:  # the instance has it already: nothing to tell, now or later
        mark_told(named)
    return [(n, d["plugins"][n]) for n in sorted(set(d["plugins"]) - told - named)]


def news_text(new):
    """The #update line for news(): each plugin's title and pitch, and how to put it on."""
    return "🧩 官方插件清單多了：%s。要裝的話，到 #setup 跟 Claude 說%s。" % ("；".join(
        "%s（%s）%s" % (e["plugin"].get("title", n), n, "：" + e["plugin"]["pitch"].rstrip("。") if e["plugin"].get("pitch") else "")
        for n, e in new), "「裝 %s」" % new[0][0] if len(new) == 1 else "「裝」加上括號裡的名稱")

