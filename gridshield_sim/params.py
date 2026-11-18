"""Module parameters (datasheet values + fitted single-diode parameters)."""
from __future__ import annotations
from dataclasses import dataclass

K_B = 1.380649e-23      # Boltzmann constant, J/K
Q_E = 1.602176634e-19   # elementary charge, C
G_REF = 1000.0          # STC irradiance, W/m2
T_REF = 298.15          # STC cell temperature, K


@dataclass(frozen=True)
class ModuleParams:
    name: str = "generic-270"
    n_cells: int = 60
    n_substrings: int = 3          # one bypass diode per substring
    area_m2: float = 1.65
    # datasheet at STC
    isc_ref: float = 9.20
    voc_ref: float = 38.2
    vmp_ref: float = 31.0
    imp_ref: float = 8.71
    alpha_isc: float = 0.0005      # 1/K, relative Isc temperature coefficient
    # single-diode assumptions
    n_ideality: float = 1.2
    rsh_ref: float = 400.0         # ohm, whole module at STC
    eg_ev: float = 1.121           # silicon band gap
    bypass_vf: float = 0.5         # bypass diode forward drop, V
    # thermal (Faiman-type: U = u0 + u1 * wind)
    absorptivity: float = 0.90
    u0: float = 22.0               # W/m2K
    u1: float = 6.0                # W/m2K per m/s
    heat_capacity: float = 11000.0 # J/m2K
    # fitted by pv_model.calibrate()
    rs_ref: float = 0.0
    iph_ref: float = 0.0
    i0_ref: float = 0.0

    @property
    def p_mp_ref(self) -> float:
        return self.vmp_ref * self.imp_ref


_CATALOG = {
    "generic-270": dict(),
    "generic-330-72c": dict(name="generic-330-72c", n_cells=72, area_m2=1.96,
                            isc_ref=9.00, voc_ref=46.0, vmp_ref=37.4, imp_ref=8.83),
}
_CACHE: dict = {}


def get_module(name: str = "generic-270") -> ModuleParams:
    """Return a calibrated module from the catalogue."""
    from .pv_model import calibrate
    if name not in _CATALOG:
        raise KeyError(f"unknown module '{name}', available: {list(_CATALOG)}")
    if name not in _CACHE:
        _CACHE[name] = calibrate(ModuleParams(**_CATALOG[name]))
    return _CACHE[name]
