#!/usr/bin/env python3
"""AMI-based call originiator. Connects to local AMI, fires Originate async
with random caller-id/destination from configured prefixes, ramps to a target
concurrency, and supports an optional rate limit and total-count cap.

Outbound dialing uses the static 'caller-out' endpoint (rendered by
entrypoint.sh into /var/lib/asterisk/pjsip-caller.conf with a contact pointing
at CALLER_PEER_HOST:PORT). Dial string: PJSIP/<number>@caller-out.
"""
import json
import os
import random
import socket
import sys
import threading
import time

CFG = "/var/lib/asterisk/sim.json"
AMI_HOST = "127.0.0.1"
AMI_PORT = int(os.environ.get("AMI_PORT", "5038"))
AMI_USER = os.environ.get("AMI_USER", "sim")
AMI_SECRET = os.environ.get("AMI_SECRET", "callsim")
OUT_ENDPOINT = "caller-out"
MAX_RECONNECTS = 20


def load_cfg():
    with open(CFG) as f:
        return json.load(f)


class AMI:
    """AMI client over a TCP socket with a single dedicated reader thread.

    The reader thread drains all inbound blocks and (a) keeps a live map of
    originated channels by uniqueid and (b) pushes decoded event dicts onto a
    queue for the caller. Only the reader touches the socket's read side; the
    main thread only writes. Reconnect is safe: closing the socket unblocks the
    reader (readline returns b'') and it exits, sending a None sentinel.
    """

    def __init__(self, host, port, user, secret, retries=30, delay=0.5):
        last = None
        for attempt in range(retries):
            try:
                self.sock = socket.create_connection((host, port))
                self.rbuf = b""
                # banner
                self._read_block(timeout=2.0)
                # login
                self.send([
                    ("Action", "Login"),
                    ("Username", user),
                    ("Secret", secret),
                ])
                resp = self._read_block(timeout=2.0)
                full = " ".join(resp)
                if "Authentication accepted" not in full and "Success" not in full:
                    raise RuntimeError(f"AMI login failed: {full}")
                break
            except Exception as e:
                last = e
                try:
                    self.sock.close()
                except Exception:
                    pass
                time.sleep(delay)
        else:
            raise RuntimeError(f"AMI connection/login failed after {retries} tries: {last}")
        self.events = []
        self.ev_cond = threading.Condition()
        self.active = {}           # uniqueid -> start_time
        self.lock = threading.Lock()
        self.stopped = False
        self._reader = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader.start()

    # ---- low level read/write ----
    def _read_block(self, timeout=None):
        """Read one AMI block (headers until a blank line) as a list of str."""
        self.sock.settimeout(timeout)
        lines = []
        raw = b""
        try:
            while True:
                if b"\r\n\r\n" in self.rbuf:
                    raw, self.rbuf = self.rbuf.split(b"\r\n\r\n", 1)
                    break
                chunk = self.sock.recv(8192)
                if not chunk:
                    break
                self.rbuf += chunk
        except socket.timeout:
            pass
        for ln in raw.split(b"\r\n"):
            line = ln.rstrip(b"\r\n").decode(errors="replace").strip()
            if line:
                lines.append(line)
        return lines

    def _flush_pending(self):
        """Read whatever is already buffered (non-blocking)."""
        self.sock.setblocking(False)
        try:
            while b"\r\n\r\n" not in self.rbuf:
                chunk = self.sock.recv(8192)
                if not chunk:
                    break
                self.rbuf += chunk
        except (BlockingIOError, socket.error):
            pass
        self.sock.setblocking(True)

    def send(self, pairs):
        body = "".join(f"{k}: {v}\r\n" for k, v in pairs) + "\r\n"
        self.sock.sendall(body.encode())

    # ---- reader thread ----
    def _reader_loop(self):
        self.sock.settimeout(0.5)
        while not self.stopped:
            try:
                block = self._read_block(timeout=0.5)
            except Exception:
                block = []
            if not block:
                if self.stopped:
                    break
                continue
            header = {}
            for ln in block:
                if ":" in ln:
                    k, v = ln.split(":", 1)
                    header[k.strip().lower()] = v.strip()
            event = header.get("event", "").lower()
            chan = header.get("channel", "")
            uid = header.get("uniqueid", "")
            with self.lock:
                if event == "newchannel" and chan.startswith("PJSIP/caller-out-"):
                    self.active[uid] = time.time()
                elif event == "hangup" and uid:
                    self.active.pop(uid, None)
            if event:
                with self.ev_cond:
                    self.events.append(header)
                    self.ev_cond.notify_all()

    def wait_event(self, predicate, timeout=5.0):
        deadline = time.time() + timeout
        with self.ev_cond:
            while True:
                for ev in self.events:
                    if predicate(ev):
                        self.events.remove(ev)
                        return ev
                if time.time() >= deadline:
                    return None
                self.ev_cond.wait(max(0.05, deadline - time.time()))

    def active_count(self):
        with self.lock:
            return len(self.active)

    def close(self):
        self.stopped = True
        try:
            self._flush_pending()
            self.send([("Action", "Logoff")])
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass


class OriginateRunner:
    def __init__(self, cfg):
        self.ami = AMI(AMI_HOST, AMI_PORT, AMI_USER, AMI_SECRET)
        self.cfg = cfg
        caller = cfg.get("caller", {})
        self.target_concurrency = int(caller.get("max_concurrent", 100))
        self.rate_per_sec = float(caller.get("rate_per_sec", 20.0))
        self.max_total = int(caller.get("max_total_calls", 0))  # 0 = unlimited
        self.max_seconds = float(caller.get("max_seconds", 0))  # 0 = unlimited
        self.from_prefixes = caller.get("from_prefixes", ["1000"])
        self.to_prefixes = caller.get("to_prefixes", ["2000"])
        self.from_len = int(caller.get("from_number_len", 4))
        self.to_len = int(caller.get("to_number_len", 4))
        self.total_originated = 0
        self.stop = False

    def _random_number(self, prefixes, length):
        p = random.choice(prefixes)
        n = "".join(random.choice("0123456789") for _ in range(length))
        return p + n

    def originate_one(self):
        if self.max_total and self.total_originated >= self.max_total:
            return
        caller_id = self._random_number(self.from_prefixes, self.from_len)
        callee = self._random_number(self.to_prefixes, self.to_len)
        # Route through a Local-channel dialplan hop so the channel CallerID is
        # applied to the PJSIP outbound INVITE (direct-originate-to-endpoint
        # ignored the Originate CallerID when the endpoint had no callerid set).
        channel = f"Local/{callee}@caller-dial/n"
        self.ami.send([
            ("Action", "Originate"),
            ("Channel", channel),
            ("Async", "true"),
            ("CallerID", caller_id),
            ("Variable", "CALL_SIM=1"),
            ("Variable", f"MYCALLERID={caller_id}"),
        ])
        sys.stderr.write(f"[originate] callee={callee} caller={caller_id} chan={channel} total={self.total_originated+1}\n")
        sys.stderr.flush()
        self.total_originated += 1
        return channel

    def _wait_originate_result(self, fine_to_fail=True):
        """Return True if the OriginateResponse reported success (queued+'ok').
        A 'Failure' response is logged (still counts the attempt)."""
        ev = self.ami.wait_event(
            lambda e: e.get("event", "").lower() == "originate_response", timeout=5.0)
        if ev is None:
            return True  # no response yet; not necessarily fatal
        status = ev.get("response", "").lower()
        if status != "success":
            sys.stderr.write(
                f"Originate {ev.get('actionid','')} -> {ev.get('response','')}: "
                f"{ev.get('message','')} channel={ev.get('channel','')}\n")
        return True

    def ramp(self):
        while True:
            if self.max_seconds and (time.time() - self._start) >= self.max_seconds:
                self.stop = True
                break
            if self.max_total and self.total_originated >= self.max_total:
                if self.ami.active_count() == 0:
                    break
                time.sleep(0.2)
                continue
            if self.ami.active_count() >= self.target_concurrency:
                time.sleep(0.05)
                continue
            try:
                self.originate_one()
            except Exception as e:
                sys.stderr.write(f"originate err: {e}\n")
                time.sleep(0.5)
                try:
                    self.ami.close()
                    self.ami = AMI(AMI_HOST, AMI_PORT, AMI_USER, AMI_SECRET)
                except Exception:
                    time.sleep(2.0)
            interval = max(1.0 / max(self.rate_per_sec, 0.01), 0.001)
            time.sleep(interval)

    def run(self):
        self._start = time.time()
        try:
            self.ramp()
        finally:
            self.ami.close()


def main():
    cfg = load_cfg()
    runner = OriginateRunner(cfg)
    runner.run()


if __name__ == "__main__":
    main()
