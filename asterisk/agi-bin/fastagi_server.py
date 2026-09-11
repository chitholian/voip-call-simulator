#!/usr/bin/env python3
"""FastAGI server for the call simulator.

Asterisk dialplan: AGI(agi://127.0.0.1:FASTAGI_PORT/<script>).
On connect, res_agi streams AGI header then commands over the socket.
One persistent process replaces one spawn-per-call (normal AGI).
One thread per active call.
"""
import errno
import os
import socket
import sys
import threading
import time

sys.path.insert(0, "/var/lib/asterisk/agi-bin")
import agi_lib
import sim_callee
import sim_caller_media

HOST = "127.0.0.1"
PORT = int(os.environ.get("FASTAGI_PORT", "4573"))

HANDLERS = {
    "callee": sim_callee.main,
    "caller_media": sim_caller_media.main,
}


def _handle(sock):
    script = "?"
    try:
        agi_lib.bind(sock)
        script = agi_lib.agi_env().get("agi_network_script", "")
        handler = HANDLERS.get(script)
        if handler is None:
            sys.stderr.write("fastagi: no handler for %r\n" % (script,))
            agi_lib.command("HANGUP 0", 2.0)
            return
        handler()
    except Exception as e:
        sys.stderr.write("fastagi: %r handler err: %s\n" % (script, e))
        try:
            agi_lib.command("HANGUP 0", 1.0)
        except Exception:
            pass
    finally:
        try:
            sock.close()
        except Exception:
            pass


def _serve(srv, accept_fn=None):
    accept = accept_fn or srv.accept
    while True:
        try:
            conn, _ = accept()
        except OSError as e:
            if e.errno in (errno.EBADF, errno.EINVAL):
                break
            sys.stderr.write("fastagi: accept error, retrying: %s\n" % (e,))
            time.sleep(0.1)
            continue
        threading.Thread(target=_handle, args=(conn,), daemon=True).start()


def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT))
    srv.listen(512)
    sys.stderr.write("fastagi: listening on %s:%d\n" % (HOST, PORT))
    _serve(srv)


def selftest():
    """Protocol smoke test. Mocks Asterisk client."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.listen(8)

    ready = threading.Event()
    def run():
        ready.set()
        _serve(srv)
    threading.Thread(target=run, daemon=True).start()
    ready.wait(2.0)

    ok = True

    def check(label, got, want):
        nonlocal ok
        if got == want:
            sys.stderr.write("selftest: PASS %s\n" % label)
        else:
            sys.stderr.write("selftest: FAIL %s: got %r want %r\n" % (label, got, want))
            ok = False

    # Test 1: unknown script → HANGUP then close
    try:
        c = socket.create_connection(("127.0.0.1", port), timeout=3)
        f = c.makefile("rwb")
        f.write(b"agi_network: yes\nagi_network_script: nosuch\nagi_request: agi://x\n\n")
        f.flush()
        line = f.readline()
        check("unknown-script-cmd", line.decode().strip(), "HANGUP 0")
        c.close()
    except Exception as e:
        sys.stderr.write("selftest: FAIL unknown-script: %s\n" % e)
        ok = False

    # Test 2: callee handler → GET VARIABLE + CHANNEL STATUS + EOF
    try:
        c = socket.create_connection(("127.0.0.1", port), timeout=5)
        f = c.makefile("rwb")
        hdr = (
            b"agi_network: yes\n"
            b"agi_request: agi://127.0.0.1:%d/callee\n" % port +
            b"agi_network_script: callee\n"
            b"agi_channel: PJSIP/test-1\n"
            b"agi_language: en\n"
            b"agi_extension: 6888\n"
            b"agi_context: callee\n"
            b"agi_priority: 1\n"
            b"agi_uniqueid: test-001\n\n"
        )
        f.write(hdr)
        f.flush()

        # sim_callee.main() first command: GET VARIABLE PJSIP_HEADER(read,From)
        cmd1 = f.readline().decode().strip()
        check("callee-cmd1", cmd1, "GET VARIABLE PJSIP_HEADER(read,From)")
        f.write(b"200 result=0\n")
        f.flush()

        cmd2 = f.readline().decode().strip()
        check("callee-cmd2", cmd2, "CHANNEL STATUS")
        f.write(b"200 result=0\n")
        f.flush()

        cmd3 = f.readline().decode().strip()
        check("callee-cmd3", cmd3, "CHANNEL STATUS")
        f.write(b"200 result=0\n")
        f.flush()

        # Handler returns, server closes socket
        eof = f.readline()
        check("callee-eof", eof, b"")
        c.close()
    except Exception as e:
        sys.stderr.write("selftest: FAIL callee-test: %s\n" % e)
        ok = False

    # Test 3: a transient accept() error (ECONNABORTED under load) must not kill
    # the server — the loop retries and keeps serving.
    try:
        real = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        real.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        real.bind(("127.0.0.1", 0))
        real_port = real.getsockname()[1]
        real.listen(1)
        calls = {"n": 0}

        def flaky_accept():
            if calls["n"] == 0:
                calls["n"] += 1
                raise OSError(errno.ECONNABORTED, "Connection aborted")
            return real.accept()

        threading.Thread(target=_serve, args=(real, flaky_accept), daemon=True).start()
        c = socket.create_connection(("127.0.0.1", real_port), timeout=3)
        f = c.makefile("rwb")
        f.write(b"agi_network: yes\nagi_network_script: callee\n\n")
        f.flush()
        line = f.readline().decode().strip()
        check("transient-accept-survives", line, "GET VARIABLE PJSIP_HEADER(read,From)")
        c.close()
        real.close()
    except Exception as e:
        sys.stderr.write("selftest: FAIL transient-accept: %s\n" % e)
        ok = False

    srv.close()
    sys.stderr.write("selftest: %s\n" % ("ALL PASSED" if ok else "FAILED"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
