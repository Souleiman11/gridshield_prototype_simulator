import csv, json, os
import numpy as np
from gridshield_sim.engine import Engine
from gridshield_sim.faults import Fault
from gridshield_sim.scenario import get_scenario, Scenario, NodeConfig
from gridshield_sim.sinks import Sink, DatasetWriter, MEAS_COLS


class Collect(Sink):
    def __init__(self): self.truth, self.msgs = [], []
    def on_truth(self, r): self.truth.append({k: v for k, v in r.items() if not k.startswith("_")})
    def on_message(self, m): self.msgs.append(m)


def run(sc):
    c = Collect(); Engine(sc, [c]).run(); return c


def noon(**kw):
    sc = get_scenario("normal_clear_day")
    sc.start_utc, sc.duration_s, sc.step_s = "2026-06-21T11:00:00", 1800, 2.0
    for k, v in kw.items(): setattr(sc, k, v)
    return sc


def test_clear_day_is_physically_plausible():
    sc = get_scenario("normal_clear_day"); sc.step_s = 60
    c = run(sc)
    g = max(r["g_poa_true"] for r in c.truth); tc = max(r["t_cell_s1"] for r in c.truth)
    p = max(r["p_true"] for r in c.truth)
    assert 850 < g < 1100 and 35 < tc < 70 and 200 < p < 290


def test_ground_truth_never_leaks_into_published_payload():
    sc = noon(faults=[Fault("partial_shading", "pv01", 300, None, 0, {})])
    c = run(sc)
    banned = ("fault", "label", "truth", "severity", "shading", "true", "mpp")
    for m in c.msgs:
        for key, val in m["payload"].items():
            assert not any(b in key.lower() for b in banned), key
            assert not (isinstance(val, str) and any(b in val.lower() for b in banned)), val
    assert any(r["fault_labels"] == "partial_shading" for r in c.truth)   # truth does have it


def test_dataset_files_are_separate(tmp_path):
    sc = noon(duration_s=120)
    Engine(sc, [DatasetWriter(str(tmp_path))]).run()
    head = open(tmp_path / "measurements.csv").readline().strip().split(",")
    assert head == MEAS_COLS
    assert "fault_labels" in open(tmp_path / "ground_truth.csv").readline()


def test_reproducible_with_same_seed():
    a, b = run(noon(duration_s=300)), run(noon(duration_s=300))
    assert [m["payload"] for m in a.msgs] == [m["payload"] for m in b.msgs]
    c = run(noon(duration_s=300, seed=7))
    assert [m["payload"] for m in a.msgs] != [m["payload"] for m in c.msgs]


def test_soiling_reduces_power_relative_to_clean_reference():
    sc = noon(nodes=[NodeConfig("pv01"), NodeConfig("pv_ref", role="reference")],
              faults=[Fault("soiling", "pv01", 0, None, 0, {"loss_max": 0.15})])
    c = run(sc)
    a = np.mean([r["p_true"] for r in c.truth if r["node_id"] == "pv01"][100:])
    b = np.mean([r["p_true"] for r in c.truth if r["node_id"] == "pv_ref"][100:])
    assert 0.80 < a / b < 0.90


def test_open_circuit_shows_zero_current_and_voc():
    c = run(noon(faults=[Fault("open_circuit", "pv01", 0)]))
    late = [m["payload"] for m in c.msgs][-20:]
    assert all(p["i_dc"] < 0.05 and p["v_dc"] > 33 for p in late)


def test_sensor_freeze_and_disconnect():
    sc = noon(faults=[Fault("sensor_freeze", "pv01", 300, 900, 0, {"channel": "t_module"}),
                      Fault("sensor_disconnect", "pv01", 1000, 1200, 0, {"channel": "g_poa"})])
    c = run(sc)
    fr = [m["payload"]["t_module_c"] for m in c.msgs if 320 <= m["sent_t_s"] < 900]
    assert len(set(fr)) == 1
    dis = [m["payload"]["g_poa_wm2"] for m in c.msgs if 1010 <= m["sent_t_s"] < 1200]
    assert all(x is None for x in dis)


def test_comm_faults_change_delivery_but_not_sequence_numbers():
    sc = noon(faults=[Fault("comm_outage", "pv01", 300, 600)])
    c = run(sc)
    seqs = [m["payload"]["seq"] for m in c.msgs]
    assert len(seqs) < len(c.truth)
    assert max(seqs) - min(seqs) + 1 > len(set(seqs))         # visible gap in seq
    assert not any(300 <= m["sent_t_s"] < 600 for m in c.msgs)


def test_runtime_fault_injection():
    sc = noon(duration_s=200)
    eng = Engine(sc, [Collect()])
    eng.submit({"cmd": "inject", "fault": {"type": "open_circuit", "node": "pv01", "start_s": 100}})
    eng.run()
    assert any(f.type == "open_circuit" for f in eng.faults)


def test_scenario_json_roundtrip(tmp_path):
    sc = get_scenario("mixed_day"); p = tmp_path / "s.json"; sc.save(str(p))
    back = Scenario.load(str(p))
    assert [f.type for f in back.faults] == [f.type for f in sc.faults] and len(back.nodes) == 2
