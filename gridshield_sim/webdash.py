"""Browser-based live dashboard (standard library only: no matplotlib, no extra packages).

Run with:  python -m gridshield_sim run <scenario> --web
The simulator starts a small HTTP server on 127.0.0.1; the page polls it a few times per second.
"""
from __future__ import annotations
import json, threading, time, webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .faults import ALL_TYPES, CHANNELS, Fault
from .params import get_module
from .sinks import Sink

PRESETS = {
    "partial_shading": {"substrings": [0], "remaining": 0.2},
    "soiling": {"loss_max": 0.15},
    "degradation": {"rs_factor": 2.5, "rsh_factor": 0.4, "iph_loss": 0.04},
    "hotspot": {"substring": 1, "extra_heat_wm2": 450},
    "open_circuit": {},
    "bypass_diode_short": {"substring": 0},
    "sensor_offset": {"channel": "i_dc", "offset": 0.4},
    "sensor_drift": {"channel": "v_dc", "rate_per_h": 0.6},
    "sensor_gain": {"channel": "i_dc", "factor": 0.9},
    "sensor_freeze": {"channel": "t_module"},
    "sensor_disconnect": {"channel": "g_poa", "mode": "null"},
    "sensor_noise": {"channel": "i_dc", "factor": 10},
    "sensor_spike": {"channel": "v_dc", "prob": 0.05, "amplitude": 5},
    "packet_loss": {"prob": 0.4},
    "comm_outage": {},
    "comm_delay": {"delay_s": 30},
    "comm_duplicate": {"prob": 0.3},
}
HTML_PATH = Path(__file__).parent / "web" / "dashboard.html"


class WebDashboard(Sink):
    def __init__(self, port: int = 8050, open_browser: bool = True, history: int = 40000):
        self.port, self.open_browser, self.history = port, open_browser, history
        self._lock = threading.Lock()
        self._truth, self._msgs = deque(maxlen=history), deque(maxlen=history)
        self._t_idx = self._m_idx = 0
        self._iv: dict = {}
        self.finished = False
        self.server = None
        self.url = ""

    # ---- Sink interface -------------------------------------------------
    def open(self, engine):
        super().open(engine)
        sc = engine.sc
        self.info = {
            "scenario": sc.name, "description": sc.description, "site": sc.site,
            "start_ms": int(sc.start.timestamp() * 1000), "duration_s": sc.duration_s, "step_s": sc.step_s,
            "nodes": [{"id": n.node_id, "role": n.role, "area": get_module(n.module).area_m2,
                       "p_mp_ref": get_module(n.module).p_mp_ref} for n in sc.nodes],
            "fault_types": ALL_TYPES, "presets": PRESETS, "channels": list(CHANNELS),
        }
        self._start_server()

    def on_truth(self, r):
        item = {"n": r["node_id"], "t": r["t_s"], "seq": r["seq"], "g": r["g_poa_true"], "ta": r["t_amb_true"],
                "tc": [v for k, v in r.items() if k.startswith("t_cell_s")],
                "v": r["v_true"], "i": r["i_true"], "p": r["p_true"], "pm": r["p_mpp_true"],
                "vm": r["v_mpp_true"], "im": r["i_mpp_true"], "voc": r["v_oc_true"], "isc": r["i_sc_true"],
                "np": r["n_peaks"], "mode": r["mppt_mode"], "lab": r["fault_labels"],
                "sev": r["fault_severity"], "dl": r["delivered"]}
        with self._lock:
            self._t_idx += 1
            self._truth.append((self._t_idx, item))
            iv = r.get("_iv")
            self._iv[r["node_id"]] = None if iv is None else (iv[0], iv[1])

    def on_message(self, m):
        p = m["payload"]
        item = {"n": p["node_id"], "t": m["sent_t_s"], "rx": m["rx_t_s"], "seq": p["seq"], "v": p["v_dc"],
                "i": p["i_dc"], "p": p["p_dc"], "tm": p["t_module_c"], "ta": p["t_ambient_c"],
                "g": p["g_poa_wm2"], "rssi": p["rssi_dbm"]}
        with self._lock:
            self._m_idx += 1
            self._msgs.append((self._m_idx, item))

    def close(self):
        self.finished = True

    # ---- serving ----------------------------------------------------------
    def _start_server(self):
        dash = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype="application/json"):
                data = body if isinstance(body, bytes) else body.encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype + ("; charset=utf-8" if "text" in ctype else ""))
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

            def _host_ok(self):
                host = (self.headers.get("Host") or "").split(":")[0]
                return host in ("127.0.0.1", "localhost")

            def do_GET(self):
                if not self._host_ok():
                    return self._send(403, '{"error":"bad host"}')
                path, _, qs = self.path.partition("?")
                q = dict(x.split("=", 1) for x in qs.split("&") if "=" in x)
                try:
                    if path in ("/", "/index.html"):
                        return self._send(200, HTML_PATH.read_bytes(), "text/html")
                    if path == "/api/info":
                        return self._send(200, json.dumps(dash.info))
                    if path == "/api/poll":
                        return self._send(200, json.dumps(dash.poll(int(q.get("t", -1)), int(q.get("m", -1)))))
                except Exception as exc:                       # never kill the server thread
                    return self._send(500, json.dumps({"error": str(exc)}))
                self._send(404, '{"error":"not found"}')

            def do_POST(self):
                if not self._host_ok():
                    return self._send(403, '{"error":"bad host"}')
                if (self.headers.get("Content-Type") or "").split(";")[0].strip() != "application/json":
                    return self._send(415, '{"error":"content-type must be application/json"}')
                if self.path != "/api/cmd":
                    return self._send(404, '{"error":"not found"}')
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    cmd = json.loads(self.rfile.read(n) or b"{}")
                    err = dash.command(cmd)
                except Exception as exc:
                    err = f"bad request: {exc}"
                self._send(400 if err else 200, json.dumps({"error": err} if err else {"ok": True}))

        last = None
        for p in ([self.port] if self.port == 0 else range(self.port, self.port + 11)):
            try:
                self.server = ThreadingHTTPServer(("127.0.0.1", p), H)
                break
            except OSError as exc:
                last = exc
        if self.server is None:
            raise OSError(f"no free port from {self.port}: {last}")
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/"
        print(f"[dashboard] live view at {self.url}")
        if self.open_browser:
            try:
                webbrowser.open(self.url)
            except Exception:
                pass

    def poll(self, t_since: int, m_since: int, limit: int = 20000):
        eng = self.engine
        with self._lock:
            def tail(dq, since):
                out = []
                for idx, item in reversed(dq):
                    if idx <= since or len(out) >= limit:
                        break
                    out.append(item)
                out.reverse()
                return out
            truth, msgs = tail(self._truth, t_since), tail(self._msgs, m_since)
            t_idx, m_idx = self._t_idx, self._m_idx
            iv = {k: {"v": [round(float(x), 3) for x in v[0]], "i": [round(float(x), 4) for x in v[1]]}
                  for k, v in self._iv.items() if v is not None}
        t = eng.t
        faults = []
        for f in list(eng.faults):
            sev = f.severity(t)
            faults.append({"id": f.id, "type": f.type, "node": f.node, "start": f.start_s, "end": f.end_s,
                           "ramp": f.ramp_s, "sev": round(sev, 3), "params": f.params,
                           "state": "active" if sev > 0 else ("scheduled" if t < f.start_s else "ended")})
        return {"truth": truth, "msgs": msgs, "t_idx": t_idx, "m_idx": m_idx, "iv": iv, "faults": faults,
                "state": {"t": t, "speed": eng.speed, "paused": eng.paused, "finished": self.finished,
                          "duration_s": eng.sc.duration_s}}

    def command(self, cmd: dict):
        """Validate a browser command and hand it to the engine. Returns an error string or None."""
        k = cmd.get("cmd")
        if k == "inject":
            f = dict(cmd.get("fault") or {})
            dur = f.pop("duration_s", None)
            if dur not in (None, ""):
                f["end_s"] = float(f.get("start_s", self.engine.t)) + float(dur)
            if f.get("node", "pv01") not in [n["id"] for n in self.info["nodes"]] + ["*"]:
                return f"unknown node '{f.get('node')}'"
            Fault(**f)                                          # raises on unknown type / bad fields
            cmd = {"cmd": "inject", "fault": f}
        elif k == "clear":
            cmd = {"cmd": "clear", "id": str(cmd["id"])}
        elif k == "set_speed":
            if float(cmd["speed"]) < 0:
                return "speed must be >= 0"
            cmd = {"cmd": "set_speed", "speed": float(cmd["speed"])}
        elif k in ("clear_all", "pause", "resume"):
            cmd = {"cmd": k}
        else:
            return f"unknown command '{k}'"
        self.engine.submit(cmd)
        return None

    def wait(self):
        """Keep the dashboard reachable after the simulation ends (Ctrl+C to quit)."""
        print(f"[dashboard] simulation finished - dashboard stays at {self.url}  (Ctrl+C to quit)")
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass

    def shutdown(self):
        if self.server:
            self.server.shutdown(); self.server.server_close()
