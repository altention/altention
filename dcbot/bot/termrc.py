"""The rc files that let the bot see when a command in a terminal channel ends (standard library only: also
used by tools/dcremote on the other machines, Python 3.8 included).

The shell starts with a small rc of ours that loads the owner's own configuration, then adds a prompt hook
writing the last exit status as one more line of a "done" file. The owner's dotfiles are not touched.
"""
import os
import pwd
import shlex


def shell():
    try:
        return pwd.getpwuid(os.getuid()).pw_shell or "/bin/bash"
    except KeyError:
        return os.environ.get("SHELL") or "/bin/bash"


def rc_files(folder, done):
    """Our rc files for the shell: load the owner's configuration, then add the prompt hook. Returns argv."""
    os.makedirs(folder, exist_ok=True)
    q = shlex.quote
    sh = shell()
    if os.path.basename(sh) == "zsh":
        files = {
            ".zshenv": '[ -f "$HOME/.zshenv" ] && source "$HOME/.zshenv"\n'
                       'DCBOT_USER_ZDOTDIR="${ZDOTDIR:-$HOME}"\nZDOTDIR=%s\n' % q(folder),
            ".zprofile": 'ZDOTDIR="$DCBOT_USER_ZDOTDIR"\n[ -f "$ZDOTDIR/.zprofile" ] && source "$ZDOTDIR/.zprofile"\n'
                         'ZDOTDIR=%s\n' % q(folder),
            ".zshrc": 'ZDOTDIR="$DCBOT_USER_ZDOTDIR"\n[ -f "$ZDOTDIR/.zshrc" ] && source "$ZDOTDIR/.zshrc"\n'
                      '_dcbot_done() { local s=$?; print -r -- "$s" >>| %s; }\n'  # >>|: the owner may set noclobber
                      'precmd_functions=(_dcbot_done $precmd_functions)\n' % q(done),
        }
        argv = [sh, "-l"]
    else:
        files = {"bashrc": '[ -f "$HOME/.bashrc" ] && source "$HOME/.bashrc"\n'
                           '_dcbot_done() { local s=$?; echo "$s" >> %s; }\n'
                           'PROMPT_COMMAND="_dcbot_done${PROMPT_COMMAND:+;$PROMPT_COMMAND}"\n' % q(done)}
        argv = [sh, "--rcfile", os.path.join(folder, "bashrc"), "-i"]
    for name, text in files.items():
        with open(os.path.join(folder, name), "w") as f:
            f.write(text)
    open(done, "a").close()  # exists from the start: with noclobber a shell would not create it
    return argv, ({"ZDOTDIR": folder} if os.path.basename(sh) == "zsh" else {})


def done_count(path):
    try:
        with open(path) as f:
            return [line.strip() for line in f if line.strip()]
    except OSError:
        return []
