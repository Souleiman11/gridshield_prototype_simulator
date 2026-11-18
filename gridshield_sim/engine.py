"""Simulation engine: environment + ESP32-node emulators + fault injection + delivery queue."""
from __future__ import annotations
import heapq, itertools, json, queue, time
import numpy as np

from .comms import CommChannel
from .environment import Environment, Location
from .faults import Fault, physical_modifiers, CHANNELS
from .params import get_module
from .pv_model import module_curve, MPPT
from .rng import Rng
from .scenario import Scenario, NodeConfig
from .sensors import Channel
from .thermal import update_cell_temps


class NodeSim:
    """One PV module + one emulated ESP32 acquisition node."""

    def __init__(self, cfg: NodeConfig, rng):
        self.cfg = cfg
        self.p = get_module(cfg.module)
        s_rng, c_rng = rng.spawn(2)
        self.channels = {n: Channel(n, s_rng) for n in CHANNELS}
        self.comm = CommChannel(c_rng)
        self.rssi_rng = s_rng
        self.mppt = MPPT(scan_period_s=cfg.mppt_scan_period_s)
        self.t_cell = None
        self.seq = 0

    def step(self, t, dt, env, active, site):
        p, S = self.p, self.p.n_substrings
        mods = physical_modifiers([(f, s) for f, s in active if f.category == "physical"], S)
        g_sub = env.g_poa * mods.g_factor
        if self.t_cell is None:
            self.t_cell = np.full(S, env.t_amb)
        t_used = self.t_cell.copy()

        curve, v, i, mode = None, 0.0, 0.0, "off"
        if g_sub.max() > 2.0:
            curve = module_curve(p, g_sub, t_used, mods)
            if mods.open_circuit:
                v, mode = curve.voc, "open"; self.mppt.reset()
            elif g_sub.max() >= self.mppt.on_threshold:
                v, i, mode = self.mppt.update(curve, dt, t)
            else:
                v, mode = curve.voc, "idle"; self.mppt.reset()
        else:
            self.mppt.reset()
        p_sub = curve.v_sub_at_i(i) * i if (curve is not None and i > 0) else np.zeros(S)
        self.t_cell = update_cell_temps(p, self.t_cell, g_sub, p_sub, mods.extra_heat,
                                        env.t_amb, env.wind, dt)

        # ---- sensors (what the ESP32 actually measures) -------------------
        t_back = t_used[self.cfg.sensor_substring] - 3.0 * env.g_poa / 1000.0
        truth_in = {"v_dc": v, "i_dc": i, "t_module": t_back, "t_ambient": env.t_amb, "g_poa": env.g_poa}
        sens_faults = [(f, s) for f, s in active if f.category == "sensor"]
        meas = {}
        for name, ch in self.channels.items():
            fl = [(f, s) for f, s in sens_faults if f.params.get("channel", "*") in (name, "*")]
            meas[name] = ch.read(truth_in[name], t, dt, fl)
        pw = None if meas["v_dc"] is None or meas["i_dc"] is None else round(meas["v_dc"] * meas["i_dc"], 2)

        self.seq += 1
        ts_ms = int(env.when.timestamp() * 1000)
        r = lambda x, n: None if x is None else round(float(x), n)
        payload = {"node_id": self.cfg.node_id, "seq": self.seq, "ts_ms": ts_ms,
                   "v_dc": r(meas["v_dc"], 3), "i_dc": r(meas["i_dc"], 3), "p_dc": pw,
                   "t_module_c": r(meas["t_module"], 3), "t_ambient_c": r(meas["t_ambient"], 3),
                   "g_poa_wm2": r(meas["g_poa"], 1),
                   "rssi_dbm": int(-62 + self.rssi_rng.normal(0, 2)), "fw": "sim-1.0"}
        msg = {"topic": f"gridshield/{site}/{self.cfg.node_id}/telemetry",
               "node_id": self.cfg.node_id, "sent_t_s": t, "payload": payload}
        comm_faults = [(f, s) for f, s in active if f.category == "comm"]
        deliveries = self.comm.transmit(msg, t, comm_faults)

        # ---- ground truth (never sent to GridShield) ----------------------
        vm, im, pm = curve.mpp() if curve is not None else (0.0, 0.0, 0.0)
        labels = sorted({f.type for f, _ in active})
        truth = {
            "t_s": t, "time_utc": env.when.isoformat(), "node_id": self.cfg.node_id,
            "seq": self.seq, "ts_ms": ts_ms,
            "g_poa_true": round(env.g_poa, 2), "kt": round(env.kt, 4),
            "g_sub_min": round(float(g_sub.min()), 2), "t_amb_true": round(env.t_amb, 3),
            "wind_ms": round(env.wind, 3),
            **{f"t_cell_s{k}": round(float(t_used[k]), 3) for k in range(S)},
            "v_true": round(v, 4), "i_true": round(i, 4), "p_true": round(v * i, 3),
            "v_mpp_true": round(vm, 3), "i_mpp_true": round(im, 4), "p_mpp_true": round(pm, 3),
            "v_oc_true": round(curve.voc, 3) if curve else 0.0,
            "i_sc_true": round(curve.isc, 4) if curve else 0.0,
            "n_peaks": curve.n_peaks() if curve is not None and pm > 20 else 0,
            "mppt_mode": mode,
            "fault_labels": ";".join(labels) if labels else "none",
            "fault_severity": json.dumps({f.id: round(s, 3) for f, s in active}),
            "delivered": int(len(deliveries) > 0),
            "_iv": None if curve is None else (curve.v[::4], curve.i[::4]),
        }
        return truth, deliveries


class Engine:
    def __init__(self, scenario: Scenario, sinks=None):
        self.sc, self.sinks = scenario, list(sinks or [])
        self.dt = scenario.step_s
        self.n_steps = int(scenario.duration_s / self.dt) + 1
        env_rng, *node_rngs = Rng(scenario.seed).spawn(1 + len(scenario.nodes))
        self.env = Environment(scenario.start, Location(**scenario.location),
                               env_rng, **scenario.weather)
        self.nodes = {n.node_id: NodeSim(n, ss) for n, ss in zip(scenario.nodes, node_rngs)}
        self.faults: list[Fault] = []
        for f in scenario.faults:
            self.add_fault(f)
        self._heap, self._ctr = [], itertools.count()
        self.commands = queue.SimpleQueue()
        self.speed, self.paused, self.t = 0.0, False, 0.0

    # ---- fault management (also used by the MQTT control channel) ----------
    def add_fault(self, f: Fault) -> Fault:
        if not f.id:
            f.id = f"F{len(self.faults) + 1:02d}"
        self.faults.append(f)
        return f

    def inject_fault(self, **kw) -> Fault:
        kw.setdefault("start_s", self.t)
        return self.add_fault(Fault(**kw))

    def clear_fault(self, fault_id: str):
        for f in self.faults:
            if f.id == fault_id and (f.end_s is None or f.end_s > self.t):
                f.end_s = self.t

    def submit(self, cmd: dict):
        self.commands.put(cmd)

    def _process_commands(self):
        while not self.commands.empty():
            c = self.commands.get()
            try:
                k = c.get("cmd")
                if k == "inject":
                    self.inject_fault(**c["fault"])
                elif k == "clear":
                    self.clear_fault(c["id"])
                elif k == "clear_all":
                    for f in list(self.faults):
                        self.clear_fault(f.id)
                elif k == "set_speed":
                    self.speed = float(c["speed"])
                elif k in ("pause", "resume"):
                    self.paused = k == "pause"
            except Exception as exc:                       # bad command must not kill the run
                print(f"[engine] ignored command {c!r}: {exc}")

    def _active(self, node_id, t):
        out = []
        for f in self.faults:
            if f.node in (node_id, "*"):
                s = f.severity(t)
                if s > 0:
                    out.append((f, s))
        return out

    # ---- main loop ----------------------------------------------------------
    def step(self, t):
        self.t = t
        env = self.env.step(t, self.dt)
        for node in self.nodes.values():
            truth, deliveries = node.step(t, self.dt, env, self._active(node.cfg.node_id, t), self.sc.site)
            for rx_t, m in deliveries:
                m = dict(m, rx_t_s=rx_t, rx_ts_ms=int((self.sc.start.timestamp() + rx_t) * 1000))
                heapq.heappush(self._heap, (rx_t, next(self._ctr), m))
            for s in self.sinks:
                s.on_truth(truth)
        self._drain(t)

    def _drain(self, t):
        while self._heap and self._heap[0][0] <= t:
            _, _, m = heapq.heappop(self._heap)
            for s in self.sinks:
                s.on_message(m)

    def run(self, speed: float = 0.0):
        """speed: 0 = as fast as possible, 1 = real time, 60 = 60x faster."""
        self.speed = speed
        for s in self.sinks:
            s.open(self)
        try:
            for k in range(self.n_steps):
                t0 = time.monotonic()
                self._process_commands()
                while self.paused:
                    time.sleep(0.1)
                    self._process_commands()
                self.step(k * self.dt)
                if self.speed > 0:
                    time.sleep(max(0.0, self.dt / self.speed - (time.monotonic() - t0)))
            self._drain(float("inf"))
        finally:
            for s in self.sinks:
                s.close()
