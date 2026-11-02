"""altention.catalog (C-81 K1): the official plugin list, against a stand-in GitHub (the API and raw files answered
here, the repositories bare ones in a throwaway folder, read by the real git ls-remote)."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest import mock

from altention import catalog, update_go

ORG = catalog.ORG


def git(*args, cwd=None):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.org", "-c", "init.defaultBranch=main"]
                          + list(args), cwd=cwd, check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          universal_newlines=True).stdout.strip()


def plugin_json(name, **more):
    return dict({"format": 1, "name": name, "title": name.title(), "description": "d", "machines": "all"}, **more)


class FakeGitHub:
    """The API's organisation list (with an ETag, pages of `per_page`) and raw.githubusercontent.com, from `repos`;
    every request written down in `asked`."""
    def __init__(self, base):
        self.base, self.repos, self.asked, self.per_page, self.answer = base, [], [], 100, None
        self.etag = '"one"'

    def row(self, name, owner=ORG, private=False, archived=False):
        self.repos.append({"name": name, "owner": {"login": owner}, "private": private, "archived": archived})

    def __call__(self, url, headers, timeout=None):
        self.asked.append((url, dict(headers)))
        if self.answer:
            return self.answer(url, headers)
        if url.startswith(catalog.API):
            if headers.get("If-None-Match") == self.etag:
                return 304, {"ETag": self.etag}, b""
            page = int(url.split("&page=")[1]) if "&page=" in url else 1
            rows = self.repos[(page - 1) * self.per_page:page * self.per_page]
            h = {"ETag": self.etag}
            if page * self.per_page < len(self.repos):
                h["Link"] = '<%s/orgs/%s/repos?type=public&per_page=100&page=%d>; rel="next"' % (catalog.API, ORG, page + 1)
            return 200, h, json.dumps(rows).encode()
        rest = url[len(catalog.RAW) + 1:].split("/", 3)  # org, repo, tag, path
        bare = os.path.join(self.base, rest[0], rest[1] + ".git")
        p = subprocess.run(["git", "-C", bare, "show", "%s:%s" % (rest[2], rest[3])], stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL)
        return (200, {}, p.stdout) if p.returncode == 0 else (404, {}, b"")


class Rig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="清單 catalog ")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        env = mock.patch.dict(os.environ, {"HOME": self.home, "ALT_ROOT": os.path.join(self.home, "altention")})
        env.start()
        self.addCleanup(env.stop)
        self.gh = FakeGitHub(os.path.join(self.tmp, "github"))
        for p in (mock.patch.object(catalog, "_http", self.gh),
                  mock.patch.object(catalog, "GIT_BASE", os.path.join(self.tmp, "github"))):
            p.start()
            self.addCleanup(p.stop)

    def publish(self, repo, d, tags, changelog=None, owner=ORG):
        """A bare repository github/<owner>/<repo>.git with plugin.json `d` (and CHANGELOG.md) at each of `tags`."""
        work = os.path.join(self.tmp, "work", repo)
        os.makedirs(work)
        git("init", "-q", work)
        for tag in tags:
            with open(os.path.join(work, "plugin.json"), "w") as f:
                f.write(d if isinstance(d, str) else json.dumps(d))
            if changelog:
                with open(os.path.join(work, "CHANGELOG.md"), "w") as f:
                    f.write(changelog % {"tag": tag})
            git("add", "-A", cwd=work)
            git("commit", "-qm", tag, "--allow-empty", cwd=work)
            git("tag", tag, cwd=work)
        git("clone", "-q", "--bare", work, os.path.join(self.tmp, "github", owner, repo + ".git"))
        self.gh.row(repo, owner=owner)

    def asked(self, kind):
        return [u for u, _ in self.gh.asked if u.startswith(kind)]


NOTES = "# Changes\n\n## %(tag)s — 2026-10-21\n\n### For people\n\n- **Draws formulas.** As pictures.\n"


class Takes(Rig):
    def test_only_the_organisations_public_plugin_repositories_with_a_tag_and_their_own_plugin_json(self):
        self.publish("plugin-draw", plugin_json("draw", pitch="Draws. @everyone see [here](https://x.example) <@123>"),
                     ["v0.9.0", "v0.10.0", "notes"], NOTES)
        self.publish("plugin-quiz", plugin_json("quiz"), ["v0.1.0"])
        self.publish("plugin-wrong", plugin_json("other"), ["v0.1.0"])     # its plugin.json names another plugin
        self.publish("plugin-broken", "{", ["v0.1.0"])                       # cannot be read
        self.publish("plugin-quiz-private", plugin_json("quiz-private"), ["v0.1.0"])  # a -private one made public
        self.publish("plugin-chatlog", plugin_json("chatlog"), ["v9.0.0"])   # the engine's own name
        self.publish("plugin-untagged", plugin_json("untagged"), [])
        self.publish("plugin-Bad", plugin_json("bad"), ["v0.1.0"])
        self.publish("plugin-theirs", plugin_json("theirs"), ["v0.1.0"], owner="someone")
        self.publish("tools", plugin_json("tools"), ["v0.1.0"])
        self.gh.row("plugin-hidden", private=True)
        self.gh.row("plugin-old", archived=True)
        state, words = catalog.refresh()
        self.assertEqual(state, "fresh", words)
        d = catalog.load()
        self.assertEqual(sorted(d["plugins"]), ["draw", "quiz"])
        draw = d["plugins"]["draw"]
        self.assertEqual(draw["version"], "v0.10.0", "the newest by number, not by text")
        self.assertEqual(draw["repo"], catalog.repo_url("draw"))
        self.assertEqual(draw["plugin"]["pitch"], "Draws. see here")
        self.assertEqual(draw["points"], ["**Draws formulas.**"])
        self.assertEqual(d["plugins"]["quiz"]["points"], [])
        why = dict((x["repo"], x["why"]) for x in d["left_out"])
        self.assertEqual(sorted(why), ["plugin-broken", "plugin-wrong"])
        self.assertIn("讀不懂", why["plugin-broken"])
        self.assertIn("name 是 'other'", why["plugin-wrong"])
        raw = self.asked(catalog.RAW)
        self.assertFalse([u for u in raw if "private" in u or "chatlog" in u or "theirs" in u or "hidden" in u],
                         "nothing read of what is not taken by its row")
        self.assertEqual(len(self.asked(catalog.API)), 1)

    def test_words_cut_short(self):
        self.assertEqual(catalog.plain("a\n b   c", 10), "a b c")
        self.assertEqual(catalog.plain("x" * 50, 10), "x" * 9 + "…")
        self.assertEqual(catalog.plain("<#12> @here ok", 40), "ok")

    def test_pages(self):
        self.gh.per_page = 2
        for n in ("a", "b", "c"):
            self.publish("plugin-%s" % n, plugin_json(n), ["v0.1.0"])
        self.assertEqual(catalog.refresh()[0], "fresh")
        self.assertEqual(sorted(catalog.load()["plugins"]), ["a", "b", "c"])
        self.assertEqual(len(self.asked(catalog.API)), 2)


class Rounds(Rig):
    def setUp(self):
        super().setUp()
        self.publish("plugin-draw", plugin_json("draw"), ["v0.1.0"], NOTES)
        self.now = time.time()
        self.assertEqual(catalog.refresh(now=self.now)[0], "fresh")
        self.gh.asked.clear()

    def test_once_a_day_then_unchanged_costs_one_request(self):
        self.assertEqual(catalog.refresh(now=self.now + 3600), ("kept", ""))
        self.assertEqual(self.gh.asked, [], "not due: nothing asked")
        state, _ = catalog.refresh(now=self.now + catalog.DAY + 1)
        self.assertEqual(state, "same")
        self.assertEqual([(u, h.get("If-None-Match")) for u, h in self.gh.asked], [(self.asked(catalog.API)[0], '"one"')])
        self.assertEqual(catalog.load()["plugins"]["draw"]["version"], "v0.1.0")

    def test_a_new_tag_is_read_the_same_one_is_not(self):
        self.gh.etag = '"two"'
        state, _ = catalog.refresh(now=self.now + catalog.DAY + 1)
        self.assertEqual(state, "fresh")
        self.assertEqual(self.asked(catalog.RAW), [], "same tag: plugin.json not read again")
        work = os.path.join(self.tmp, "work", "plugin-draw")
        git("commit", "-qm", "two", "--allow-empty", cwd=work)
        git("tag", "v0.2.0", cwd=work)
        git("push", "-q", os.path.join(self.tmp, "github", ORG, "plugin-draw.git"), "v0.2.0", cwd=work)
        self.gh.etag = '"three"'
        self.assertEqual(catalog.refresh(now=self.now + 2 * catalog.DAY + 2)[0], "fresh")
        self.assertEqual(catalog.load()["plugins"]["draw"]["version"], "v0.2.0")
        self.assertTrue(self.asked(catalog.RAW))

    def test_a_tag_pushed_alone_is_seen_though_the_list_is_unchanged(self):
        """The organisation's list may answer 304 when only a tag was pushed (#code-review of a287f64): the kept
        plugins' tags are looked at anyway (git, no API request); the same tag reads nothing."""
        work = os.path.join(self.tmp, "work", "plugin-draw")
        git("commit", "-qm", "two", "--allow-empty", cwd=work)
        git("tag", "v0.2.0", cwd=work)
        git("push", "-q", os.path.join(self.tmp, "github", ORG, "plugin-draw.git"), "v0.2.0", cwd=work)
        state, _ = catalog.refresh(now=self.now + catalog.DAY + 1)
        self.assertEqual(state, "fresh")
        self.assertEqual([h.get("If-None-Match") for u, h in self.gh.asked if u.startswith(catalog.API)], ['"one"'],
                         "one API request, answered 304")
        self.assertEqual(catalog.load()["plugins"]["draw"]["version"], "v0.2.0")
        self.assertEqual(catalog.load()["etag"], '"one"')
        self.gh.asked.clear()
        self.assertEqual(catalog.refresh(now=self.now + 2 * catalog.DAY + 2)[0], "same")
        self.assertEqual(self.asked(catalog.RAW), [], "same tag: nothing read")

    def test_one_left_out_is_not_read_again_at_the_same_tag_and_is_taken_once_fixed(self):
        self.publish("plugin-wrong", plugin_json("other"), ["v0.1.0"])
        self.gh.etag = '"two"'
        catalog.refresh(now=self.now + catalog.DAY + 1)
        self.assertEqual([x["repo"] for x in catalog.load()["left_out"]], ["plugin-wrong"])
        self.gh.asked.clear()
        self.gh.etag = '"three"'
        catalog.refresh(now=self.now + 2 * catalog.DAY + 2)
        self.assertEqual(self.asked(catalog.RAW), [], "the same tag: neither read again")
        self.assertEqual([x["repo"] for x in catalog.load()["left_out"]], ["plugin-wrong"])
        work = os.path.join(self.tmp, "work", "plugin-wrong")
        with open(os.path.join(work, "plugin.json"), "w") as f:
            f.write(json.dumps(plugin_json("wrong")))
        git("commit", "-qam", "fixed", cwd=work)
        git("tag", "v0.1.1", cwd=work)
        git("push", "-q", os.path.join(self.tmp, "github", ORG, "plugin-wrong.git"), "v0.1.1", cwd=work)
        self.assertEqual(catalog.refresh(now=self.now + 3 * catalog.DAY + 3)[0], "fresh", "304, and still seen")
        d = catalog.load()
        self.assertEqual((sorted(d["plugins"]), d["left_out"]), (["draw", "wrong"], []))

    def test_a_passing_fault_of_raw_is_not_a_plugin_left_out(self):
        """#code-review of d12afd0, manager 2026-10-21: raw answering 5xx or 429 is not reached (the list kept, nothing
        left out); once it answers, the same tag is taken. One with no plugin.json (404) is looked for again."""
        self.publish("plugin-quiz", plugin_json("quiz"), ["v0.1.0"])
        self.gh.etag = '"two"'
        fault = []

        def http(url, headers, timeout=None):
            if url.startswith(catalog.RAW) and fault:
                return fault[0], {}, b""
            return self.gh(url, headers, timeout)
        t = self.now + catalog.DAY + 1
        with mock.patch.object(catalog, "_http", http):
            for status in (503, 429):
                with self.subTest(status=status):
                    fault[:] = [status]
                    t += catalog.BACKOFF_MAX + 1
                    state, _ = catalog.refresh(now=t)
                    self.assertEqual(state, "unreached")
                    d = catalog.load()
                    self.assertEqual((sorted(d["plugins"]), d["left_out"]), (["draw"], []), "kept as it was")
            fault[:] = []
            state, _ = catalog.refresh(now=t + catalog.BACKOFF_MAX + 1)
        self.assertEqual(state, "fresh")
        self.assertEqual(sorted(catalog.load()["plugins"]), ["draw", "quiz"], "the same tag, taken once it answers")

    def test_one_without_plugin_json_is_looked_for_again(self):
        self.publish("plugin-empty", "{}", ["v0.1.0"])
        os.remove(os.path.join(self.tmp, "work", "plugin-empty", "plugin.json"))
        work = os.path.join(self.tmp, "work", "plugin-empty")
        git("commit", "-qam", "none", cwd=work)
        git("tag", "-f", "v0.1.0", cwd=work)
        git("push", "-qf", os.path.join(self.tmp, "github", ORG, "plugin-empty.git"), "v0.1.0", cwd=work)
        self.gh.etag = '"two"'
        catalog.refresh(now=self.now + catalog.DAY + 1)
        out = catalog.load()["left_out"]
        self.assertEqual([(x["repo"], x["read"]) for x in out], [("plugin-empty", False)])
        self.gh.asked.clear()
        catalog.refresh(now=self.now + 2 * catalog.DAY + 2)
        self.assertTrue([u for u in self.asked(catalog.RAW) if "plugin-empty" in u], "looked for again at the same tag")

    def test_not_reached_keeps_the_list_and_backs_off(self):
        def down(url, headers):
            raise catalog.Unreached("no network")
        self.gh.answer = down
        later = self.now + catalog.DAY + 1
        state, words = catalog.refresh(now=later)
        self.assertEqual(state, "unreached")
        self.assertIn("用上次的", words)
        d = catalog.load()
        self.assertEqual(sorted(d["plugins"]), ["draw"], "the list from last time stays")
        self.assertEqual(d["wait_until"], later + catalog.BACKOFF_FIRST)
        self.assertEqual(catalog.refresh(now=later + 60), ("kept", ""))
        self.assertEqual(catalog.refresh(now=later + 60, force=True), ("kept", ""), "not even forced while waiting")
        catalog.refresh(now=later + catalog.BACKOFF_FIRST + 1)
        self.assertEqual(catalog.load()["wait_until"], later + catalog.BACKOFF_FIRST + 1 + 2 * catalog.BACKOFF_FIRST)
        self.gh.answer = None
        self.gh.etag = '"two"'
        self.assertEqual(catalog.refresh(now=later + 10 * catalog.DAY)[0], "fresh")
        self.assertEqual((catalog.load()["fails"], catalog.load()["wait_until"]), (0, 0))

    def test_limited_waits_for_the_reset_and_is_not_unreached(self):
        reset = self.now + catalog.DAY + 1800
        self.gh.answer = lambda url, headers: (403, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(reset)}, b"")
        state, words = catalog.refresh(now=self.now + catalog.DAY + 1)
        self.assertEqual(state, "limited", words)
        d = catalog.load()
        self.assertEqual((d["wait_until"], d.get("fails", 0)), (reset, 0))
        self.assertEqual(catalog.refresh(now=reset - 1), ("kept", ""))

    def test_a_repository_unreachable_by_git_is_not_reached(self):
        shutil.rmtree(os.path.join(self.tmp, "github", ORG, "plugin-draw.git"))
        self.gh.etag = '"two"'
        with mock.patch.object(catalog.plugin_update, "_network", return_value=True):
            state, _ = catalog.refresh(now=self.now + catalog.DAY + 1)
        self.assertEqual(state, "unreached")
        self.assertEqual(sorted(catalog.load()["plugins"]), ["draw"])

    def test_the_deadline_leaves_the_rest_for_next_time(self):
        self.publish("plugin-quiz", plugin_json("quiz"), ["v0.1.0"])
        self.gh.etag = '"two"'
        state, words = catalog.refresh(deadline=time.time() + 1, now=self.now + catalog.DAY + 1)
        self.assertEqual(state, "partial", words)
        d = catalog.load()
        self.assertEqual(sorted(d["plugins"]), ["draw"], "kept as it was; quiz next time")
        self.assertNotIn("etag", d)
        self.assertEqual(d["at"], self.now, "the day's mark not moved: looked at again next round")
        self.assertEqual(catalog.refresh(now=self.now + catalog.DAY + 2)[0], "fresh")
        self.assertEqual(sorted(catalog.load()["plugins"]), ["draw", "quiz"])

    def test_forced_at_most_once_in_a_while(self):
        self.assertEqual(catalog.refresh(force=True, now=self.now + 60), ("kept", ""))
        self.assertEqual(catalog.refresh(force=True, now=self.now + catalog.FORCE_GAP + 1)[0], "same")

    def test_an_unreadable_list_is_none_and_looked_for_again(self):
        with open(catalog.path(), "w") as f:
            f.write("{")
        self.assertIsNone(catalog.load())
        self.assertEqual(catalog.refresh(now=self.now + 60)[0], "fresh")


class News(Rig):
    """Owner 2026-10-21: a new plugin on the list is said once in #update, never asked about."""
    def keep(self, names):
        os.makedirs(os.path.dirname(catalog.path()), exist_ok=True)
        with open(catalog.path(), "w") as f:
            json.dump({"format": 1, "at": time.time(), "left_out": [], "plugins": {
                n: {"version": "v0.1.0", "plugin": {"name": n, "title": n.title(), "pitch": "做 %s。" % n}} for n in names}}, f)

    def test_the_first_list_is_not_news_then_each_new_one_once(self):
        self.assertEqual(catalog.news(), [], "no list")
        self.keep(["draw"])
        self.assertEqual(catalog.news(), [], "the first list: what #setup offers, not news")
        self.keep(["draw", "quiz", "mine"])
        new = catalog.news(named=["mine"])
        self.assertEqual([n for n, _ in new], ["quiz"], "the instance's own: not news")
        self.assertEqual([n for n, _ in catalog.news()], ["quiz"], "not told until marked")
        self.assertEqual(catalog.news_text(new), "🧩 官方插件清單多了：Quiz（quiz）：做 quiz。要裝的話，到 #setup 跟 Claude 說「裝 quiz」。")
        two = catalog.news_text(new + [("draw", {"plugin": {"title": "Draw"}})])
        self.assertTrue(two.endswith("說「裝」加上括號裡的名稱。"), two)
        self.assertIn("Draw（draw）", two)
        catalog.mark_told(["quiz"])
        self.assertEqual(catalog.news(), [])
        self.keep(["draw", "quiz", "mine"])
        self.assertEqual(catalog.news(), [], "mine was marked when the instance named it")


class Round(Rig):
    def test_the_round_says_a_look_and_never_fails_for_it(self):
        self.publish("plugin-draw", plugin_json("draw"), ["v0.1.0"])
        self.assertEqual(update_go.catalog_look(time.time() + 300), ["官方插件清單：1 個"])
        self.assertEqual(update_go.catalog_look(time.time() + 300), [], "not due: nothing said")
        with mock.patch.object(catalog, "refresh", side_effect=RuntimeError("boom")):
            self.assertEqual(update_go.catalog_look(time.time() + 300), ["官方插件清單這一輪出錯（RuntimeError: boom）"])

    def test_news_said_in_update_and_marked_only_once_sent(self):
        News.keep(self, ["draw"])
        self.assertEqual(update_go.catalog_news(), [])
        News.keep(self, ["draw", "quiz"])
        sent = []
        with mock.patch.object(update_go.update, "send", side_effect=lambda t: sent.append(t) and False), \
                mock.patch.object(update_go.config, "settings", return_value={}):
            self.assertEqual(update_go.catalog_news(), ["官方插件清單多了：quiz（#update 沒送出去，下一輪再說）"])
        with mock.patch.object(update_go.update, "send", side_effect=lambda t: sent.append(t) or True), \
                mock.patch.object(update_go.config, "settings", return_value={}):
            self.assertEqual(update_go.catalog_news(), ["官方插件清單多了：quiz（已在 #update 說）"])
            self.assertEqual(update_go.catalog_news(), [])
        self.assertEqual(len(sent), 2)
        self.assertIn("Quiz（quiz）", sent[-1])


if __name__ == "__main__":
    unittest.main()
