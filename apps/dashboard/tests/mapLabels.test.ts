import assert from "node:assert/strict";
import { test } from "node:test";
import {
  layoutMapLabels,
  type MapLabelCandidate,
} from "../src/features/map/mapLabelLayout.ts";

const label = (
  key: string,
  x: number,
  y: number,
  width = 120,
  priority = 1,
): MapLabelCandidate => ({ key, x, y, width, height: 20, priority });
test("map labels use their full pixel width and keep higher-priority names", () => {
  const result = layoutMapLabels(
    [
      label("road", 160, 100),
      label("place", 190, 100, 140, 0),
      label("separate", 360, 100),
    ],
    500,
    250,
  );
  assert.deepEqual(
    result.map((c) => c.key),
    ["place", "separate"],
  );
  assert.equal(
    layoutMapLabels([label("one", 150, 100), label("two", 200, 100)], 400, 250)
      .length,
    1,
  );
});
test("label placement is recalculated for a new zoom, including duplicate road fragments", () => {
  const close = [label("A", 120, 80, 120, 0), label("B", 150, 80)];
  const wide = [
    label("A", 80, 80, 120, 0),
    label("B", 250, 80),
    label("B", 350, 80),
  ];
  assert.deepEqual(
    new Set(layoutMapLabels(close, 450, 200).map((c) => c.key)),
    new Set(["A"]),
  );
  const selected = layoutMapLabels(wide, 450, 200);
  assert.equal(selected.length, 2);
  assert.equal(selected.filter((c) => c.key === "B").length, 1);
});
test("labels avoid controls, clipped edges, and invalid or hidden map sizes", () => {
  assert.deepEqual(
    layoutMapLabels(
      [
        label("left", 5, 80),
        label("control", 120, 100),
        label("clear", 300, 100),
      ],
      450,
      220,
      [{ left: 90, top: 80, right: 150, bottom: 120 }],
    ).map((c) => c.key),
    ["clear"],
  );
  assert.equal(layoutMapLabels([label("hidden", 100, 100)], 0, 0).length, 0);
  assert.equal(
    layoutMapLabels([label("invalid", NaN, 100)], 400, 200).length,
    0,
  );
});
