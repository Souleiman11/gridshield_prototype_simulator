"""Fault definitions: physical, sensor and communication faults with severity ramps."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Optional
import numpy as np
from .pv_model import Modifiers

PHYSICAL = {"partial_shading", "soiling", "degradation", "hotspot",
            "open_circuit", "bypass_diode_short"}
SENSOR = {"sensor_offset", "sensor_drift", "sensor_gain", "sensor_freeze",
          "sensor_disconnect", "sensor_noise", "sensor_spike"}
COMM = {"packet_loss", "comm_outage", "comm_delay", "comm_duplicate"}
ALL_TYPES = sorted(PHYSICAL | SENSOR | COMM)
CHANNELS = ("v_dc", "i_dc", "t_module", "t_ambient", "g_poa")


@dataclass
class Fault:
    type: str
    node: str = "pv01"                # node id or "*" for all nodes
    start_s: float = 0.0
    end_s: Optional[float] = None     # None = until end of simulation
    ramp_s: float = 0.0               # linear ramp 0 -> 1 after start
    params: dict = field(default_factory=dict)
    id: str = ""

    def __post_init__(self):
        if self.type not in ALL_TYPES:
            raise ValueError(f"unknown fault type '{self.type}'. Known: {ALL_TYPES}")

    @property
    def category(self) -> str:
        return "physical" if self.type in PHYSICAL else "sensor" if self.type in SENSOR else "comm"

    def severity(self, t: float) -> float:
        if t < self.start_s or (self.end_s is not None and t >= self.end_s):
            return 0.0
        return 1.0 if self.ramp_s <= 0 else min(1.0, (t - self.start_s) / self.ramp_s)

    @staticmethod
    def from_dict(d: dict) -> "Fault":
        return Fault(**d)

    def to_dict(self) -> dict:
        return asdict(self)


def physical_modifiers(active, s: int) -> Modifiers:
    """Translate active physical faults into model modifiers."""
    m = Modifiers.neutral(s)
    for f, sev in active:
        P = f.params
        if f.type == "partial_shading":
            rem = P.get("remaining", 0.15)         # irradiance fraction left on shaded substring
            for k in P.get("substrings", [0]):
                m.g_factor[k] *= 1 - sev * (1 - rem)
        elif f.type == "soiling":
            m.g_factor *= 1 - sev * P.get("loss_max", 0.12)
        elif f.type == "degradation":
            m.rs_scale *= 1 + sev * (P.get("rs_factor", 2.5) - 1)
            m.rsh_scale *= 1 - sev * (1 - P.get("rsh_factor", 0.4))
            m.iph_scale *= 1 - sev * P.get("iph_loss", 0.04)
        elif f.type == "hotspot":
            k = P.get("substring", 1)
            m.extra_heat[k] += sev * P.get("extra_heat_wm2", 450.0)
            m.rsh_scale[k] *= 1 - sev * (1 - P.get("rsh_factor", 0.5))
        elif f.type == "open_circuit":
            m.open_circuit = True
        elif f.type == "bypass_diode_short":
            m.shorted[P.get("substring", 0)] = True
    return m
