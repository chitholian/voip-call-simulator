#!/usr/bin/env python3
"""Human-like callee simulation (AGI), driven by /var/lib/asterisk/sim.json.

Compute-only FastAGI handler: rolls the *entire* call scenario upfront (outcome,
all durations, decline points, talk plan), writes it as CALLEE_* channel
variables, and returns instantly. No sleeps, no media — the dialplan (rendered
by entrypoint.sh into extensions-agi.conf) executes the timeline with Wait/
Ringing/Progress/Answer/Playback, so no AGI thread/socket is held per call.

Scenario variables (all consumed by dialplan):
  CALLEE_OUTCOME        NO_ANSWER | BUSY | RING_DECLINE | EARLY | TALK
  CALLEE_PDD            silent post-dial delay  (all branches)
  CALLEE_RING           ring hold when no decline (180)
  CALLEE_RING_DECLINE_AT  seconds into ring where 603 fires; 0 = no decline
  CALLEE_ABANDON_AT     no-answer hold before hangup (NO_ANSWER)
  CALLEE_EARLY_PLAY     early-media hold length (EARLY)
  CALLEE_EARLY_DECLINE  1 = decline during early media
  CALLEE_EARLY_ABANDON  1 = hangup after early media (40% roll)
  CALLEE_GAP            pickup gap after Answer
  CALLEE_SOUNDS         '&'-joined sound sequence (one 1s sound per talk second)
"""
import sys
import json
import random
import math

sys.path.insert(0, "/var/lib/asterisk/agi-bin")
from agi_lib import command, set_var
from sounds import random_sound

CFG = "/var/lib/asterisk/sim.json"


def load_cfg():
    try:
        with open(CFG) as f:
            return json.load(f)
    except Exception:
        return {}


# --- distributions (real-world) ----------------------------------------------

def exp_clamped(mean, lo, hi):
    """Exponential, clamped to [lo, hi]. Models durations with right-tail
    (post-dial delay, time-to-answer, ring hold)."""
    v = random.expovariate(1.0 / mean)
    return max(lo, min(hi, v))


def lognormal_clamped(median, sigma, lo, hi):
    """Lognormal duration, clamped to [lo, hi]. Models talk/hold times
    (heavy right tail — most calls short, a few long)."""
    mu = math.log(median)
    v = math.exp(random.gauss(mu, sigma))
    return max(lo, min(hi, v))


def decline_time(prob, duration, poll_s=0.5):
    """Single-shot equivalent of the old per-poll decline loop.

    The old code polled every poll_s and declined when random() < q, with
    q = prob / max(duration,1) * poll_s. That is a binomial process with
    steps = duration/poll_s and per-step success q. Inverting the geometric
    CDF yields the survival count in one draw. Returns the decline time on
    the poll grid, or None if the hold survives to completion (0 = instantly).
    """
    if prob <= 0 or duration <= 0:
        return None
    steps = max(1, int(duration / poll_s))
    q = prob / max(duration, 1.0) * poll_s
    if q >= 1.0:
        return 0.0
    survived = int(math.log(1.0 - random.random()) / math.log(1.0 - q))
    if survived >= steps:
        return None
    return survived * poll_s


# --- main flow ----------------------------------------------------------------

def main():
    try:
        raw_from = command("GET VARIABLE PJSIP_HEADER(read,From)", 2.0)
        from_val = ""
        if raw_from and "(" in raw_from and ")" in raw_from:
            inside = raw_from.split("(", 1)[1].rsplit(")", 1)[0].strip().strip('"')
            if "sip:" in inside:
                try:
                    user = inside.split("sip:", 1)[1].split("@", 1)[0].strip("<> ")
                    if user and user.isdigit():
                        from_val = user
                except Exception:
                    pass
        if from_val:
            command(f"SET CALLERID {from_val}", 2.0)
    except Exception:
        pass
    cfg = load_cfg().get("callee", {})

    # Umbrella probabilities (must sum to ≤ 1.0; remainder = direct answer).
    p_no_answer = float(cfg.get("prob_no_answer", 0.20))
    p_busy = float(cfg.get("prob_busy", 0.08))
    p_early = float(cfg.get("prob_early_media", 0.08))
    # Decline probs per stage (probability of declining *at* this stage).
    p_decline_ring = float(cfg.get("prob_decline_in_ring", 0.06))
    p_decline_early = float(cfg.get("prob_decline_in_early", 0.35))

    # Random durations.
    pdd = exp_clamped(
        mean=float(cfg.get("pdd_mean", 3.0)),
        lo=float(cfg.get("pdd_min", 1.0)),
        hi=float(cfg.get("pdd_max", 10.0)),
    )
    ring = exp_clamped(
        mean=float(cfg.get("ring_mean", 12.0)),
        lo=float(cfg.get("ring_min", 2.0)),
        hi=float(cfg.get("ring_max", 45.0)),
    )
    early_play = exp_clamped(
        mean=float(cfg.get("early_play_mean", 4.0)),
        lo=float(cfg.get("early_play_min", 2.0)),
        hi=float(cfg.get("early_play_max", 8.0)),
    )
    gap = random.uniform(
        float(cfg.get("gap_min_ms", 600)) / 1000.0,
        float(cfg.get("gap_max_ms", 3000)) / 1000.0,
    )
    talk_total = lognormal_clamped(
        median=float(cfg.get("talk_median", 60.0)),
        sigma=float(cfg.get("talk_sigma", 0.9)),
        lo=float(cfg.get("talk_min", 8.0)),
        hi=float(cfg.get("talk_max", 180.0)),
    )

    # Roll the umbrella first (no_answer/busy are terminal before any ring risk).
    r = random.random()
    outf = ""
    ring_decline_at = decline_time(p_decline_ring, ring)
    early_play_at = early_play
    early_declined = False
    early_abandon = False
    abandon_at = 0.0
    if r < p_no_answer:
        outf = "NO_ANSWER"
        abandon_at = exp_clamped(mean=25.0, lo=8.0, hi=40.0)
    elif r < p_no_answer + p_busy:
        outf = "BUSY"
    elif ring_decline_at is not None:
        outf = "RING_DECLINE"
        ring = ring_decline_at
    else:
        if random.random() < p_early:
            outf = "EARLY"
            early_declined = decline_time(p_decline_early, early_play) is not None
            early_abandon = (not early_declined) and random.random() < 0.4
        else:
            outf = "TALK"

    # Talk plan (only consumed for TALK; harmless elsewhere).
    # Precomputed per-call sound sequence: one 1s sound per talk second,
    # '&'-joined so the dialplan plays the whole call with ONE Playback()
    # (func_cut is broken in this Asterisk build, so no dialplan-side selection).
    talk_sounds = max(1, int(round(talk_total))) if outf == "TALK" else 0
    seq = []
    for _ in range(talk_sounds):
        s = random_sound()
        if s:
            seq.append(s)
    sounds_csv = "&".join(seq)

    for name, val in {
        "CALLEE_OUTCOME": outf,
        "CALLEE_PDD": f"{pdd:.2f}",
        "CALLEE_RING": f"{ring:.2f}" if outf in ("EARLY", "TALK") else "0.00",
        "CALLEE_RING_DECLINE_AT": f"{ring_decline_at:.2f}" if ring_decline_at is not None else "0.00",
        "CALLEE_ABANDON_AT": f"{abandon_at:.2f}",
        "CALLEE_EARLY_PLAY": f"{early_play_at:.2f}",
        "CALLEE_EARLY_DECLINE": "1" if early_declined else "0",
        "CALLEE_EARLY_ABANDON": "1" if early_abandon else "0",
        "CALLEE_GAP": f"{gap:.2f}",
        "CALLEE_SOUNDS": sounds_csv,
    }.items():
        set_var(name, val)