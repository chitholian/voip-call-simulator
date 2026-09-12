#!/bin/bash
# Container entrypoint.
# - Renders /var/lib/asterisk/sim.json from env vars.
# - Starts Asterisk.
# - In callee mode: keeps Asterisk alive (incoming calls are handled by AGI scripts).
# - In caller mode: runs sim_caller.py to fire calls via AMI.
set -euo pipefail

# Raise the FD limit early so Asterisk + AGI scripts inherit it. Best-effort:
# non-root runs can only raise soft up to the existing hard limit.
ulimit -Hn unlimited 2>/dev/null || true
ulimit -n "${MAX_OPEN_FILES:-1048576}" 2>/dev/null || true

: "${MODE:=callee}"          # callee | caller
: "${SIP_PORT:=5060}"        # PJSIP transport bind port (host networking)
: "${FASTAGI_PORT:=4573}"    # FastAGI server port (distinct per container on one host)
: "${AMI_USER:=sim}"
: "${AMI_SECRET:=callsim}"
: "${AMI_PORT:=5038}"
: "${CALLER_FROM_PREFIXES:=1000,1001,1002}"
: "${CALLER_TO_PREFIXES:=2000,2001,2002}"
: "${CALLER_MAX_CONCURRENT:=100}"
: "${CALLER_RATE_PER_SEC:=20}"
: "${CALLER_MAX_TOTAL_CALLS:=0}"
: "${CALLER_MAX_SECONDS:=0}"
: "${CALLER_FROM_LEN:=4}"
: "${CALLER_TO_LEN:=4}"
: "${CALLER_RING_MIN_MS:=15000}"
: "${CALLER_RING_MAX_MS:=60000}"
: "${CALLER_PEER_HOST:=127.0.0.1}"
: "${CALLER_PEER_PORT:=5060}"
: "${CALLER_DEFAULT_FROM:=}"

: "${CALLEE_PROB_NO_ANSWER:=0.20}"
: "${CALLEE_PROB_BUSY:=0.08}"
: "${CALLEE_PROB_EARLY_MEDIA:=0.08}"
: "${CALLEE_PROB_DECLINE_IN_RING:=0.06}"
: "${CALLEE_PROB_DECLINE_IN_EARLY:=0.35}"
: "${CALLEE_PROB_HANGUP_PER_CHUNK:=0.22}"
: "${CALLEE_PDD_MIN:=1.0}"
: "${CALLEE_PDD_MAX:=10.0}"
: "${CALLEE_PDD_MEAN:=3.0}"
: "${CALLEE_RING_MIN:=2.0}"
: "${CALLEE_RING_MAX:=45.0}"
: "${CALLEE_RING_MEAN:=12.0}"
: "${CALLEE_EARLY_PLAY_MIN:=2.0}"
: "${CALLEE_EARLY_PLAY_MAX:=8.0}"
: "${CALLEE_EARLY_PLAY_MEAN:=4.0}"
: "${CALLEE_GAP_MIN_MS:=600}"
: "${CALLEE_GAP_MAX_MS:=3000}"
: "${CALLEE_TALK_MIN:=8.0}"
: "${CALLEE_TALK_MAX:=180.0}"
: "${CALLEE_TALK_MEDIAN:=60.0}"
: "${CALLEE_TALK_SIGMA:=0.9}"
: "${CALLEE_TALK_CHUNK:=8.0}"
# IP whitelist (comma-separated IPs/CIDRs) permitted to call the callee. Empty
# = allow all sources. Applied to the [anonymous] endpoint as a contact ACL.
: "${CALLEE_ALLOW_IPS:=}"

: "${CALLER_TALK_MIN:=8.0}"
: "${CALLER_TALK_MAX:=180.0}"
: "${CALLER_TALK_MEDIAN:=60.0}"
: "${CALLER_TALK_SIGMA:=0.9}"

split_csv() { echo "$1" | tr ',' '\n' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' | awk 'NF'; }

from_prefixes_json=$(printf '"%s",' $(split_csv "$CALLER_FROM_PREFIXES") | sed 's/,$//')
to_prefixes_json=$(printf '"%s",' $(split_csv "$CALLER_TO_PREFIXES") | sed 's/,$//')

cat > /var/lib/asterisk/sim.json <<JSON
{
  "callee": {
    "prob_no_answer": ${CALLEE_PROB_NO_ANSWER},
    "prob_busy": ${CALLEE_PROB_BUSY},
    "prob_early_media": ${CALLEE_PROB_EARLY_MEDIA},
    "prob_decline_in_ring": ${CALLEE_PROB_DECLINE_IN_RING},
    "prob_decline_in_early": ${CALLEE_PROB_DECLINE_IN_EARLY},
    "prob_hangup_per_chunk": ${CALLEE_PROB_HANGUP_PER_CHUNK},
    "pdd_min": ${CALLEE_PDD_MIN}, "pdd_max": ${CALLEE_PDD_MAX}, "pdd_mean": ${CALLEE_PDD_MEAN},
    "ring_min": ${CALLEE_RING_MIN}, "ring_max": ${CALLEE_RING_MAX}, "ring_mean": ${CALLEE_RING_MEAN},
    "early_play_min": ${CALLEE_EARLY_PLAY_MIN}, "early_play_max": ${CALLEE_EARLY_PLAY_MAX}, "early_play_mean": ${CALLEE_EARLY_PLAY_MEAN},
    "gap_min_ms": ${CALLEE_GAP_MIN_MS}, "gap_max_ms": ${CALLEE_GAP_MAX_MS},
    "talk_min": ${CALLEE_TALK_MIN}, "talk_max": ${CALLEE_TALK_MAX},
    "talk_median": ${CALLEE_TALK_MEDIAN}, "talk_sigma": ${CALLEE_TALK_SIGMA},
    "talk_chunk": ${CALLEE_TALK_CHUNK}
  },
  "caller": {
    "from_prefixes": [${from_prefixes_json}],
    "to_prefixes": [${to_prefixes_json}],
    "from_number_len": ${CALLER_FROM_LEN},
    "to_number_len": ${CALLER_TO_LEN},
    "max_concurrent": ${CALLER_MAX_CONCURRENT},
    "rate_per_sec": ${CALLER_RATE_PER_SEC},
    "max_total_calls": ${CALLER_MAX_TOTAL_CALLS},
    "max_seconds": ${CALLER_MAX_SECONDS},
    "ring_min_ms": ${CALLER_RING_MIN_MS},
    "ring_max_ms": ${CALLER_RING_MAX_MS},
    "peer_host": "${CALLER_PEER_HOST}",
    "peer_port": ${CALLER_PEER_PORT},
    "talk_min": ${CALLER_TALK_MIN}, "talk_max": ${CALLER_TALK_MAX},
    "talk_median": ${CALLER_TALK_MEDIAN}, "talk_sigma": ${CALLER_TALK_SIGMA}
  }
}
JSON

echo "MODE=${MODE}"
echo "SIP_PORT=${SIP_PORT}"
echo "sim.json:"
cat /var/lib/asterisk/sim.json

# Ensure runtime dirs are owned by asterisk. /var/lib/asterisk/sounds is an
# RO image mount; recursing chown over /var/lib/asterisk would fail on it.
mkdir -p /var/run/asterisk /var/log/asterisk /var/log/asterisk/cdr-csv /var/spool/asterisk /var/lib/asterisk/agi-bin /var/lib/asterisk/sounds
chown -R asterisk:asterisk /var/run/asterisk /var/log/asterisk /var/spool/asterisk /var/lib/asterisk/agi-bin || true
chown asterisk:asterisk /var/lib/asterisk/sounds 2>/dev/null || true

# Sound inventory for the talk phase (flat .ulaw scan, same as sounds.py).
shopt -s nullglob
sound_list=""
for f in /var/lib/asterisk/sounds/*.ulaw; do
  case "$sound_list" in
    "") sound_list="$(basename "$f" .ulaw)" ;;
    *)  sound_list="$sound_list,$(basename "$f" .ulaw)" ;;
  esac
done
sound_count=$(for f in /var/lib/asterisk/sounds/*.ulaw; do echo x; done | wc -l)

# Render FastAGI dialplan include (supports distinct ports per container on one host).
# sim_callee.py rolls the whole scenario into CALLEE_* vars and returns instantly;
# the callee context executes it (Wait/Ringing/Progress/Answer/Playback) so no AGI
# thread is held for the call duration. Dialplan '$' refs are escaped (\$) so bash
# leaves them to Asterisk.
cat > /var/lib/asterisk/extensions-agi.conf <<EOF
; --- caller_talk: outbound leg after answer (Dial option B) ---
; sim_caller_media.py computes the talk script into CALLER_* vars and returns
; instantly; this context executes it (Playback aborts natively on hangup).
[caller_talk]
exten => s,1,NoOp(CALLER-MEDIA start)
 same => n,AGI(agi://127.0.0.1:${FASTAGI_PORT}/caller_media)
 same => n,GotoIf(\$["\${CALLER_SOUNDS}" = ""]?caller_talk,end,1)
 same => n,Goto(caller_talk,sounds,1)
exten => sounds,1,Playback(\${CALLER_SOUNDS})
 same => n,Hangup()
exten => end,1,Hangup()

; --- callee: inbound calls from anonymous endpoint ---
[callee]
exten => _X.,1,NoOp(CALLEE-SIM start)
 same => n,AGI(agi://127.0.0.1:${FASTAGI_PORT}/callee)
 same => n,GotoIf(\$["\${CALLEE_OUTCOME}" = ""]?callee,end,1)
 same => n,Wait(\${CALLEE_PDD})
 same => n,GotoIf(\$["\${CALLEE_OUTCOME}" = "NO_ANSWER"]?callee,noanswer,1)
 same => n,GotoIf(\$["\${CALLEE_OUTCOME}" = "BUSY"]?callee,busy,1)
 same => n,Ringing()
 same => n,GotoIf(\$[\${CALLEE_RING_DECLINE_AT} > 0]?callee,ring-decline,1)
 same => n,Wait(\${CALLEE_RING})
 same => n,GotoIf(\$["\${CALLEE_OUTCOME}" = "EARLY"]?callee,early,1)
 same => n,Goto(callee,talk,1)
exten => ring-decline,1,Wait(\${CALLEE_RING_DECLINE_AT})
 same => n,Hangup(21)
exten => noanswer,1,Ringing()
 same => n,Wait(\${CALLEE_ABANDON_AT})
 same => n,Hangup()
exten => busy,1,Busy()
 same => n,Hangup()
exten => early,1,Progress()
 same => n,GotoIf(\$[\${CALLEE_EARLY_DECLINE} = 1]?callee,end,1)
 same => n,Wait(\${CALLEE_EARLY_PLAY})
 same => n,GotoIf(\$[\${CALLEE_EARLY_ABANDON} = 1]?callee,end,1)
 same => n,Goto(callee,talk,1)
exten => talk,1,Answer()
 same => n,Wait(\${CALLEE_GAP})
 same => n,GotoIf(\$["\${CALLEE_SOUNDS}" = ""]?callee,end,1)
 same => n,Goto(callee,sounds,1)
exten => sounds,1,Playback(\${CALLEE_SOUNDS})
 same => n,Hangup()
exten => end,1,Hangup()
EOF
chown asterisk:asterisk /var/lib/asterisk/extensions-agi.conf

# Render PJSIP transports with the configured bind port.
cat > /var/lib/asterisk/pjsip-transport.conf <<EOF
[transport-udp]
type=transport
protocol=udp
bind=0.0.0.0:${SIP_PORT}

[transport-tcp]
type=transport
protocol=tcp
bind=0.0.0.0:${SIP_PORT}
EOF
chown asterisk:asterisk /var/lib/asterisk/pjsip-transport.conf

# Render the inbound (callee) endpoint. If CALLEE_ALLOW_IPS is set it becomes a
# strict whitelist (blanket deny + per-IP permit); otherwise all sources allowed.
{
  echo "[anonymous]"
  echo "type=endpoint"
  echo "context=callee"
  echo "allow=all"
  echo "trust_id_inbound=yes"
  if [ -n "$CALLEE_ALLOW_IPS" ]; then
    echo "deny=0.0.0.0/0"
    split_csv "$CALLEE_ALLOW_IPS" | while read -r ip; do
      echo "permit=${ip}"
    done
  else
    echo "permit=0.0.0.0/0"
  fi
} > /var/lib/asterisk/pjsip-anonymous.conf
chown asterisk:asterisk /var/lib/asterisk/pjsip-anonymous.conf

# Render the outbound (caller) endpoint that dials the peer. dial
# PJSIP/<number>@caller-out sends the INVITE to CALLER_PEER_HOST:PORT.
cat > /var/lib/asterisk/pjsip-caller.conf <<EOF
[caller-out]
type=endpoint
context=caller
disallow=all
allow=ulaw
aors=caller-out-aor
callerid=${CALLER_DEFAULT_FROM}

[caller-out-aor]
type=aor
contact=sip:${CALLER_PEER_HOST}:${CALLER_PEER_PORT}
EOF
chown asterisk:asterisk /var/lib/asterisk/pjsip-caller.conf

# Render AMI with the configured bind port.
cat > /var/lib/asterisk/manager-ami.conf <<EOF
[general]
enabled=yes
bindaddr=127.0.0.1
port=${AMI_PORT}
displayconnects=no

[sim]
secret=${AMI_SECRET}
read=system,call,originate,reporting
write=system,call,originate,reporting
permit=127.0.0.1/255.255.255.255
EOF
chown asterisk:asterisk /var/lib/asterisk/manager-ami.conf

# Drop privileges to asterisk for everything from here.
if [ "$(id -u)" = "0" ]; then
  exec_runas() { runuser -u asterisk -- "$@"; }
else
  exec_runas() { "$@"; }
fi

# Launch Asterisk.
exec_runas asterisk -f -U asterisk -G asterisk &
ASTERISK_PID=$!

# Wait for AMI to come up.
for _ in $(seq 1 50); do
  if exec_runas bash -c "echo > /dev/tcp/127.0.0.1/${AMI_PORT}" 2>/dev/null; then
    break
  fi
  sleep 0.2
done

# Start FastAGI server (persistent process replaces per-call fork), supervised:
# respawn on any unexpected exit; stop on TERM/INT (container shutdown).
FASTAGI_STOP=0
( trap 'FASTAGI_STOP=1; kill "$FASTAGI_PID" 2>/dev/null || true; exit 0' TERM INT
  while [ "$FASTAGI_STOP" -eq 0 ]; do
    exec_runas python3 /var/lib/asterisk/agi-bin/fastagi_server.py &
    FASTAGI_PID=$!
    wait "$FASTAGI_PID" 2>/dev/null || true
    [ "$FASTAGI_STOP" -eq 0 ] || exit 0
    echo "fastagi: server exited unexpectedly, restarting" >&2
    sleep 1
  done ) &
FASTAGI_SUPER_PID=$!

cleanup() {
  kill "$FASTAGI_SUPER_PID" 2>/dev/null || true
  wait "$FASTAGI_SUPER_PID" 2>/dev/null || true
  kill "$ASTERISK_PID" 2>/dev/null || true
  wait "$ASTERISK_PID" 2>/dev/null || true
}
trap cleanup EXIT

case "$MODE" in
  caller)
    export AMI_USER AMI_SECRET AMI_PORT
    python3 /var/lib/asterisk/agi-bin/sim_caller.py &
    CALLER_PID=$!
    _term() { kill -TERM "$CALLER_PID" 2>/dev/null || true; }
    trap _term TERM INT
    # wait is interrupted when TERM arrives (trap runs), so re-wait until the
    # caller actually exits after draining its in-flight calls.
    wait "$CALLER_PID" 2>/dev/null || true
    while kill -0 "$CALLER_PID" 2>/dev/null; do
      wait "$CALLER_PID" 2>/dev/null || true
    done
    trap - TERM INT
    ;;
  callee|*)
    # Forward TERM/INT to Asterisk (SIGTERM = graceful stop: no new calls,
    # in-flight calls finish) so the container exits cleanly, not 143.
    _term() { kill -TERM "$ASTERISK_PID" 2>/dev/null || true; }
    trap _term TERM INT
    # wait is interrupted when TERM arrives (trap runs); re-wait until
    # Asterisk has actually exited.
    wait "$ASTERISK_PID" 2>/dev/null || true
    while kill -0 "$ASTERISK_PID" 2>/dev/null; do
      wait "$ASTERISK_PID" 2>/dev/null || true
    done
    trap - TERM INT
    ;;
esac
