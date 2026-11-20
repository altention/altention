"""The checks ops/release.py adds before a release (instance docs/refactor/release-checklist.md, part A and E): the
CHANGELOG's shape (A7), golden cases changed on purpose (A8), the whole test suite on the candidate's tree (A9), the
version line (A10), every commit reviewed (A12), no conflict marker (A13), skill and agent files' heads (A14), every
command in the guides (A15), the official plugins load (A16) and the checklist of the release (A17).

Each check takes what it needs and appends one line per reason to `said` (refused) or `notes` (only said), as the
rest of release.py does; release.run() calls them. Nothing here pushes or changes a repository; the test run works in
an exported copy of the tree with a throwaway HOME. Standard library only, Python 3.8.
"""
import datetime
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from altention import config, text as alt_text  # noqa: E402

TAG = re.compile(r"^v([0-9]+)\.([0-9]+)\.([0-9]+)$")
PARTS = ("for people", "for people (zh-tw)", "for agents")  # A7: the three parts every version has
ZH_MOST = 6
B_ITEMS = ["B%d" % i for i in range(1, 13)]
LATE = ("B11", "B12")  # may still be open on a check or a run without --push (A17)
SUITE_LIMIT = 30 * 60  # seconds one suite may run (A9)
ENGINE_SUITES = ("altention", "chatlog", "bin", "ops", "tools", "progress")  # dcbot/deploy.sh's loop, kept equal by a test
EXEMPT = "docs/release-guide-exempt.json"


def t(_key, **kw):
    return alt_text.t("release." + _key, **kw)


def tag_key(tag):
    m = TAG.match(tag)
    return tuple(int(x) for x in m.groups()) if m else None


# ------------------------------------------------------------------------------------------- the version
def first_unpublished(git, rev, published):
    """The version of the first "## vX.Y.Z" heading in REV's CHANGELOG.md with no public tag yet, or None."""
    p = git("show", "%s:CHANGELOG.md" % rev)
    if p.returncode != 0:
        return None
    for line in p.stdout.splitlines():
        m = re.match(r"^##\s+v([0-9]+\.[0-9]+\.[0-9]+)(\s|$)", line)
        if m and "v" + m.group(1) not in published:
            return m.group(1)
    return None


def last_tag(git, commit, version):
    """The newest private vX.Y.Z tag that is in COMMIT's history (the last release before this one), or None. The
    tag of VERSION itself (a run again after it was pushed) is not taken."""
    best = None
    for tg in git("tag", "-l", "v*").stdout.split():
        key = tag_key(tg)
        if key is None or tg == "v%s" % version:
            continue
        if git("merge-base", "--is-ancestor", tg, commit).returncode == 0 and (best is None or key > tag_key(best)):
            best = tg
    return best


# ------------------------------------------------------------------------------------------- the checklist (A17)
def checklist_path(version):
    return os.path.join(config.instance_dir(), "docs", "refactor", "releases", "v%s-checklist.md" % version)


# The placeholders the template (release-checklist.md, section F) puts in a B line. Only these mean "not filled in":
# a proof may well hold other <...> text, such as Discord's <@...> or <#...> (v0.5.4 B4).
TEMPLATE_PLACEHOLDERS = ("<寫在這裡>", "<連結或 hash>")


def read_checklist(version):
    """{"path", "hash", "golden_intended", "reviewed_exceptions", "branch", "items": {B1: (ticked, proof)}} of the
    release's checklist, or None when it is not there. Lines it does not know are left alone."""
    path = checklist_path(version)
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    out = {"path": path, "hash": None, "golden_intended": None, "reviewed_exceptions": None, "branch": None,
           "items": {}}
    first = next((x.strip() for x in lines if x.strip()), "")
    m = re.match(r"^hash:\s*(\S*)\s*$", first)
    out["hash"] = m.group(1) if m else None
    for line in lines:
        m = re.match(r"^(golden_intended|reviewed_exceptions|branch):\s*(.*?)\s*$", line.strip())
        if m:
            out[m.group(1)] = m.group(2)
            continue
        m = re.match(r"^-\s*\[([ xX])\]\s*(B[0-9]+)\b(.*)$", line.strip())
        if m and m.group(2) not in out["items"]:
            rest = m.group(3)
            proof = rest.split("證明：", 1)[1].strip() if "證明：" in rest else ""
            todo = any(x in rest for x in TEMPLATE_PLACEHOLDERS)
            out["items"][m.group(2)] = (m.group(1) in "xX", "" if todo else proof)
    return out


def words(value):
    """The entries of a checklist line (comma, 、, or spaces between); none for 無 or nothing."""
    value = (value or "").strip()
    if value in ("", "無", "none", "-"):
        return []
    return [w for w in re.split(r"[\s,、，;；（）()]+", value) if w]


def hashes(value):
    """The commit hashes (7 to 40 hex digits) written in a checklist line, whatever words stand around them (a reason
    in brackets right after one, as the manager writes them)."""
    return [h.lower() for h in re.findall(r"(?<![0-9a-fA-F])[0-9a-fA-F]{7,40}(?![0-9a-fA-F])", value or "")]


def checklist(record, commit, push, said):
    """A17: the checklist is there, names COMMIT, and has every B item ticked with its proof (B11, B12 may wait
    unless pushing)."""
    if record is None:
        return
    if not record["hash"]:
        said.append(t("checklist_no_hash", path=record["path"]))
    elif len(record["hash"]) < 7 or not commit.startswith(record["hash"].lower()):
        said.append(t("checklist_other_hash", path=record["path"], hash=record["hash"], commit=commit[:12]))
    open_ = []
    for b in B_ITEMS:
        ticked, proof = record["items"].get(b, (False, ""))
        if (not ticked or not proof) and (push or b not in LATE):
            open_.append(b)
    if open_:
        said.append(t("checklist_open", items="、".join(open_), path=record["path"]))


# ------------------------------------------------------------------------------------------- CHANGELOG (A7)
def sections(textual):
    """[(version or "Unreleased", heading line, body)] of a CHANGELOG, in order."""
    out, cur = [], None
    for line in textual.splitlines():
        if line.startswith("## "):
            m = re.match(r"^##\s+v([0-9]+\.[0-9]+\.[0-9]+)(\s|$)", line)
            name = m.group(1) if m else ("Unreleased" if line[3:].strip().lower() == "unreleased" else line[3:].strip())
            cur = [name, line, []]
            out.append(cur)
        elif cur is not None:
            cur[2].append(line)
    return [(n, h, "\n".join(b).strip()) for n, h, b in out]


def changelog_shape(git, rev, version, last, push, said, notes, today=None):
    """A7: the "## vVERSION" section has the three parts, at most six one-line zh-TW points, today's date (only
    refused when pushing), "## Unreleased" (if there) above it, and no older version's section changed since LAST."""
    p = git("show", "%s:CHANGELOG.md" % rev)
    if p.returncode != 0:
        return  # release.changelog() says it
    found = sections(p.stdout)
    names = [n for n, _h, _b in found]
    if version not in names:
        return  # release.changelog() says it
    i = names.index(version)
    _n, head, body = found[i]
    parts, part, zh, zh_wrapped = set(), None, 0, 0
    for line in body.splitlines():
        if line.startswith("### "):
            part = line[4:].strip().lower()
            parts.add(part)
        elif part == "for people (zh-tw)":
            if line.startswith("- "):
                zh += 1
            elif line.strip():
                zh_wrapped += 1
    missing = [x for x in ("For people", "For people (zh-TW)", "For agents") if x.lower() not in parts]
    if missing:
        said.append(t("changelog_parts", version=version, parts="、".join(missing)))
    if zh > ZH_MOST:
        said.append(t("changelog_zh_many", version=version, n=zh, most=ZH_MOST))
    if zh_wrapped:
        said.append(t("changelog_zh_wrapped", version=version, n=zh_wrapped))
    m = re.search(r"([0-9]{4}-[0-9]{2}-[0-9]{2})\s*$", head)
    today = today or local_today()
    if not m:
        said.append(t("changelog_no_date", version=version))
    elif today and m.group(1) != today:
        (said if push else notes).append(t("changelog_date", version=version, date=m.group(1), today=today))
    if "Unreleased" in names and names.index("Unreleased") > i:
        said.append(t("changelog_unreleased_below", version=version))
    if last:
        old = git("show", "%s:CHANGELOG.md" % last)
        if old.returncode == 0:
            now = dict((n, (h, b)) for n, h, b in found)
            changed = [n for n, h, b in sections(old.stdout) if TAG.match("v" + n) and now.get(n) != (h, b)]
            if changed:
                said.append(t("changelog_old_changed", tag=last, versions="、".join("v" + n for n in changed)))


def local_today():
    """Today's date (YYYY-MM-DD) in the instance's time zone (core.timezone), None when it cannot be told."""
    try:
        from altention import scheduler
        zone, _src, _problem = scheduler.timezone()
        if zone is None:
            return None
        return zone.local(time.time()).strftime("%Y-%m-%d")
    except Exception:
        return None


# ------------------------------------------------------------------------------------------- golden (A8)
def golden(changed_cases, last, record, said):
    """A8: every golden case changed since LAST is named in the checklist's golden_intended."""
    if not changed_cases:
        return
    meant = set(words(record["golden_intended"])) if record else set()
    left = [c for c in changed_cases if c not in meant]
    if left:
        said.append(t("golden_unmeant", tag=last, cases="、".join(left)))


# ------------------------------------------------------------------------------------------- version line (A10)
def version_line(git, commit, version, record, remote, said):
    """A10: COMMIT is on the checklist's branch as the remote has it, and a fix release (Z > 0) holds the line's
    previous private tag."""
    key = tag_key("v" + version)
    if key and key[2] > 0:
        before = sorted((tg for tg in git("tag", "-l", "v%d.%d.*" % key[:2]).stdout.split()
                         if tag_key(tg) and tag_key(tg)[:2] == key[:2] and tag_key(tg)[2] < key[2]), key=tag_key)
        if not before:
            said.append(t("line_no_previous", version=version, line="v%d.%d" % key[:2]))
        elif git("merge-base", "--is-ancestor", before[-1], commit).returncode != 0:
            said.append(t("line_previous_missing", version=version, tag=before[-1]))
    if record is None:
        return
    branch = (record["branch"] or "").strip()
    if not branch:
        said.append(t("line_no_branch", path=record["path"]))
        return
    p = git("ls-remote", "--heads", remote, "refs/heads/%s" % branch)
    tip = p.stdout.split("\t")[0].strip() if p.returncode == 0 and p.stdout.strip() else ""
    if not tip:
        said.append(t("line_branch_missing", remote=remote, branch=branch))
        return
    if git("cat-file", "-e", tip + "^{commit}").returncode != 0:
        git("fetch", "-q", remote, "refs/heads/%s" % branch)
    if git("merge-base", "--is-ancestor", commit, tip).returncode != 0:
        said.append(t("line_not_on_branch", commit=commit[:12], branch=branch, remote=remote, tip=tip[:12]))


# ------------------------------------------------------------------------------------------- reviewed (A12)
SQUASHED = re.compile(r"^release: merge \S+ ([0-9a-f]{7,40})\b")  # a release merge's branch and hash, written by hand
REVIEWED = re.compile(r"\(reviewed ([^)]*)\)")  # deploy_candidate.prepare(): "(reviewed h1, h2; h3 lint only)"
HEX = re.compile(r"\b[0-9a-f]{7,40}\b")


def named_reviewed(message):
    """The hashes a commit message names as reviewed: in each "(reviewed …)", anywhere in it (the subject or a line
    after it; C-206 low), and after "release: merge <branch>" at its start."""
    out = [h for group in REVIEWED.findall(message) for h in HEX.findall(group)]
    m = SQUASHED.match(message)
    return out + ([m.group(1)] if m else [])


def reviewed(git, commit, last, record, said):
    """A12: every non-merge commit in LAST..COMMIT came in through one of L3's "release: merge" commits (it is in
    that merge's second parent and not in its first), or is one itself made by `deploy.sh --commit` (one parent, its
    tree the same as that of a hash its message names as reviewed: "(reviewed h1, h2; h3 lint only)", which
    deploy_candidate.prepare() appends, or "release: merge <branch> <hash>": C-206); the others must be in the
    checklist's reviewed_exceptions."""
    if not last:
        return
    span = "%s..%s" % (last, commit)
    commits = git("rev-list", "--no-merges", span).stdout.split()
    if not commits:
        return
    covered = set()
    for row in git("log", "--merges", "--format=%H%x01%P%x01%s", span).stdout.splitlines():
        parts = row.split("\x01")
        if len(parts) != 3 or not parts[2].startswith("release: merge"):
            continue
        parents = parts[1].split()
        if len(parents) >= 2:
            covered.update(git("rev-list", parents[1], "--not", parents[0]).stdout.split())
    for row in git("log", "--no-merges", "--format=%H%x01%T%x01%B%x00", span).stdout.split("\x00"):
        parts = row.strip("\n").split("\x01")  # (the whole message: "(reviewed …)" may be on a line of its own)
        if len(parts) != 3:
            continue
        for h in named_reviewed(parts[2]):
            p = git("rev-parse", "-q", "--verify", "%s^{tree}" % h)
            if p.returncode == 0 and p.stdout.strip() == parts[1]:
                covered.add(parts[0])
                break
    allowed = hashes(record["reviewed_exceptions"]) if record else []
    left = [c for c in commits if c not in covered and not any(len(a) >= 7 and c.startswith(a) for a in allowed)]
    if left:
        shown = ["%s %s" % (c[:12], git("log", "-1", "--format=%s", c).stdout.strip()[:60]) for c in left[:8]]
        said.append(t("unreviewed", n=len(left), tag=last, commits="；".join(shown)))


# ------------------------------------------------------------------------------------------- conflict markers (A13)
MARKER = re.compile(r"^(<{7}|>{7})( |$)|^={7}$")


def markers(git, rev, said):
    """A13: no line starting with seven < or > (then a space or the end), nor a line of just =======, outside a .md
    file's heading underline."""
    p = git("grep", "-I", "-n", "-E", "^(<{7}|>{7})( |$)|^={7}$", rev, "--", ".")
    if p.returncode not in (0, 1):
        said.append(t("markers_failed", error=p.stderr.strip()[:200]))
        return
    found = []
    for row in p.stdout.splitlines():
        parts = row.split(":", 3)  # rev:file:line:text
        if len(parts) < 4 or not MARKER.match(parts[3]):
            continue
        if parts[3] == "=" * 7 and parts[1].endswith(".md"):
            continue  # a heading's underline
        found.append("%s:%s" % (parts[1], parts[2]))
    if found:
        said.append(t("markers", n=len(found), where="、".join(found[:8])))


# ------------------------------------------------------------------------------------------- skill heads (A14)
HEAD_FILE = re.compile(r"(^|/)SKILL\.md$|(^|/)agents/[^/]+\.md$")


def front_problem(textual):
    """Why the head (front matter) of a skill or agent file would not read as YAML with its description whole, or
    "". Read by the rules that matter here (stdlib has no YAML): `key: value` lines, a plain value that " #" cuts,
    a quoted value that must close, block values (| or >) and their indented lines; an empty value whose indented
    lines are `key: ` or `- ` is a nested mapping or list, left alone."""
    lines = textual.splitlines()
    if not lines or lines[0].strip() != "---":
        return t("front_none")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() in ("---", "..."))
    except StopIteration:
        return t("front_unclosed")
    keys, body, i = {}, lines[1:end], 0
    while i < len(body):
        line = body[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        m = re.match(r"^([A-Za-z0-9_-]+):(?:\s+(.*))?$", line)
        if not m:
            return t("front_line", line=line.strip()[:60])
        key, value = m.group(1), (m.group(2) or "").rstrip()
        i += 1
        more = []
        while i < len(body) and (body[i].startswith((" ", "\t")) or not body[i].strip()):
            more.append(body[i])
            i += 1
        inner = [x.strip() for x in more if x.strip()]
        if not value and inner and (re.match(r"^[A-Za-z0-9_-]+:(\s|$)", inner[0]) or inner[0] == "-"
                                    or inner[0].startswith("- ")):
            keys[key] = ""  # a nested mapping or list (metadata:, allowed-tools:): YAML's own, not one plain value
        elif value[:1] in ("|", ">"):
            keys[key] = " ".join(x.strip() for x in more if x.strip())
        elif value[:1] in ('"', "'"):
            whole = " ".join([value] + [x.strip() for x in more if x.strip()])
            end_at = _closing(whole, whole[0])
            if end_at < 0:
                return t("front_quote", field=key)
            rest = whole[end_at:].strip()
            if rest and not rest.startswith("#"):
                return t("front_after_quote", field=key)
            keys[key] = whole[1:end_at - 1]
        else:
            whole = " ".join([value] + [x.strip() for x in more if x.strip()])
            if re.search(r"\s#", whole):
                return t("front_cut", field=key, kept=re.split(r"\s#", whole, 1)[0][-40:])
            if re.search(r":\s", whole):
                return t("front_colon", field=key)
            keys[key] = whole
    if not keys.get("description", "").strip():
        return t("front_no_description")
    return ""


def _closing(s, q):
    """The index just after the quote closing the quoted value that starts S (with Q), or -1: in double quotes a
    backslash escapes the next character, in single quotes '' is one quote."""
    i = 1
    while i < len(s):
        c = s[i]
        if q == '"' and c == "\\":
            i += 2
            continue
        if c == q:
            if q == "'" and s[i + 1:i + 2] == "'":
                i += 2
                continue
            return i + 1
        i += 1
    return -1


def heads(git, rev, said):
    """A14: every SKILL.md and agents/*.md in REV's tree has a head that reads, with a whole description."""
    names = [n for n in git("ls-tree", "-r", "--name-only", rev).stdout.splitlines() if HEAD_FILE.search(n)]
    for name in names:
        p = git("show", "%s:%s" % (rev, name))
        why = front_problem(p.stdout) if p.returncode == 0 else t("front_unreadable")
        if why:
            said.append(t("front", path=name, why=why))


# ------------------------------------------------------------------------------------------- commands in the guides (A15)
def _ast_of(git, rev, path):
    import ast
    p = git("show", "%s:%s" % (rev, path))
    if p.returncode != 0:
        return None
    try:
        return ast.parse(p.stdout)
    except SyntaxError:
        return None


def _assigned(tree, name):
    import ast
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(x, ast.Name) and x.id == name for x in node.targets):
            return node.value
    return None


def commands(git, rev):
    """({"!x", …}, {"/x", …}, {"altention x", "altention dc x", …}) as REV's code has them: the bot's BUILTINS,
    every tree.command(name="…") and PLAIN key of its slash commands, bin/altention's TOP and DC; None for a part
    that could not be read."""
    import ast
    bang = slash = sub = None
    tree = _ast_of(git, rev, "dcbot/bot/main.py")
    node = _assigned(tree, "BUILTINS") if tree is not None else None
    if isinstance(node, (ast.Tuple, ast.List)):
        bang = set(e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str))
    tree = _ast_of(git, rev, "dcbot/bot/slash.py")
    if tree is not None:
        slash = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "command":
                slash.update("/" + k.value.value for k in n.keywords
                             if k.arg == "name" and isinstance(k.value, ast.Constant) and isinstance(k.value.value, str))
        plain = _assigned(tree, "PLAIN")
        if isinstance(plain, ast.Dict):
            slash.update("/" + k.value for k in plain.keys if isinstance(k, ast.Constant) and isinstance(k.value, str))
    tree = _ast_of(git, rev, "bin/altention")
    if tree is not None:
        sub = set()
        for name, prefix in (("TOP", "altention "), ("DC", "altention dc ")):
            d = _assigned(tree, name)
            if isinstance(d, ast.Dict):
                sub.update(prefix + k.value for k in d.keys if isinstance(k, ast.Constant) and isinstance(k.value, str))
    return bang, slash, sub


def mentioned(doc, word):
    return re.search(r"(?<![\w!/-])" + re.escape(word) + r"(?![\w-])", doc) is not None


def guides(git, rev, said):
    """A15: every ! and slash command is in dcbot/GUIDE.md and dcbot/docs/usage.md, every altention subcommand in
    usage.md, but for those listed in docs/release-guide-exempt.json ({"guide": {name: why}, "usage": {name: why}})."""
    bang, slash, sub = commands(git, rev)
    for part, what in ((bang, "dcbot/bot/main.py BUILTINS"), (slash, "dcbot/bot/slash.py"), (sub, "bin/altention")):
        if part is None:
            said.append(t("guide_unreadable", what=what))
    docs = {}
    for name in ("dcbot/GUIDE.md", "dcbot/docs/usage.md"):
        p = git("show", "%s:%s" % (rev, name))
        docs[name] = p.stdout if p.returncode == 0 else None
        if p.returncode != 0:
            said.append(t("guide_doc_missing", path=name))
    exempt = {"guide": {}, "usage": {}}
    p = git("show", "%s:%s" % (rev, EXEMPT))
    if p.returncode == 0:
        try:
            got = json.loads(p.stdout)
            if not isinstance(got, dict) or not all(isinstance(got.get(k, {}), dict) for k in ("guide", "usage")):
                raise ValueError("guide/usage")
            exempt = {k: got.get(k, {}) for k in ("guide", "usage")}
        except ValueError as e:
            said.append(t("guide_exempt_bad", path=EXEMPT, error=str(e)[:100]))
    lacking = []
    for word in sorted((bang or set()) | (slash or set())):
        for name, key in (("dcbot/GUIDE.md", "guide"), ("dcbot/docs/usage.md", "usage")):
            if docs[name] is not None and word not in exempt[key] and not mentioned(docs[name], word):
                lacking.append("%s（%s）" % (word, os.path.basename(name)))
    usage = docs["dcbot/docs/usage.md"]
    for word in sorted(sub or set()):
        if usage is not None and word not in exempt["usage"] and word not in usage:
            lacking.append("%s（usage.md）" % word)
    if lacking:
        said.append(t("guide_lacking", n=len(lacking), items="、".join(lacking), exempt=EXEMPT))


# ------------------------------------------------------------------------------------------- official plugins (A16)
def official_plugins():
    """[(name, tag, plugin.json text)] of the official list read fresh (C-81's catalog: the organisation's public
    plugin-<name> repositories, each one's newest vX.Y.Z), with no login. RuntimeError when it cannot be read."""
    from altention import catalog
    try:
        rows, _etag = catalog._repos(None)
        builtin = catalog.builtin_names()
        out = []
        for r in rows or []:
            name = catalog.wanted(r, builtin)
            if not name:
                continue
            tag = catalog.newest_tag(name)
            if not tag:
                continue
            out.append((name, tag, catalog._file(name, tag, "plugin.json")))
        return out
    except (catalog.Unreached, catalog.Limited) as e:
        raise RuntimeError(str(e)[:200])


PROBE = r'''
import json, sys
root = sys.argv[1]
sys.path.insert(0, root)
from altention import plugin
rows = json.load(sys.stdin)
core = plugin.interfaces(engine=root)
read, out = {}, {}
for name, tag, text in rows:
    try:
        d = plugin.check(json.loads(text), "plugin-%s:%s:plugin.json" % (name, tag))
        if d.get("name") != name:
            raise plugin.PluginError("name %r" % d.get("name"))
        read[name] = d
    except Exception as e:
        out[name] = "%s: %s" % (tag, str(e)[:200])
offered = dict(core)
for name, d in read.items():
    for n, e in (d.get("interfaces") or {}).items():
        offered.setdefault(n, dict(e, provider=name))
for name, d in read.items():
    problem = plugin.uses_problem(d, offered)
    if problem:
        out[name] = problem
print(json.dumps(out, ensure_ascii=False))
'''


def plugins_load(root, said, rows=None):
    """A16: each official plugin's newest public plugin.json reads in the candidate's engine (exported at ROOT), and
    the interface versions its "uses" asks are there. No network: refused, with why."""
    try:
        rows = official_plugins() if rows is None else rows
    except RuntimeError as e:
        said.append(t("plugins_unreached", error=e))
        return
    gone = [n for n, _tg, text in rows if text is None]
    for name in gone:
        said.append(t("plugin_no_json", name=name))
    rows = [r for r in rows if r[2] is not None]
    if not rows:
        return
    p = subprocess.run([sys.executable, "-I", "-c", PROBE, root], input=json.dumps(rows), stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, universal_newlines=True, timeout=120)
    try:
        bad = json.loads(p.stdout)
    except ValueError:
        said.append(t("plugins_probe_failed", error=(p.stderr.strip() or p.stdout.strip())[-300:]))
        return
    tags = dict((n, tg) for n, tg, _x in rows)
    for name in sorted(bad):
        said.append(t("plugin_not_loaded", name=name, tag=tags.get(name, "?"), why=bad[name]))


# ------------------------------------------------------------------------------------------- the tests (A9)
def record_dir():
    return os.path.expanduser("~/.local/state/altention/release-tests")


def record_of(tree):
    try:
        with open(os.path.join(record_dir(), tree + ".json"), encoding="utf-8") as f:
            got = json.load(f)
        return got if isinstance(got, dict) and got.get("tree") == tree and got.get("passed") is True else None
    except (OSError, ValueError):
        return None


def venv_python():
    """The bot's Python (with discord.py): the .venv of the engine this file belongs to, else (a worktree has none)
    the installed engine's (<ALT_ROOT or ~/altention>/engine)."""
    places = [os.path.dirname(HERE), os.path.join(config.root(), "engine")]
    found = [os.path.join(p, "dcbot", ".venv", "bin", "python") for p in places]
    return next((p for p in found if os.path.exists(p)), found[0])


def suites(root):
    """[(name, cwd, argv, extra env)] of the suites dcbot/deploy.sh runs, as far as ROOT has them."""
    out = []
    if os.path.isdir(os.path.join(root, "dcbot", "tests")):
        py = venv_python()
        out.append(("dcbot", os.path.join(root, "dcbot"), [py, "-m", "unittest", "discover", "-s", "tests", "-q"],
                    {"DCBOT_SUPERVISOR_TESTS": "skip"}))
        if os.path.isfile(os.path.join(root, "dcbot", "tests", "test_supervisor.py")):
            out.append(("supervisor", os.path.join(root, "dcbot"), [py, "-m", "unittest", "tests.test_supervisor", "-q"],
                        {}))
    for name in ENGINE_SUITES:
        if not os.path.isdir(os.path.join(root, name, "tests")):
            continue
        top = ["-t", "."] if os.path.isfile(os.path.join(root, name, "__init__.py")) else []
        out.append((name, root, [sys.executable, "-m", "unittest", "discover", "-q", "-s", name + "/tests"] + top, {}))
    return out


def scratch_env(base):
    home = os.path.join(base, "home")
    for d in ("home", "tmux", "tmp"):
        os.makedirs(os.path.join(base, d), exist_ok=True)
    env = dict((k, v) for k, v in os.environ.items()
               if not k.startswith("ALT_") and k not in ("TMUX", "TMUX_PANE", "XDG_RUNTIME_DIR"))
    env.update(HOME=home, TMUX_TMPDIR=os.path.join(base, "tmux"), TMPDIR=os.path.join(base, "tmp"),
               XDG_CONFIG_HOME=os.path.join(home, ".config"), XDG_STATE_HOME=os.path.join(home, ".local", "state"),
               XDG_CACHE_HOME=os.path.join(home, ".cache"), XDG_DATA_HOME=os.path.join(home, ".local", "share"))
    return env


def run_suites(todo, env, out_dir, limit):
    """{name: True/False} of running TODO side by side, each cut at LIMIT seconds (its whole process group)."""
    procs = {}
    for name, cwd, argv, extra in todo:
        f = open(os.path.join(out_dir, name), "w")
        procs[name] = (subprocess.Popen(argv, cwd=cwd, env=dict(env, **extra), stdout=f, stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL, start_new_session=True), f)
    done, end = {}, time.time() + limit
    for name, (proc, f) in procs.items():
        try:
            proc.wait(timeout=max(1, end - time.time()))
            done[name] = proc.returncode == 0
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            proc.wait()
            f.write("\n" + t("tests_cut", limit=limit // 60) + "\n")
            done[name] = False
        f.close()
    return done


def summary(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return ""
    ran = re.findall(r"^Ran [0-9]+ tests?.*$", text, re.M)
    return (ran[-1] + " " if ran else "") + (text.strip().splitlines() or [""])[-1][:120]


def export(repo, rev, base):
    """REV's tree written out under BASE/tree (git archive; never the working directory): that folder."""
    root = os.path.join(base, "tree")
    os.makedirs(root)
    p = subprocess.run(["git", "-C", repo, "archive", "--format=tar", rev], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=600)
    if p.returncode != 0:
        raise RuntimeError("git archive: %s" % p.stderr.decode(errors="replace").strip()[:200])
    with tarfile.open(fileobj=io.BytesIO(p.stdout)) as tar:
        if hasattr(tarfile, "data_filter"):
            tar.extractall(root, filter="data")
        else:
            tar.extractall(root)
    return root


def tests(tree, root, base, said, notes, limit=SUITE_LIMIT):
    """A9: the suites dcbot/deploy.sh runs, on the candidate's tree exported at ROOT (BASE is the throwaway folder
    around it), in a throwaway HOME (no real HOME, tmux server or instance), each at most LIMIT seconds; a failed
    suite is run once more and both results are said. A tree that passed before (its record, by tree hash) is not
    run again. True when passed."""
    got = record_of(tree)
    if got:
        print(t("tests_recorded", tree=tree[:12], at=got.get("at", "?"), path=os.path.join(record_dir(), tree + ".json")))
        for name in got.get("reran") or []:
            notes.append(t("tests_reran", name=name))
        return True
    todo = suites(root)
    if not todo:
        said.append(t("tests_none"))
        return False
    if any(n in ("dcbot", "supervisor") for n, *_r in todo) and not os.path.exists(venv_python()):
        said.append(t("tests_no_venv", path=venv_python()))
        return False
    out_dir, again_dir = os.path.join(base, "out"), os.path.join(base, "again")
    os.makedirs(out_dir)
    os.makedirs(again_dir)
    print(t("tests_running", names="、".join(n for n, *_r in todo), limit=limit // 60))
    first = run_suites(todo, scratch_env(base), out_dir, limit)
    failed = [x for x in todo if not first[x[0]]]
    second = run_suites(failed, scratch_env(base), again_dir, limit) if failed else {}
    reran = []
    for name, *_r in todo:
        line = "%s %s %s" % (name, "ok" if first[name] else "FAIL", summary(os.path.join(out_dir, name)))
        if name in second:
            line += " ｜ " + t("tests_again", result="ok" if second[name] else "FAIL",
                               summary=summary(os.path.join(again_dir, name)))
            if second[name]:
                reran.append(name)
        print("  " + line)
    bad = [n for n, ok in second.items() if not ok]
    for name in reran:
        notes.append(t("tests_reran", name=name))
    os.makedirs(record_dir(), exist_ok=True)
    if bad:
        kept = os.path.join(record_dir(), tree + "-failed")
        shutil.rmtree(kept, ignore_errors=True)
        shutil.copytree(out_dir, os.path.join(kept, "first"))
        shutil.copytree(again_dir, os.path.join(kept, "again"))
        said.append(t("tests_failed", names="、".join(bad), kept=kept))
        return False
    tmp = os.path.join(record_dir(), ".%s.json.tmp" % tree)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"tree": tree, "passed": True, "reran": reran,
                   "at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                   "suites": dict((n, summary(os.path.join(out_dir, n))) for n, *_r in todo)}, f,
                  ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(record_dir(), tree + ".json"))
    return True
