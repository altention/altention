#!/usr/bin/env python3
"""Publish the engine: one new commit on the public repository whose tree is a committed tree of this repository
(DESIGN.md 4.5).

  python3 ops/release.py check [REV]                     every check, nothing made: may REV be published?
  python3 ops/release.py publish VERSION [REV] [--push]  make the public commit and its tag vVERSION; pushed only
                                                         with --push (else it is kept as refs/altention-release/next)

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

Exit status: 0 done (or check passed); 1 refused (each reason said); 2 wrong usage or a step failed.
Standard library only, Python 3.8.
"""
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.dirname(HERE)  # the repository whose commit is published (tests point it elsewhere)
SCAN = os.path.join(ENGINE, "tools", "privacy-scan")
sys.path.insert(0, ENGINE)
from altention import config  # noqa: E402

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


class Failed(Exception):
    """A step that could not be done at all (not a refusal): exit 2."""


def git(*args, inp=None, env=None, check=False, repo=None):
    p = subprocess.run(["git", "-C", repo or ENGINE, "-c", "core.quotePath=false"] + list(args), input=inp, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, universal_newlines=True, env=env, timeout=600)
    if check and p.returncode != 0:
        raise Failed("git %s：%s" % (args[0], (p.stderr.strip() or p.stdout.strip())[:300]))
    return p


def settings():
    """(repo, (name, address)) from the instance's release settings; Failed when either is missing or odd."""
    try:
        release = config.setting("release", None)
    except Exception as e:
        raise Failed("讀不出實例的設定（%s）" % e)
    if not isinstance(release, dict):
        raise Failed("實例的 plugins.json 沒有 release 設定（release.repo、release.author）：不發佈")
    repo, author = release.get("repo"), release.get("author")
    if not isinstance(repo, str) or not repo.strip():
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
    p = subprocess.run([sys.executable, SCAN, rev, "--repo", ENGINE,
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


# ------------------------------------------------------------------------------------------- making it
def message(version, text):
    return "Altention v%s\n\n%s\n" % (version, text)


def make_commit(tree, parent, version, text, author):
    env = dict(os.environ, GIT_AUTHOR_NAME=author[0], GIT_AUTHOR_EMAIL=author[1], GIT_COMMITTER_NAME=author[0],
               GIT_COMMITTER_EMAIL=author[1])
    for k in ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE"):
        env.pop(k, None)
    args = ["-c", "commit.gpgsign=false", "commit-tree", tree] + (["-p", parent] if parent else [])
    commit = git(*args, inp=message(version, text), env=env, check=True).stdout.strip()
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
    p = subprocess.run([sys.executable, SCAN, probe, "--repo", ENGINE, "--require-blacklist", "--path", META,
                        "--summary"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
                       timeout=600)
    if p.returncode != 0:
        said.append("發佈 commit 的訊息過不了個資掃描：%s" % " ｜ ".join(p.stdout.strip().splitlines()[-6:])[:600])
        return False
    return True


def run(action, version, rev, push):
    said, notes = [], []
    repo, author = settings()
    commit, tree = resolve(rev)
    print("要發佈的：%s（tree %s）" % (commit[:12], tree[:12]))
    clean = privacy(commit, said)
    registered = compat(commit, said, notes)
    main, tags = fetch_public(repo)
    print("公開 repo：%s" % ("main 在 %s，發佈過 %s" % (main[:12], "、".join(sorted(tags)) or "（沒有 tag）") if main else "還是空的（這是第一版）"))
    took = imported(commit, main, author, said)
    text = None
    if action == "publish":
        remote = private_remote()
        tagged = private_tag(remote, version, commit, said) if VERSION.match(version or "") else False
        good_version = version_ok(version, tags, said)
        text = changelog(commit, version, said) if good_version else None
    for n in notes:
        print("提醒：" + n)
    if said or not (clean and registered and took):
        for s in said:
            print("拒絕：" + s)
        return 1
    if action == "check":
        print("可以發佈：個資掃描、COMPAT: 登記、公開 repo 的貢獻都過了")
        return 0
    if text is None:
        return 1
    if not meta_scan(commit, message(version, text), said):
        for s in said:
            print("拒絕：" + s)
        return 1
    new = make_commit(tree, main, version, text, author)
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
    if tagged:
        print(mine)
        return 0
    p = git("push", remote, "%s:refs/tags/v%s" % (commit, version))
    if p.returncode != 0:
        print("公開 repo 發佈了，但這個 repo 的 remote %s 的 tag 沒推上（%s）。補推：git push %s %s:refs/tags/v%s"
              % (remote, (p.stderr.strip() or p.stdout.strip())[:200], remote, commit, version))
        return 2
    print("這個 repo 的 remote %s 也打了 v%s 在 %s" % (remote, version, commit[:12]))
    return 0


def main(argv):
    args = [a for a in argv if a != "--push"]
    push = "--push" in argv
    if not args or args[0] not in ("check", "publish") or (args[0] == "check" and (push or len(args) > 2)) \
            or (args[0] == "publish" and not 2 <= len(args) <= 3):
        print(__doc__.split("\n\n")[1])
        return 2
    action = args[0]
    version = args[1] if action == "publish" else None
    rev = (args[2] if len(args) > 2 else "HEAD") if action == "publish" else (args[1] if len(args) > 1 else "HEAD")
    try:
        return run(action, version, rev, push)
    except Failed as e:
        print("沒有發佈：%s" % e)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
