# VoIP Call Simulator (Asterisk)

A minimal, containerized Asterisk instance that **generates and receives SIP
calls** with human-like, randomized call behavior — designed to be pointed at a
**target SIP server** (e.g. call-switching / routing software) so you can compare
its behavior against a **CSV call-detail-record (CDR)** log.

Runs completely *headless* in two roles that can be live on the same host via
`network_mode: host`:

- **`caller`** — uses AMI `Originate` to *fire outbound calls* at a target peer
  with random caller-ids/destinations, ramp/concurrency/rate control, and
  randomized ring/cancel/answer timing.
- **`callee`** — *answers inbound calls* with a realistic state machine
  (post-dial delay → ring → early media → answer → talk → hangup), each step
  governed by tunable probabilities and durations.

Every answered, unanswered, busy, or failed call is written to a CSV CDR you can
reconcile against the target software.

---

## Quick start

```bash
# 1. Copy the environment template and tune it
cp .env.example .env

# 2. Build and start in callee mode (default MODE=callee, SIP_PORT=5060)
docker compose up -d --build

# 3. Confirm a clean start (zero [ERROR]/[WARNING])
docker logs voip-asterisk-1 2>&1 | sed -E 's/\x1b\[[0-9;]*m//g' | grep -E '\[ERROR\]|\[WARNING\]' || echo "clean"

# 4. Verify PJSIP transport + AMI
docker exec voip-asterisk-1 asterisk -rx 'pjsip show transports'
docker exec voip-asterisk-1 asterisk -rx 'cdr show status'
```

That is the **callee** role: it listens on UDP/TCP `SIP_PORT` and answers inbound
calls via `sim_callee.py`.

---

## Roles: caller / callee

Two independently configurable containers, both using the **same image**.
Because `network_mode: host` is used, give each a **distinct `SIP_PORT` + `AMI_PORT`**.

### Callee (receive calls)

```bash
docker run -d --name voip-callee --network host \
  -e MODE=callee \
  -e SIP_PORT=5060 \
  -e AMI_PORT=5038 \
  -e AMI_USER=sim -e AMI_SECRET=callsim \
  -v "$PWD/asterisk/etc:/etc/asterisk:ro" \
  chitholian/voip-call-simulator
```

`[anonymous]` endpoint (in `asterisk/etc/pjsip.conf`) sends every inbound call to
the `callee` context, which invokes `sim_callee.py`. Register with a softphone or
dial from any UAC: `sip:6888@<host>:<SIP_PORT>`.

To restrict which sources may call, set `CALLEE_ALLOW_IPS` (comma-separated
IPs/CIDRs); the callee then rejects (403) calls from any other address:

```bash
docker run -d --name voip-callee --network host \
  -e MODE=callee -e SIP_PORT=5060 -e AMI_PORT=5038 \
  -e CALLEE_ALLOW_IPS=192.168.1.0/24,127.0.0.1/32 \
  ghcr.io/chitholian/voip-call-simulator:latest
```

> When running the **caller and callee on the same host** (`network_mode: host`),
> include `127.0.0.1/32` in `CALLEE_ALLOW_IPS` or the caller's INVITEs will be
> rejected.

### Caller (make calls against a peer)

Point `CALLER_PEER_HOST` / `CALLER_PEER_PORT` at a **callee instance or your
target SIP server**, then:

```bash
docker run -d --name voip-caller --network host \
  -e MODE=caller \
  -e SIP_PORT=2280 \
  -e AMI_PORT=5039 \
  -e AMI_USER=sim -e AMI_SECRET=callsim \
  -e CALLER_PEER_HOST=127.0.0.1 -e CALLER_PEER_PORT=5060 \
  -e CALLER_FROM_PREFIXES=9101,9202 -e CALLER_FROM_LEN=4 \
  -e CALLER_TO_PREFIXES=6888 -e CALLER_TO_LEN=4 \
  -e CALLER_MAX_TOTAL_CALLS=500 -e CALLER_MAX_CONCURRENT=50 -e CALLER_RATE_PER_SEC=10 \
  -v "$PWD/asterisk/etc:/etc/asterisk:ro" \
  chitholian/voip-call-simulator
```

The caller drives its **own Asterisk** via AMI `Originate` and dials out through a
`caller-out` PJSIP endpoint whose AOR contact points at the peer. `sim_caller.py`
ramps up to `CALLER_MAX_CONCURRENT` while pacing at `CALLER_RATE_PER_SEC`, stops
after `CALLER_MAX_TOTAL_CALLS` originate attempts, and exits when all calls finish
(`docker ps` → `Exited (0)`). Set a cap to 0 for unlimited.

> Two roles on one host need distinct ports because of `network_mode: host`:
> callee on `SIP_PORT=5060 / AMI_PORT=5038`, caller on `SIP_PORT=2280 / AMI_PORT=5039`.

---

## Call flow

```
caller Asterisk                    peer Asterisk (or target server)
---------------                    --------------------------------
sim_caller.py (AMI Originate)
   → Local/<n>@caller-dial/n          callee context
      Set(CALLERID(all)=MYCALLERID)     → sim_callee.py state machine
      → Dial(PJSIP/<n>@caller-out)  ── INVITE (From: <random>) ──▶  160/180/183/200
         caller_talk (option B)                               ◀── SIP responses ──
         sim_caller_media.py                                    plays core sounds
                                                                 random talk length
   ──────────────── CSV CDR (both sides) ────────────────
```

Both Asterisks produce **independent** CSV CDRs, so you can inspect:

- **Caller side** — `/var/log/asterisk/cdr-csv/Master.csv` in the caller container.
- **Peer / callee side** — the same path in the peer container. Reconcile the two
  to validate the target software's call switching/routing.

---

## Configuration

### Image-level (baked in)

| File | Purpose |
|------|---------|
| `asterisk/Dockerfile` | Builds on `andrius/asterisk:22`, downloads core-en sounds (g729/g723/gsm/ulaw/alaw), copies modules/etc/sounds/agi-bin/entrypoint. |
| `asterisk/etc/modules.conf` | **Explicit** module list (`autoload=no`): PJSIP stack (incl. mandatory `res_pjsip_pubsub`), formats, codecs, dialplan apps (`Dial`/`Playback`/`AGI`), bridging (`bridge_simple`, `bridge_native_rtp`), and `cdr_csv`. Dropping any dependency (e.g. `res_pjsip_pubsub`, `res_agi`, `res_speech`, `func_callerid`) breaks chan_pjsip / AGI / CallerID. |
| `asterisk/etc/pjsip.conf` | Endpoints + runtime transport/caller includes. |
| `asterisk/etc/extensions.conf` | Dialplan: `caller`, `caller-dial`, `caller_talk`, `callee` contexts. |
| `asterisk/etc/cdr.conf` | CSV CDR backend; `unanswered=yes`/`congestion=yes` logs non-answered dispositions too. |
| `asterisk/etc/*.conf` (logger, manager, asterisk, acl, udptl, pjproject, ccss, features, cel, indications) | Minimal stubs to keep startup clean. |

### Runtime (rendered by `asterisk/entrypoint.sh`, mounted read-only)

| Path | Source |
|------|--------|
| `/var/lib/asterisk/sim.json` | All `CALLEE_*` / `CALLER_*` env knobs (JSON consumed by the AGI scripts). |
| `/var/lib/asterisk/pjsip-transport.conf` | `SIP_PORT` → `[transport-udp]` / `[transport-tcp]` bind. |
| `/var/lib/asterisk/pjsip-caller.conf` | `[caller-out]` endpoint + AOR `contact=sip:<CALLER_PEER_HOST>:<CALLER_PEER_PORT>`. |
| `/var/lib/asterisk/manager-ami.conf` | `[sim]` AMI user, `AMI_PORT`, secret. |
| `/var/log/asterisk/cdr-csv/Master.csv` | CSV CDR output (dir auto-created + chowned). |

> `asterisk/etc` is bind-mounted **read-only** (`:ro`); `sim.json` and the
> rendered `*_prefixes`/transport/AMI/caller configs are written to the **writable**
> `/var/lib/asterisk`, so set ports/secrets via environment rather than editing
> the mounted confs.

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MODE` | `callee` | `callee` (answer) or `caller` (originate). |
| `SIP_PORT` | `5060` | PJSIP UDP/TCP bind port (host networking → set distinct per container). |
| `AMI_USER` / `AMI_SECRET` / `AMI_PORT` | `sim` / `callsim` / `5038` | AMI credentials the caller controller connects to. |
| **Caller** | | |
| `CALLER_PEER_HOST` / `CALLER_PEER_PORT` | `127.0.0.1` / `5060` | Peer to dial. |
| `CALLER_FROM_PREFIXES` | `1000,1001,1002` | Comma-separated caller-id prefixes. |
| `CALLER_FROM_LEN` | `4` | Random trailing digits appended to a from-prefix. |
| `CALLER_TO_PREFIXES` | `2000,2001,2002` | Comma-separated destination prefixes. |
| `CALLER_TO_LEN` | `4` | Random trailing digits appended to a to-prefix. |
| `CALLER_MAX_CONCURRENT` | `100` | Target concurrent active calls. |
| `CALLER_RATE_PER_SEC` | `20` | Max originate attempts/sec. |
| `CALLER_MAX_TOTAL_CALLS` | `0` | Stop after N originate attempts (0 = unlimited). |
| `CALLER_MAX_SECONDS` | `0` | Stop after N seconds (0 = unlimited). |
| `CALLER_DEFAULT_FROM` | *(empty)* | Optional static caller-id on the `caller-out` endpoint. |
| `CALLER_TALK_*` | `8 / 180 / 60 / 0.9` | Caller-leg talk duration: min, max, lognormal median, sigma. |
| **Callee** | | |
| `CALLEE_ALLOW_IPS` | *(empty)* | Comma-separated source IPs/CIDRs allowed to call the callee. Empty = allow all. When set, the callee rejects (403) calls from any other source. |
| `CALLEE_PROB_NO_ANSWER` | `0.20` | Probability of not answering (no 200, eventually timeout). |
| `CALLEE_PROB_BUSY` | `0.08` | Probability of returning Busy (486). |
| `CALLEE_PROB_EARLY_MEDIA` | `0.08` | Probability of sending early media (183). |
| `CALLEE_PROB_DECLINE_IN_RING` | `0.06` | Per-ring-step probability of declining (603) while ringing. |
| `CALLEE_PROB_DECLINE_IN_EARLY` | `0.35` | Probability of declining during early media. |
| `CALLEE_PROB_HANGUP_PER_CHUNK` | `0.22` | Probability of hanging up per talk chunk. |
| `CALLEE_PDD_MIN/MAX/MEAN` | `1.0 / 10.0 / 3.0` | Post-dial-delay (before answer) — exponential distribution. |
| `CALLEE_RING_MIN/MAX/MEAN` | `2.0 / 45.0 / 12.0` | Ringing hold (before answer) — exponential. |
| `CALLEE_EARLY_PLAY_MIN/MAX/MEAN` | `2.0 / 8.0 / 4.0` | Early-media playback duration — exponential. |
| `CALLEE_GAP_MIN_MS` / `CALLEE_GAP_MAX_MS` | `600` / `3000` | Random pause before answering. |
| `CALLEE_TALK_MIN/MAX/MEDIAN/SIGMA` | `8 / 180 / 60 / 0.9` | Talking-phase duration — lognormal. |
| `CALLEE_TALK_CHUNK` | `8.0` | Seconds between talk re-evaluations. |

---

## Implementation notes

- **`network_mode: host`** — no port mapping; run each role with distinct ports.
- The image runs as **non-root** user `asterisk` (uid 1000); `entrypoint.sh` drops
  privileges and `AD`-guards writes to `sim.json` etc.
- **Per-call random caller-id** is applied in the `caller-dial` dialplan hop via
  `Set(CALLERID(all)=${MYCALLERID})` — direct originate-to-endpoint ignored the
  `CallerID` when the endpoint had no `callerid`. `trust_id_inbound=yes` on the
  callee endpoint lets the received caller-id flow into channel/CDR.
- **CSV CDR** (`cdr_csv.so` backend) writes to `/var/log/asterisk/cdr-csv/Master.csv`
  with `unanswered=yes`/`congestion=yes` so NO ANSWER / BUSY / FAILED calls are
  recorded for reconciliation. Columns: `src, dst, context, clid, channel,
  app, appdata, start, answer, end, duration, billsec, disposition, amaflags,
  uniqueid, userfield` (plus `usegmtime` UTC timestamps).
- **AGI library fix**: `agi_lib.py::_ensure_env()` consumes the initial AGI
  MIME header block so `GET VARIABLE` / `PJSIP_HEADER(read,From)` return real
  values instead of being misinterpreted as command replies.

### Useful debugging commands

```bash
# Live CDR (callee side after a caller run)
docker exec voip-asterisk-1 cat /var/log/asterisk/cdr-csv/Master.csv
docker exec voip-asterisk-1 awk -F',' '{print $2" -> "$3" | "$15}' /var/log/asterisk/cdr-csv/Master.csv

# Clean-log check (strip ANSI first, or grep returns false zeros)
docker logs voip-asterisk-1 2>&1 | sed -E 's/\x1b\[[0-9;]*m//g' | grep -E '\[ERROR\]|\[WARNING\]'

# Calls / endpoints / AMI state
docker exec voip-asterisk-1 asterisk -rx 'core show channels concise'
docker exec voip-asterisk-1 asterisk -rx 'pjsip show endpoints'
docker exec voip-asterisk-1 asterisk -rx 'manager show connected'
```

---

## Design constraints (why it's "tiny")

`asterisk/etc/modules.conf` uses `autoload=no` and lists exactly the modules
needed to **place/receive a call, play media, and log CDR** — no CDR advanced
billing, no CEL, no ARI, no channel drivers other than PJSIP. Every listed module
is required; removing a dependency (notably `res_pjsip_pubsub`, `res_agi`,
`res_speech`, `func_callerid`, `bridge_native_rtp`/`bridge_simple`) produces a
load failure or a runtime warning, which this setup is deliberately kept free of.
