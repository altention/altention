"""The Discord bot. The engine's own packages (altention/, with the connectors of the AI tools) lie beside dcbot/:
its folder is put on the path here, once, for the bot and for every tool that imports a module of it
(tools/dcremote on other machines, tools/dchook when it installs the hooks, tools/dcctl)."""
import os
import sys

_ENGINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ENGINE not in sys.path:
    sys.path.insert(1, _ENGINE)
