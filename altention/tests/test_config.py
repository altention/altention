"""altention.config: the instance, the old places while it is missing, and one machine without any list."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import json
import os
import tempfile
import unittest
from unittest import mock

from altention import config, text


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(data if isinstance(data, str) else json.dumps(data))


class Base(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.engine = tempfile.mkdtemp()
        env = {"HOME": self.home}
        self.patches = [mock.patch.dict(os.environ, env), mock.patch.object(config, "ENGINE", self.engine),
                        mock.patch.object(config, "hostname", return_value="box")]
        for p in self.patches:
            p.start()
        for k in ("ALT_INSTANCE", "ALT_ROOT"):
            os.environ.pop(k, None)

    def tearDown(self):
        for p in self.patches:
            p.stop()
        import shutil
        shutil.rmtree(self.home, ignore_errors=True)
        shutil.rmtree(self.engine, ignore_errors=True)


class Nothing(Base):
    def test_no_instance_no_list_is_one_local_machine(self):
        self.assertIsNone(config.instance_dir())
        self.assertEqual(config.load("machines.json"), (None, "none"))
        self.assertEqual(config.machines(), {"box": {"ssh": ["local"], "roles": []}})
        self.assertEqual(config.this_machine(), "box")
        self.assertEqual(config.bot_machine(), (None, None, False))
        write(os.path.join(self.home, ".config", "dcbot", "env"), "x")
        name, m, here = config.bot_machine()
        self.assertEqual((name, here), ("box", True), "the bot's settings here: the bot is here")
        self.assertEqual(config.setting("core.language", "zh-TW"), "zh-TW")


class OldPlaces(Base):
    def test_the_engines_old_list_and_chatlogs_host_file_are_not_read(self):
        write(os.path.join(self.engine, "ops", "machines.json"), {"machines": {
            "host-c": {"ssh": ["host-c"], "roles": ["dcbot"]}, "mac": {"ssh": ["MAC"], "chatlog_name": "host-a"}}})
        write(os.path.join(self.home, ".config", "chatlog", "host"), "host-a\n")
        self.assertEqual(config.load("machines.json"), (None, "none"))
        self.assertEqual(config.this_machine(), "box", "neither the chatlog name nor its host file")
        self.assertEqual(config.bot_machine(), (None, None, False))
        self.assertEqual(config.ssh_aliases({"ssh": ["local", "A", "B"]}), ["A", "B"])

    def test_a_chatlog_name_in_the_list_names_no_machine(self):
        write(os.path.join(self.home, "altention", "instance", "machines.json"), {"machines": {"mac": {"ssh": ["MAC"], "chatlog_name": "box"}}})
        self.assertEqual(config.this_machine(), "box")
        write(os.path.join(self.home, ".config", "altention", "host"), "mac\n")
        self.assertEqual(config.this_machine(), "mac", "the name it was given")


class Instance(Base):
    def test_instance_found_by_pointer_and_preferred(self):
        inst = os.path.join(self.home, "elsewhere", "inst")
        write(os.path.join(inst, "machines.json"), {"format": 1, "machines": {"box": {"ssh": ["local"], "roles": ["dcbot"]}}})
        write(os.path.join(inst, "plugins.json"), {"format": 1, "core": {"language": "en"}})
        os.makedirs(os.path.join(inst, "knowledge"))
        write(os.path.join(self.engine, "ops", "machines.json"), {"machines": {"old": {}}})
        self.assertIsNone(config.instance_dir())
        write(os.path.join(self.home, ".config", "altention", "instance"), inst + "\n")
        self.assertEqual(config.instance_dir(), os.path.realpath(inst))
        self.assertEqual(config.load("machines.json")[1], "instance")
        self.assertEqual(list(config.machines()), ["box"])
        self.assertEqual(config.setting("core.language"), "en")

    def test_a_machine_that_left_has_no_instance(self):
        write(os.path.join(self.home, "altention", "instance", "plugins.json"), {})
        write(os.path.join(self.home, ".config", "altention", "instance"), "-\n")
        self.assertIsNone(config.instance_dir())

    def test_env_and_default_root(self):
        write(os.path.join(self.home, "altention", "instance", "plugins.json"), {})
        self.assertEqual(config.instance_dir(), os.path.realpath(os.path.join(self.home, "altention", "instance")))
        other = os.path.join(self.home, "x")
        os.makedirs(other)
        with mock.patch.dict(os.environ, {"ALT_INSTANCE": other}):
            self.assertEqual(config.instance_dir(), os.path.realpath(other))

    def test_newer_format_is_read_with_a_warning(self):
        write(os.path.join(self.home, "altention", "instance", "plugins.json"), {"format": 99, "core": {"x": 1}})
        with mock.patch("sys.stderr") as err:
            self.assertEqual(config.setting("core.x"), 1)
        self.assertTrue(err.write.called)


class Broken(Base):
    def test_broken_files_and_pointers_are_errors_not_fallbacks(self):
        write(os.path.join(self.engine, "ops", "machines.json"), {"machines": {"old": {}}})
        write(os.path.join(self.home, "altention", "instance", "machines.json"), "{half writ")
        with self.assertRaises(config.ConfigError):
            config.inventory()
        write(os.path.join(self.home, ".config", "altention", "instance"), "/nowhere/at/all\n")
        with self.assertRaises(config.ConfigError):
            config.instance_dir()

    def test_a_broken_list_is_not_reread_at_once_and_the_bot_here_is_still_found(self):
        path = os.path.join(self.home, "altention", "instance", "machines.json")
        write(path, "{broken,")
        import time as _time
        t0 = _time.time()
        with self.assertRaises(config.ConfigError):
            config.inventory()
        with self.assertRaises(config.ConfigError):
            config.inventory()
        self.assertLess(_time.time() - t0, 0.7, "the second failure is remembered, not waited for again")
        write(os.path.join(self.home, ".config", "dcbot", "env"), "x")
        self.assertEqual(config.bot_machine()[2], True, "dcpost on the bot's machine still works")
        write(path, {"machines": {"box": {"ssh": ["local"]}}})
        self.assertIn("box", config.inventory()["machines"], "fixed: read again (its mtime changed)")

    def test_stray_bot_settings_do_not_beat_the_list(self):
        write(os.path.join(self.home, "altention", "instance", "machines.json"), {"machines": {"main": {"ssh": ["MAIN"], "roles": ["dcbot"]}}})
        write(os.path.join(self.home, ".config", "dcbot", "env"), "x")
        self.assertEqual(config.bot_machine()[0], "main")
        self.assertTrue(config.stray_bot_settings())


class TheBotsMachine(Base):
    """C-57 U5 (manager 2026-10-20): the list's dcbot entry, this machine by name only: the examples write the bot's
    machine "ssh": ["local"] (reached from itself), which says nothing of who reads the list."""

    def listing(self, me, machines):
        write(os.path.join(self.home, "altention", "instance", "machines.json"), {"machines": machines})
        write(os.path.join(self.home, ".config", "altention", "host"), me + "\n")

    def test_the_examples_list_read_from_another_machine_is_elsewhere(self):
        self.listing("host-b", {"host-a": {"ssh": ["local"], "roles": ["dcbot"]}, "host-b": {"ssh": ["host-b"], "roles": []}})
        name, m, here = config.bot_machine()
        self.assertEqual((name, here), ("host-a", False))
        self.assertTrue(config.known_here())
        self.listing("host-a", {"host-a": {"ssh": ["local"], "roles": ["dcbot"]}, "host-b": {"ssh": ["host-b"], "roles": []}})
        self.assertEqual(config.bot_machine()[::2], ("host-a", True))

    def test_both_written_local_the_name_decides(self):
        machines = {"host-a": {"ssh": ["local"], "roles": ["dcbot"]}, "host-b": {"ssh": ["local"], "roles": []}}
        self.listing("host-b", machines)
        self.assertEqual(config.bot_machine()[::2], ("host-a", False))
        self.listing("host-a", machines)
        self.assertEqual(config.bot_machine()[::2], ("host-a", True))

    def test_a_name_the_list_does_not_know(self):
        self.listing("stranger", {"host-a": {"ssh": ["local"], "roles": ["dcbot"]}})
        self.assertFalse(config.known_here())
        self.assertEqual(config.bot_machine()[::2], ("host-a", True), "its posts still go: the entry is reached locally")


class Text(Base):
    def test_every_language_uses_the_same_fields(self):
        import string
        fields = lambda s: sorted(f for _, f, _, _ in string.Formatter().parse(s) if f)  # noqa: E731
        for lang, table in text.TEXT.items():
            for key, value in table.items():
                self.assertEqual(fields(value), fields(text.TEXT[text.DEFAULT].get(key, value)), (lang, key))
        self.assertIn("{age}", text.t("post.offline"), "no fields given: the text as it is")
        self.assertIn("離線", text.t("post.offline", wrong="x"), "a missing field: the text, no exception")

    def test_broken_settings_do_not_break_text(self):
        write(os.path.join(self.home, "altention", "instance", "plugins.json"), "{broken")
        self.assertIn("離線", text.t("post.offline", age=""))

    def test_text_falls_back_to_zh_tw_then_key(self):
        self.assertIn("離線", text.t("post.offline", age=""))
        self.assertEqual(text.t("no.such.key"), "no.such.key")
        write(os.path.join(self.home, "altention", "instance", "plugins.json"), {"core": {"language": "en"}})
        self.assertIn("離線", text.t("post.offline", age=""), "no English yet: zh-TW")


if __name__ == "__main__":
    unittest.main()
