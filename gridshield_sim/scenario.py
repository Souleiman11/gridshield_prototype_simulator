"""Scenario description, JSON loading and built-in scenarios."""
from __future__ import annotations
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from .faults import Fault


@dataclass
class NodeConfig:
    node_id: str = "pv01"
    role: str = "main"                 # "main" | "reference" (informational)
    module: str = "generic-270"
    sensor_substring: int = 1          # substring the back-sheet T sensor is glued to
    mppt_scan_period_s: float = 900.0


@dataclass
class Scenario:
    name: str = "custom"
    description: str = ""
    start_utc: str = "2026-06-21T05:00:00"
    duration_s: float = 15 * 3600
    step_s: float = 1.0
    seed: int = 42
    site: str = "site01"
    location: dict = field(default_factory=dict)      # lat, lon, tilt_deg, azimuth_deg
    weather: dict = field(default_factory=dict)       # cloud_cover, base_kt, t_mean_c, t_amp_c, wind_mean
    nodes: list = field(default_factory=lambda: [NodeConfig()])
    faults: list = field(default_factory=list)

    @property
    def start(self) -> datetime:
        return datetime.fromisoformat(self.start_utc).replace(tzinfo=timezone.utc)

    @staticmethod
    def from_dict(d: dict) -> "Scenario":
        d = dict(d)
        d["nodes"] = [NodeConfig(**n) for n in d.get("nodes", [{}])]
        d["faults"] = [Fault.from_dict(f) for f in d.get("faults", [])]
        return Scenario(**d)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @staticmethod
    def load(path: str) -> "Scenario":
        with open(path) as fh:
            return Scenario.from_dict(json.load(fh))

    def save(self, path: str):
        with open(path, "w") as fh:
            json.dump(self.to_dict(), fh, indent=2)


H = 3600.0


def _base(name, desc, **kw) -> Scenario:
    return Scenario(name=name, description=desc, **kw)


def _sc_normal():
    return _base("normal_clear_day", "Clear day, healthy module, no faults.")


def _sc_clouds():
    return _base("cloud_transients", "Intermittent clouds causing rapid irradiance changes.",
                 weather=dict(cloud_cover=0.7))


def _sc_shading():
    return _base("partial_shading", "A shadow covers one substring between 10:00 and 12:30 UTC.",
                 faults=[Fault("partial_shading", "pv01", 5 * H, 7.5 * H, 600,
                               {"substrings": [0], "remaining": 0.2})])


def _sc_soiling():
    return _base("progressive_soiling", "Dust accumulates: 14 % optical loss built up over the day. "
                 "A clean reference module is present for comparison.",
                 nodes=[NodeConfig("pv01"), NodeConfig("pv_ref", role="reference")],
                 faults=[Fault("soiling", "pv01", 0, None, 14 * H, {"loss_max": 0.14})])


def _sc_degradation():
    return _base("electrical_degradation", "Series resistance rises and shunt resistance falls from 09:00 UTC.",
                 nodes=[NodeConfig("pv01"), NodeConfig("pv_ref", role="reference")],
                 faults=[Fault("degradation", "pv01", 4 * H, None, 3 * H,
                               {"rs_factor": 3.0, "rsh_factor": 0.35, "iph_loss": 0.03})])


def _sc_hotspot():
    return _base("thermal_anomaly", "Localised hotspot develops on the middle substring.",
                 faults=[Fault("hotspot", "pv01", 5 * H, None, 1800,
                               {"substring": 1, "extra_heat_wm2": 500.0})])


def _sc_sensors():
    return _base("sensor_failures", "Offset, freezing, disconnection, drift and noise on different sensors.",
                 faults=[Fault("sensor_offset", "pv01", 3 * H, 5 * H, 0, {"channel": "i_dc", "offset": 0.4}),
                         Fault("sensor_freeze", "pv01", 6 * H, 7 * H, 0, {"channel": "t_module"}),
                         Fault("sensor_disconnect", "pv01", 8 * H, 9 * H, 0, {"channel": "g_poa", "mode": "zero"}),
                         Fault("sensor_drift", "pv01", 9.5 * H, 12 * H, 0, {"channel": "v_dc", "rate_per_h": 0.6}),
                         Fault("sensor_noise", "pv01", 12.5 * H, 13.5 * H, 0, {"channel": "i_dc", "factor": 15}),
                         Fault("sensor_disconnect", "pv01", 13.6 * H, 14 * H, 0,
                               {"channel": "t_ambient", "mode": "sentinel"})])


def _sc_comm():
    return _base("communication_faults", "Packet loss, outage, delay and duplicates.",
                 faults=[Fault("packet_loss", "pv01", 2 * H, 3 * H, 0, {"prob": 0.4}),
                         Fault("comm_outage", "pv01", 5 * H, 5.25 * H),
                         Fault("comm_delay", "pv01", 7 * H, 7.5 * H, 0, {"delay_s": 45.0}),
                         Fault("comm_duplicate", "pv01", 9 * H, 10 * H, 0, {"prob": 0.3})])


def _sc_mixed():
    return _base("mixed_day", "Cloudy day with soiling, a shading episode, a sensor failure and a comm outage; "
                 "clean reference module present.",
                 weather=dict(cloud_cover=0.35),
                 nodes=[NodeConfig("pv01"), NodeConfig("pv_ref", role="reference")],
                 faults=[Fault("soiling", "pv01", 0, None, 14 * H, {"loss_max": 0.10}),
                         Fault("partial_shading", "pv01", 6 * H, 8 * H, 900, {"substrings": [2], "remaining": 0.25}),
                         Fault("sensor_offset", "pv01", 9 * H, 11 * H, 0, {"channel": "v_dc", "offset": -1.5}),
                         Fault("comm_outage", "*", 12 * H, 12.2 * H),
                         Fault("hotspot", "pv01", 11 * H, 13 * H, 900, {"substring": 1, "extra_heat_wm2": 400})])


BUILTIN = {f().name: f for f in (_sc_normal, _sc_clouds, _sc_shading, _sc_soiling, _sc_degradation,
                                 _sc_hotspot, _sc_sensors, _sc_comm, _sc_mixed)}


def get_scenario(name_or_path: str) -> Scenario:
    if name_or_path in BUILTIN:
        return BUILTIN[name_or_path]()
    return Scenario.load(name_or_path)
