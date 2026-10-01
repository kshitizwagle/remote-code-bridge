"""A stand-in for `ssh`, selected with RCB_SSH='["python", "fake_ssh.py"]'.

Environment knobs (all optional):
  FAKE_SSH_LOG          append one JSON line per call: {"argv": [...], "alias": ..., "command": ...}
  FAKE_SSH_HOSTS        JSON {"alias": "hostname user port"} for `ssh -G`; unknown aliases map to themselves
  FAKE_SSH_G_FAIL       comma-separated aliases for which `ssh -G` fails
  FAKE_SSH_UNREACHABLE  comma-separated aliases that fail like a dead host
  FAKE_SSH_TUNNEL       for `-N` calls: "stay" (run until killed), "exit:<code>:<stderr message>",
                        or "after:<seconds>:<code>:<stderr message>" (stay up that long, then exit)
  FAKE_SSH_PREP         "fail:<stderr message>" makes non-`-N` commands fail
  FAKE_REMOTE_HOME      run remote commands locally with this HOME (an emulated remote machine)
  FAKE_REMOTE_PATH      extra PATH entries for remote commands (e.g. a fake `uname`)
"""

import json
import os
import subprocess
import sys
import time

VALUE_OPTIONS = set("bcDEeFIiJLlmOoPpQRSWw")


def main() -> int:
    argv = sys.argv[1:]
    flags, alias, command, index = set(), None, [], 0
    while index < len(argv):
        arg = argv[index]
        if alias is None and arg.startswith("-") and len(arg) == 2:
            if arg[1] in VALUE_OPTIONS:
                index += 1
            else:
                flags.add(arg[1])
        elif alias is None:
            alias = arg
        else:
            command.append(arg)
        index += 1
    remote_command = " ".join(command)

    log = os.environ.get("FAKE_SSH_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"argv": argv, "alias": alias, "command": remote_command}) + "\n")

    if "G" in flags:
        if alias in (os.environ.get("FAKE_SSH_G_FAIL") or "").split(","):
            print(f"{alias}: bad configuration", file=sys.stderr)
            return 255
        hosts = json.loads(os.environ.get("FAKE_SSH_HOSTS") or "{}")
        hostname, user, port = (hosts.get(alias) or f"{alias} user 22").split()
        print(f"hostname {hostname}\nuser {user}\nport {port}")
        return 0
    if alias in (os.environ.get("FAKE_SSH_UNREACHABLE") or "").split(","):
        print(f"ssh: connect to host {alias} port 22: Connection refused", file=sys.stderr)
        return 255

    if "N" in flags:
        behaviour = os.environ.get("FAKE_SSH_TUNNEL", "stay")
        if behaviour == "stay":
            while True:
                time.sleep(0.05)
        if behaviour.startswith("after:"):
            _, seconds, behaviour = behaviour.split(":", 2)
            time.sleep(float(seconds))
            behaviour = "exit:" + behaviour
        _, code, message = behaviour.split(":", 2)
        print(message, file=sys.stderr)
        return int(code)

    prep = os.environ.get("FAKE_SSH_PREP", "")
    if prep.startswith("fail:"):
        print(prep[5:], file=sys.stderr)
        return 255
    remote_home = os.environ.get("FAKE_REMOTE_HOME")
    if not remote_home or not remote_command:
        return 0
    env = dict(os.environ, HOME=remote_home, SHELL=os.environ.get("FAKE_REMOTE_SHELL", "/bin/bash"))
    extra_path = os.environ.get("FAKE_REMOTE_PATH")
    if extra_path:
        env["PATH"] = extra_path + os.pathsep + env["PATH"]
    return subprocess.run(["sh", "-c", remote_command], env=env, cwd=remote_home).returncode


if __name__ == "__main__":
    sys.exit(main())
