#!/usr/bin/env python3
"""Caller-side media AGI. Runs on the outbound leg after the callee answers
(Dial option B). Streams random core-sounds for a lognormal-duration call."""
import sys
import json
import math
import random

sys.path.insert(0, "/var/lib/asterisk/agi-bin")
from agi_lib import stream_file, wait_seconds, hangup, alive
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

    elapsed = 0.0
    while elapsed < target:
        if not alive():
            return
        s = random_sound()
        if not s:
            wait_seconds(0.5)
            elapsed += 0.5
            continue
        stream_file(s)
        elapsed += 1.0
        # brief inter-chunk pause for natural pacing
        if random.random() < 0.4:
            wait_seconds(random.uniform(0.2, 1.0))

    hangup(0)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        try:
            sys.stderr.write("caller_media err: %s\n" % e)
        except Exception:
            pass
        hangup(0)
