"""Real-time dashboard (matplotlib) and static dataset plots."""
from __future__ import annotations
from collections import deque
import csv
import numpy as np
from .sinks import Sink


class LiveDashboard(Sink):
    """Shows what GridShield receives (solid) next to simulator truth (dashed) for one node."""

    def __init__(self, node_id="pv01", window_s=1800, refresh_every=5, backend=None):
        import matplotlib
        if backend:
            matplotlib.use(backend)
        import matplotlib.pyplot as plt
        self.plt, self.node, self.window, self.refresh_every = plt, node_id, window_s, refresh_every
        self.t, self.truth, self.meas = deque(), deque(), deque()
        self.iv, self.labels, self.n = None, "none", 0

    def open(self, engine):
        super().open(engine)
        plt = self.plt
        if plt.get_backend().lower() not in ("agg", "pdf", "svg"):
            plt.ion()
        self.fig, ax = plt.subplots(2, 3, figsize=(14, 7))
        self.ax = ax.ravel()
        self.fig.suptitle(f"GridShield PV simulator - {engine.sc.name} - node {self.node}")

    def on_truth(self, r):
        if r["node_id"] != self.node:
            return
        self.t.append(r["t_s"] / 3600.0)
        self.truth.append(r)
        self.iv, self.labels = r["_iv"], r["fault_labels"]
        while self.t and (self.t[-1] - self.t[0]) * 3600 > self.window:
            self.t.popleft(); self.truth.popleft()
            if self.meas: self.meas.popleft()
        self.n += 1
        if self.n % self.refresh_every == 0:
            self.draw()

    def on_message(self, m):
        pl = m["payload"]
        if pl["node_id"] == self.node:
            self.meas.append((m["sent_t_s"] / 3600.0, pl))

    def draw(self):
        a, T = self.ax, list(self.t)
        tr = lambda k: [x[k] for x in self.truth]
        mt = [x[0] for x in self.meas]
        ms = lambda k: [np.nan if x[1][k] is None else x[1][k] for x in self.meas]
        for x in a: x.clear()
        a[0].plot(mt, ms("p_dc"), label="measured"); a[0].plot(T, tr("p_true"), "--", label="true")
        a[0].plot(T, tr("p_mpp_true"), ":", label="true MPP"); a[0].set_title("DC power (W)"); a[0].legend(loc="upper left")
        a[1].plot(mt, ms("v_dc")); a[1].plot(T, tr("v_true"), "--"); a[1].set_title("Voltage (V)")
        a[2].plot(mt, ms("i_dc")); a[2].plot(T, tr("i_true"), "--"); a[2].set_title("Current (A)")
        a[3].plot(mt, ms("t_module_c"), label="module (meas)"); a[3].plot(mt, ms("t_ambient_c"), label="ambient (meas)")
        for k in range(3): a[3].plot(T, tr(f"t_cell_s{k}"), "--", lw=0.8, label=f"cell s{k} (true)")
        a[3].set_title("Temperature (C)"); a[3].legend(fontsize=7, loc="upper left")
        a[4].plot(mt, ms("g_poa_wm2")); a[4].plot(T, tr("g_poa_true"), "--"); a[4].set_title("Irradiance (W/m2)")
        if self.iv is not None:
            v, i = self.iv; a[5].plot(v, i, label="I-V"); a[5].plot(v, v * i / 10, label="P/10")
            r = self.truth[-1]; a[5].plot(r["v_true"], r["i_true"], "ro"); a[5].set_xlim(0, max(v.max(), 1))
            a[5].set_ylim(0, None); a[5].legend(fontsize=7)
        a[5].set_title(f"I-V curve  |  truth: {self.labels}", fontsize=9)
        for x in a[:5]: x.set_xlabel("sim time (h)"); x.grid(alpha=0.3)
        self.fig.tight_layout(rect=(0, 0, 1, 0.96))
        self.plt.pause(0.001)

    def close(self):
        self.draw()
        if self.plt.get_backend().lower() not in ("agg", "pdf", "svg"):
            self.plt.ioff(); self.plt.show()


def plot_dataset(dataset_dir, node_id="pv01", out_png="overview.png"):
    """Static overview: measurements vs ground truth with fault intervals shaded."""
    import os, matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    def load(name):
        with open(os.path.join(dataset_dir, name)) as fh:
            return list(csv.DictReader(fh))
    meas = [r for r in load("measurements.csv") if r["node_id"] == node_id]
    truth = [r for r in load("ground_truth.csv") if r["node_id"] == node_id]
    events = load("fault_events.csv")
    t0 = float(truth[0]["ts_ms"]) / 1000.0
    f = lambda v: np.nan if v in ("", None) else float(v)
    tm = np.array([float(r["ts_ms"]) / 1000.0 - t0 for r in meas]) / 3600
    tt = np.array([float(r["t_s"]) for r in truth]) / 3600
    fig, ax = plt.subplots(4, 1, figsize=(12, 10), sharex=True)
    for a, (mk, tk, title) in zip(ax, [("p_dc", "p_true", "DC power (W)"), ("v_dc", "v_true", "Voltage (V)"),
                                       ("t_module_c", "t_cell_s1", "Temperature (C)"), ("g_poa_wm2", "g_poa_true", "Irradiance (W/m2)")]):
        a.plot(tm, [f(r[mk]) for r in meas], lw=0.6, label="measured (GridShield sees)")
        a.plot(tt, [f(r[tk]) for r in truth], "--", lw=0.8, label="ground truth")
        a.set_ylabel(title); a.grid(alpha=0.3)
        for e in events:
            if e["node"] in (node_id, "*"):
                end = float(e["end_s"]) if e["end_s"] not in ("", "None") else tt[-1] * 3600
                a.axvspan(float(e["start_s"]) / 3600, end / 3600, alpha=0.08, color="red")
    ax[0].legend(loc="upper left")
    for e in events:
        if e["node"] in (node_id, "*"):
            ax[0].text(float(e["start_s"]) / 3600, ax[0].get_ylim()[1] * 0.97, e["type"], rotation=90, va="top", fontsize=7)
    ax[-1].set_xlabel("simulation time (h)")
    fig.tight_layout(); fig.savefig(out_png, dpi=110); plt.close(fig)
    return out_png
