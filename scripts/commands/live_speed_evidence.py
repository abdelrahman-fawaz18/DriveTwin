"""Read the bounded speed comparison from accepted monitoring observations."""

import math

HISTORY = "/v1/observations?asset_id={asset}&limit=17"
SAMPLES = 16


def speeds(history, asset, *, count=SAMPLES):
    if history["mode"] != "simulation" or len(history["items"]) != count:
        raise ValueError("Unexpected number of live simulation observations")
    values = []
    for second, row in enumerate(history["items"], 1):
        body = row["payload"]
        samples = [s for s in body["samples"] if s["signal_id"] == "motion.speed"]
        if (
            body["mode"] != "simulation"
            or (body["run_id"], body["asset_id"], body["source_id"], body["source_epoch"])
            != (history["run_id"], asset, "sumo-adapter", 1)
            or row["event_id"] != body["event_id"]
            or row["stream_seq"] != second - 1
            or body["stream_seq"] != second - 1
            or row["sim_time_s"] != second
            or body["sim_time_s"] != second
            or len(samples) != 1
        ):
            raise ValueError("Monitoring observations differ from the registered comparison window")
        sample = samples[0]
        value = sample["value"]
        if (
            (sample["unit"], sample["quality"], sample["provenance"])
            != ("m/s", "good", "simulated-observation")
            or type(value) not in (int, float)
            or not math.isfinite(value)
        ):
            raise ValueError("Monitoring speed measurement is unavailable")
        values.append(value)
    return values
