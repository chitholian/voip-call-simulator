<p align="center">
  <strong>VoIP Call Simulator</strong><br/>
  A minimal, containerized Asterisk 22 instance that places and receives SIP calls
  with human-like, randomized behavior — built for load-testing and validating
  call-switching / routing software against a CSV call-detail-record (CDR).
</p>

## What it does

Runs headless in two roles (set via `MODE`):

- **`callee`** – answers inbound SIP calls with a realistic state machine:
  post-dial delay → ring → early media → answer → talk → hangup, each phase
  governed by tunable probabilities (no-answer, busy, decline, hangup) and
  lognormal/exponential durations.
- **`caller`** – fires outbound calls at a target peer via Asterisk AMI
  `Originate`, with random caller-ids/destinations, ramp/concurrency control,
  a per-second rate limit, and randomized ring/answer/cancel timing.

Every answered, busy, unanswered, or failed call is written to a CSV CDR on
both sides, so you can reconcile your switching software's behavior against the
recorded disposition.

## Quick start (callee – answer calls)

```bash
docker run -d --name voip-callee --network host \
  -e MODE=callee \
  -e SIP_PORT=5060 \
  -e AMI_PORT=5038 \
  -e AMI_USER=sim -e AMI_SECRET=callsim \
  chitholian/voip-call-simulator:latest
```

The container listens on UDP/TCP **5060** (PJSIP) and answers inbound calls via
a state-machine AGI. Dial `sip:6888@<host>:5060` from any UAC/softphone.

## Quick start (caller – make calls against a peer)

Point `CALLER_PEER_HOST`/`CALLER_PEER_PORT` at a callee instance or your target
SIP server, then:

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
  chitholian/voip-call-simulator:latest
```

The caller will ramp to 50 concurrent calls at 10/sec, stop after 500 originate
attempts, and exit with code 0 once calls drain.

> **Two roles on one host** (because of `network_mode: host`): give each a
> distinct `SIP_PORT` + `AMI_PORT` — e.g. callee `5060/5038`, caller `2280/5039`.

## Configuration

All knobs are environment variables. Key ones:

| Variable | Default | Description |
|----------|---------|-------------|
| `MODE` | `callee` | `callee` (answer) or `caller` (originate). |
| `SIP_PORT` | `5060` | PJSIP UDP/TCP bind port (host networking → distinct per container). |
| `AMI_USER` / `AMI_SECRET` / `AMI_PORT` | `sim` / `callsim` / `5038` | AMI credentials used by the caller controller. |
| `CALLER_PEER_HOST` / `CALLER_PEER_PORT` | `127.0.0.1` / `5060` | Peer to dial. |
| `CALLER_FROM_PREFIXES` / `CALLER_FROM_LEN` | `1000,1001,1002` / `4` | Random caller-id prefixes + trailing digits. |
| `CALLER_TO_PREFIXES` / `CALLER_TO_LEN` | `2000,2001,2002` / `4` | Random destination prefixes + trailing digits. |
| `CALLER_MAX_CONCURRENT` | `100` | Target concurrent active calls. |
| `CALLER_RATE_PER_SEC` | `20` | Max originate attempts/sec. |
| `CALLER_MAX_TOTAL_CALLS` | `0` | Stop after N attempts (0 = unlimited). |
| `CALLER_MAX_SECONDS` | `0` | Stop after N seconds (0 = unlimited). |
| `CALLEE_PROB_NO_ANSWER` | `0.20` | Probability of not answering. |
| `CALLEE_PROB_BUSY` | `0.08` | Probability of returning 486 Busy. |
| `CALLEE_PROB_EARLY_MEDIA` | `0.08` | Probability of early media (183). |
| `CALLEE_PROB_DECLINE_IN_RING` | `0.06` | Per-ring-step probability of declining (603). |
| `CALLEE_PROB_HANGUP_PER_CHUNK` | `0.22` | Probability of hanging up per talk chunk. |
| `CALLEE_TALK_MEDIAN` / `CALLEE_TALK_SIGMA` | `60` / `0.9` | Talking-phase duration (lognormal). |
| `CALLEE_RING_MEAN` | `12` | Ringing hold before answer (exponential, seconds). |
| `CALLEE_PDD_MEAN` | `3` | Post-dial-delay before answer (exponential, seconds). |

See the repository `README.md` for the full variable reference and implementation notes.

## CDR output

CSV call records are written to
`/var/log/asterisk/cdr-csv/Master.csv` **inside the container**
(omit `--rm` or copy it out):

```bash
docker exec voip-callee cat /var/log/asterisk/cdr-csv/Master.csv
```

Includes dispositions for answered, busy, no-answer, and failed calls
(`unanswered=yes`), with UTC timestamps, so you can reconcile against the
target software.

## Build & publish

- Local build: `docker compose up -d --build` (alias `docker build -t voip-asterisk ./asterisk`).
- Automatic publish: pushes to **GitHub Container Registry** (`ghcr.io`) on
  every push to `main`, tagged `latest` and with the commit SHA.
- Base image: `andrius/asterisk:22` (Asterisk 22).

## License

Open source. See the repository for details and to report issues.
