#!/usr/bin/env python3
"""Publish the engine: one new commit on the public repository whose tree is a committed tree of this repository
(DESIGN.md 4.5).

  python3 ops/release.py check [REV]                     every check, nothing made: may REV be published?
  python3 ops/release.py publish VERSION [REV] [--push]  make the public commit and its tag vVERSION; pushed only
                                                         with --push (else it is kept as refs/altention-release/next)
  python3 ops/release.py check-plugin NAME [REV]          the same, for the external plugin NAME
  python3 ops/release.py publish-plugin NAME VERSION [REV] [--push]
  python3 ops/release.py seen-plugin NAME VERSION        read NAME's public vVERSION as anyone would, nothing made

REV is a commit of this repository (default HEAD): what is published is its tree, never the working directory.
Where it goes and as whom come from the instance's settings (plugins.json), never from the engine:
  "release": {"repo": "<the public repository's git URL>", "author": "Name <address>"}
Without either, nothing is published.

Before anything is made, every one of these must hold, else it refuses (exit 1) and says which:
- the privacy scan of REV's tree finds nothing, with the instance's blacklist (tools/privacy-scan
  --require-blacklist: no blacklist, no release);
- every COMPAT comment in the tree (# or //, then COMPAT and a colon) is in the tree's docs/compat.json, marked
  after-release (matched by file and the text after COMPAT:; .md files are prose, not counted; entries no longer in the tree
  are only said). What cannot carry a comment (JSON, .gitignore: registered with text "(json)") is not checked:
  those entries are listed for a person to compare by hand;
- every commit made on the public repository since the last release (by anyone but the release author; merges
  aside) is in REV's history as a cherry-pick (`git cherry-pick -x`: "(cherry picked from commit <hash>)"), so a
  release never takes back someone's contribution;
- VERSION is X.Y.Z, its tag is not on the public repository yet and it is newer than every release tag there;
  CHANGELOG.md in REV's tree has a section "## vX.Y.Z", whose text becomes the commit's message, which is scanned
with the same blacklist; the release author's address is a noreply one.
The commit: REV's tree, its one parent the public repository's main (none for the first release), author and
committer the release author (dates now). The main and the tag are pushed in one atomic push, without force (the
public main moved meanwhile, or either ref refused: neither is taken, nothing half done).
The same tag goes on REV in this repository's own remote (its branch's upstream remote, else origin), pushed after
the public one, without force, so a machine following this repository sees the release too (C-55). Before anything
is made: that remote must be there, and a vX.Y.Z it already has must be on REV (then it is left as it is). Its push
failing after the public one went out: said, with the command that pushes it (exit 2); a tag once pushed never moves.

An external plugin (C-61: the folder ~/altention/plugins/NAME, a git repository of its own, kept private with its
whole history) is published the same way from its own commits, to its own public repository, which then holds one
commit per release: "release": {"plugins": {"NAME": {"repo": "<its public repository's git URL>"}}} in the
instance, as the release author above. The checks are the same but the COMPAT register (the engine's own); and
REV's plugin.json must be one the engine reads, named NAME. After --push its public repository is read as anyone
would (no login, no git settings or ~/.netrc of this machine) and must show vVERSION with REV's tree, and main on
it: else it is said (exit 2), e.g. a repository not made public yet; seen-plugin reads it again later. Repositories are never made or renamed here.

Exit status: 0 done (or check passed); 1 refused (each reason said); 2 wrong usage or a step failed.
Standard library only, Python 3.8.
"""
import ast
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tokenize

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.dirname(HERE)  # the repository whose commit is published (tests point it elsewhere)
SCAN = os.path.join(ENGINE, "tools", "privacy-scan")
sys.path.insert(0, ENGINE)
from altention import config, plugin as alt_plugin, plugin_update, text as alt_text  # noqa: E402

VERSION = re.compile(r"^([0-9]+)\.([0-9]+)\.([0-9]+)$")
TAG = re.compile(r"^v([0-9]+)\.([0-9]+)\.([0-9]+)$")
AUTHOR = re.compile(r"^\s*([^<>]+?)\s*<([^<>\s]+@[^<>\s]+)>\s*$")
COMPAT_LINE = re.compile(r"(?:#|//)\s*COMPAT:(.*)$")
PUBLIC_REF = "refs/altention-release/public"   # the public main, as fetched
PUBLIC_TAGS = "refs/altention-release/tags/"   # the public tags, as fetched
NEXT_REF = "refs/altention-release/next"       # the commit made and not pushed
REGISTER = "docs/compat.json"
CHANGELOG = "CHANGELOG.md"
PICKED = re.compile(r"\(cherry picked from commit ([0-9a-f]{40})\)")
REPO = None  # the repository being published when it is not the engine: an external plugin's folder (publish-plugin)


def here():
    return REPO or ENGINE


class Failed(Exception):
    """A step that could not be done at all (not a refusal): exit 2."""


def git(*args, inp=None, env=None, check=False, repo=None):
    p = subprocess.run(["git", "-C", repo or here(), "-c", "core.quotePath=false"] + list(args), input=inp, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, universal_newlines=True, env=env, timeout=600)
    if check and p.returncode != 0:
        raise Failed("git %s：%s" % (args[0], (p.stderr.strip() or p.stdout.strip())[:300]))
    return p


def settings(name=None):
    """(repo, (name, address)) from the instance's release settings (for plugin `name`: its own repo under
    release.plugins, the same author); Failed when either is missing or odd."""
    try:
        release = config.setting("release", None)
    except Exception as e:
        raise Failed("讀不出實例的設定（%s）" % e)
    if not isinstance(release, dict):
        raise Failed("實例的 plugins.json 沒有 release 設定（release.repo、release.author）：不發佈")
    repo, author = release.get("repo"), release.get("author")
    if name is not None:
        mine = (release.get("plugins") or {}).get(name) if isinstance(release.get("plugins"), dict) else None
        repo = mine.get("repo") if isinstance(mine, dict) else None
        if not isinstance(repo, str) or not repo.strip():
            raise Failed("實例沒有設定 release.plugins.%s.repo（這個插件公開 repo 的 git 網址）：不發佈" % name)
    elif not isinstance(repo, str) or not repo.strip():
        raise Failed("實例沒有設定 release.repo（公開 repo 的 git 網址）：不發佈")
    m = AUTHOR.match(author) if isinstance(author, str) else None
    if not m:
        raise Failed("實例的 release.author 要寫成「名字 <信箱>」：不發佈")
    if "noreply" not in m.group(2).lower():  # DESIGN 4.5: the public history names no one's address
        raise Failed("release.author 的信箱要是 noreply 的（例如 GitHub 的 <id>+<帳號>@users.noreply.github.com）：不發佈")
    return repo.strip(), (m.group(1), m.group(2))


def resolve(rev):
    commit = git("rev-parse", "-q", "--verify", rev + "^{commit}").stdout.strip()
    if not commit:
        raise Failed("%s 不是這個 repo 的 commit" % rev)
    return commit, git("rev-parse", commit + "^{tree}", check=True).stdout.strip()


# ------------------------------------------------------------------------------------------- the checks
def privacy(rev, said):
    """The privacy scan of REV's tree with the instance's blacklist: True when clean."""
    p = subprocess.run([sys.executable, SCAN, rev, "--repo", here(),
                        "--require-blacklist", "--summary"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       universal_newlines=True, timeout=600)
    if p.returncode == 0:
        return True
    lines = [x for x in p.stdout.strip().splitlines() if x.strip()]
    why = {1: "個資掃描找到東西", 2: "沒有實例的黑名單（privacy.json 要列 terms）", 3: "個資掃描本身失敗"}.get(p.returncode, "個資掃描結束碼 %d" % p.returncode)
    said.append("%s：%s" % (why, " ｜ ".join(lines[-6:])[:600]))
    return False


def compat_lines(rev):
    """{(file, text)} of every COMPAT: comment in REV's tree, .md files aside."""
    p = git("grep", "-I", "-n", "-E", "(#|//)[[:space:]]*COMPAT:", rev, "--", ".", ":(exclude)*.md")
    if p.returncode not in (0, 1):
        raise Failed("git grep：%s" % p.stderr.strip()[:200])
    found = set()
    for row in p.stdout.splitlines():
        parts = row.split(":", 3)  # rev:file:line:text
        if len(parts) < 4:
            continue
        m = COMPAT_LINE.search(parts[3])
        if m:
            found.add((parts[1], m.group(1).strip()))
    return found


def compat(rev, said, notes):
    """Every COMPAT: comment registered as after-release in REV's docs/compat.json: True when so."""
    p = git("show", "%s:%s" % (rev, REGISTER))
    if p.returncode != 0:
        said.append("%s 不在要發佈的樹裡：沒有登記簿就不知道剩下的 COMPAT: 是不是都能留" % REGISTER)
        return False
    try:
        entries = json.loads(p.stdout)
    except ValueError as e:
        said.append("%s 不是合法的 JSON（%s）" % (REGISTER, e))
        return False
    if not isinstance(entries, list) or not all(
            isinstance(e, dict) and all(isinstance(e.get(k), str) for k in ("file", "text", "status")) for e in entries):
        said.append("%s 要是一個陣列，每一項都有文字的 file、text、status" % REGISTER)
        return False
    known, by_hand = {}, []
    for e in entries:
        if e["text"].strip() != "(json)":
            known.setdefault((e["file"], e["text"].strip()), []).append(e["status"])
        else:
            by_hand.append(e)
    if by_hand:  # JSON, .gitignore: no comment to match, so not checked here; listed for a person to look at
        notes.append("%s 有 %d 項沒有註解可對（JSON、.gitignore 等），這裡不檢查、要人工核對：%s" % (
            REGISTER, len(by_hand), "；".join("%s（%s）" % (e["file"], e["status"]) for e in by_hand[:10])))
    found = compat_lines(rev)
    ok = True
    missing = sorted(found - set(known))
    if missing:
        ok = False
        said.append("有 %d 處 COMPAT: 沒有登記在 %s：%s" % (len(missing), REGISTER, "；".join(
            "%s「%s」" % (f, t[:60]) for f, t in missing[:5])))
    wrong = sorted(k for k in found & set(known) if any(s != "after-release" for s in known[k]))
    if wrong:
        ok = False
        said.append("有 %d 處 COMPAT: 登記的狀態不是 after-release：%s" % (len(wrong), "；".join(
            "%s「%s」" % (f, t[:60]) for f, t in wrong[:5])))
    gone = sorted(set(known) - found)
    if gone:
        notes.append("%s 有 %d 項在樹裡已經找不到（只提醒）：%s" % (REGISTER, len(gone), "；".join(
            "%s「%s」" % (f, t[:40]) for f, t in gone[:3])))
    return ok


def fetch_public(repo):
    """The public main and tags as local refs; (main commit or "" when the repository is still empty, {tag: commit})."""
    for ref in [PUBLIC_REF] + [r for r in git("for-each-ref", "--format=%(refname)", PUBLIC_TAGS).stdout.split()]:
        git("update-ref", "-d", ref)
    p = git("ls-remote", repo)
    if p.returncode != 0:
        raise Failed("讀不到公開 repo %s：%s" % (repo, p.stderr.strip()[:200]))
    heads = [x.split("\t")[1] for x in p.stdout.splitlines() if "\t" in x]
    refspecs = ["+refs/tags/*:%s*" % PUBLIC_TAGS]
    if "refs/heads/main" in heads:
        refspecs.insert(0, "+refs/heads/main:%s" % PUBLIC_REF)
    if heads:
        git("fetch", "-q", "--no-tags", repo, *refspecs, check=True)
    main = git("rev-parse", "-q", "--verify", PUBLIC_REF).stdout.strip()
    tags = {}
    for row in git("for-each-ref", "--format=%(refname) %(objectname)", PUBLIC_TAGS).stdout.splitlines():
        ref, obj = row.split(" ", 1)
        tags[ref[len(PUBLIC_TAGS):]] = git("rev-parse", obj + "^{commit}").stdout.strip()
    return main, tags


def imported(rev, main, author, said):
    """Every commit on the public main since the last release (not the release author's, not a merge) is
    cherry-picked into REV's history: True when so."""
    if not main:
        return True
    rows = git("log", "--format=%H %ae", "--first-parent", main).stdout.splitlines()
    last = next((h for h, a in (r.split(" ", 1) for r in rows) if a == author[1]), None)
    span = main if last is None else "%s..%s" % (last, main)
    theirs = [r.split(" ", 1)[0] for r in git("log", "--no-merges", "--format=%H %ae", span).stdout.splitlines()
              if r.split(" ", 1)[1] != author[1]]
    if not theirs:
        return True
    picked = set(PICKED.findall(git("log", "--format=%B", rev).stdout))
    missing = [h for h in theirs if h not in picked]
    if missing:
        said.append("公開 repo 上有 %d 個 commit 還沒 cherry-pick 回來（git cherry-pick -x），發佈會把它們蓋掉：%s"
                    % (len(missing), "、".join(h[:12] for h in missing[:5])))
        return False
    return True


def private_remote():
    """This repository's own remote: its branch's upstream remote, else origin; Failed when there is none."""
    branch = git("symbolic-ref", "-q", "--short", "HEAD").stdout.strip()
    remote = git("config", "branch.%s.remote" % branch).stdout.strip() if branch else ""
    if not remote or remote == ".":
        remote = "origin"
    if git("remote", "get-url", remote).returncode != 0:
        raise Failed("找不到這個 repo 自己的 remote（%s）：發佈的標籤也要打在它上面，不知道打在哪" % remote)
    return remote


def private_tag(remote, version, commit, said):
    """Whether remote already has vVERSION: True on `commit` (nothing to push), False not there; said when it is on
    another commit (a tag never moves)."""
    p = git("ls-remote", "--tags", remote, "refs/tags/v%s" % version, "refs/tags/v%s^{}" % version)
    if p.returncode != 0:
        raise Failed("讀不到這個 repo 的 remote %s：%s" % (remote, p.stderr.strip()[:200]))
    rows = dict((x.split("\t")[1], x.split("\t")[0]) for x in p.stdout.splitlines() if "\t" in x)
    there = rows.get("refs/tags/v%s^{}" % version) or rows.get("refs/tags/v%s" % version)
    if there is None:
        return False
    if there != commit:
        said.append("這個 repo 的 remote %s 已經有 tag v%s，在 %s，不是要發佈的 %s（tag 不移動）"
                    % (remote, version, there[:12], commit[:12]))
    return True


def version_ok(version, tags, said):
    m = VERSION.match(version or "")
    if not m:
        said.append("版本要寫成 X.Y.Z（例如 0.1.0），收到 %r" % version)
        return False
    if "v" + version in tags:
        said.append("公開 repo 已經有 tag v%s" % version)
        return False
    mine = tuple(map(int, m.groups()))
    newer = sorted(t for t in tags if TAG.match(t) and tuple(map(int, TAG.match(t).groups())) >= mine)
    if newer:
        said.append("公開 repo 已經有不比 v%s 舊的發佈：%s" % (version, "、".join(newer)))
        return False
    return True


def changelog(rev, version, said):
    """The text of CHANGELOG.md's "## vX.Y.Z" section in REV's tree, or None (said why)."""
    p = git("show", "%s:%s" % (rev, CHANGELOG))
    if p.returncode != 0:
        said.append("要發佈的樹裡沒有 %s" % CHANGELOG)
        return None
    head = re.compile(r"^##\s+v%s(\s.*)?$" % re.escape(version))
    lines, body, inside = p.stdout.splitlines(), [], False
    for line in lines:
        if inside and line.startswith("## "):
            break
        if inside:
            body.append(line)
        elif head.match(line.rstrip()):
            inside = True
    text = "\n".join(body).strip()
    if not inside or not text:
        said.append("%s 沒有「## v%s」這一節（或它是空的）" % (CHANGELOG, version))
        return None
    return text


def own_words(version, text, notes):
    """C-57 U8: the update notices quote a release's "### For people (<language>)" part in an installation of that
    language, its English "For people" when it has none. Each language the engine speaks (altention/text.py) whose
    part `text` (the "## vVERSION" section) lacks, or has without a point, noted: a reminder, not a refusal. English
    (en, en-*) is not asked for: plain "For people" is its part (C-70: once text.py speaks it)."""
    parts, part = {}, None
    for line in text.splitlines():
        if line.startswith("### "):
            part = line[4:].strip().lower()
        elif line.startswith("- "):
            parts[part] = parts.get(part, 0) + 1
    for lang in sorted(alt_text.TEXT):
        if lang.lower().split("-")[0] == "en":
            continue
        if not parts.get("for people (%s)" % lang.lower()):
            notes.append("%s 的 v%s 沒有「### For people (%s)」這段（或裡面沒有項目）：那個語言的安裝在更新卡片上"
                         "會看到英文的 For people" % (CHANGELOG, version, lang))


def plugin_json(rev, name, said, notes):
    """REV's plugin.json is one the engine reads, and it is plugin `name`'s: True when so. Keys the engine no longer
    reads (plugin.RETIRED) are noted, not refused."""
    p = git("show", "%s:plugin.json" % rev)
    if p.returncode != 0:
        said.append("要發佈的樹裡沒有 plugin.json")
        return False
    try:
        d = alt_plugin.check(json.loads(p.stdout), "plugin.json")
    except (ValueError, alt_plugin.PluginError) as e:
        said.append("plugin.json 讀不懂：%s" % str(e)[:300])
        return False
    if d.get("name") != name:
        said.append("plugin.json 的 name 是 %r，不是 %r" % (d.get("name"), name))
        return False
    for where, todo in alt_plugin.retired(d):
        notes.append("plugin.json 還寫著 %s，引擎已經不讀了（%s）" % (where, todo))
    return engine_use(rev, d, said, notes)


DCBOT_PATH = re.compile(r"\bdcbot/[A-Za-z0-9_.][A-Za-z0-9_./-]*")
SKILL_RUNS = re.compile(r"\b(?:dcbot|ops)/[A-Za-z0-9_.][A-Za-z0-9_./-]*")  # engine files a skill may tell Claude to run
DC_USE = re.compile(r"\bdc\b[\"']?\s*,?\s*[\"']?(post|notify|read)\b")  # altention dc post, [.., "dc", "post"]
SKILL_FILE = re.compile(r"(^|/)skills/.*\.md$|(^|/)SKILL\.md$")
SKIPPED = re.compile(r"(^|/)(tests?|docs)/|(^|/)test_[^/]*\.py$|\.md$")  # not what the plugin runs


def uses_in(path, text):
    """[(kind, value, line)] of what file `path` (its text) takes from the engine: ("path", "dcbot/…") a file under
    dcbot/ named in it; ("module", "altention.x") an import; ("function", "altention.x.f") a function of an imported
    module used by name; ("unclear", what) a dcbot/ path it builds from values not written in it. Python is read
    with ast (never run): its strings, not its comments or docstrings; any other file by its words, and Python that
    does not parse too."""
    out = []
    tree = None
    if path.endswith(".py"):
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            tree = None
        if tree is not None:
            docs, taken = set(), set()
            for node in ast.walk(tree):
                body = getattr(node, "body", None)
                if isinstance(body, list) and body and isinstance(body[0], ast.Expr) and \
                        isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                    docs.add(id(body[0].value))
            inner = set(id(c) for node in ast.walk(tree) if _is_div(node) for c in (node.left, node.right) if _is_div(c))
            for node in ast.walk(tree):  # a path put together in one expression: join(…, "dcbot", …), Path(…) / "dcbot" / …
                pieces = node.args if isinstance(node, ast.Call) else _div_pieces(node) if _is_div(node) and \
                    id(node) not in inner else None
                found = _dcbot_path(pieces or [])
                if found:
                    kind, value, used = found
                    out.append((kind, value, node.lineno))
                    taken.update(used)
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs | taken:
                    out += [("path", m.group(0).rstrip("./"), node.lineno) for m in DCBOT_PATH.finditer(node.value)]
            bound = {}  # local name → module
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for a in node.names:
                        if a.name == "altention" or a.name.startswith("altention."):
                            out.append(("module", a.name, node.lineno))
                            if a.asname:  # `import altention.config` alone: its functions are not looked at
                                bound[a.asname] = a.name
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module and \
                        (node.module == "altention" or node.module.startswith("altention.")):
                    for a in node.names:
                        if node.module == "altention" or not _is_function_import(node.module, a.name):
                            module = "%s.%s" % (node.module, a.name)
                            out.append(("module", module, node.lineno))
                            bound[a.asname or a.name] = module
                        else:
                            out.append(("module", node.module, node.lineno))
                            out.append(("function", "%s.%s" % (node.module, a.name), node.lineno))
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in bound:
                    out.append(("function", "%s.%s" % (bound[node.value.id], node.attr), node.lineno))
    if tree is None:
        for i, line in enumerate(text.splitlines(), 1):
            out += [("path", m.group(0).rstrip("./"), i) for m in DCBOT_PATH.finditer(line)]
    return out


def _is_div(node):
    return isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)


def _div_pieces(node):
    """The parts of a / b / c, left to right."""
    return (_div_pieces(node.left) if _is_div(node.left) else [node.left]) + \
        (_div_pieces(node.right) if _is_div(node.right) else [node.right])


def _dcbot_path(pieces):
    """(kind, path, ids of the strings used) when `pieces` (the arguments of one call, or the parts of one a / b)
    name a place under dcbot/ from a piece "dcbot" (or "dcbot/…") on: "path" when every later piece is written
    out, "unclear" when one is a value; None when no piece starts there (#code-review-2 of 8742d70: pathlib)."""
    words = [p.value if isinstance(p, ast.Constant) and isinstance(p.value, str) else None for p in pieces]
    start = next((i for i, w in enumerate(words) if w is not None and (w == "dcbot" or w.startswith("dcbot/"))), None)
    if start is None:
        return None
    rest = words[start:]
    known = rest[:rest.index(None)] if None in rest else rest
    path = "/".join(w.strip("/") for w in known)
    used = set(id(p) for p in pieces[start:start + len(known)])
    return ("path", path, used) if len(known) == len(rest) else ("unclear", path + "/…", used)


def _is_function_import(module, name):
    """`from module import name`: whether name is something in the module rather than a module of its own (a
    package's submodule is a .py file or a folder there)."""
    base = os.path.join(alt_plugin.ENGINE, *module.split("."))
    return not (os.path.isfile(os.path.join(base, name + ".py")) or os.path.isdir(os.path.join(base, name)))


def code_lines(path, text):
    """[(line, words)] of a file as it runs: Python without its comments and docstrings (one line of its tokens
    each), any other file line by line."""
    if not path.endswith(".py"):
        return list(enumerate(text.splitlines(), 1))
    try:
        docs = set()
        for node in ast.walk(ast.parse(text)):
            body = getattr(node, "body", None)
            if isinstance(body, list) and body and isinstance(body[0], ast.Expr) and \
                    isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                docs.update(range(body[0].lineno, body[0].end_lineno + 1))
        lines = {}
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type in (tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE) or tok.start[0] in docs:
                continue
            lines.setdefault(tok.start[0], []).append(tok.string)
        return [(n, " ".join(words)) for n, words in sorted(lines.items())]
    except (SyntaxError, ValueError, tokenize.TokenError):
        return list(enumerate(text.splitlines(), 1))


def derived_uses(path, text):
    """{interface: line} that the words of file `path` show it uses although no engine file is named (#display-review,
    manager 2026-10-24): altention dc post/notify/read, $DCBOT_CHANNEL, and in plugin.json a {token}."""
    out = {}
    for n, line in code_lines(path, text):
        for m in DC_USE.finditer(line):
            out.setdefault("dc." + m.group(1), n)
        if "DCBOT_CHANNEL" in line or (path == "plugin.json" and alt_plugin.TOKEN.search(line)):
            out.setdefault("tokens", n)
    return out


def text_at(rev, path):
    """The file `path` at REV as text, or None when it is not there or not UTF-8 text (a font, a picture): read as
    bytes, since git() decodes and a binary file would raise (#code-review of 8742d70)."""
    try:
        p = subprocess.run(["git", "-C", here(), "show", "%s:%s" % (rev, path)], stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, timeout=600)
        return p.stdout.decode("utf-8") if p.returncode == 0 and b"\0" not in p.stdout[:8000] else None
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError):
        return None


def engine_use(rev, d, said, notes):
    """C-94: what the plugin at REV takes from the engine, against altention/interfaces.json. Refused: a file
    under dcbot/ or a module of altention that no interface lists, an interface used that "uses" does not name,
    and "uses" asking for what this engine does not offer. Noted: a transitional interface (and a module's function
    not among its public ones), and a dcbot/ path it builds from values (cannot be told). True when nothing is
    refused."""
    try:
        core = alt_plugin.interfaces()
        offered = alt_plugin.offered_here()
    except alt_plugin.PluginError as e:
        said.append(alt_text.t("release.interfaces_unreadable", error=e))
        return False
    by_path = {p: n for n, e in offered.items() for p in e.get("paths", [])}
    by_module = {m: n for n, e in offered.items() for m in e.get("modules", [])}
    tree = [f for f in git("ls-tree", "-r", "--name-only", rev).stdout.splitlines() if f]
    files = [f for f in tree if not SKIPPED.search(f)]
    used, bad = {}, len(said)
    for f in (f for f in tree if SKILL_FILE.search(f)):  # what a skill tells Claude to run (manager on 8742d70): said only
        for name, i in derived_uses(f, text_at(rev, f) or "").items():
            used.setdefault(name, "%s:%d" % (f, i))
        for i, line in enumerate((text_at(rev, f) or "").splitlines(), 1):
            for m in SKILL_RUNS.finditer(line):
                path = m.group(0).rstrip("./")
                if path not in by_path and path not in tree:  # its own ops/… is the plugin's, not the engine's
                    notes.append(alt_text.t("release.skill_runs", at="%s:%d" % (f, i), path=path))
    for f in files:
        content = text_at(rev, f)
        if content is None:
            continue
        for name, i in derived_uses(f, content).items():
            used.setdefault(name, "%s:%d" % (f, i))
        for kind, value, line in uses_in(f, content):
            at = "%s:%d" % (f, line)
            if kind == "unclear":
                notes.append(alt_text.t("release.path_built", at=at, path=value))
            elif kind == "path" and value not in by_path and any(p.startswith(value + "/") for p in by_path):
                notes.append(alt_text.t("release.path_folder", at=at, path=value))
                for p in (p for p in by_path if p.startswith(value + "/")):  # the folder first, the file's name later:
                    m = re.search(r"[\"']%s[\"']" % re.escape(p[len(value) + 1:]), content)  # counts as that file
                    if m:
                        used.setdefault(by_path[p], "%s:%d" % (f, content.count("\n", 0, m.start()) + 1))
            elif kind == "path":
                name = by_path.get(value)
                if name is None:
                    said.append(alt_text.t("release.path_private", at=at, path=value))
                used.setdefault(name, at) if name else None
            elif kind == "module":
                name = by_module.get(value)
                if name is None:
                    said.append(alt_text.t("release.module_private", at=at, module=value))
                used.setdefault(name, at) if name else None
            elif kind == "function":
                module, _, fn = value.rpartition(".")
                e = offered.get(by_module.get(module) or "") or {}
                if e.get("functions") and fn not in e["functions"]:
                    notes.append(alt_text.t("release.function_private", at=at, function=value,
                                            words=e.get("others") or alt_text.t("release.function_private_default")))
    if d.get("rewrite"):  # what plugin.json itself declares the bot does for it
        used.setdefault("rewrite", "plugin.json")
    if isinstance(d.get("activity"), dict) and d["activity"].get("pages"):
        used.setdefault("activity.pages", "plugin.json")
    uses = d.get("uses") or {}
    for name, at in sorted(used.items()):
        if name not in uses:
            said.append(alt_text.t("release.uses_needed", at=at, name=name, version=offered[name]["version"]))
        if offered[name].get("transitional"):
            notes.append(alt_text.t("release.transitional", at=at, name=name, words=offered[name]["transitional"]))
    for name in sorted(n for n in (d.get("interfaces") or {}) if n in core):  # its own offers: not the engine's names
        said.append(alt_text.t("release.interface_clash", name=name))
    problem = alt_plugin.uses_problem(d, offered)
    if problem:
        said.append(alt_text.t("release.uses_mismatch", problem=problem))
    return len(said) == bad


def anonymous_url(repo):
    """The address anyone reads the repository at: a GitHub ssh address as https, any other as it is."""
    m = re.match(r"^(?:ssh://)?git@github\.com[:/](.+?)/?$", repo)
    return "https://github.com/%s" % m.group(1) if m else repo


def seen_by_anyone(repo, commit, version, tree):
    """The public repository read with no login and none of this machine's git settings: (True, "") when its vVERSION
    has `tree` and (unless `commit` is None) its main is `commit`, else (False, why). HOME is an empty folder for it, so
    no ~/.netrc, ~/.gitconfig or XDG setting of this machine's is read, whatever git's version (#code-review)."""
    url = anonymous_url(repo)
    tmp = tempfile.mkdtemp(prefix="release-check-")
    home, bare = os.path.join(tmp, "home"), os.path.join(tmp, "repo.git")
    os.mkdir(home)
    env = dict(os.environ, HOME=home, XDG_CONFIG_HOME=os.path.join(home, ".config"), GIT_TERMINAL_PROMPT="0",
               GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_ASKPASS="", SSH_ASKPASS="")
    try:
        git("init", "-q", "--bare", bare, env=env, repo=tmp, check=True)
        p = git("fetch", "-q", "--no-tags", url, "+refs/heads/main:refs/heads/main",
                "+refs/tags/v%s:refs/tags/v%s" % (version, version), env=env, repo=bare)
        if p.returncode != 0:
            return False, "沒登入讀不到 %s（還不是 public？）：%s" % (url, (p.stderr.strip() or p.stdout.strip())[:200])
        main = git("rev-parse", "refs/heads/main", env=env, repo=bare).stdout.strip()
        got = git("rev-parse", "refs/tags/v%s^{tree}" % version, env=env, repo=bare).stdout.strip()
        if got != tree or (commit is not None and main != commit):
            return False, "%s 上的 main 是 %s、v%s 的 tree 是 %s，不是%s %s" % (
                url, main[:12], version, got[:12], "剛推的 %s、" % commit[:12] if commit else "這裡 v%s 的 tree" % version,
                tree[:12])
        return True, ""
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def seen(name, version):
    """seen-plugin: plugin `name`'s vVERSION read from its public repository as anyone would, against this
    repository's own vVERSION tree (after a publish whose last step said the repository could not be read)."""
    repo, _ = settings(name)
    if not VERSION.match(version or ""):
        print("版本要寫成 X.Y.Z（例如 0.1.0），收到 %r" % version)
        return 2
    tree = git("rev-parse", "-q", "--verify", "v%s^{tree}" % version).stdout.strip()
    if not tree:  # publish-plugin put the tag on this repository's own remote, not here
        p = git("ls-remote", "--tags", private_remote(), "refs/tags/v%s" % version, "refs/tags/v%s^{}" % version)
        rows = dict((x.split("\t")[1], x.split("\t")[0]) for x in p.stdout.splitlines() if "\t" in x)
        there = rows.get("refs/tags/v%s^{}" % version) or rows.get("refs/tags/v%s" % version)
        tree = git("rev-parse", "-q", "--verify", "%s^{tree}" % there).stdout.strip() if there else ""
    if not tree:
        print("這個 repo（和它的 remote）沒有 tag v%s：不知道要對哪個 tree" % version)
        return 2
    ok, why = seen_by_anyone(repo, None, version, tree)
    print("別人看得到：沒登入讀到的 v%s 跟這裡的一樣" % version if ok else why)
    return 0 if ok else 2


# ------------------------------------------------------------------------------------------- making it
def message(version, text, name=None):
    return "%s v%s\n\n%s\n" % ("Altention" if name is None else name, version, text)


def make_commit(tree, parent, version, text, author, name=None):
    env = dict(os.environ, GIT_AUTHOR_NAME=author[0], GIT_AUTHOR_EMAIL=author[1], GIT_COMMITTER_NAME=author[0],
               GIT_COMMITTER_EMAIL=author[1])
    for k in ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE"):
        env.pop(k, None)
    args = ["-c", "commit.gpgsign=false", "commit-tree", tree] + (["-p", parent] if parent else [])
    commit = git(*args, inp=message(version, text, name), env=env, check=True).stdout.strip()
    # what was made is what was meant: the tree, the one parent, the author and committer
    shown = git("show", "-s", "--format=%T%n%P%n%ae%n%ce", commit, check=True).stdout.split("\n")
    if shown[0] != tree or shown[1].strip() != (parent or "") or shown[2] != author[1] or shown[3] != author[1]:
        raise Failed("做出來的 commit 跟要的不一樣（%r）" % shown[:4])
    return commit


META = "RELEASE-MESSAGE"  # the message, as a file of a throwaway commit on top of REV, for the privacy scan


def meta_scan(rev, message, said):
    """The release commit's message, scanned like the tree with the same blacklist: put into a file of a throwaway
    commit on top of REV, made as REV's own author so the scan's list of authors is REV's history alone (the
    release author is checked apart: a noreply address). True when clean."""
    who = git("show", "-s", "--format=%an%n%ae", rev, check=True).stdout.split("\n")
    env = dict(os.environ, GIT_AUTHOR_NAME=who[0], GIT_AUTHOR_EMAIL=who[1], GIT_COMMITTER_NAME=who[0],
               GIT_COMMITTER_EMAIL=who[1])
    blob = git("hash-object", "-w", "--stdin", inp=message, check=True).stdout.strip()
    tree = git("mktree", inp="100644 blob %s\t%s\n" % (blob, META), check=True).stdout.strip()
    probe = git("-c", "commit.gpgsign=false", "commit-tree", tree, "-p", rev, inp="release message probe\n", env=env,
                check=True).stdout.strip()
    p = subprocess.run([sys.executable, SCAN, probe, "--repo", here(), "--require-blacklist", "--path", META,
                        "--summary"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
                       timeout=600)
    if p.returncode != 0:
        said.append("發佈 commit 的訊息過不了個資掃描：%s" % " ｜ ".join(p.stdout.strip().splitlines()[-6:])[:600])
        return False
    return True


def run(action, version, rev, push, name=None):
    """name: an external plugin's (REPO is then its folder), else the engine."""
    said, notes = [], []
    repo, author = settings(name)
    commit, tree = resolve(rev)
    print("要發佈的：%s%s（tree %s）" % ("插件 %s 的 " % name if name else "", commit[:12], tree[:12]))
    clean = privacy(commit, said)
    registered = compat(commit, said, notes) if name is None else plugin_json(commit, name, said, notes)
    main, tags = fetch_public(repo)
    print("公開 repo：%s" % ("main 在 %s，發佈過 %s" % (main[:12], "、".join(sorted(tags)) or "（沒有 tag）") if main else "還是空的（這是第一版）"))
    took = imported(commit, main, author, said)
    text = None
    if action in ("publish", "publish-plugin"):
        remote = private_remote()
        tagged = private_tag(remote, version, commit, said) if VERSION.match(version or "") else False
        good_version = version_ok(version, tags, said)
        text = changelog(commit, version, said) if good_version else None
        if text:
            own_words(version, text, notes)
    for n in notes:
        print("提醒：" + n)
    if said or not (clean and registered and took):
        for s in said:
            print("拒絕：" + s)
        return 1
    if action in ("check", "check-plugin"):
        print("可以發佈：個資掃描、%s、公開 repo 的貢獻都過了" % ("COMPAT: 登記" if name is None else "plugin.json"))
        return 0
    if text is None:
        return 1
    if not meta_scan(commit, message(version, text, name), said):
        for s in said:
            print("拒絕：" + s)
        return 1
    new = make_commit(tree, main, version, text, author, name)
    git("update-ref", NEXT_REF, new, check=True)
    mine = ("這個 repo 的 remote %s 已經有 v%s 在 %s，不用再打" if tagged else "也會在這個 repo 的 remote %s 把 v%s 打在 %s") \
        % (remote, version, commit[:12])
    if not push:
        print("做好了 %s（%s，parent %s），沒有推。要推：加 --push 重跑（會重做同一個 commit）；看內容：git show %s"
              % (new[:12], "v" + version, main[:12] or "沒有", NEXT_REF))
        print("推的時候" + mine)
        return 0
    # both or neither (no force: if the public main moved meanwhile, git refuses the whole push)
    git("push", "--atomic", repo, "%s:refs/heads/main" % new, "%s:refs/tags/v%s" % (new, version), check=True)
    print("發佈了 v%s：%s 推到 %s" % (version, new[:12], repo))
    code = 0
    if tagged:
        print(mine)
    else:
        p = git("push", remote, "%s:refs/tags/v%s" % (commit, version))
        if p.returncode != 0:
            print("公開 repo 發佈了，但這個 repo 的 remote %s 的 tag 沒推上（%s）。補推：git push %s %s:refs/tags/v%s"
                  % (remote, (p.stderr.strip() or p.stdout.strip())[:200], remote, commit, version))
            code = 2
        else:
            print("這個 repo 的 remote %s 也打了 v%s 在 %s" % (remote, version, commit[:12]))
    if name is not None:  # a plugin's public repository is new and made by hand: read it as anyone would
        ok, why = seen_by_anyone(repo, new, version, tree)
        print("別人看得到：沒登入讀到的 main 和 v%s 都對" % version if ok else "推上去了，但" + why +
              "。改好之後再看一次：python3 ops/release.py seen-plugin %s %s" % (name, version))
        code = code or (0 if ok else 2)
    return code


def main(argv):
    global REPO
    args = [a for a in argv if a != "--push"]
    push = "--push" in argv
    name = None
    if args[:1] == ["seen-plugin"]:
        if push or len(args) != 3 or args[1] == "core" or not alt_plugin.check_name(args[1]):
            print(__doc__.split("\n\n")[1])
            return 2
        try:
            REPO = plugin_update.folder_of(args[1])
            return seen(args[1], args[2])
        except Failed as e:
            print("沒有看：%s" % e)
            return 2
        finally:
            REPO = None
    if args[:1] in (["check-plugin"], ["publish-plugin"]) and len(args) > 1:
        name, args = args[1], [args[0][:-len("-plugin")]] + args[2:]
    if not args or args[0] not in ("check", "publish") or (args[0] == "check" and (push or len(args) > 2)) \
            or (args[0] == "publish" and not 2 <= len(args) <= 3) \
            or (name is not None and (name == "core" or not alt_plugin.check_name(name))):
        print(__doc__.split("\n\n")[1])
        return 2
    action = args[0] + ("-plugin" if name is not None else "")
    version = args[1] if args[0] == "publish" else None
    rev = (args[2] if len(args) > 2 else "HEAD") if args[0] == "publish" else (args[1] if len(args) > 1 else "HEAD")
    try:
        if name is not None:
            REPO = plugin_update.folder_of(name)
            if not os.path.isdir(os.path.join(REPO, ".git")) and not os.path.isfile(os.path.join(REPO, ".git")):
                raise Failed("%s 不是 git repo：插件從它自己的 repo 發佈" % REPO)
        return run(action, version, rev, push, name)
    except Failed as e:
        print("沒有發佈：%s" % e)
        return 2
    finally:
        REPO = None


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
