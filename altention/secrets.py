"""Where secrets lie, so that no agent is let read them (backlog K-03; the hook that refuses: dcbot/tools/dchook
--guard). Standard library only, Python 3.8: the guard runs on every machine, before every tool call of every
Claude Code there, so this reads one small file at most and starts nothing.

The list: DEFAULT (the bot's token, private SSH keys, the plugins' secrets folder) and the instance's own additions (a plugin's key files)
(setting "core.secret_paths", a list of paths; "~" and "*" as in DEFAULT: "*" within the last name only, a path ending
in "/**" everything under that folder, a "**" anywhere else not supported). A path ending in
".pub" is a public key and never secret. ~/.ssh/config and known_hosts are not secret: the machines' settings rest
on them and the owner has Claude edit them; nor authorized_keys (public keys, though its name has "key").

What it covers and what not (written for the owner, docs and GUIDE say the same): a tool asked to read, write or
search a listed path, or a shell command that names one, is refused. A path built at run time (a variable, a
script that opens the file) is not seen: the token file is also mode 600, and a separate system user for the
bot is the stronger guard (stage 6).
"""
import fnmatch
import glob
import json
import os
import re
import shlex
import sys

DEFAULT = ("~/.config/dcbot/**", "~/.ssh/id_*", "~/.ssh/*key*",
           "~/.config/altention/secrets/**")  # plugins' secrets (C-77 G3, altention/secret_store.py); an installation adds its own: core.secret_paths
PUBLIC = ".pub"
OPEN = ("authorized_keys", "authorized_keys2")  # public keys under a name "*key*" takes in: never secret
SETTING = "secret_paths"  # under "core" in the instance's plugins.json
SWITCH = "secret_guard"   # under "core": false turns the guard off (it is on when the settings cannot be read)


def core():
    """The instance's "core" settings, read straight from its plugins.json: no retries, no waiting (a broken file is
    not this module's to wait for). -> (dict, problem): problem is None, or why they could not be read."""
    try:
        from . import config
        inst = config.instance_dir()
    except Exception as e:
        return {}, "%s: %s" % (type(e).__name__, e)
    path = os.path.join(inst, "plugins.json") if inst else None
    if not path or not os.path.exists(path):
        return {}, None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        said = data.get("core") if isinstance(data, dict) else None
    except Exception as e:
        return {}, "%s: %s" % (type(e).__name__, e)
    return (said if isinstance(said, dict) else {}), None


def guard_on():
    """Is the guard on? Only an explicit false in the instance's settings turns it off."""
    return core()[0].get(SWITCH) is not False


def extra():
    """The instance's own additions. -> (paths, problem): problem is None, or why they could not be read."""
    settings, problem = core()
    if problem:
        return [], problem
    said = settings.get(SETTING)
    if said is None:
        return [], None
    if not isinstance(said, list):
        return [], "core.%s is not a list" % SETTING
    return [p for p in said if isinstance(p, str) and p.strip()], None


def patterns(home=None):
    """Every listed path as an absolute pattern. -> (patterns, problem) as extra() says."""
    home = home or os.path.expanduser("~")
    more, problem = extra()
    out = []
    for p in list(DEFAULT) + more:
        p = p.strip()
        p = home + p[1:] if p == "~" or p.startswith("~/") else p
        out.append(p if os.path.isabs(p) else os.path.join(home, p))
    return out, problem


def deny_rules(home=None):
    """Claude Code's own deny rules for the listed paths (for the conversations the bot opens: --settings), in its
    syntax: "~/…" under the home folder, "//…" for another absolute path ("/…" alone would be the settings file's
    folder). Read and Edit. Not for a pattern whose last name has a "*" in it (~/.ssh/id_*): Claude Code's rules have
    no "but not *.pub", so those are left to the guard, which knows the exceptions."""
    home = home or os.path.expanduser("~")
    out = []
    for p in patterns(home)[0]:
        if "*" in os.path.basename(p) and not p.endswith("/**"):
            continue
        rule = "~" + p[len(home):] if p.startswith(home + "/") else "/" + p
        out += ["Read(%s)" % rule, "Edit(%s)" % rule]
    return out


FOLD = sys.platform == "darwin"  # a file system that does not tell .ssh from .SSH (macOS's APFS by default): compare casefolded


def _fold(text, fold=None):
    return text.casefold() if (FOLD if fold is None else fold) else text


def matches(path, pattern, fold=None):
    """Does an absolute `path` fall under `pattern`? A pattern ending in "/**": anything under that folder; else
    "*" within its last name only (a "**" elsewhere is not supported: manager 2026-11-03). On a file system that does
    not tell case apart (`fold`, by default macOS) ~/.SSH/ID_RSA is ~/.ssh/id_rsa."""
    path, pattern = _fold(path, fold), _fold(pattern, fold)
    if path.endswith(PUBLIC) or os.path.basename(path) in OPEN:
        return False
    if pattern.endswith("/**"):
        top = pattern[:-3]
        return path == top or path.startswith(top + "/")
    head, name = os.path.split(pattern)
    return os.path.dirname(path) == head and fnmatch.fnmatchcase(os.path.basename(path), name)


def is_secret(path, listed=None, cwd=None, fold=None):
    """Is `path` (as a tool was handed it: "~", relative to `cwd`, through links) a listed secret?"""
    if not isinstance(path, str) or not path:
        return False
    listed = listed if listed is not None else patterns()[0]
    p = os.path.expanduser(path)
    if not os.path.isabs(p):
        p = os.path.join(cwd or os.getcwd(), p)
    seen = {os.path.normpath(p)}
    try:
        seen.add(os.path.realpath(p))
    except OSError:
        pass
    return any(matches(q, pat, fold) for q in seen for pat in listed)


def holds(folder, listed=None, home=None, fold=None, cwd=None):
    """Does a search of `folder` reach into a listed secret: is it a folder above one, inside the home folder but
    not the home folder itself (~/.config, ~/.ssh, ~/.config/dcbot)? A search of the home folder itself, of the
    root, or of a folder outside home is not judged here (manager 2026-10-14: a known limit). A relative `folder` is
    taken from `cwd` (the tool call's)."""
    home = home or os.path.expanduser("~")
    if not isinstance(folder, str) or not folder:
        return False
    f = _normal(folder, home, cwd)
    if not f or not os.path.isabs(f):
        return False
    hf, ff = _fold(home, fold), _fold(f.rstrip("/") or "/", fold)
    if not ff.startswith(hf + "/"):
        return False
    listed = listed if listed is not None else patterns(home)[0]
    return any(_fold(p, fold).startswith(ff + "/") or (p.endswith("/**") and _fold(p[:-3], fold) == ff) for p in listed)


WILD = re.compile(r"[*?\[{]")  # a glob's wildcards (Glob takes {a,b} too)


def _plain(path):
    """(the folder a glob starts from: its names up to the first with a wildcard, whether it had one)."""
    names = path.split("/")
    for i, name in enumerate(names):
        if WILD.search(name):
            return "/".join(names[:i]) or "/", True
    return path, False


def searched(base, pattern=None, listed=None, home=None, fold=None, cwd=None, deep=True, any_level=False):
    """Could a search reach a listed secret (C-126, manager 2026-11-03: judged broadly)? `base` the folder searched
    (Grep's or Glob's path, else the call's cwd), `pattern` a glob within it, or an absolute one (Glob's pattern,
    Grep's glob); deep: the whole tree under it is looked through (Grep), else only what the glob names (Glob).
    The folder the glob starts from is taken (the names before the first wildcard): a secret itself, or any folder
    above one, the home folder and the root included, when the rest has a wildcard or the search is deep. A glob
    with nothing absolute to start from (no base, no cwd) is not judged. any_level (Glob): a last name with no
    wildcard is looked for at every level under its folder (Claude Code hands a name with no "/" to ripgrep as
    such: #code-review-2 on e815c9b), so that folder is where it starts. -> the folder that reaches one, or None."""
    home = home or os.path.expanduser("~")
    listed = listed if listed is not None else patterns(home)[0]
    base = base if isinstance(base, str) and base else cwd  # no path given: the call's own folder
    b = _normal(base, home, cwd) if isinstance(base, str) and base else None
    where = b
    if isinstance(pattern, str) and pattern:
        p = _normal(pattern, home)  # ~, $HOME at its start; relative: from the base below
        where = p if os.path.isabs(p) else (os.path.join(b, p) if b and os.path.isabs(b) else None)
    if not where or not os.path.isabs(where):
        return None
    where = re.sub(r"/+", "/", where)
    top, wild = _plain(where)
    if wild and ".." in where[len(top):].split("/"):  # climbs back up past its start: it may reach anywhere
        top = "/"
    top = os.path.normpath(top)
    if is_secret(top, listed, fold=fold):
        return top
    if not wild and any_level:
        top = os.path.dirname(top) or "/"
    if not (wild or deep or any_level):
        return None
    t = _fold(top.rstrip("/") or "/", fold)
    for pat in listed:
        start = _fold(_plain(pat)[0].rstrip("/") or "/", fold)
        if start == t or start.startswith(t.rstrip("/") + "/"):
            return top
    return None


def _normal(word, home, cwd=None):
    """A path as a command line writes it, as the shell would see it before running: quotes are gone already
    (shlex); "$HOME", "${HOME}", "~" and "~<this user>" at its start are the home folder; "//" and "/./" and ".."
    are folded. Any other word is taken from `cwd` when one is given (the folder the command starts in: what a cd
    in the command changes is not followed), else given back as it is; an option (-x, --x=…) is never a path."""
    w = word
    for lead in ("${HOME}", "$HOME"):
        if w == lead or w.startswith(lead + "/"):
            w = home + w[len(lead):]
    if w.startswith("~"):
        user, _, rest = w[1:].partition("/")
        if user in ("", _user()):
            w = home + ("/" + rest if rest or w.endswith("/") else "")
    if not w.startswith("/"):
        if not cwd or not os.path.isabs(cwd) or not w or w.startswith("-"):
            return w
        w = os.path.join(cwd, w)
    return os.path.normpath(re.sub(r"/+", "/", w))


def _user():
    try:
        import pwd
        return pwd.getpwuid(os.getuid()).pw_name
    except Exception:
        return os.environ.get("USER") or os.environ.get("LOGNAME") or ""


BRACES = 256  # brace expansions of one word, at most: a word of JSON ({"a":1,"b":2}…) multiplies past any time


def _braces(word):
    """Brace expansion as the shell does it, for one word ("a{b,c}d" -> ["abd", "acd"]); nested ones too. At most
    BRACES words come back (C-108: a JSON line in a command took minutes)."""
    out, todo = [], [word]
    while todo and len(out) + len(todo) <= BRACES:
        w = todo.pop(0)
        m = re.search(r"\{([^{}]*,[^{}]*)\}", w)
        if not m:
            out.append(w)
            continue
        todo += [w[:m.start()] + part + w[m.end():] for part in m.group(1).split(",")]
    return (out + todo)[:BRACES]


GLOB = re.compile(r"[*?\[]")
RECURSIVE = {"grep": ("-r", "-R", "--recursive", "-d"), "egrep": ("-r", "-R"), "fgrep": ("-r", "-R"), "rg": None, "ag": None,
             "ack": None, "find": None, "tar": None, "zip": None, "7z": None, "rsync": None, "scp": ("-r",), "cp": ("-r", "-R", "-a"),
             "ls": ("-R",), "tree": None, "base64": None, "xxd": None}
# a command that, given a folder, reads what is inside it (None: whatever its options); du only sizes, ls only names


def _reads_inside(words):
    """Does this command (its words, the command first) read into a folder it is given?"""
    name = os.path.basename(words[0]) if words else ""
    if name not in RECURSIVE:
        return False
    flags = RECURSIVE[name]
    if flags is None:
        return True
    for w in words[1:]:
        if w.startswith("--") and w in flags:
            return True
        if w.startswith("-") and not w.startswith("--") and any(("-" + c) in flags for c in w[1:]):
            return True
    return False


KEY_USERS = ("ssh", "scp", "sftp")  # take a private key to log in with as -i's argument: they use it, not show it


def _key_only_used(words):
    """The places (indices into `words`, the command first) where a key is handed over to be used, not read out
    (manager 2026-10-14): -i's argument of ssh, scp, sftp; the keys ssh-add loads; -f's argument of ssh-keygen -y
    (prints the public key). Anywhere else in the same command (a command ssh runs over there) is judged as usual."""
    name = os.path.basename(words[0]) if words else ""
    out = set()
    if name in KEY_USERS:
        out = set(i + 1 for i, w in enumerate(words[:-1]) if w == "-i" and i > 0)
    elif name == "ssh-add":
        out = set(i for i, w in enumerate(words) if i > 0 and not w.startswith("-"))
        out -= set(i + 1 for i, w in enumerate(words) if w in ("-t", "-E", "-S", "-H", "-h"))  # options taking a value
    elif name == "ssh-keygen" and "-y" in words:
        out = set(i + 1 for i, w in enumerate(words[:-1]) if w == "-f" and i > 0)
    return out


def _segments(command):
    """The simple commands of a command line, each as its words (quotes taken off), split at ; && || | and new
    lines; leading VAR=value words, sudo, env, time, nohup and command left out. A line shlex cannot read (an
    unclosed quote) is split on blanks."""
    out = []
    for part in re.split(r"\|\||&&|[;|\n]", command):
        try:
            words = shlex.split(part, comments=False, posix=True)
        except ValueError:
            words = part.split()
        while words and (re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]) or words[0] in ("sudo", "env", "time", "nohup", "command", "exec")):
            words = words[1:]
        if words:
            out.append(words)
    return out


def _pieces(word):
    """The paths inside one word: the word, and what follows "=", "<", ">" or ":" in it (--file=~/x, <~/x, host:~/x)."""
    out = [word]
    for sep in ("=", "<", ">", ":"):
        if sep in word:
            out += [p for p in word.split(sep)[1:] if p]
    return out


def mentions(command, listed=None, home=None, fold=None, cwd=None):
    """The listed secrets a shell command names, or []: a path written in it (quotes, "$HOME", "${HOME}", "~",
    "~<user>", "//", "/./" as the shell reads them) that is a listed secret; a pattern in it (* ? [ { ) that the shell
    would expand to one, judged against the files there are and against the list itself; a folder above a listed
    secret, inside home but not home itself, given to a command that reads into folders (grep -r, rg, find, tar, zip,
    rsync, cp -r, ls -R: not du, not plain ls). A relative path is taken from `cwd` (the folder the tool call starts
    in). Text the command only hands over (a heredoc's body, a quoted string over several lines, a * in quotes) is
    judged without its patterns: the shell does not expand them there (C-108: a JSDoc "/**" line in a heredoc); text
    handed to a shell (bash -c, ssh, eval, a heredoc fed to one) and every $(…) and `…` the shell runs are judged as
    commands. Not seen: a relative path after a cd in the command, a search of the whole home
    folder, a path put together at run time, a program that opens the file itself (a known limit: GUIDE, SECURITY)."""
    if not isinstance(command, str) or not command:
        return []
    home = home or os.path.expanduser("~")
    listed = listed if listed is not None else patterns(home)[0]
    return list(dict.fromkeys(_mentions(command, listed, home, fold, cwd, 0)))


def _by_lines(command, listed, home, fold, cwd, text=False):
    """The way every command was judged before C-108: split at lines and ; && || |, each part read by shlex. Used
    for what the reader below does not follow (it refuses more), and with text=True for text that is only handed
    over: a pattern in it is not expanded, and every word counts, the first of a line too (it is no command name);
    a path written out in full is refused there as before."""
    found = []
    for words in _segments(command):
        _judge(words, [not text] * len(words), listed, home, fold, cwd, found, None, first=text)
    return found


def _judge(words, globbing, listed, home, fold, cwd, found, depth, first=False, runs=False):
    """One simple command's words (wrappers already off), each with whether a pattern in it is expanded; the first
    is its name and left out unless `first`. With a `depth` (the C-108 reader), a word with a blank in it that comes
    after a shell (SHELLS) is judged as commands, and a word with a blank, a new line or ; | & in it as text too (a
    quoted sentence that writes a secret's path out in full: the judgement by lines caught it, splitting the quotes).
    `runs`: what this command prints is run as a command, so its words are judged as a shell's."""
    inside, used = _reads_inside(words), _key_only_used(words)
    shell = (0 if runs else _shell_at(words)) if depth is not None else None
    for at, word in enumerate(words):
        if (at == 0 and len(words) > 1 and not first) or at in used:
            continue
        if shell is not None and at > shell and re.search(r"\s", word):
            found += _mentions(word, listed, home, fold, cwd, depth + 1, fed=True)
            continue
        if depth is not None and re.search(r"[\s;|&]", word):
            found += _by_lines(word, listed, home, fold, cwd, text=True)
            if "\n" in word:
                continue
        globs = globbing[at] or (shell is not None and at > shell)  # (ssh, eval, watch join their words for a shell
        # there, which reads them again: a quoted or escaped pattern is expanded after all)
        for piece in _pieces(word):
            for shape in (_braces(piece) if globs else [piece]):
                path = _normal(shape, home, cwd)
                if not path.startswith("/"):
                    continue
                if globs and GLOB.search(path):
                    if _could_be(path, listed, fold) or any(is_secret(p, listed, fold=fold) for p in glob.glob(path)[:200]):
                        found.append(piece)
                elif is_secret(path, listed, fold=fold) or (inside and holds(path, listed, home, fold)):  # (absolute by now)
                    found.append(piece)


SHELLS = ("sh", "bash", "zsh", "dash", "ksh", "ash", "fish", "su", "ssh", "eval", "watch", "tmux")  # run text they are handed as commands
SOURCES = ("source", ".")  # the same, as a command's name only (". " elsewhere is the folder)
WRAPPERS = ("sudo", "env", "time", "nohup", "command", "exec")  # left out before the command's own name (as _segments)
DEPTH = 10  # text handed to a shell inside text handed to a shell…: deeper than this, judged by lines (as before C-108)
BREAKS = ";&|()"
ASSIGNS = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
RESERVED = ("if", "then", "elif", "else", "while", "until", "do", "!", "{")  # a command's name comes next
LAUNCHERS = WRAPPERS + ("timeout", "nice", "stdbuf", "ionice", "xargs", "setsid", "doas", "taskset", "chrt", "builtin",
                        "coproc")  # run their arguments (time is one: time -p …); other programs that do are a known limit
REDIRECT = re.compile(r"\d*(>>|>&|<&|&>|>\||[<>])")  # a redirection's start; fullmatch: its target is the next word


class Unread(Exception):
    """A command line the reader does not follow (an unclosed quote, a heredoc with no end, $'…')."""


def _shell_at(words):
    """Where in a simple command's words a shell is (SHELLS anywhere, SOURCES as its name), or None."""
    if words and words[0] in SOURCES:
        return 0
    return next((k for k, w in enumerate(words) if os.path.basename(w) in SHELLS), None)


def _mentions(command, listed, home, fold, cwd, depth, fed=False, runs=False):
    """mentions() without the clean-up: the C-108 reader, or by lines when it does not follow the command, or trips
    on anything at all (the guard lets a call through when it fails, so a fault here must refuse more, not less).
    `fed`: the command is text a shell runs, so every heredoc in it, inside a $(…) too, is commands. `runs`: what
    it prints is run as a command too (a $(…) in a command's name)."""
    if depth > DEPTH:
        return _by_lines(command, listed, home, fold, cwd)
    try:
        return _read(command, listed, home, fold, cwd, depth, fed, runs)
    except Exception:
        return _by_lines(command, listed, home, fold, cwd)


def _read(command, listed, home, fold, cwd, depth, fed=False, runs=False):
    lines, subs, notes = _lex(command)
    found = []
    for sub, named in subs:  # (named: $(…) in a command's name, whose output runs as a command, so its text is commands)
        found += _mentions(sub, listed, home, fold, cwd, depth + 1, fed or named, runs=named)
    for note in notes:
        found += _by_lines(note, listed, home, fold, cwd, text=True)
    for segs, docs in lines:
        line_fed = fed
        for words in segs:
            start = 0
            while start < len(words) and (ASSIGNS.match(words[start][0]) or words[start][0] in WRAPPERS):
                value = words[start][0].partition("=")[2]
                if re.search(r"[\s;|&]", value):  # (M='a sentence that writes a path out in full': text, as in _judge)
                    found += _by_lines(value, listed, home, fold, cwd, text=True)
                start += 1
            words = words[start:]
            if words:
                line_fed = line_fed or _shell_at([w for w, _ in words]) is not None
                _judge([w for w, _ in words], [g for _, g in words], listed, home, fold, cwd, found, depth, runs=runs)
        for body, quoted in docs:
            if line_fed:  # (bash <<EOF, ssh host <<EOF, cat <<EOF | sh, source /dev/stdin <<EOF: the body is commands)
                found += _mentions(body, listed, home, fold, cwd, depth + 1)
                continue
            found += _by_lines(body, listed, home, fold, cwd, text=True)
            if not quoted:  # (<<EOF, not <<'EOF': the shell runs the $(…) and `…` in the body)
                for sub in _subs_in(body):
                    found += _mentions(sub, listed, home, fold, cwd, depth + 1)
    return found


def _lex(s):
    """A command line read the way the shell reads it (C-108). Gives its lines, each (simple commands, heredocs): a
    simple command is its words, each (word with quotes and backslashes taken off, whether a * ? [ { in it stands
    outside quotes, so the shell expands it); a heredoc is (body, whether its end word is quoted). And the text of
    every $(…) and `…` outside single quotes, each (text, whether it stands in a command's name: the shell runs what
    it prints). A line ending in | && || goes on into the next. Raises Unread on what it does not follow. And the
    comments (# at the start of a word, to the end of the line), as text."""
    lines, subs, notes = [], [], []
    st = {"segs": [], "words": [], "docs": [], "cur": [], "glob": False, "started": False}

    def word():
        if st["started"]:
            st["words"].append(("".join(st["cur"]), st["glob"]))
        st["cur"], st["glob"], st["started"] = [], False, False

    def seg():
        word()
        if st["words"]:
            st["segs"].append(st["words"])
        st["words"] = []

    def named():
        """Could the word being read be what the shell runs: not a VAR=… itself, and before it only VAR=… and
        reserved words, or a command that runs its arguments (LAUNCHERS: then any word of it may be, its options
        are not told apart; it refuses more)."""
        if ASSIGNS.match("".join(st["cur"])):
            return False
        prior = [w for w, _ in st["words"]]
        k = 0
        while k < len(prior):
            w = prior[k]
            if ASSIGNS.match(w) or w in RESERVED:
                k += 1
            elif w == "function":  # (function f { …: the name comes next, then the body's commands)
                k += 2
            elif REDIRECT.match(w):  # (2>/dev/null, or > and its target as two words)
                k += 2 if REDIRECT.fullmatch(w) else 1
            else:
                break
        return k >= len(prior) or os.path.basename(prior[k]) in LAUNCHERS

    i, n, waiting, after_break = 0, len(s), [], ""
    while i < n:
        c = s[i]
        if c == "\\":
            if i + 1 >= n:
                raise Unread("a backslash at the end")
            if s[i + 1] != "\n":
                st["cur"].append(s[i + 1])
                st["started"] = True
            i += 2
        elif c == "'":
            j = s.find("'", i + 1)
            if j < 0:
                raise Unread("an unclosed quote")
            st["cur"].append(s[i + 1:j])
            st["started"], i = True, j + 1
        elif c == '"':
            i = _double(s, i + 1, st["cur"], subs, named())
            st["started"] = True
        elif s.startswith("$'", i):
            raise Unread("$'…'")
        elif s.startswith("$(", i) or c == "`":
            j = _sub_end(s, i)
            subs.append((s[i + 2:j] if c == "$" else s[i + 1:j], named()))
            st["cur"].append(s[i:j + 1])
            st["started"], i = True, j + 1
        elif s.startswith("<<", i) and not s.startswith("<<<", i):
            word()
            i, end, strip, quoted = _doc_word(s, i + 2)
            waiting.append((end, strip, quoted))
        elif c == "\n":
            word()
            goes_on = not st["words"] and after_break in ("|", "&")
            seg()
            i += 1
            for end, strip, quoted in waiting:
                i, body = _doc_body(s, i, end, strip)
                st["docs"].append((body, quoted))
            waiting = []
            if not goes_on:
                if st["segs"] or st["docs"]:
                    lines.append((st["segs"], st["docs"]))
                st["segs"], st["docs"] = [], []
        elif c == "#" and not st["started"]:
            j = s.find("\n", i)
            j = n if j < 0 else j
            notes.append(s[i + 1:j])
            i = j
        elif c in " \t":
            word()
            i += 1
        elif c in BREAKS:
            seg()
            after_break, i = c, i + 1
            continue
        else:
            st["glob"] = st["glob"] or c in "*?[{"
            st["cur"].append(c)
            st["started"], i = True, i + 1
        if st["started"]:
            after_break = ""
    if waiting:
        raise Unread("a heredoc with no end")
    seg()
    if st["segs"] or st["docs"]:
        lines.append((st["segs"], st["docs"]))
    return lines, subs, notes


def _double(s, i, cur, subs, named):
    """Read a "…" from just after its opening quote into `cur`; the index after the closing one. `named`: it is in
    a command's name (see _lex)."""
    n = len(s)
    while i < n:
        c = s[i]
        if c == '"':
            return i + 1
        if c == "\\" and i + 1 < n and s[i + 1] in '$`"\\\n':
            if s[i + 1] != "\n":
                cur.append(s[i + 1])
            i += 2
        elif s.startswith("$(", i) or c == "`":
            j = _sub_end(s, i)
            subs.append((s[i + 2:j] if c == "$" else s[i + 1:j], named))
            cur.append(s[i:j + 1])
            i = j + 1
        else:
            cur.append(c)
            i += 1
    raise Unread("an unclosed quote")


def _sub_end(s, i):
    """Where the $(…) or `…` starting at s[i] ends (the index of its ) or closing `)."""
    n = len(s)
    if s[i] == "`":
        j = i + 1
        while j < n:
            if s[j] == "\\":
                j += 2
                continue
            if s[j] == "`":
                return j
            j += 1
        raise Unread("an unclosed `")
    depth, j = 0, i + 1
    while j < n:
        c = s[j]
        if c == "\\":
            j += 2
            continue
        if c in "'\"":
            k = j + 1
            while k < n and s[k] != c:
                k += 2 if (c == '"' and s[k] == "\\") else 1
            if k >= n:
                raise Unread("an unclosed quote")
            j = k
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return j
        j += 1
    raise Unread("an unclosed $(")


def _doc_word(s, i):
    """A heredoc's end word, from just after "<<": (index after it, the word, <<- , whether any of it is quoted)."""
    n = len(s)
    strip = s.startswith("-", i)
    i += strip
    while i < n and s[i] in " \t":
        i += 1
    end, quoted = [], False
    while i < n and s[i] not in " \t\n<>" + BREAKS:
        c = s[i]
        if c in "'\"":
            j = s.find(c, i + 1)
            if j < 0:
                raise Unread("an unclosed quote")
            end.append(s[i + 1:j])
            quoted, i = True, j + 1
        elif c == "\\":
            if i + 1 >= n:
                raise Unread("a backslash at the end")
            end.append(s[i + 1])
            quoted, i = True, i + 2
        elif c in "$`":
            raise Unread("a heredoc's end word with $ or `")
        else:
            end.append(c)
            i += 1
    if not end:
        raise Unread("a heredoc with no end word")
    return i, "".join(end), strip, quoted


def _doc_body(s, i, end, strip):
    """A heredoc's body from s[i] up to its end line: (index after the end line, the body)."""
    n, body = len(s), []
    while i < n:
        j = s.find("\n", i)
        j = n if j < 0 else j
        line = s[i:j]
        if (line.lstrip("\t") if strip else line) == end:
            return min(j + 1, n), "\n".join(body)
        body.append(line)
        i = j + 1
    raise Unread("a heredoc with no end")


def _subs_in(body):
    """The $(…) and `…` in the body of a heredoc whose end word is not quoted (the shell runs them)."""
    out, i, n = [], 0, len(body)
    while i < n:
        c = body[i]
        if c == "\\":
            i += 2
        elif body.startswith("$(", i) or c == "`":
            j = _sub_end(body, i)
            out.append(body[i + 2:j] if c == "$" else body[i + 1:j])
            i = j + 1
        else:
            i += 1
    return out


def _could_be(pattern, listed, fold=None):
    """Would the shell pattern `pattern` match a listed secret, by the list alone (whether or not the file is there):
    tried against each listed path with its own "*" and "**" stood in by a name."""
    pattern = _fold(pattern, fold)
    parts = pattern.split("/")
    for p in listed:
        p = _fold(p, fold)
        if p.endswith("/**"):  # a pattern reaching the folder, or anything in it (~/.config/dcbo?/env)
            top = p[:-3]
            if any(fnmatch.fnmatchcase(top, "/".join(parts[:k])) for k in range(2, len(parts) + 1)):
                return True
            continue
        sample = p.replace("*", "x")  # the listed one written out, against the command's pattern
        if fnmatch.fnmatchcase(sample, pattern) and matches(sample, p, fold=False):
            return True
        # and the command's pattern written out (a class by its first letter, * by nothing), against the listed one
        written = re.sub(r"\[!?\^?(.)[^\]]*\]", r"\1", pattern).replace("*", "").replace("?", "x")
        if matches(written, p, fold=False):
            return True
    return False
