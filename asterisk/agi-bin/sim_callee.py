#!/usr/bin/env python3
"""Human-like callee simulation (AGI), driven by /var/lib/asterisk/sim.json.

State machine matches real-world telephony. Each stage has a random delay
followed by a probabilistic transition (decline/abandon/continue), so the
overall path is non-deterministic but anchored in realistic distributions.

Stages:
  A. PDD        — random wait before any signalling.
  B. Umbrella   — no-answer (ring-only) | busy (486) | proceed to ring.
  C. Ring       — send 180, hold with per-step decline risk.
  D. Early      — small chance: 183 + play, then decline/continue.
  E. Answer     — 200, random pickup gap.
  F. Talk loop  — stream random audio in chunks; per-chunk hangup chance.
"""
import sys
import json
import random
import time
import math

sys.path.insert(0, "/var/lib/asterisk/agi-bin")
from agi_lib import (
    answer,
    ringing,
    progress,
    exec_app,
    stream_file,
    wait_seconds,
    hangup,
    alive,
    get_var,
    command,
)
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


# --- high-level actions -------------------------------------------------------

def play_for_seconds(total_s, max_chunk_s=10.0):
    """Stream random sounds until total_s elapsed (or channel dies).
    Chunked so the AGI process can detect peer hangup promptly."""
    if total_s <= 0:
        return
    elapsed = 0.0
    while elapsed < total_s:
        if not alive():
            return
        s = random_sound()
        if not s:
            # fallback to short WAIT if no sounds available
            wait_seconds(min(0.5, total_s - elapsed))
            elapsed += 0.5
            continue
        stream_file(s)  # blocks for the file's actual duration
        elapsed += 1.0   # approx; each stream_file consumes ≥1s
        if elapsed >= total_s:
            return


def hold_with_decline(total_s, decline_prob, poll_s=0.5):
    """Hold for up to total_s, evaluating a decline at each poll interval.
    Returns True if held to completion, False if declined, None if peer hung up."""
    steps = max(1, int(total_s / poll_s))
    for _ in range(steps):
        if not alive():
            return None
        if random.random() < decline_prob:
            return False
        wait_seconds(min(poll_s, total_s))
        total_s -= poll_s
        if total_s <= 0:
            return True
    return True


# --- main flow ----------------------------------------------------------------

def main():
    try:
        from agi_lib import command
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
    p_hangup_per_chunk = float(cfg.get("prob_hangup_per_chunk", 0.22))

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
    chunk = float(cfg.get("talk_chunk", 8.0))

    # A. Post-dial delay (silent setup, no signalling yet).
    wait_seconds(pdd)
    if not alive():
        return

    # B. Umbrella decision.
    r = random.random()
    if r < p_no_answer:
        # No-answer: never answer. Caller abandons.
        ringing()  # 180
        abandoned_at = exp_clamped(mean=25.0, lo=8.0, hi=40.0)
        hold_with_decline(abandoned_at, decline_prob=0.0, poll_s=1.0)
        hangup(0)
        return
    if r < p_no_answer + p_busy:
        exec_app("Busy")  # 486
        return

    # C. Ring (180 Ringing) — hold with decline risk.
    ringing()
    ring_result = hold_with_decline(ring, decline_prob=p_decline_ring / max(ring, 1.0) * 0.5,
                                    poll_s=0.5)
    if ring_result is None:
        return  # peer hung up
    if ring_result is False:
        hangup(21)  # 603 Decline
        return

    # D. Early media (small chance): 183+Progress, play, then decline or answer.
    if random.random() < p_early:
        progress()
        early_result = hold_with_decline(early_play, decline_prob=p_decline_early / max(early_play, 1.0) * 0.5,
                                         poll_s=0.5)
        if early_result is None:
            return
        if early_result is False or random.random() < 0.4:
            hangup(0)
            return
        # else continue to answer.

    # E. Answer (200 OK) + pickup gap.
    answer()
    if not alive():
        return
    wait_seconds(gap)

    # F. Talk loop with per-chunk hangup risk.
    elapsed = 0.0
    while elapsed < talk_total:
        if not alive():
            return
        # Stream a chunk (real audio, ~chunk seconds on average).
        play_for_seconds(min(chunk, talk_total - elapsed))
        elapsed += chunk
        if not alive():
            return
        if random.random() < p_hangup_per_chunk:
            hangup(0)
            return
        wait_seconds(random.uniform(0.2, 1.5))  # brief inter-chunk pause

    hangup(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        try:
            sys.stderr.write("callee err: %s\n" % e)
        except Exception:
            pass
        hangup(0)
