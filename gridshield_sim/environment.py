"""Environment: solar geometry, clear-sky irradiance, cloud transients, ambient T, wind."""
from __future__ import annotations
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
import numpy as np


@dataclass
class Location:
    lat: float = 33.57
    lon: float = -7.59
    tilt_deg: float | None = None
    azimuth_deg: float | None = None     # 180 = facing south
    albedo: float = 0.2

    def __post_init__(self):
        if self.tilt_deg is None:
            self.tilt_deg = min(abs(self.lat), 60.0)
        if self.azimuth_deg is None:
            self.azimuth_deg = 180.0 if self.lat >= 0 else 0.0


def solar_position(when: datetime, lat: float, lon: float):
    """Spencer (1971) declination/equation of time. Returns (cos zenith, azimuth rad)."""
    n = when.timetuple().tm_yday
    b = 2 * math.pi * (n - 1) / 365.0
    eot = 229.18 * (0.000075 + 0.001868 * math.cos(b) - 0.032077 * math.sin(b)
                    - 0.014615 * math.cos(2 * b) - 0.04089 * math.sin(2 * b))
    decl = (0.006918 - 0.399912 * math.cos(b) + 0.070257 * math.sin(b)
            - 0.006758 * math.cos(2 * b) + 0.000907 * math.sin(2 * b)
            - 0.002697 * math.cos(3 * b) + 0.00148 * math.sin(3 * b))
    utc_h = when.hour + when.minute / 60 + when.second / 3600 + when.microsecond / 3.6e9
    solar_h = (utc_h + lon / 15.0 + eot / 60.0) % 24.0
    omega = math.radians(15.0 * (solar_h - 12.0))
    phi = math.radians(lat)
    cosz = max(-1.0, min(1.0, math.sin(phi) * math.sin(decl)
                         + math.cos(phi) * math.cos(decl) * math.cos(omega)))
    sinz = math.sqrt(1 - cosz ** 2)
    if sinz < 1e-6:
        return cosz, math.pi
    cosaz = max(-1.0, min(1.0, (math.sin(decl) - cosz * math.sin(phi)) / (sinz * math.cos(phi))))
    az = math.acos(cosaz)
    return cosz, (2 * math.pi - az if omega > 0 else az)


def clear_sky_poa(cosz, az, kt, loc: Location):
    """Haurwitz clear-sky GHI scaled by clearness index kt, transposed to the module plane."""
    if cosz <= 0.0:
        return 0.0, 0.0
    ghi = 1098.0 * cosz * math.exp(-0.059 / cosz) * kt
    df = min(1.0, 0.12 + 0.88 * (1.0 - kt) ** 1.5)       # diffuse fraction grows with clouds
    dhi, bh = ghi * df, ghi * (1 - df)
    beta, gam = math.radians(loc.tilt_deg), math.radians(loc.azimuth_deg)
    sinz = math.sqrt(1 - cosz ** 2)
    cos_inc = max(0.0, cosz * math.cos(beta) + sinz * math.sin(beta) * math.cos(az - gam))
    beam = bh * cos_inc / max(cosz, 0.087)
    diffuse = dhi * (1 + math.cos(beta)) / 2
    ground = loc.albedo * ghi * (1 - math.cos(beta)) / 2
    return beam + diffuse + ground, ghi


@dataclass
class EnvSample:
    g_poa: float
    ghi: float
    t_amb: float
    wind: float
    kt: float
    cosz: float
    when: datetime


class Environment:
    def __init__(self, start_utc: datetime, location: Location, rng, cloud_cover=0.0,
                 base_kt=1.0, t_mean_c=22.0, t_amp_c=6.0, wind_mean=2.5,
                 cloud_rate_max_per_h=24.0):
        self.start, self.loc, self.rng = start_utc, location, rng
        self.cloud_cover, self.base_kt = cloud_cover, base_kt
        self.t_mean, self.t_amp, self.wind_mean = t_mean_c, t_amp_c, wind_mean
        self.rate_max = cloud_rate_max_per_h
        self.events: list[dict] = []
        self.ou_t = self.ou_w = self.ou_kt = 0.0

    @staticmethod
    def _ou(x, dt, tau, sigma, rng):
        phi = math.exp(-dt / tau)
        return x * phi + sigma * math.sqrt(1 - phi ** 2) * rng.standard_normal()

    def _cloud_depth(self, t):
        keep, trans = [], 1.0
        for e in self.events:
            end = e["t0"] + e["rise"] + e["hold"] + e["fall"]
            if t > end:
                continue
            keep.append(e)
            x = t - e["t0"]
            if x < 0:
                continue
            if x < e["rise"]:
                d = e["depth"] * x / e["rise"]
            elif x < e["rise"] + e["hold"]:
                d = e["depth"]
            else:
                d = e["depth"] * (1 - (x - e["rise"] - e["hold"]) / e["fall"])
            trans *= 1 - d
        self.events = keep
        return 1 - trans

    def step(self, t, dt) -> EnvSample:
        when = self.start + timedelta(seconds=t)
        rng = self.rng
        # new cloud events (Poisson arrivals)
        lam = self.cloud_cover * self.rate_max * dt / 3600.0
        for _ in range(rng.poisson(lam)):
            self.events.append(dict(t0=t, rise=rng.uniform(4, 20),
                                    hold=float(rng.lognormal(math.log(90), 0.8)),
                                    fall=rng.uniform(4, 20), depth=rng.uniform(0.3, 0.85)))
        self.ou_kt = self._ou(self.ou_kt, dt, 120.0, 0.012, rng)
        self.ou_t = self._ou(self.ou_t, dt, 900.0, 0.4, rng)
        self.ou_w = self._ou(self.ou_w, dt, 300.0, 1.0, rng)
        kt = float(np.clip(self.base_kt * (1 - self._cloud_depth(t)) + self.ou_kt, 0.03, 1.0))
        cosz, az = solar_position(when, self.loc.lat, self.loc.lon)
        g_poa, ghi = clear_sky_poa(cosz, az, kt, self.loc)
        hour = (when.hour + when.minute / 60 + self.loc.lon / 15.0) % 24
        t_amb = self.t_mean + self.t_amp * math.cos(2 * math.pi * (hour - 15.0) / 24.0) + self.ou_t
        wind = max(0.0, self.wind_mean + self.ou_w)
        return EnvSample(g_poa, ghi, t_amb, wind, kt, cosz, when)
