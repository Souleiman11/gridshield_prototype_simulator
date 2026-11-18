"""Minimal stand-in for the GridShield acquisition layer.

It subscribes ONLY to the telemetry topics, exactly as it would with real ESP32 nodes,
and never sees the simulator's ground truth.
"""
import json, sys
import paho.mqtt.client as mqtt

host = sys.argv[1] if len(sys.argv) > 1 else "localhost"
last_seq = {}

def on_message(client, userdata, msg):
    d = json.loads(msg.payload)
    n = d["node_id"]
    if n in last_seq and d["seq"] != last_seq[n] + 1:
        print(f"[{n}] sequence gap {last_seq[n]} -> {d['seq']}  (lost/late packets)")
    last_seq[n] = d["seq"]
    print(f"{n} seq={d['seq']} V={d['v_dc']} I={d['i_dc']} P={d['p_dc']} Tm={d['t_module_c']} G={d['g_poa_wm2']}")

try:
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="gridshield-demo")
except AttributeError:
    c = mqtt.Client(client_id="gridshield-demo")
c.on_message = on_message
c.connect(host, 1883)
c.subscribe("gridshield/+/+/telemetry", qos=1)
c.loop_forever()
