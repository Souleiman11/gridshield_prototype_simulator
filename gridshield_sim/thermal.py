"""Lumped thermal model: one node per substring, exact exponential integration."""
from __future__ import annotations
import numpy as np
from .params import ModuleParams


def update_cell_temps(p: ModuleParams, t_cells, g_sub, p_sub_w, extra_heat, t_amb, wind, dt):
    """C dT/dt = alpha*G - P_el/A + Q_fault - U (T - Ta),  U = u0 + u1*wind."""
    u = p.u0 + p.u1 * wind
    tau = p.heat_capacity / u
    area_sub = p.area_m2 / p.n_substrings
    q = p.absorptivity * g_sub - np.maximum(p_sub_w, 0.0) / area_sub + extra_heat
    t_ss = t_amb + q / u
    return t_ss + (t_cells - t_ss) * np.exp(-dt / tau)
