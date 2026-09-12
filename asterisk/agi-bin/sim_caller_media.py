#!/usr/bin/env python3
"""Caller-side media AGI. Runs on the outbound leg after the callee answers
(Dial option B). Computes the whole talk plan up front — duration and the
exact sound sequence — into CALLER_* channel variables and returns instantly.
The caller_talk dialplan then runs it (Playback aborts natively on peer
hangup), so no AGI thread is held for the call duration.

Vars set (caller_talk context):
  CALLER_OUTCOME  always "TALK" (debug/consistency)
  CALLER_SOUNDS   '&'-joined sound names, one 1s sound per talk second
"""
import sys
import json
import math
import random

sys.path.insert(0, "/var/lib/asterisk/agi-bin")
from agi_lib import set_var
from sounds import random_sound

CFG = "/var/lib/asterisk/sim.json"


def load_cfg():
    try:
        with open(CFG) as f:
            return json.load(f)
    except Exception:
        return {}


def main():
    cfg = load_cfg().get("caller", {})
    median = float(cfg.get("talk_median", 60.0))
    sigma = float(cfg.get("talk_sigma", 0.9))
    lo = float(cfg.get("talk_min", 8.0))
    hi = float(cfg.get("talk_max", 180.0))
    target = math.exp(random.gauss(math.log(median), sigma))
    target = max(lo, min(hi, target))

    n = max(1, int(round(target)))
    seq = []
    for _ in range(n):
        s = random_sound()
        if s:
            seq.append(s)

    set_var("CALLER_OUTCOME", "TALK")
    set_var("CALLER_SOUNDS", "&".join(seq))