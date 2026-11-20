// Render the built UI in the pinned browser against recorded, local fixture data.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../../", import.meta.url));
const require = createRequire(
  path.join(root, "qualification/browser/package.json"),
);
const { chromium } = require("playwright");
const { expect } = require("@playwright/test");
const directory = path.resolve(process.argv[2] ?? "");
assert.match(
  path.relative(path.join(root, "evidence/p6"), directory),
  /^extension-[a-z0-9-]+$/,
);
const dashboard = path.join(directory, "dashboard");
const sha256 = (bytes) => createHash("sha256").update(bytes).digest("hex");
const read = (filename) => JSON.parse(readFileSync(filename, "utf8"));
const report = read(path.join(directory, "qualification.json"));
assert.equal(report.passed && report.worker.cleanup_passed, true);
assert.deepEqual(report.source, report.worker.source_after);
const coolant = "coolant.inlet.temperature";
const snapshots = new Map();
const originalReports = {};
for (const [asset, filename] of [
  ["bus-001", "monitoring-signals.json"],
  ["bus-002", "monitoring-coolant-1.3.0.json"],
]) {
  const catalog = read(path.join(root, "catalog", filename));
  const raw = readFileSync(path.join(directory, `${asset}-originals.json`));
  originalReports[asset] = sha256(raw);
  const { events, passed } = JSON.parse(raw);
  assert.equal(passed, true);
  assert.equal(events.length, report.worker.checks.assets[asset].originals);
  const { body: final } = events.at(-1);
  const items = final.samples.map((sample) => ({
    signal_id: sample.signal_id,
    source_id: final.source_id,
    quality: sample.quality,
    freshness: "stale",
    source_state: "finished",
    age_s: null,
    definition: catalog.signals[sample.signal_id],
    observation: {
      event_id: final.event_id,
      sim_time_s: final.sim_time_s,
      observed_at: final.observed_at,
      received_at: final.observed_at,
      sample,
    },
  }));
  assert.equal(items.length, report.worker.checks.assets[asset].signals);
  snapshots.set(asset, {
    version: "1.0.0",
    run_id: final.run_id,
    asset_id: asset,
    mode: "simulation",
    as_of: final.observed_at,
    cursor: `${final.run_id}:${asset}:${events.length}`,
    items,
  });
}

const browser = await chromium.launch({
  channel: "chromium",
  headless: true,
  timeout: 10000,
});
try {
  const page = await browser.newPage({
    viewport: { width: 1400, height: 960 },
  });
  page.setDefaultTimeout(4000);
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.name));
  const origin = "http://evdt-fixture.test";
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== origin) return route.abort();
    if (url.pathname === "/v1/catalog") {
      return route.fulfill({
        json: { assets: { "bus-001": {}, "bus-002": {} } },
      });
    }
    if (url.pathname === "/v1/stream") {
      const snapshot = snapshots.get(url.searchParams.get("asset_id"));
      assert.ok(snapshot);
      return route.fulfill({
        contentType: "text/event-stream",
        body: `data: ${JSON.stringify(snapshot)}\n\n`,
      });
    }
    const filename =
      url.pathname === "/" ? "index.html" : url.pathname.slice(1);
    if (!/^(index\.html|assets\/[A-Za-z0-9_.-]+)$/.test(filename)) {
      return route.fulfill({ status: 404, body: "Fixture path unavailable" });
    }
    return route.fulfill({ path: path.join(dashboard, filename) });
  });
  await page.goto(origin);
  await page.getByLabel("Reader credential").fill("offline-test");
  await page.getByRole("button", { name: "Connect to monitoring" }).click();
  await page.getByRole("button", { name: "Measurements", exact: true }).click();
  await expect(page.locator("[data-signal]")).toHaveCount(20);
  const signal = page.locator(`[data-signal="${coolant}"]`);
  await expect(signal).toHaveCount(0);
  await page.getByLabel("Vehicle", { exact: true }).selectOption("bus-002");
  await expect(page.locator("[data-signal]")).toHaveCount(21);
  const expected = snapshots
    .get("bus-002")
    .items.find((row) => row.signal_id === coolant);
  await expect(signal).toHaveAttribute(
    "data-event",
    expected.observation.event_id,
  );
  await expect(signal.locator("td").nth(1)).toContainText("K");
  await signal.getByText("Metadata", { exact: true }).click();
  await expect(signal).toContainText("conditioned-coolant-inlet/1.0.0");
  await page.getByLabel("Search measurements").fill(coolant);
  await expect(page.locator("[data-signal]")).toHaveCount(1);
  await page.screenshot({ path: path.join(directory, "coolant-browser.png") });
  await page.getByLabel("Search measurements").fill("");
  await page.getByLabel("Vehicle", { exact: true }).selectOption("bus-001");
  await expect(page.locator("[data-signal]")).toHaveCount(20);
  await expect(signal).toHaveCount(0);
  assert.deepEqual(errors, []);
  writeFileSync(
    path.join(directory, "extension-browser.json"),
    JSON.stringify(
      {
        passed: true,
        scope:
          "Built UI with offline snapshots projected from validated original readings",
        browser_version: browser.version(),
        dashboard_image: report.worker.checks.dashboard_image_id,
        original_reports_sha256: originalReports,
        signals_by_asset: { "bus-001": 20, "bus-002": 21 },
        coolant_event_identity_and_metadata: true,
        asset_switch_clears_coolant: true,
      },
      null,
      2,
    ) + "\n",
    { flag: "wx" },
  );
} finally {
  await browser.close();
}
