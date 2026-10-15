"""altention/redact.py: what is hidden before chatlog pushes to the logs repo and before the bot posts tool output to
Discord; what must stay (hashes, ids) so that logs and posts stay readable; chatlog uses this one."""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import importlib.util
import json
import os
import shutil
import tempfile
import unittest

from altention import redact as r

ENGINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PEM = "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAA\nAAAABG5vbmUAAAAEbm9uZQ==\n-----END OPENSSH PRIVATE KEY-----"
SECRETS = (
    "sk-ant-" + "a" * 30,
    "ghp_" + "b" * 36,
    "github_pat_" + "c" * 40,
    "AKIA" + "C" * 16,
    "xoxb-" + "1" * 12 + "-abc",
    "AIza" + "d" * 35,
    "MTIzNDU2Nzg5MDEyMzQ1Njc4OQ.GaBcDe." + "d" * 30,  # Discord token
    "discord.com/api/webhooks/123456/" + "e" * 40,
    "discordapp.com/api/webhooks/123456/" + "e" * 40,
    "discord.com/api/webhooks/123456/5TmwfD",  # cut short in a tool summary
    PEM,
)
SNOWFLAKE = "1" + "0" * 15 + "42"  # a Discord id's length, built so that no long number is in this file
STAYS = (
    "version 2.1.289", "MTIz.ab.cd", "see bot/files.py:12", "https://github.com/alice/life",
    "aa79535",  # short git hash
    "aa79535" + "0123456789abcdef" * 2 + "f",  # full git hash, 40 hex
    "0123456789abcdef" * 4,  # sha256
    "0123456789ABCDEF" * 4,  # sha256, upper case
    "sha256:" + "fedcba9876543210" * 4,
    "123e4567-e89b-12d3-a456-426614174000",  # session id (UUID)
    SNOWFLAKE, "<#%s>" % SNOWFLAKE,
    "MTIzNDU2Nzg5MDEyMzQ1Njc4OQ",  # base64 without the token's dots
    "commit aa79535 (reviewed 8bcacae, 1e13613)",
)


class Redact(unittest.TestCase):
    def test_secrets_are_hidden(self):
        for secret in SECRETS:
            out = r.redact("x %s y" % secret)
            self.assertNotIn(secret, out, secret[:12])
            self.assertIn(r.MARK, out, secret[:12])
            self.assertTrue(out.startswith("x ") and out.endswith(" y"), "only the secret is replaced: %r" % out)

    def test_secret_inside_json_is_hidden(self):  # chatlog redacts json.dumps of a record: newlines become \n
        for secret in SECRETS:
            out = r.redact(json.dumps({"tool_output": "here: %s" % secret}))
            self.assertNotIn(json.dumps(secret)[1:-1], out, secret[:12])

    def test_ordinary_text_stays(self):
        for text in STAYS:
            self.assertEqual(r.redact(text), text)
            self.assertEqual(r.redact("x %s y" % text), "x %s y" % text)

    def test_every_secret_in_a_text(self):
        text = "\n".join("line %d: %s" % (i, s) for i, s in enumerate(SECRETS + STAYS))
        out = r.redact(text)
        self.assertEqual(out.count(r.MARK), len(SECRETS))
        for keep in STAYS:
            self.assertIn(keep, out)


class ChatlogUsesIt(unittest.TestCase):
    """chatlog loads it when it first redacts (its hook must not fail for the engine's shared code), and hides the same."""

    def test_same_function(self):
        spec = importlib.util.spec_from_file_location("chatlog", os.path.join(ENGINE, "chatlog", "chatlog.py"))
        chatlog = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(chatlog)
        self.assertFalse(hasattr(chatlog, "SECRET"), "no pattern of its own")
        for text in SECRETS + STAYS:
            self.assertEqual(chatlog.redact(text), r.redact(text))
        self.assertIs(chatlog._redact, r.redact)

    def test_shared_code_that_does_not_load_fails_the_export(self):
        """Nothing unredacted is written instead: the first redact (the title, before any file) raises."""
        with tempfile.TemporaryDirectory() as tmp:
            for n, broken in enumerate(("def redact(:\n", "raise OSError(13, 'no')\n", "PATTERN = None\n")):
                root = os.path.join(tmp, "engine%d" % n)
                os.makedirs(os.path.join(root, "chatlog"))
                os.makedirs(os.path.join(root, "altention"))
                shutil.copy(os.path.join(ENGINE, "chatlog", "chatlog.py"), os.path.join(root, "chatlog"))
                open(os.path.join(root, "altention", "__init__.py"), "w").close()
                with open(os.path.join(root, "altention", "redact.py"), "w") as f:
                    f.write(broken)
                saved = {k: v for k, v in _sys.modules.items() if k == "altention" or k.startswith("altention.")}
                for k in saved:
                    del _sys.modules[k]
                try:
                    spec = importlib.util.spec_from_file_location("chatlog%d" % n, os.path.join(root, "chatlog", "chatlog.py"))
                    chatlog = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(chatlog)  # loading it is fine (its hook must not fail)
                    with self.assertRaises(Exception, msg=broken):
                        chatlog.redact("x %s y" % SECRETS[0])
                finally:
                    for k in [k for k in _sys.modules if k == "altention" or k.startswith("altention.")]:
                        del _sys.modules[k]
                    _sys.modules.update(saved)
                    while root in _sys.path:
                        _sys.path.remove(root)


if __name__ == "__main__":
    unittest.main()
