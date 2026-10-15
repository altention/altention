"""The Claude Code connector's own tests and the samples they read: real screens (screens/<version>/, see the
README there) and real hook payloads (hook_payloads/<version>/). They lie in the same tree as the connector, so
a new version of Claude Code is a change here and in altention/connectors/claude_code/ only (stage 3b step 1.3;
until then in dcbot/tests/). Run by the system Python with the rest of altention/tests: nothing of the bot is
imported here. What the bot does with a screen is tested in dcbot/tests/, which takes its samples from here."""
