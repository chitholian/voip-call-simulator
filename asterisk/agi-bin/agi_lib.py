#!/usr/bin/env python3
"""FastAGI minimal helper. One persistent server; per-thread socket transport."""
import select
import threading
import time


_tls = threading.local()


def bind(sock):
    """Bind current thread to a FastAGI socket (one call per connection)."""
    _tls.sock = sock
    _tls.f = sock.makefile("rwb")
    _tls.env = {}
    _tls.env_read = False


def agi_env():
    """Read and parse AGI MIME header from socket (once per call)."""
    if not getattr(_tls, "env_read", False):
        t = _tls
        r, _, _ = select.select([t.sock], [], [], 5.0)
        if r:
            while True:
                line = t.f.readline()
                if not line or line.strip() == b"":
                    break
                if b":" in line:
                    k, v = line.split(b":", 1)
                    t.env[k.decode().strip()] = v.decode().strip()
        t.env_read = True
    return _tls.env


def command(cmd_str, timeout=2.0):
    """Send one AGI command, read the reply. None on timeout."""
    agi_env()
    t = _tls
    t.f.write((cmd_str + "\n").encode())
    t.f.flush()
    r, _, _ = select.select([t.sock], [], [], timeout)
    if not r:
        return None
    line = t.f.readline()
    if not line:
        return ""
    return line.decode().rstrip("\n")


def alive():
    """True if the channel is still up (status != 0)."""
    line = command("CHANNEL STATUS", 1.0)
    if line is None or line == "":
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
    exec_app("Ringing")


def progress():
    """Send 183 Session Progress with SDP for early media."""
    exec_app("Progress")


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
