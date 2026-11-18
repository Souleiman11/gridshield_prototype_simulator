"""Command line interface:  python -m gridshield_sim <list|show|export|run|plot> ..."""
from __future__ import annotations
import argparse, json, os
from .engine import Engine
from .scenario import BUILTIN, get_scenario
from .sinks import ConsoleSink, DatasetWriter, MqttSink


def main(argv=None):
    ap = argparse.ArgumentParser(prog="gridshield_sim", description="GridShield PV Edge simulator")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list built-in scenarios")
    sh = sub.add_parser("show", help="print a scenario as JSON"); sh.add_argument("scenario")
    ex = sub.add_parser("export", help="write a scenario JSON file"); ex.add_argument("scenario"); ex.add_argument("path")
    r = sub.add_parser("run", help="run a scenario")
    r.add_argument("scenario", help="built-in name or path to JSON")
    r.add_argument("--out", help="write dataset (measurements + ground truth) to this directory")
    r.add_argument("--mqtt", action="store_true", help="publish telemetry to an MQTT broker")
    r.add_argument("--live", action="store_true", help="matplotlib real-time dashboard (needs matplotlib)")
    r.add_argument("--web", action="store_true", help="browser live dashboard (no extra packages needed)")
    r.add_argument("--web-port", type=int, default=8050); r.add_argument("--no-browser", action="store_true")
    r.add_argument("--node", default=None, help="node shown in the dashboard")
    r.add_argument("--speed", type=float, default=None, help="0=max, 1=real time, 60=60x (default: 60 with --web, 1 with --mqtt/--live, else 0)")
    r.add_argument("--step", type=float, help="override time step (s)")
    r.add_argument("--duration-h", type=float, help="override duration (h)")
    r.add_argument("--start", help="start time, UTC, e.g. 2026-06-21T10:00:00 (fault times stay relative to it)")
    r.add_argument("--seed", type=int); r.add_argument("--site")
    r.add_argument("--host", default="localhost"); r.add_argument("--port", type=int, default=1883)
    r.add_argument("--user"); r.add_argument("--password"); r.add_argument("--qos", type=int, default=1)
    r.add_argument("--publish-truth", action="store_true", help="also publish ground truth on the simulator-only topic")
    pl = sub.add_parser("plot", help="static overview plot of a dataset directory")
    pl.add_argument("dataset"); pl.add_argument("--node", default="pv01"); pl.add_argument("--png", default="overview.png")
    a = ap.parse_args(argv)

    if a.cmd == "list":
        for n, f in BUILTIN.items():
            print(f"{n:24s} {f().description}")
    elif a.cmd == "show":
        print(json.dumps(get_scenario(a.scenario).to_dict(), indent=2))
    elif a.cmd == "export":
        get_scenario(a.scenario).save(a.path); print("written", a.path)
    elif a.cmd == "plot":
        from .viz import plot_dataset
        print(plot_dataset(a.dataset, a.node, a.png))
    else:
        sc = get_scenario(a.scenario)
        if a.step: sc.step_s = a.step
        if a.duration_h: sc.duration_s = a.duration_h * 3600
        if a.start: sc.start_utc = a.start
        if a.seed is not None: sc.seed = a.seed
        if a.site: sc.site = a.site
        sinks = []
        if a.out or not (a.mqtt or a.live or a.web):
            sinks.append(DatasetWriter(a.out or os.path.join("runs", sc.name)))
        if a.mqtt:
            sinks.append(MqttSink(a.host, a.port, a.qos, a.user, a.password, a.publish_truth))
        if a.live:
            from .viz import LiveDashboard
            sinks.append(LiveDashboard(a.node or sc.nodes[0].node_id))
        web = None
        if a.web:
            from .webdash import WebDashboard
            web = WebDashboard(a.web_port, open_browser=not a.no_browser)
            sinks.append(web)
        sinks.append(ConsoleSink(every_s=3600 if a.speed in (None, 0) else 300))
        speed = a.speed if a.speed is not None else (60.0 if a.web else 1.0 if (a.mqtt or a.live) else 0.0)
        Engine(sc, sinks).run(speed)
        print("done.", f"dataset in {sinks[0].out}" if isinstance(sinks[0], DatasetWriter) else "")
        if web:
            web.wait(); web.shutdown()
