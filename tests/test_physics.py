import numpy as np
from gridshield_sim.params import get_module
from gridshield_sim.pv_model import module_curve, Modifiers

P = get_module()


def curve(g=1000.0, t=25.0, g0=None):
    gs = [g] * 3
    if g0 is not None:
        gs[0] = g0
    return module_curve(P, gs, [t] * 3)


def test_stc_matches_datasheet():
    c = curve()
    assert abs(c.voc - P.voc_ref) < 0.05
    assert abs(c.isc - P.isc_ref) < 0.02
    assert abs(c.mpp()[2] - P.p_mp_ref) < 1.5


def test_temperature_coefficients():
    c25, c45 = curve(t=25), curve(t=45)
    beta = (c45.voc - c25.voc) / 20 / c25.voc
    gamma = (c45.mpp()[2] - c25.mpp()[2]) / 20 / c25.mpp()[2]
    assert -0.0045 < beta < -0.0025
    assert -0.0050 < gamma < -0.0030
    assert c45.isc > c25.isc


def test_irradiance_scaling():
    assert abs(curve(g=500).isc / curve().isc - 0.5) < 0.01
    ratio = curve(g=200).mpp()[2] / curve().mpp()[2]
    assert 0.17 < ratio < 0.21


def test_shading_creates_multi_peak_curve():
    assert curve().n_peaks() == 1
    shaded = curve(g0=300)
    assert shaded.n_peaks() == 2
    assert shaded.mpp()[2] < 0.75 * curve().mpp()[2]


def test_short_circuited_bypass_diode_loses_one_third_voltage():
    m = Modifiers.neutral(3); m.shorted[0] = True
    c = module_curve(P, [1000] * 3, [25] * 3, m)
    assert abs(c.voc / P.voc_ref - 2 / 3) < 0.01
