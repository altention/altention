How a conversation ends or is replaced: the hook payloads of 22 cases, as Claude Code 2.1.296 and 2.1.288 handed them
to a hook, in the order they came (measured by #code-review-2, 2026-10-13: a real Claude Code against a stand-in
API, a scratch home, a new world for each case). One file for a case: the list of events. Every path of the scratch
world is replaced (/w, /w/proj, projects/-w-proj); the ids are those of the runs.

clear clearbusy resume resumeid resumesame exit ctrld ctrlc killpane killwin killsession server term hup kill9
resumestart continuestart resumepick reset compact autocompact bg

Read by test_normalize.py. Both versions gave the same events in every case.

(autocompact: the measured folder first held one event too many, a Stop of an earlier run of the same case that
had not compacted; whoever measured found and removed it, and what is here is the run that did compact: 11 events.)

agent, agentset (measured the same way, 2026-10-13): a conversation started with `claude --agent rigagent`, and one
started plainly where the user's settings say "agent": "rigagent". A turn, a turn with a tool, /clear, /resume, /exit,
started again with --resume. Every event bears agent_type but one (agent: the last start, given --resume and no --agent).
