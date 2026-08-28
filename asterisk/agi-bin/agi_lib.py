#!/usr/bin/env python3
"""AGI minimal helper. Blocking commands; polls CHANNEL STATUS to detect hangup."""
import sys
import select
import time


AGI_ENV = {}
_env_read = False

def _ensure_env():
    global _env_read
    if _env_read:
        return
    _env_read = True
    r, _, _ = select.select([sys.stdin], [], [], 5.0)
    if not r:
        return
    while True:
        line = sys.stdin.readline()
        if not line or line.strip() == "":
            break
        if ":" in line:
            k, v = line.split(":", 1)
            AGI_ENV[k.strip()] = v.strip()
        else:
            break


def command(cmd_str, timeout=2.0):
    _ensure_env()
    sys.stdout.write(cmd_str + "\n")
    sys.stdout.flush()
    r, _, _ = select.select([sys.stdin], [], [], timeout)
    if not r:
        return None
    return sys.stdin.readline()


def alive():
    """True if the channel is still up (status != 0)."""
    line = command("CHANNEL STATUS", 1.0)
    if line is None:
        return False
    return "result=0" not in line


def wait_seconds(secs, poll=0.25):
    """Wait up to secs, aborting early if the channel dies. Returns False if died."""
    end = time.time() + secs
    while time.time() < end:
        if not alive():
            return False
        time.sleep(poll)
    return True


def answer():
    command("ANSWER", 3.0)


def ringing():
    """Send 180 Ringing."""
    command("RINGING", 2.0)


def progress():
    """Send 183 Session Progress with SDP for early media."""
    command("PROGRESS", 2.0)


def hangup(code=0):
    command(f"HANGUP {code}", 2.0)


def exec_app(app, *args):
    a = ""
    if args:
        a = " " + " ".join(str(x) for x in args)
    return command(f"EXEC {app}{a}", 5.0)


def stream_file(fname):
    """Play a file to the channel (interrupts on hangup)."""
    command(f'STREAM FILE {fname} ""', 10.0)


def get_var(v, full=False):
    kind = "GET FULL VARIABLE" if full else "GET VARIABLE"
    line = command(f"{kind} {v}", 2.0)
    if line and "=" in line:
        return line.split("=", 1)[1].strip().strip('"')
    return None
