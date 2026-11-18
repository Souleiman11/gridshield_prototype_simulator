import json, urllib.request, urllib.error
from gridshield_sim.engine import Engine
from gridshield_sim.scenario import get_scenario
from gridshield_sim.webdash import WebDashboard


def _call(url, data=None, ctype="application/json"):
    req = urllib.request.Request(url, data=None if data is None else json.dumps(data).encode(),
                                 headers={"Content-Type": ctype} if data is not None else {})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_web_dashboard_api():
    sc = get_scenario("mixed_day")
    sc.start_utc, sc.duration_s, sc.step_s = "2026-06-21T11:00:00", 120, 2.0
    web = WebDashboard(port=0, open_browser=False)
    Engine(sc, [web]).run()
    try:
        base = web.url.rstrip("/")
        code, body = _call(base + "/")
        assert code == 200 and b"GridShield PV Simulator" in body
        info = json.loads(_call(base + "/api/info")[1])
        assert [n["id"] for n in info["nodes"]] == ["pv01", "pv_ref"]
        d = json.loads(_call(base + "/api/poll?t=-1&m=-1")[1])
        assert len(d["truth"]) == 2 * 61 and len(d["msgs"]) > 100
        assert "pv01" in d["iv"] and d["state"]["finished"]
        assert all("lab" not in m and "sev" not in m for m in d["msgs"])      # messages carry no truth
        assert json.loads(_call(base + f"/api/poll?t={d['t_idx']}&m={d['m_idx']}")[1])["truth"] == []   # incremental
        # commands
        assert _call(base + "/api/cmd", {"cmd": "inject", "fault": {"type": "hotspot", "node": "pv01"}})[0] == 200
        assert _call(base + "/api/cmd", {"cmd": "inject", "fault": {"type": "nonsense"}})[0] == 400
        assert _call(base + "/api/cmd", {"cmd": "inject", "fault": {"type": "soiling", "node": "zzz"}})[0] == 400
        assert _call(base + "/api/cmd", {"cmd": "set_speed", "speed": -1})[0] == 400
        assert _call(base + "/api/cmd", {"cmd": "pause"}, ctype="text/plain")[0] == 415   # blocks cross-site posts
        assert web.engine.commands.qsize() == 1
    finally:
        web.shutdown()
