"""Single-diode PV model with bypass-diode substrings and a P&O MPPT tracker.

Module = S series-connected substrings, each protected by a bypass diode.
Every substring has its own irradiance and cell temperature, so partial
shading produces the characteristic multi-peak P-V curve.
"""
from __future__ import annotations
from dataclasses import dataclass, field, replace
import numpy as np

from .params import ModuleParams, K_B, Q_E, G_REF, T_REF


# --------------------------------------------------------------------------
# Diode equation
# --------------------------------------------------------------------------
def diode_params(p: ModuleParams, g, t_c, iph_scale=1.0, rs_scale=1.0, rsh_scale=1.0):
    """Module-level single-diode parameters (iph, i0, rs, rsh, a) at (g, t_c)."""
    tk = t_c + 273.15
    g = max(float(g), 1e-3)
    a = p.n_cells * p.n_ideality * K_B * tk / Q_E
    iph = iph_scale * (g / G_REF) * (p.iph_ref + p.alpha_isc * p.isc_ref * (tk - T_REF))
    i0 = p.i0_ref * (tk / T_REF) ** 3 * np.exp(
        Q_E * p.eg_ev / (p.n_ideality * K_B) * (1.0 / T_REF - 1.0 / tk))
    rs = p.rs_ref * rs_scale
    rsh = min(p.rsh_ref * (G_REF / g), 1e5) * rsh_scale
    return iph, i0, rs, rsh, a


def v_of_i(i, iph, i0, rs, rsh, a, iters=30):
    """Terminal voltage for a given current array (Newton on the implicit equation)."""
    i = np.asarray(i, dtype=float)
    v = a * np.log(np.maximum(iph - i, 1e-12) / i0 + 1.0) - i * rs
    for _ in range(iters):
        x = v + i * rs
        e = np.exp(np.minimum(x / a, 60.0))
        f = iph - i0 * (e - 1.0) - x / rsh - i
        df = -i0 * e / a - 1.0 / rsh
        v = v - f / df
    return v


# --------------------------------------------------------------------------
# Calibration
# --------------------------------------------------------------------------
def _fit_iph_i0(p: ModuleParams, rs: float):
    a = p.n_cells * p.n_ideality * K_B * T_REF / Q_E
    r = (np.exp(p.isc_ref * rs / a) - 1.0) / (np.exp(p.voc_ref / a) - 1.0)
    iph = (p.isc_ref * (1 + rs / p.rsh_ref) - p.voc_ref * r / p.rsh_ref) / (1 - r)
    i0 = (iph - p.voc_ref / p.rsh_ref) / (np.exp(p.voc_ref / a) - 1.0)
    return iph, i0, a


def calibrate(p: ModuleParams) -> ModuleParams:
    """Fit Rs, Iph, I0 so that Isc, Voc and Pmp match the datasheet at STC."""
    target = p.vmp_ref * p.imp_ref

    def pmp(rs):
        iph, i0, a = _fit_iph_i0(p, rs)
        i = np.linspace(0, iph, 600)
        v = v_of_i(i, iph, i0, rs, p.rsh_ref, a)
        return float(np.max(np.where(v > 0, v * i, 0.0)))

    lo, hi = 1e-4, 2.0
    for _ in range(50):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if pmp(mid) > target else (lo, mid)
    rs = 0.5 * (lo + hi)
    iph, i0, _ = _fit_iph_i0(p, rs)
    return replace(p, rs_ref=rs, iph_ref=float(iph), i0_ref=float(i0))


# --------------------------------------------------------------------------
# Fault modifiers (filled by faults.physical_modifiers)
# --------------------------------------------------------------------------
@dataclass
class Modifiers:
    g_factor: np.ndarray
    extra_heat: np.ndarray
    rsh_scale: np.ndarray
    shorted: np.ndarray
    iph_scale: float = 1.0
    rs_scale: float = 1.0
    open_circuit: bool = False

    @staticmethod
    def neutral(s: int) -> "Modifiers":
        return Modifiers(np.ones(s), np.zeros(s), np.ones(s), np.zeros(s, bool))


# --------------------------------------------------------------------------
# I-V curve of the whole module
# --------------------------------------------------------------------------
class IVCurve:
    def __init__(self, i, v_sub, v_tot):
        self.i = i                      # ascending current grid
        self.v_sub = v_sub              # (S, N)
        self.v = v_tot                  # descending total voltage
        self._vr = v_tot[::-1]
        self._ir = i[::-1]
        self.voc = float(v_tot[0])
        self._mpp = None

    def i_at_v(self, v):
        return float(np.interp(v, self._vr, self._ir))

    def v_sub_at_i(self, i):
        return np.array([np.interp(i, self.i, row) for row in self.v_sub])

    @property
    def isc(self):
        return float(np.interp(0.0, self._vr, self._ir))

    def pv_curve(self, n=1200):
        vf = np.linspace(0.0, self.voc, n)
        i_f = np.interp(vf, self._vr, self._ir)
        return vf, i_f, vf * i_f

    def mpp(self):
        """Global maximum power point (v, i, p)."""
        if self._mpp is None:
            vf, i_f, pf = self.pv_curve()
            k = int(np.argmax(pf))
            self._mpp = (float(vf[k]), float(i_f[k]), float(pf[k]))
        return self._mpp

    def n_peaks(self, rel=0.02):
        _, _, p = self.pv_curve(400)
        thr = rel * p.max()
        peaks, direction, ext = 0, 0, p[0]
        for x in p[1:]:
            if direction >= 0:
                if x > ext:
                    ext = x
                elif ext - x > thr:
                    peaks, direction, ext = peaks + 1, -1, x
            else:
                if x < ext:
                    ext = x
                elif x - ext > thr:
                    direction, ext = 1, x
        return peaks + (1 if direction >= 0 and ext > thr else 0)


def module_curve(p: ModuleParams, g_sub, t_sub, mods: Modifiers | None = None,
                 n_pts: int = 200) -> IVCurve:
    s = p.n_substrings
    mods = mods or Modifiers.neutral(s)
    pars = [diode_params(p, g_sub[k], t_sub[k], mods.iph_scale, mods.rs_scale,
                         mods.rsh_scale[k]) for k in range(s)]
    imax = max(par[0] for par in pars)
    u = np.linspace(0.0, 1.0, n_pts)
    i = imax * (1.0 - (1.0 - u) ** 2)          # denser near the knee
    rows, cache = [], {}
    for k, par in enumerate(pars):
        if mods.shorted[k]:
            rows.append(np.zeros_like(i))
            continue
        if par not in cache:
            iph, i0, rs, rsh, a = par
            v = v_of_i(i, iph, i0, rs / s, rsh / s, a / s)
            cache[par] = np.maximum(v, -p.bypass_vf)
        rows.append(cache[par])
    v_sub = np.vstack(rows)
    return IVCurve(i, v_sub, v_sub.sum(axis=0))


# --------------------------------------------------------------------------
# MPPT (perturb & observe + periodic global scan)
# --------------------------------------------------------------------------
class MPPT:
    def __init__(self, step_v=0.25, period_s=0.25, scan_period_s=900.0,
                 max_iter=80, start_frac=0.8, on_threshold=30.0):
        self.step_v, self.period_s = step_v, period_s
        self.scan_period_s, self.max_iter = scan_period_s, max_iter
        self.start_frac, self.on_threshold = start_frac, on_threshold
        self.reset()

    def reset(self):
        self.v, self.dir, self.last_p = None, -1, 0.0
        self.last_scan = None

    def update(self, curve: IVCurve, dt: float, t: float):
        mode = "track"
        if self.v is None:
            self.v, self.dir, self.last_p, self.last_scan = self.start_frac * curve.voc, -1, 0.0, t
        elif self.scan_period_s > 0 and t - self.last_scan >= self.scan_period_s:
            self.v, self.last_scan, mode = curve.mpp()[0], t, "scan"
        n = int(min(max(round(dt / self.period_s), 1), self.max_iter))
        for _ in range(n):
            pw = self.v * curve.i_at_v(self.v)
            if pw < self.last_p:
                self.dir = -self.dir
            self.last_p = pw
            self.v = float(np.clip(self.v + self.dir * self.step_v, 1.0, curve.voc * 0.999))
        return self.v, max(curve.i_at_v(self.v), 0.0), mode
