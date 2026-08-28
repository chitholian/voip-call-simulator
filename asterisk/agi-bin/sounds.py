#!/usr/bin/env python3
"""Random core-sound selection. Scans the sounds dir; picks verified .ulaw files."""
import os
import random

SOUNDS_DIR = "/var/lib/asterisk/sounds"


def _candidates():
    out = []
    try:
        for name in os.listdir(SOUNDS_DIR):
            if name.endswith(".ulaw"):
                out.append(name[: -len(".ulaw")])
    except OSError:
        pass
    return out


def random_sound():
    cand = _candidates()
    if not cand:
        return None
    return random.choice(cand)
