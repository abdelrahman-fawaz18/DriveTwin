"""Rebuild the project guide figures from reviewed local inputs."""

import hashlib
import json
import math
import xml.etree.ElementTree as ET
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/engineering/figures"
INK, MUTED, BLUE, TEAL = "#10263b", "#456078", "#245ab2", "#087a6b"
LINE, PALE = "#d9e5ed", "#f2f8fb"


def read(path):
    return json.loads((ROOT / path).read_bytes())


def label(x, y, value, size=16, color=INK, weight=400, anchor="start", *, halo=False):
    outline = (
        ' paint-order="stroke" stroke="white" stroke-width="4" stroke-linejoin="round"'
        if halo
        else ""
    )
    return (
        f'<text x="{x}" y="{y}" fill="{color}" font-size="{size}" '
        f'font-weight="{weight}" text-anchor="{anchor}"{outline}>{escape(str(value))}</text>'
    )


def box(x, y, width, height, fill, radius=12):
    return f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="{radius}" fill="{fill}"/>'


def line(x1, y1, x2, y2, color=LINE, width=1, dash="none"):
    return (
        f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
        f'stroke="{color}" stroke-width="{width}" stroke-dasharray="{dash}"/>'
    )


def figure(name, width, height, title, description, layers):
    (OUT / f"{name}.svg").write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'role="img" aria-labelledby="title desc" font-family="Segoe UI,Arial,sans-serif">\n'
        f'<title id="title">{escape(title)}</title><desc id="desc">{escape(description)}</desc>\n'
        + box(0, 0, width, height, "#ffffff", 0)
        + "\n"
        + "\n".join(layers)
        + "\n</svg>\n",
        encoding="utf-8",
        newline="\n",
    )


def overview():
    layers = [
        label(30, 40, "One simulated bus, four steps in the loop", 28, weight=700),
        label(
            30,
            68,
            "Read the bus, estimate what comes next, act within limits, verify the effect.",
            16,
            MUTED,
        ),
        box(30, 100, 218, 169, PALE),
        box(66, 141, 140, 58, BLUE, 11),
        box(72, 147, 123, 25, "#cce9ff", 5),
        box(90, 132, 86, 9, TEAL, 4),
        '<circle cx="93" cy="199" r="11" fill="#10263b"/><circle cx="178" cy="199" r="11" fill="#10263b"/>',
        label(139, 237, "Simulated electric bus", 16, weight=700, anchor="middle"),
        line(248, 143, 308, 143, BLUE, 3),
        '<path d="M299 137l9 6-9 6" fill="none" stroke="#245ab2" stroke-width="3"/>',
        line(248, 230, 308, 230, TEAL, 3),
        '<path d="M257 224l-9 6 9 6" fill="none" stroke="#087a6b" stroke-width="3"/>',
        label(277, 130, "Data", 13, BLUE, 700, "middle"),
        label(277, 252, "Action", 13, TEAL, 700, "middle"),
    ]
    for x, y, number, title, body in (
        (312, 100, "01", "Monitor", "Live readings and history"),
        (585, 100, "02", "Forecast", "Energy and reserve estimates"),
        (312, 188, "03", "Command", "Send an authorized action"),
        (585, 188, "04", "Verify effect", "Check speed, charge, or motion"),
    ):
        layers.extend(
            [
                box(x, y, 262, 81, PALE),
                label(x + 14, y + 25, number, 13, TEAL, 700),
                label(x + 41, y + 28, title, 20, weight=700),
                label(x + 14, y + 56, body, 15, MUTED),
            ]
        )
    figure(
        "twin-at-a-glance",
        880,
        292,
        "DriveTwin at a glance",
        "A simulated bus sends observations to the twin. The twin can send a guarded action back and verify its effect.",
        layers,
    )


def evidence_bars():
    candidate = read("docs/verification/energy-heldout.json")
    # Keep the two study units and scales separate.
    scores = candidate["evaluation"]
    command = read("docs/verification/live-speed-command.json")["independent_speed_effect"]
    panels = [
        (
            30,
            "Route-energy forecast error",
            "Mean absolute error across six reserved trips · kWh",
            (scores["baseline_energy_mae_kwh"], scores["energy_mae_kwh"]),
            ("Reference", "DriveTwin model"),
            2.5,
            3,
        ),
        (
            475,
            "Observed peak speed after command",
            "Same simulated drive setup · meters per second",
            (command["maximum_baseline_m_s"], command["maximum_commanded_m_s"]),
            ("Reference", "With speed cap"),
            8,
            2,
        ),
    ]
    layers = []
    for x, title, sub, values, names, limit, places in panels:
        layers.extend([label(x, 36, title, 21, weight=700), label(x, 62, sub, 14, MUTED)])
        for index, (name, value) in enumerate(zip(names, values, strict=True)):
            y = 100 + 58 * index
            color = MUTED if index == 0 else BLUE
            layers.extend(
                [
                    label(x, y, name, 15, weight=600),
                    box(x, y + 9, 316, 11, PALE, 5),
                    box(x, y + 9, 316 * value / limit, 11, color, 5),
                    label(x + 330, y + 20, f"{value:.{places}f}", 16, color, 700),
                ]
            )
        layers.extend(
            [
                label(x, 211, "0", 13, MUTED),
                label(x + 316, 211, f"{limit:g}", 13, MUTED, anchor="end"),
            ]
        )
    layers.append(line(446, 24, 446, 217))
    figure(
        "forecast-and-speed-evidence",
        920,
        235,
        "Two different checks: prediction error and physical response",
        "Route-energy mean absolute error fell from 2.384 to 0.285 kilowatt-hours on six synthetic trips. Peak speed fell from 7.80 to 4.00 meters per second in the speed-cap case.",
        layers,
    )


def route_map():
    mission = read("simulation/montreal/mission.json")
    catalog = read("simulation/montreal/charging-stations-1.0.0.json")["stations"]
    roads = {
        r["edge_id"]: r["shape_lon_lat"]
        for r in read("qualification/energy/station-navigation.json")["roads"]
    }
    segments = [roads[edge] for edge in mission["route_edges"]]
    points = [p for segment in segments for p in segment] + [
        [s["longitude"], s["latitude"]] for s in catalog
    ]
    mid_lat = sum(p[1] for p in points) / len(points)
    ratio = math.cos(math.radians(mid_lat))
    xmin, xmax = min(p[0] for p in points), max(p[0] for p in points)
    ymin, ymax = min(p[1] for p in points), max(p[1] for p in points)
    scale = min(760 / ((xmax - xmin) * ratio), 345 / (ymax - ymin))

    def project(lon, lat):
        return (
            450 + (lon - (xmin + xmax) / 2) * ratio * scale,
            264 - (lat - (ymin + ymax) / 2) * scale,
        )

    def polyline(coords, color, width):
        pixels = " ".join(f"{x:.1f},{y:.1f}" for x, y in (project(*p) for p in coords))
        return f'<polyline points="{pixels}" fill="none" stroke="{color}" stroke-width="{width}" stroke-linecap="round"/>'

    layers = [
        label(30, 37, "Montréal test route and hypothetical chargers", 26, weight=700),
        label(30, 64, "Static simulator inputs · sites are illustrative", 15, MUTED),
        '<defs><clipPath id="map-window"><rect x="30" y="82" width="840" height="366" rx="12"/></clipPath></defs>',
        box(30, 82, 840, 366, PALE),
        '<g clip-path="url(#map-window)">',
    ]
    osm = ET.parse(ROOT / "data/public/montreal-core-v1/roads.osm.xml").getroot()
    nodes = {
        node.attrib["id"]: (float(node.attrib["lon"]), float(node.attrib["lat"]))
        for node in osm.findall("node")
    }
    for way in osm.findall("way"):
        if way.find("tag[@k='highway']") is None:
            continue
        coords = [nodes[n.attrib["ref"]] for n in way.findall("nd") if n.attrib["ref"] in nodes]
        if len(coords) > 1 and any(
            30 <= x <= 870 and 82 <= y <= 448 for x, y in (project(*p) for p in coords)
        ):
            layers.append(polyline(coords, "#d8e4ea", 1))
    layers.extend(polyline(coords, BLUE, 3.5) for coords in segments)
    layers.append("</g>")
    for station, name, dx, dy in zip(
        catalog, ("Depot A", "Opportunity B", "West C"), (11, 11, -68), (18, -12, -12), strict=True
    ):
        x, y = project(station["longitude"], station["latitude"])
        layers.extend(
            [
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="6" fill="{TEAL}" stroke="white" stroke-width="2"/>',
                label(round(x + dx, 1), round(y + dy, 1), name, 15, INK, 700, halo=True),
            ]
        )
    layers.extend(
        [
            line(33, 474, 59, 474, BLUE, 4),
            label(68, 479, "Simulated mission road segments", 14),
            '<a href="https://www.openstreetmap.org/copyright">'
            + label(868, 479, "© OpenStreetMap contributors · ODbL 1.0", 13, MUTED, anchor="end")
            + "</a>",
        ]
    )
    figure(
        "montreal-simulation-map",
        900,
        500,
        "Montréal simulation route and three hypothetical charging sites",
        "Static geographic view of the modeled route and sites. Road geometry is derived from OpenStreetMap; no live bus or charger data is displayed.",
        layers,
    )


def charging_trace():
    folder = ROOT / "evidence/p4/automatic-charging-01/mission/mission"
    result = read(folder / "result.json")
    source = (folder / "measurements.jsonl").read_bytes()
    observed = read(folder.parent / "observed-effect.json")
    if not hashlib.sha256(source).hexdigest() == result["trace_sha256"] == observed["trace_sha256"]:
        raise ValueError("Charging trace does not match its qualified result")
    events = {row["event"]: row for row in result["events"]}
    start, target = (events[event]["sim_s"] for event in ("charging_observed", "target_observed"))
    bins, milestones = {}, {}
    for row in map(json.loads, source.splitlines()):
        t = row["sim_s"]
        pair = (t, round(row["battery_wh"] / 1000, 2))
        bins[int(t / 15)] = pair
        if t in (0.2, start, target):
            milestones[t] = pair
    points = sorted(set(bins.values()) | set(milestones.values()))

    def x(t):
        return round(86 + t * 790 / 1140, 2)

    def y(kwh):
        return round(332 - (kwh - 30) * 225 / 65, 2)

    layers = [
        label(30, 40, "Battery energy during the automatic charging mission", 27, weight=700),
        label(30, 66, "Measured simulated battery energy · one qualified run", 16, MUTED),
        box(x(start), 107, x(target) - x(start), 225, PALE, 0),
        label(82, 96, "Energy (kWh)", 14, MUTED),
        label(x(start) + 12, 130, "Charging at Depot A", 15, TEAL, 700),
    ]
    for tick in (35, 50, 65, 80, 95):
        layers.extend(
            [
                line(
                    86,
                    y(tick),
                    876,
                    y(tick),
                    TEAL if tick == 35 else LINE,
                    1,
                    "5 4" if tick == 35 else "none",
                ),
                label(75, y(tick) + 5, tick, 13, MUTED, anchor="end"),
            ]
        )
    for minute in (0, 3, 6, 9, 12, 15, 18):
        layers.extend(
            [
                line(x(minute * 60), 332, x(minute * 60), 338),
                label(x(minute * 60), 357, minute, 13, MUTED, anchor="middle"),
            ]
        )
    pixels = " ".join(f"{x(t)},{y(kwh)}" for t, kwh in points)
    layers.extend(
        [
            f'<polyline points="{pixels}" fill="none" stroke="{BLUE}" stroke-width="3.5" stroke-linejoin="round"/>',
            label(860, y(35) - 8, "35 kWh reserve", 14, TEAL, 700, "end"),
            label(x(target) - 8, y(87.5) - 9, "87.5 kWh target reached", 14, BLUE, 700, "end"),
            label(97, y(52.35) + 25, "52.35 kWh before charge", 13, MUTED),
            label(481, 383, "Simulated time (minutes)", 15, MUTED, anchor="middle"),
            label(
                30,
                413,
                "Last measured sample per 15-second bin, plus event samples. The bus resumes and finishes near minute 18.1.",
                13,
                MUTED,
            ),
        ]
    )
    figure(
        "automatic-charge-trace",
        920,
        431,
        "Measured battery energy during the automatic diversion",
        "The bus starts at 54 kilowatt-hours, reaches the charger with about 52.35, charges to 87.5, then resumes and finishes. The 35 kilowatt-hour reserve is never crossed.",
        layers,
    )


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    overview()
    evidence_bars()
    route_map()
    charging_trace()
