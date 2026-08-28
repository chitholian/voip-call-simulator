#!/usr/bin/env python3
"""Realistic telephony distributions. Exponential for arrivals/holds (memoryless),
lognormal for talk durations (long tail), truncated/clamped to sane bounds."""
import random
import math


def exp_mean(mean, lo, hi):
    """Exponential with given mean, clamped to [lo,hi]."""
    v = random.expovariate(1.0 / mean)
    if v < lo:
        v = lo
    if v > hi:
        v = hi
    return v


def lognormal(median, sigma, lo, hi):
    """Lognormal duration with given median (s) and sigma, clamped to [lo,hi]."""
    mu = math.log(median)
    v = math.exp(random.gauss(mu, sigma))
    if v < lo:
        v = lo
    if v > hi:
        v = hi
    return v
