"""Sensor models: lag, gain/offset error, noise, quantisation, range limits, faults."""
from __future__ import annotations
import math
from dataclasses import dataclass
import numpy as np


@dataclass(frozen=True)
class ChannelSpec:
    noise_sd: float
    gain_sd: float
    offset_sd: float
    resolution: float
    lo: float
    hi: float
    lag_tau: float = 0.0
    sentinel: float = 0.0          # value reported on disconnect in "sentinel" mode


SPECS = {
    "v_dc":      ChannelSpec(0.03, 0.003, 0.02, 0.01, 0.0, 80.0),
    "i_dc":      ChannelSpec(0.01, 0.005, 0.01, 0.005, 0.0, 20.0),
    "t_module":  ChannelSpec(0.10, 0.0, 0.30, 0.0625, -55.0, 125.0, lag_tau=20.0, sentinel=-127.0),
    "t_ambient": ChannelSpec(0.10, 0.0, 0.30, 0.0625, -55.0, 125.0, lag_tau=60.0, sentinel=-127.0),
    "g_poa":     ChannelSpec(1.5, 0.02, 0.0, 1.0, 0.0, 1500.0),
}


class Channel:
    def __init__(self, name: str, rng):
        self.name, self.spec, self.rng = name, SPECS[name], rng
        self.gain_err = rng.normal(0, self.spec.gain_sd)       # fixed per sensor
        self.offset = rng.normal(0, self.spec.offset_sd)
        self.lag_y = None
        self.frozen = None

    def read(self, x, t, dt, faults):
        sp = self.spec
        if self.lag_y is None or sp.lag_tau <= 0:
            self.lag_y = x
        else:
            self.lag_y += (x - self.lag_y) * (1 - math.exp(-dt / sp.lag_tau))
        y = self.lag_y * (1 + self.gain_err) + self.offset
        noise_mult, freeze, disc = 1.0, False, None
        for f, s in faults:
            P = f.params
            if f.type == "sensor_offset":
                y += s * P.get("offset", 1.0)
            elif f.type == "sensor_drift":
                y += P.get("rate_per_h", 1.0) * max(t - f.start_s, 0.0) / 3600.0
            elif f.type == "sensor_gain":
                y *= 1 + s * (P.get("factor", 0.9) - 1)
            elif f.type == "sensor_noise":
                noise_mult *= 1 + s * (P.get("factor", 6.0) - 1)
            elif f.type == "sensor_spike":
                if self.rng.random() < P.get("prob", 0.02):
                    y += self.rng.choice([-1, 1]) * P.get("amplitude", 10.0)
            elif f.type == "sensor_freeze":
                freeze = True
            elif f.type == "sensor_disconnect":
                disc = P.get("mode", "null")
        y += self.rng.normal(0, sp.noise_sd * noise_mult)
        if sp.resolution > 0:
            y = round(y / sp.resolution) * sp.resolution
        y = min(max(y, sp.lo), sp.hi)
        if disc is not None:
            return {"null": None, "zero": 0.0, "sentinel": sp.sentinel, "rail": sp.hi}[disc]
        if freeze:
            if self.frozen is None:
                self.frozen = y
            return self.frozen
        self.frozen = None
        return y
