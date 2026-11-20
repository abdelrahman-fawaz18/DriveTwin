"""Independent oracles for accounting, metric failure cases, and map sanitization."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


metrics = load("benchmark_spec")
maps = load("fetch_map")


def test_frozen_workload_counts_and_spool_lower_bound():
    report = metrics.inspect_spec(json.loads((ROOT / "benchmarks/workloads.json").read_text()))
    assert report["workloads"]["W0"] == {"events": 120, "scalar_samples": 2400}
    assert report["workloads"]["W1"] == {"events": 3000, "scalar_samples": 60000}
    assert report["workloads"]["W2"] == {"events": 72000, "scalar_samples": 1440000}
    assert report["workloads"]["W3"] == {"events": 4800, "scalar_samples": 96000}
    assert report["workloads"]["W4"] == {"events": 36000, "scalar_samples": 720000}
    assert report["nominal_30min_max_encoded_bytes_per_asset"] == 14745600
    assert report["performance_targets_measured"] is False
    assert report["planning_payload_admitted_by_current_receiver"] is False


def test_duration_revision_preserves_every_other_frozen_requirement():
    spec = json.loads((ROOT / "benchmarks/workloads.json").read_bytes())
    assert spec.pop("profile_version") == "1.3.1"
    assert spec.pop("phase_1_acceptance") == {
        "policy_version": "phase1-local-monitoring/1.3.1",
        "workloads": ["W0", "W1"],
        "measured_duration_s": {"W0": 120, "W1": 300},
        "warmup_s": 60,
        "host_memory_policy": "phase1-local-host/1.0.1",
    }
    assert spec["workloads"]["W0"]["duration_s"] == 120
    assert spec["workloads"]["W0"]["segments"][0]["duration_s"] == 120
    spec["workloads"]["W0"]["duration_s"] = 1800
    spec["workloads"]["W0"]["segments"][0]["duration_s"] = 1800
    assert spec["workloads"]["W1"].pop("duration_s") == 300
    assert spec["workloads"]["W1"]["segments"][0].pop("duration_s") == 300
    # Canonical v1.0.0 bytes at f2cc5b6, excluding only the authorized duration
    # and version fields. Protect all signal, rate, quality, resource and later
    # workload requirements against an accidental broad replacement.
    protected = json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(protected).hexdigest() == (
        "9adf818b8ba8908bef9a5abb209ba8bf48217c1da18f742e428c2c95f73cd2fc"
    )


def test_latency_fails_closed_for_missing_data_and_clock_uncertainty():
    assert metrics.latency_assessment([0.1] * 99, 100, 0)["status"] == "fail"
    assert metrics.latency_assessment([0.1] * 100, 100, 0.051)["status"] == "inconclusive"
    assert metrics.latency_assessment([1.99] * 100, 100, 0.02)["status"] == "fail"
    assert metrics.latency_assessment([1] * 99 + [4], 100, 0.02)["status"] == "pass"


@pytest.mark.parametrize("values", [[], [float("nan")], [float("inf")], [-1], [True]])
def test_percentiles_refuse_invalid_measurements(values):
    with pytest.raises(ValueError):
        metrics.percentile(values, 0.95)


def test_nearest_rank_is_not_interpolated_or_mean_of_window_percentiles():
    assert metrics.percentile(range(1, 101), 0.95) == 95
    assert metrics.percentile([1], 0.99) == 1
    with pytest.raises(ValueError):
        metrics.counts({"assets": True, "signals_per_asset": 20, "duration_s": 1, "segments": []})


def test_map_strips_account_and_unrelated_node_metadata_but_preserves_access():
    payload = b"""<osm version="0.6"><node id="1" lat="45.5" lon="-73.5" user="example" uid="4">
    <tag k="addr:housenumber" v="123"/><tag k="highway" v="traffic_signals"/></node>
    <node id="2" lat="45.51" lon="-73.51"/><way id="3" user="example"><nd ref="1"/>
    <nd ref="2"/><tag k="highway" v="residential"/><tag k="bus" v="no"/>
    <tag k="contact:phone" v="example"/></way></osm>"""
    cleaned, count, _ = maps.clean_osm(payload)
    assert count == {"node": 2, "way": 1, "relation": 0}
    assert b"user=" not in cleaned and b"uid=" not in cleaned and b"addr:" not in cleaned
    assert b"contact:" not in cleaned
    assert b'k="bus" v="no"' in cleaned and b"traffic_signals" in cleaned


def test_map_refuses_error_partial_geometry_and_external_declarations():
    for payload in [
        b"<osm><remark>timeout</remark></osm>",
        b"<!DOCTYPE osm><osm/>",
        b'<osm><node id="1" lat="1" lon="1"/><way id="2"><nd ref="9"/>'
        b'<tag k="highway" v="primary"/></way></osm>',
    ]:
        with pytest.raises(ValueError):
            maps.clean_osm(payload)


def test_map_configuration_cannot_escape_project():
    with pytest.raises(ValueError):
        maps.project_path("../outside-project/roads.osm.xml")
    assert maps.project_path("data/public") == maps.ROOT / "data/public"
