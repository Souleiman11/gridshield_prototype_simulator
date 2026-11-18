# GridShield PV Edge - PV Simulation Platform

Physics-based photovoltaic simulator that emulates PV modules, ESP32 acquisition nodes and MQTT
telemetry, with configurable fault injection and labelled dataset generation.

## Install
```
pip install -r requirements.txt
python -m pytest -q          # 16 tests
```

## Use
```
python -m gridshield_sim list                                   # built-in scenarios
python -m gridshield_sim run mixed_day --out runs/mixed --step 5   # fast dataset (≈ seconds)
python -m gridshield_sim plot runs/mixed                         # overview.png
python -m gridshield_sim run mixed_day --web                      # browser live dashboard (opens http://127.0.0.1:8050)
python -m gridshield_sim run mixed_day --live --speed 120        # matplotlib dashboard (needs matplotlib)
python -m gridshield_sim run mixed_day --mqtt --host 192.168.1.50 --speed 1   # to the Raspberry Pi broker
python -m gridshield_sim export mixed_day my_scenario.json       # edit, then run my_scenario.json
```

## Browser live dashboard (`--web`)
Standard library only (no matplotlib). Open the printed address; the page updates while the simulation plays.
* Panels: power, voltage, current, temperatures, irradiance, I-V/P-V curve with operating point and MPP, KPIs, event log.
* **Engineer view** shows measured data (solid) next to simulator truth (dashed). **GridShield view** hides everything a
  real system would not know (truth curves, fault list, I-V curve, truth log), so you can see what the diagnostics see.
* Controls: node, time window, speed (1x ... Max), pause/resume, fault injection form, clear buttons. Hover a chart for values.
* Options: `--speed 60` (default with `--web`), `--web-port 8050`, `--no-browser`, `--start 2026-06-21T10:00:00`.
* Add `--out runs\x` to save the dataset at the same time. After the run ends the page stays up; press Ctrl+C to quit.
* The server listens on 127.0.0.1 only and accepts commands only as JSON from the page itself.

## Separation of simulator and GridShield
* Telemetry topic: `gridshield/<site>/<node>/telemetry` - measurement fields only, no labels.
* `ground_truth.csv`, `fault_events.csv` stay on the simulator side (join on `node_id`+`seq`).
* Optional `--publish-truth` uses `gridshield-sim/<site>/truth/<node>`; deny it to GridShield in the broker ACL.

## Runtime control (MQTT topic `gridshield-sim/<site>/control`)
```
{"cmd":"inject","fault":{"type":"partial_shading","node":"pv01","params":{"substrings":[0]}}}
{"cmd":"clear","id":"F03"}   {"cmd":"clear_all"}   {"cmd":"set_speed","speed":10}   {"cmd":"pause"}
```

## Fault types
physical: partial_shading, soiling, degradation, hotspot, open_circuit, bypass_diode_short
sensor (needs `"channel"`: v_dc|i_dc|t_module|t_ambient|g_poa): sensor_offset, sensor_drift, sensor_gain,
sensor_freeze, sensor_disconnect, sensor_noise, sensor_spike
communication: packet_loss, comm_outage, comm_delay, comm_duplicate

## Raspberry Pi
Only numpy (+ paho-mqtt) are needed for headless operation. Run `mosquitto` on the Pi, GridShield on the Pi,
and keep the simulator on the development PC (or on the Pi with `--mqtt --host localhost`).
`examples/gridshield_subscriber.py` shows how the edge application subscribes.
