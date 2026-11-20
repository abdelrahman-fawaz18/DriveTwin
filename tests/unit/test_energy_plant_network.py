import importlib.util
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

MODULE = Path(__file__).resolve().parents[2] / "scripts/geographic/plant_network.py"
spec = importlib.util.spec_from_file_location("plant_network", MODULE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ReferenceNetworkTests(unittest.TestCase):
    def network(self):
        root = ET.Element("net", version="1.20", lefthand="true")
        ET.SubElement(root, "location", netOffset="0,0", projParameter="!")
        for identity, start, end in (
            ("a", "A", "J"),
            ("b", "J", "B"),
            ("cross", "C", "J"),
            ("remote", "D", "C"),
            ("linked", "X", "K"),
            ("exit", "K", "Y"),
        ):
            edge = ET.SubElement(root, "edge", id=identity, **{"from": start, "to": end})
            ET.SubElement(
                edge, "lane", id=identity + "_0", shape="0,0,10 20,0,12", speed="13", length="20"
            )
        edge = ET.SubElement(root, "edge", id=":J_0", function="internal")
        ET.SubElement(edge, "lane", id=":J_0_0", shape="20,0,12 23,0,12", length="3")
        for name, incoming, internal in (
            ("A", "", ""),
            ("J", "a_0 cross_0", ":J_0_0"),
            ("B", "b_0", ""),
            ("C", "remote_0", ""),
            ("D", "", ""),
            ("X", "", ""),
            ("K", "linked_0", ""),
            ("Y", "exit_0", ""),
        ):
            junction = ET.SubElement(
                root, "junction", id=name, type="priority", incLanes=incoming, intLanes=internal
            )
            ET.SubElement(junction, "request", index="0", foes="00", response="00")
        logic = ET.SubElement(root, "tlLogic", id="shared", type="static", offset="7")
        ET.SubElement(logic, "phase", duration="30", state="Gr")
        for start, end, extra in (
            ("a", "b", {"via": ":J_0_0", "tl": "shared", "linkIndex": "0"}),
            (":J_0", "b", {}),
            ("cross", "b", {}),
            ("remote", "cross", {}),
            ("linked", "exit", {"tl": "shared", "linkIndex": "1"}),
        ):
            ET.SubElement(root, "connection", **{"from": start, "to": end, **extra})
        return root

    def test_route_intersections_and_linked_signal_groups_are_preserved_exactly(self):
        with tempfile.TemporaryDirectory() as temp:
            source, output = Path(temp) / "source.xml", Path(temp) / "bounded.xml"
            original = self.network()
            ET.ElementTree(original).write(source)
            receipt = module.corridor(source, ["a", "b"], output)
            bounded = ET.parse(output).getroot()
            self.assertEqual(bounded.attrib, original.attrib)
            for tag, identity in (
                ("edge", "a"),
                ("edge", "cross"),
                ("edge", ":J_0"),
                ("junction", "J"),
                ("junction", "K"),
                ("tlLogic", "shared"),
            ):
                self.assertEqual(
                    ET.tostring(original.find(f"{tag}[@id='{identity}']")),
                    ET.tostring(bounded.find(f"{tag}[@id='{identity}']")),
                )
            self.assertIsNone(bounded.find("edge[@id='remote']"))
            self.assertEqual(bounded.find("junction[@id='C']").get("type"), "dead_end")
            self.assertEqual(bounded.find("junction[@id='C']").get("incLanes"), "")
            self.assertEqual(receipt["protected_junctions"], 4)
            self.assertEqual(len(bounded.findall("connection")), 4)

    def test_roundabout_definitions_and_all_their_junctions_are_retained(self):
        with tempfile.TemporaryDirectory() as temp:
            source, output = Path(temp) / "source.xml", Path(temp) / "bounded.xml"
            original = self.network()
            ET.SubElement(original, "roundabout", nodes="J C D", edges="cross remote")
            ET.ElementTree(original).write(source)
            module.corridor(source, ["a", "b"], output)
            bounded = ET.parse(output).getroot()
            self.assertEqual(
                ET.tostring(bounded.find("roundabout")), ET.tostring(original.find("roundabout"))
            )
            self.assertEqual(bounded.find("junction[@id='C']").get("type"), "priority")
            self.assertIsNotNone(bounded.find("edge[@id='remote']"))

    def test_unknown_route_and_incomplete_intersection_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            source, output = Path(temp) / "source.xml", Path(temp) / "bounded.xml"
            original = self.network()
            ET.ElementTree(original).write(source)
            with self.assertRaisesRegex(ValueError, "unknown external"):
                module.corridor(source, ["absent"], output)
            original.find("junction[@id='J']").set("incLanes", "missing_0")
            ET.ElementTree(original).write(source)
            with self.assertRaisesRegex(ValueError, "omitted lane"):
                module.corridor(source, ["a", "b"], output)
