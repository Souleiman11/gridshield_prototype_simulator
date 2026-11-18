"""Communication channel model between an ESP32 node and the MQTT broker."""
from __future__ import annotations
import copy


class CommChannel:
    def __init__(self, rng, base_latency_s=0.08, jitter_s=0.04, base_loss=0.002):
        self.rng, self.base, self.jitter, self.base_loss = rng, base_latency_s, jitter_s, base_loss

    def transmit(self, msg: dict, t: float, active):
        """Return a list of (delivery_time, message); empty list = message lost."""
        loss, outage, delay, dup, jit = self.base_loss, False, 0.0, 0.0, self.jitter
        for f, s in active:
            P = f.params
            if f.type == "packet_loss":
                loss = max(loss, P.get("prob", 0.3))
            elif f.type == "comm_outage":
                outage = True
            elif f.type == "comm_delay":
                delay += P.get("delay_s", 20.0) * s
                jit = max(jit, P.get("jitter_s", 0.3 * delay))
            elif f.type == "comm_duplicate":
                dup = max(dup, P.get("prob", 0.2))
        if outage or self.rng.random() < loss:
            return []
        lat = self.base + abs(self.rng.normal(0, jit)) + delay
        out = [(t + lat, msg)]
        if self.rng.random() < dup:
            out.append((t + lat + 0.1 + abs(self.rng.normal(0, 0.05)), copy.deepcopy(msg)))
        return out
