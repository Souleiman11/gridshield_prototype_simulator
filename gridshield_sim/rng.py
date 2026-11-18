"""Random numbers from Python's standard library (no numpy.random needed).

Some Windows setups block numpy's compiled random-number modules; the standard
library generator avoids that dependency and gives the same reproducibility.
"""
from __future__ import annotations
import math
import random


class Rng:
    def __init__(self, seed: int):
        self._r = random.Random(seed)

    def random(self) -> float:
        return self._r.random()

    def normal(self, mu: float = 0.0, sigma: float = 1.0) -> float:
        return self._r.gauss(mu, sigma) if sigma > 0 else mu

    def standard_normal(self) -> float:
        return self._r.gauss(0.0, 1.0)

    def uniform(self, a: float, b: float) -> float:
        return self._r.uniform(a, b)

    def lognormal(self, mean: float, sigma: float) -> float:
        return self._r.lognormvariate(mean, sigma)

    def choice(self, seq):
        return self._r.choice(seq)

    def poisson(self, lam: float) -> int:
        if lam <= 0:
            return 0
        if lam > 30:                                   # normal approximation
            return max(0, int(round(self._r.gauss(lam, math.sqrt(lam)))))
        limit, k, p = math.exp(-lam), 0, 1.0           # Knuth
        while True:
            p *= self._r.random()
            if p <= limit:
                return k
            k += 1

    def spawn(self, n: int) -> list:
        """n independent child generators (deterministic given the parent seed)."""
        return [Rng(self._r.getrandbits(64)) for _ in range(n)]
