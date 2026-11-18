"""Output sinks. Measurements and ground truth are ALWAYS written to separate destinations."""
from __future__ import annotations
import csv, json, os, sys
from collections import deque
from .faults import ALL_TYPES

MEAS_COLS = ["rx_ts_ms", "topic", "node_id", "seq", "ts_ms", "v_dc", "i_dc", "p_dc",
             "t_module_c", "t_ambient_c", "g_poa_wm2", "rssi_dbm"]


class Sink:
    def open(self, engine): self.engine = engine
    def on_truth(self, rec): pass
    def on_message(self, msg): pass
    def close(self): pass


class DatasetWriter(Sink):
    """measurements.csv = what GridShield receives; ground_truth.csv = simulator internals."""

    def __init__(self, out_dir):
        self.out = out_dir
        os.makedirs(out_dir, exist_ok=True)
        self.n_meas = self.n_truth = 0
        self._tw = None

    def open(self, engine):
        super().open(engine)
        self._mf = open(os.path.join(self.out, "measurements.csv"), "w", newline="")
        self._mw = csv.DictWriter(self._mf, MEAS_COLS, extrasaction="ignore")
        self._mw.writeheader()
        self._tf = open(os.path.join(self.out, "ground_truth.csv"), "w", newline="")

    def on_message(self, msg):
        row = dict(msg["payload"], rx_ts_ms=msg["rx_ts_ms"], topic=msg["topic"])
        self._mw.writerow({k: ("" if row.get(k) is None else row[k]) for k in MEAS_COLS})
        self.n_meas += 1

    def on_truth(self, rec):
        row = {k: v for k, v in rec.items() if not k.startswith("_")}
        active = set(row["fault_labels"].split(";"))
        for ft in ALL_TYPES:
            row[f"lbl_{ft}"] = int(ft in active)
        if self._tw is None:
            self._tw = csv.DictWriter(self._tf, list(row), extrasaction="ignore")
            self._tw.writeheader()
        self._tw.writerow(row)
        self.n_truth += 1

    def close(self):
        self._mf.close(); self._tf.close()
        with open(os.path.join(self.out, "fault_events.csv"), "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["id", "type", "category", "node", "start_s", "end_s", "ramp_s", "params"])
            for f in self.engine.faults:
                w.writerow([f.id, f.type, f.category, f.node, f.start_s, f.end_s, f.ramp_s, json.dumps(f.params)])
        meta = {"scenario": self.engine.sc.to_dict(), "measurement_rows": self.n_meas,
                "ground_truth_rows": self.n_truth}
        with open(os.path.join(self.out, "meta.json"), "w") as fh:
            json.dump(meta, fh, indent=2, default=str)


class ConsoleSink(Sink):
    def __init__(self, every_s=600.0, stream=sys.stdout):
        self.every, self.stream, self._next = every_s, stream, {}

    def on_truth(self, r):
        if r["t_s"] >= self._next.get(r["node_id"], 0.0):
            self._next[r["node_id"]] = r["t_s"] + self.every
            print(f"{r['time_utc'][11:19]} {r['node_id']:7s} G={r['g_poa_true']:6.1f} "
                  f"Tc={r['t_cell_s1']:5.1f} V={r['v_true']:5.1f} I={r['i_true']:5.2f} "
                  f"P={r['p_true']:6.1f}W peaks={r['n_peaks']} [{r['fault_labels']}]", file=self.stream)


class MqttSink(Sink):
    """Publishes telemetry to  gridshield/<site>/<node>/telemetry  (what a real ESP32 would send).

    Optional: ground truth to  gridshield-sim/<site>/truth/<node>  (deny GridShield access via broker ACL).
    Control channel: gridshield-sim/<site>/control  (JSON commands: inject, clear, clear_all, set_speed, pause, resume).
    """

    def __init__(self, host="localhost", port=1883, qos=1, username=None, password=None,
                 publish_truth=False, keepalive=30):
        self.host, self.port, self.qos, self.keepalive = host, port, qos, keepalive
        self.username, self.password, self.publish_truth = username, password, publish_truth

    def open(self, engine):
        super().open(engine)
        import paho.mqtt.client as mqtt
        self.site = engine.sc.site
        cid = f"gridshield-sim-{self.site}"
        try:
            self.c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cid)
        except AttributeError:
            self.c = mqtt.Client(client_id=cid)
        if self.username:
            self.c.username_pw_set(self.username, self.password)
        status = f"gridshield-sim/{self.site}/status"
        self.c.will_set(status, "offline", qos=1, retain=True)
        ctrl = f"gridshield-sim/{self.site}/control"

        def on_connect(c, *a):
            c.subscribe(ctrl, qos=1)

        def on_message(c, userdata, m):
            try:
                engine.submit(json.loads(m.payload))
            except Exception as exc:
                print(f"[mqtt] bad control message: {exc}")

        self.c.on_connect, self.c.on_message = on_connect, on_message
        self.c.connect(self.host, self.port, self.keepalive)
        self.c.loop_start()
        self.c.publish(status, "online", qos=1, retain=True)

    def on_message(self, msg):
        self.c.publish(msg["topic"], json.dumps(msg["payload"]), qos=self.qos)

    def on_truth(self, rec):
        if self.publish_truth:
            row = {k: v for k, v in rec.items() if not k.startswith("_")}
            self.c.publish(f"gridshield-sim/{self.site}/truth/{rec['node_id']}", json.dumps(row), qos=0)

    def close(self):
        self.c.publish(f"gridshield-sim/{self.site}/status", "offline", qos=1, retain=True).wait_for_publish()
        self.c.loop_stop(); self.c.disconnect()
