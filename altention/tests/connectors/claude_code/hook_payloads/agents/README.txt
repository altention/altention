Events from inside subagents, as Claude Code 2.1.296 and 2.1.288 handed them to a hook (measured by #code-review-2,
2026-10-13, the same way as ../sessions/: a real Claude Code against a stand-in API, a scratch home; every path of
the scratch world replaced). The measuring hooked two events the bot does not hook: PermissionRequest for every tool
(the bot: only ExitPlanMode's) and PostToolUseFailure.

subperm   a subagent started with the Agent tool calls a tool that needs permission (permission mode: default)
wfperm    the Workflow tool asks first; then an agent of the workflow calls a tool that needs permission
subfail   a subagent's own request to the API is refused (400)
subkinds  a forked subagent, a background one that runs a tool, a plain one

What they show (both versions the same): an event bears agent_id only when it is a SubagentStart, a SubagentStop, a
PreToolUse or PostToolUse of a tool the subagent called, a PermissionRequest for such a tool, or the StopFailure of a
subagent whose request failed (then no SubagentStop comes). The Notification of a permission dialog asked from
inside a subagent bears none. No UserPromptSubmit, Stop, SessionStart or SessionEnd bears one.

Read by test_normalize.py and by the bot's tests/test_turn_kinds.py.
