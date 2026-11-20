"""Bound a one-bus reference network while preserving every traversed junction."""

import hashlib
import xml.etree.ElementTree as ET


def elements(path):
    parser = ET.iterparse(path, events=("start", "end"))
    _, root = next(parser)
    depth = 1
    for event, node in parser:
        if event == "start":
            depth += 1
        else:
            depth -= 1
            if depth == 1:
                yield node
                root.remove(node)


def corridor(source, route, destination):
    """Retain original intersections and their approaches, without net regeneration.

    Only remote boundary junctions become dead ends. No vehicle in this reference
    leaves the declared route. Signal groups spanning several junctions are kept
    together so their link indices and programs remain unchanged.
    """
    edges = {
        node.get("id"): (node.get("from"), node.get("to"))
        for node in elements(source)
        if node.tag == "edge" and node.get("function", "normal") == "normal"
    }
    if not route or any(identity not in edges for identity in route):
        raise ValueError("Reference route contains an unknown external road")
    junctions = {j for identity in route for j in edges[identity]}
    groups = {}
    for node in elements(source):
        if node.tag == "connection" and node.get("tl"):
            entry = node.get("from")
            if entry in edges:
                groups.setdefault(node.get("tl"), set()).add(edges[entry][1])
    circles = [dict(node.attrib) for node in elements(source) if node.tag == "roundabout"]
    while True:
        expanded = junctions | set().union(
            *(group for group in groups.values() if group & junctions), set()
        )
        for circle in circles:
            nodes = set(circle.get("nodes", "").split())
            if nodes & expanded:
                expanded |= nodes
        if expanded == junctions:
            break
        junctions = expanded
    signals = {name for name, group in groups.items() if group & junctions}
    keep = {identity for identity, ends in edges.items() if set(ends) & junctions}
    boundaries = {j for identity in keep for j in edges[identity]} - junctions
    internal = {
        node.get("id")
        for node in elements(source)
        if node.tag == "edge"
        and node.get("function") == "internal"
        and node.get("id")[1:].rsplit("_", 1)[0] in junctions
    }
    keep |= internal
    with source.open("rb") as stream:
        _, original = next(ET.iterparse(stream, events=("start",)))
        result = ET.Element("net", dict(original.attrib))
    preserved = hashlib.sha256()
    count = 0
    for node in elements(source):
        tag, identity = node.tag, node.get("id")
        selected = (
            tag in ("location", "type")
            or tag == "edge"
            and identity in keep
            or tag == "tlLogic"
            and identity in signals
            or tag == "roundabout"
            and set(node.get("nodes", "").split()) <= junctions
            or tag == "junction"
            and (
                identity in junctions
                or node.get("type") == "internal"
                and identity[1:].rsplit("_", 2)[0] in junctions
            )
            or tag == "connection"
            and (
                node.get("from") in internal
                or node.get("from") in edges
                and edges[node.get("from")][1] in junctions
            )
        )
        if selected:
            if tag == "connection" and (node.get("from") not in keep or node.get("to") not in keep):
                raise ValueError("A protected junction lost an approach or departure")
            preserved.update(ET.tostring(node))
            count += 1
            result.append(node)
        elif tag == "junction" and identity in boundaries:
            node.set("type", "dead_end")
            node.set(
                "incLanes",
                " ".join(
                    lane
                    for lane in node.get("incLanes", "").split()
                    if lane.rsplit("_", 1)[0] in keep
                ),
            )
            node.set("intLanes", "")
            for child in list(node):
                node.remove(child)
            result.append(node)
    lanes = {lane.get("id") for edge in result.findall("edge") for lane in edge.findall("lane")}
    for node in result.findall("junction"):
        if any(
            lane not in lanes
            for key in ("incLanes", "intLanes")
            for lane in node.get(key, "").split()
        ):
            raise ValueError("Junction references an omitted lane")
    ET.ElementTree(result).write(destination, encoding="utf-8", xml_declaration=True)
    return {
        "version": "one-bus-network-corridor/1.0.0",
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "runtime_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "external_roads": len(keep - internal),
        "internal_roads": len(internal),
        "protected_junctions": len(junctions),
        "remote_dead_end_boundaries": len(boundaries),
        "preserved_elements": count,
        "preserved_elements_sha256": preserved.hexdigest(),
        "policy": "Original route, full adjacent intersections and linked signal groups; remote boundaries terminate unused approaches",
    }
