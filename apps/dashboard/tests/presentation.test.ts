import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import { signalGroup, tabs } from "../src/app/presentation.ts";

const root = fileURLToPath(new URL("../../..", import.meta.url));

test("all twenty registered measurements have an assigned group; extensions remain visible", () => {
  const catalog = JSON.parse(
    readFileSync(path.join(root, "catalog/monitoring-signals.json"), "utf8"),
  );
  const groups: Record<string, number> = {};
  for (const id of Object.keys(catalog.signals)) {
    const group = signalGroup(id);
    assert.notEqual(group, "Additional measurements");
    groups[group] = (groups[group] ?? 0) + 1;
  }
  assert.deepEqual(groups, {
    "Navigation and motion": 6,
    Battery: 6,
    Drivetrain: 3,
    "Environment and loads": 3,
    "Position quality and braking": 2,
  });
  assert.equal(signalGroup("future.sensor.value"), "Additional measurements");
  assert.equal(new Set(tabs).size, 9);
});

test("monitoring and replay entry points cannot import sample-data preview modules", () => {
  const visited = new Set<string>();
  function visit(file: string) {
    if (visited.has(file)) return;
    visited.add(file);
    assert.ok(
      !file.split(path.sep).includes("preview"),
      `Preview leaked into production: ${file}`,
    );
    const text = readFileSync(file, "utf8");
    for (const match of text.matchAll(
      /(?:from\s*|import\s*|@import\s*)["'](\.[^"']+)["']/g,
    )) {
      const child = path.resolve(path.dirname(file), match[1]!.split("?")[0]!);
      if (/\.(tsx?|css)$/.test(child)) visit(child);
    }
  }
  for (const page of ["index.html", "replay.html"]) {
    const html = readFileSync(path.join(root, "apps/dashboard", page), "utf8");
    const entry = html.match(/<script\b[^>]*\bsrc="(\/src\/[^"]+)"/);
    assert.ok(entry, `Missing application entry: ${page}`);
    visit(path.join(root, "apps/dashboard", entry[1]!.slice(1)));
  }
  assert.ok(visited.size > 20, "Entry-point graph was not traversed");
});
