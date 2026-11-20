"""Fetch one bounded OSM road extract; keep only reviewed road/topology fields."""

import argparse
import hashlib
import json
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NODE_TAGS = {
    "highway",
    "crossing",
    "traffic_signals",
    "traffic_signals:direction",
    "barrier",
    "access",
    "bus",
    "psv",
    "motor_vehicle",
    "vehicle",
    "ele",
}
ROAD_TYPES = {
    "motorway",
    "trunk",
    "primary",
    "secondary",
    "tertiary",
    "unclassified",
    "residential",
    "living_street",
    "service",
    "motorway_link",
    "trunk_link",
    "primary_link",
    "secondary_link",
    "tertiary_link",
}
WAY_TAGS = {
    "highway",
    "name",
    "ref",
    "oneway",
    "oneway:bus",
    "access",
    "bus",
    "psv",
    "vehicle",
    "motor_vehicle",
    "motorcar",
    "width",
    "maxwidth",
    "maxheight",
    "maxweight",
    "maxlength",
    "maxspeed",
    "lanes",
    "lanes:forward",
    "lanes:backward",
    "junction",
    "bridge",
    "tunnel",
    "layer",
    "lanes:bus",
    "bus:lanes",
    "psv:lanes",
    "turn:lanes",
    "access:conditional",
    "bus:conditional",
}


def project_path(value):
    path = (ROOT / value).resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError("Map configuration paths must remain inside the canonical project")
    return path


def clean_osm(payload):
    if b"<!DOCTYPE" in payload.upper() or b"<!ENTITY" in payload.upper():
        raise ValueError("External declarations are not accepted")
    original = ET.fromstring(payload)
    if original.tag != "osm" or original.find("remark") is not None:
        raise ValueError("Not a successful OSM response")
    ways = []
    for way in original.findall("way"):
        tags = {t.attrib["k"]: t.attrib["v"] for t in way.findall("tag")}
        if tags.get("highway") in ROAD_TYPES or any(
            tags.get(key) in {"yes", "designated"} for key in ("bus", "psv")
        ):
            ways.append(way)
    way_ids = {w.attrib["id"] for w in ways}
    nodes = {nd.attrib["ref"] for w in ways for nd in w.findall("nd")}
    selected = {
        "way": ways,
        "node": [n for n in original.findall("node") if n.attrib["id"] in nodes],
        "relation": [
            r
            for r in original.findall("relation")
            if all(
                m.attrib["ref"] in (way_ids if m.attrib["type"] == "way" else nodes)
                for m in r.findall("member")
            )
        ],
    }
    output = ET.Element(
        "osm",
        version="0.6",
        generator="EV-DT road snapshot",
        attribution="OpenStreetMap contributors",
        license="https://opendatacommons.org/licenses/odbl/1-0/",
    )
    count = {"node": 0, "way": 0, "relation": 0}
    for kind in count:
        for element in sorted(selected[kind], key=lambda x: int(x.attrib["id"])):
            attributes = {"id": element.attrib["id"]}
            if kind == "node":
                attributes.update(lat=element.attrib["lat"], lon=element.attrib["lon"])
            item = ET.SubElement(output, kind, attributes)
            for child in element:
                if child.tag == "tag":
                    if kind == "node" and child.attrib["k"] not in NODE_TAGS:
                        continue
                    if kind == "way" and child.attrib["k"] not in WAY_TAGS:
                        continue
                    ET.SubElement(item, "tag", {k: child.attrib[k] for k in ("k", "v")})
                elif child.tag == "nd":
                    ET.SubElement(item, "nd", ref=child.attrib["ref"])
                elif child.tag == "member":
                    ET.SubElement(
                        item, "member", {k: child.attrib[k] for k in ("type", "ref", "role")}
                    )
            count[kind] += 1
    if not count["way"] or not count["node"]:
        raise ValueError("Empty road extract")
    node_ids = {n.attrib["id"] for n in output.findall("node")}
    if any(nd.attrib["ref"] not in node_ids for nd in output.findall("way/nd")):
        raise ValueError("Incomplete way geometry")
    ET.indent(output, space="  ")
    return ET.tostring(output, encoding="utf-8", xml_declaration=True) + b"\n", count, original


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Fetch only if no reviewed snapshot exists; overwrite is refused",
    )
    parser.add_argument(
        "--downloaded-response",
        type=Path,
        help="Use an already downloaded response in ignored .runtime",
    )
    args = parser.parse_args()
    config = json.loads((ROOT / "simulation/montreal/map-source.json").read_text())
    target = project_path(config["artifact"])
    receipt = target.parent / "source.json"
    if target.exists():
        saved = json.loads(receipt.read_text())
        if hashlib.sha256(target.read_bytes()).hexdigest() != saved["artifact_sha256"]:
            raise SystemExit("Snapshot changed; preserve it and investigate")
        print("Existing reviewed snapshot verified; no network request made.")
        return
    if not args.refresh:
        raise SystemExit("No snapshot. Use --refresh for one explicit network download.")
    query = project_path(config["query_file"]).read_bytes()
    url = config["endpoint"] + "?" + urllib.parse.urlencode({"data": query.decode()})
    if args.downloaded_response:
        response_path = args.downloaded_response.resolve()
        if not response_path.is_relative_to(ROOT / ".runtime"):
            parser.error("Cached downloads must be inside ignored .runtime")
        payload = response_path.read_bytes()
    else:
        request = urllib.request.Request(
            url, headers={"User-Agent": "EV-DT-research-map-snapshot/1.0"}
        )
        with urllib.request.urlopen(request, timeout=45) as response:
            payload = response.read(config["maximum_download_bytes"] + 1)
    if len(payload) > config["maximum_download_bytes"]:
        raise SystemExit("Response exceeds the configured size limit")
    cleaned, counts, original = clean_osm(payload)
    raw = ROOT / "data/downloads/montreal-core-v1"
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "response.osm.xml").write_bytes(payload)
    target.parent.mkdir(parents=True, exist_ok=True)
    metadata = original.find("meta")
    record = {
        **config,
        "accessed_at_utc": datetime.now(timezone.utc).isoformat(),
        "osm_base_timestamp": metadata.attrib.get("osm_base") if metadata is not None else None,
        "query_sha256": hashlib.sha256(query).hexdigest(),
        "download_sha256": hashlib.sha256(payload).hexdigest(),
        "artifact_sha256": hashlib.sha256(cleaned).hexdigest(),
        "download_bytes": len(payload),
        "artifact_bytes": len(cleaned),
        "counts": counts,
        "request_method": "GET",
        "cached_download_used": bool(args.downloaded_response),
        "transformations": [
            "Strip contributor/account/timestamp metadata",
            "Retain motor-road categories or explicit bus/psv overrides and their nodes",
            "Retain complete associated turn relations and whitelisted transport tags",
            "Remove unrelated node POI/address tags; deterministic numeric ID order",
        ],
        "limitations": [
            "Clipped snapshot, not a certified/legal bus route",
            "Conditional restrictions/elevation/completeness not independently validated",
            "No live traffic, bus schedule, telemetry or charger availability",
        ],
    }
    with target.open("xb") as output:
        output.write(cleaned)
    with receipt.open("x", encoding="utf-8", newline="\n") as output:
        output.write(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    print(f"Saved reviewed road snapshot: {len(cleaned)} bytes; {counts}")


if __name__ == "__main__":
    main()
