// Static browser qualification. Fixtures exercise the interface only; an actual
// accepted case is required for a publishable demonstration receipt.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import {
  readFileSync,
  writeFileSync,
  mkdirSync,
  existsSync,
  readdirSync,
} from "node:fs";
import { resolve, extname } from "node:path";
import { fixtureFiles } from "../../apps/dashboard/tests/demoFixture.ts";

const args = process.argv.slice(2);
const option = (name) => args[args.indexOf(name) + 1];
const fixture = args.includes("--fixture");
const casePath = args.includes("--case") ? resolve(option("--case")) : null;
if (fixture === !!casePath) throw Error("Select --fixture or --case DIRECTORY");
const output = resolve(
  args.includes("--output")
    ? option("--output")
    : ".private/browser-demo/browser-check",
);
if (
  !output.startsWith(resolve(".private") + "\\") &&
  !output.startsWith(resolve("evidence") + "\\")
)
  throw Error("Keep browser evidence private");
mkdirSync(output, { recursive: true });
const files = fixture ? fixtureFiles() : null;
const manifestBytes =
  files?.["manifest.json"] ?? readFileSync(resolve(casePath, "manifest.json"));
const manifest = JSON.parse(manifestBytes);
const asset = (name) => files?.[name] ?? readFileSync(resolve(casePath, name));
const timelines = Object.fromEntries(
  ["charge", "continue"].map((choice) => [
    choice,
    JSON.parse(asset(manifest.files[choice].path)),
  ]),
);
const built = resolve("apps/dashboard/dist-demo");
if (!existsSync(resolve(built, "demo.html"))) throw Error("Build demo first");
let failureMode = "";
const mime = {
  ".js": "text/javascript",
  ".css": "text/css",
  ".html": "text/html",
  ".json": "application/json",
  ".woff2": "font/woff2",
};
const server = createServer((request, response) => {
  try {
    const path = new URL(request.url, "http://localhost").pathname.replace(
      /^\/project\//,
      "/",
    );
    if (path.startsWith("/cases/montreal/")) {
      const name = path.split("/").at(-1);
      if (!/^[a-z][a-z0-9-]*\.json$/.test(name))
        throw Error("Invalid case path");
      if (failureMode === "missing" && name === "map.json") {
        response.writeHead(404).end();
        return;
      }
      const raw =
        failureMode === "corrupt" && name === "charge.json"
          ? Buffer.from("{}")
          : name === "manifest.json"
            ? manifestBytes
            : asset(name);
      response.writeHead(200, { "Content-Type": "application/json" }).end(raw);
      return;
    }
    const local = resolve(built, "." + path);
    if (!local.startsWith(built + "\\") && !local.startsWith(built + "/"))
      throw Error("Invalid static path");
    const raw = readFileSync(local);
    response
      .writeHead(200, {
        "Content-Type": mime[extname(local)] ?? "application/octet-stream",
      })
      .end(raw);
  } catch {
    response.writeHead(404).end();
  }
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const origin = `http://127.0.0.1:${server.address().port}`;
process.env.PLAYWRIGHT_BROWSERS_PATH = resolve(".runtime/playwright-browsers");
const require = createRequire(resolve("qualification/browser/package.json"));
const { chromium, expect } = require("@playwright/test");
const browser = await chromium.launch({ headless: true, channel: "chromium" });
const report = {
  version: "browser-demo-browser-check/1.0.0",
  fixture,
  passed: false,
  manifest_sha256: createHash("sha256").update(manifestBytes).digest("hex"),
  checks: [],
  screenshots: [],
};
function buildInventory(directory, prefix = "") {
  const entries = {};
  for (const item of readdirSync(directory, { withFileTypes: true })) {
    const name = prefix + item.name;
    if (item.isDirectory())
      Object.assign(
        entries,
        buildInventory(resolve(directory, item.name), name + "/"),
      );
    else
      entries[name] = createHash("sha256")
        .update(readFileSync(resolve(directory, item.name)))
        .digest("hex");
  }
  return entries;
}
const buildBefore = buildInventory(built);
const errors = [],
  requests = [];
async function screenshot(page, name) {
  // Leaflet's canvas paints on animation frames; the test clock is held between
  // actions. Flush those frames before inspecting a recorded, paused map.
  await page.clock.runFor(100);
  await page.screenshot({
    path: resolve(output, name + ".png"),
    fullPage: false,
  });
  report.screenshots.push(name + ".png");
}
async function seek(page, t) {
  const slider = page.getByRole("slider", { name: "Recorded trip playhead" });
  const maximum = Number(await slider.getAttribute("max"));
  assert.ok(
    t === 0 || t === maximum,
    "Use real keyboard seeks to a recording boundary",
  );
  await slider.press(t === 0 ? "Home" : "End");
  await expect(
    page.getByRole("slider", { name: "Recorded trip playhead" }),
  ).toHaveValue(String(t));
}
try {
  for (const prefix of ["/", "/project/"]) {
    const context = await browser.newContext({
      viewport: { width: 1600, height: 900 },
      reducedMotion: "reduce",
    });
    const page = await context.newPage();
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("request", (r) => requests.push(r.url()));
    await page.clock.install({ time: new Date("2026-11-17T12:00:00Z") });
    await page.clock.pauseAt(new Date("2026-11-17T12:00:01Z"));
    await page.goto(origin + prefix + "demo.html");
    await expect(
      page.getByRole("button", { name: "Start recorded trip" }),
    ).toBeEnabled();
    assert.equal(await page.locator("input[type=password]").count(), 0);
    assert.equal(
      await page.getByText("Interactive trip", { exact: true }).count(),
      0,
    );
    assert.equal(
      await page.getByText("Recorded SUMO demo", { exact: true }).count(),
      0,
    );
    await page
      .getByRole("button", { name: "About this demonstration", exact: true })
      .click();
    await expect(
      page.getByRole("dialog", { name: "About this demonstration" }),
    ).toBeVisible();
    await page
      .getByRole("dialog", { name: "About this demonstration" })
      .getByRole("button", { name: "Close", exact: true })
      .click();
    await expect(page.locator(".sidebar")).toHaveCount(1);
    await expect(page.locator(".assetbar")).toHaveCount(1);

    await screenshot(
      page,
      prefix === "/" ? "01-introduction" : "project-path-introduction",
    );
    await page.getByRole("button", { name: "Start recorded trip" }).click();
    await expect(
      page.getByRole("combobox", { name: "Playback speed" }),
    ).toBeDisabled();
    await page.clock.runFor(10000);
    await page.getByRole("button", { name: "Pause playback" }).click();
    await screenshot(
      page,
      prefix === "/" ? "01-overview" : "project-path-overview",
    );
    assert.equal(
      await page
        .getByRole("heading", { name: "Source state", exact: true })
        .count(),
      0,
    );
    assert.equal(
      await page
        .getByRole("heading", { name: "Measurement coverage", exact: true })
        .count(),
      0,
    );
    await expect(page.locator(".compact-metrics .metric-card")).toHaveCount(4);
    const metricRows = await page
      .locator(".compact-metrics .metric-card")
      .evaluateAll((cards) =>
        cards.map((card) => Math.round(card.getBoundingClientRect().top)),
      );
    assert.equal(
      new Set(metricRows).size,
      1,
      "Desktop overview metrics must share one row",
    );
    await expect(
      page.getByRole("combobox", { name: "Trend signal", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("combobox", { name: "History window", exact: true }),
    ).toBeVisible();

    const paused = await page
      .getByRole("slider", { name: "Recorded trip playhead" })
      .inputValue();
    await page.clock.runFor(10000);
    assert.equal(
      await page
        .getByRole("slider", { name: "Recorded trip playhead" })
        .inputValue(),
      paused,
    );
    await page.getByRole("button", { name: "Resume playback" }).click();
    await page.clock.runFor((manifest.decision.at - 10) * 1000 + 1000);
    await expect(
      page.getByRole("dialog", { name: "Charging stop recommended" }),
    ).toBeVisible();
    await expect(
      page.getByRole("slider", { name: "Recorded trip playhead" }),
    ).toHaveValue(String(manifest.decision.at));
    await page.clock.fastForward(120000);
    await expect(
      page.getByRole("slider", { name: "Recorded trip playhead" }),
    ).toHaveValue(String(manifest.decision.at));
    await expect(
      page.getByRole("button", { name: "Approve charging stop", exact: true }),
    ).toBeEnabled();
    await expect(
      page.getByRole("button", {
        name: "Continue without charging",
        exact: true,
      }),
    ).toBeEnabled();
    await screenshot(
      page,
      prefix === "/" ? "02-decision" : "project-path-decision",
    );
    await page.keyboard.press("Escape");
    await expect(
      page.getByRole("dialog", { name: "Charging stop recommended" }),
    ).not.toBeVisible();
    await page
      .getByRole("navigation")
      .getByRole("button", { name: "Map", exact: true })
      .click();
    await expect(page.locator(".vehicle-map .leaflet-container")).toHaveCount(
      0,
    ); // host itself owns Leaflet
    assert.ok(await page.locator(".vehicle-map.leaflet-container").count());
    await page
      .getByRole("region", { name: "Vehicle and playback controls" })
      .getByRole("button", { name: "Review charging stop", exact: true })
      .click();
    await page
      .getByRole("button", { name: "Approve charging stop", exact: true })
      .click();
    await expect(
      page.getByRole("dialog", { name: "Charging stop recommended" }),
    ).not.toBeVisible();

    await page
      .getByRole("button", { name: "Pause playback", exact: true })
      .click();
    await page
      .getByRole("navigation")
      .getByRole("button", { name: "Energy Forecast", exact: true })
      .click();
    await expect(page.locator(".route-energy-charge")).toBeVisible();
    await screenshot(
      page,
      prefix === "/" ? "03-driving-forecast" : "project-path-driving-forecast",
    );
    for (const speed of ["0.5", "1", "2", "5", "10", "20"])
      await page
        .getByRole("combobox", { name: "Playback speed" })
        .selectOption(speed);
    const chargeTime = timelines.charge.transfers.find(
      (e) => e.state === "charging",
    ).t;
    await seek(page, 0);
    await page.getByRole("button", { name: "Resume playback" }).click();
    await page.clock.runFor(Math.max(0, ((chargeTime + 2) / 20) * 1000));
    await page.getByRole("button", { name: "Pause playback" }).click();
    await page
      .getByRole("navigation")
      .getByRole("button", { name: "Charging Advisor", exact: true })
      .click();
    await screenshot(
      page,
      prefix === "/" ? "03-charging-advisor" : "project-path-charging",
    );
    await page
      .getByRole("navigation")
      .getByRole("button", { name: "Overview", exact: true })
      .click();
    await expect(page.locator(".forecast-overview")).toContainText(
      "Driving forecast paused during charging",
    );
    await page
      .locator(".recorded-overview-map .route-legend")
      .scrollIntoViewIfNeeded();
    await screenshot(
      page,
      prefix === "/" ? "03-overview-map" : "project-path-overview-map",
    );
    await page.evaluate(() => window.scrollTo(0, 0));
    for (const [tab, filename] of [
      ["Map", "charging-map"],
      ["Energy Forecast", "energy-forecast"],
      ["Measurements", "measurements"],
      ["Fleet", "fleet"],
      ["Activity", "activity"],
      ["System", "system"],
    ]) {
      await page
        .getByRole("navigation")
        .getByRole("button", { name: tab, exact: true })
        .click();
      await expect(
        page.getByRole("heading", {
          name:
            {
              Map: "Route and location",
              Measurements: "All measurements",
              Fleet: "Fleet measurements",
              System: "System health",
            }[tab] ?? tab,
          exact: true,
          level: 1,
        }),
      ).toBeVisible();
      if (tab === "Map")
        await expect(
          page.locator("[data-testid='vehicle-position']"),
        ).toHaveAttribute(
          "aria-label",
          "Recorded vehicle charging at this position",
        );
      if (tab === "Map") {
        assert.equal(
          await page
            .locator(".map-layout")
            .evaluate((el) => getComputedStyle(el).display),
          "grid",
        );
        await expect(
          page.getByRole("heading", { name: "Observed position", exact: true }),
        ).toBeVisible();
        assert.equal(
          await page
            .locator(".route-legend i.observed")
            .evaluate((el) => getComputedStyle(el).borderTopStyle),
          "solid",
        );
        assert.equal(
          await page
            .locator(".route-legend i.planned")
            .evaluate((el) => getComputedStyle(el).borderTopStyle),
          "dashed",
        );
        assert.equal(await page.getByRole("switch").count(), 4);
      }
      if (tab === "Energy Forecast") {
        await expect(
          page.getByRole("region", {
            name: "Observed charging stop",
            exact: true,
          }),
        ).toBeVisible();
        await page
          .getByRole("combobox", { name: "Forecast", exact: true })
          .selectOption("1");
        await expect(page.locator(".route-energy-charge")).toBeVisible();
        await page
          .getByRole("combobox", { name: "Forecast", exact: true })
          .selectOption("current");
      }
      if (tab === "Measurements") {
        await expect(
          page.getByRole("combobox", { name: "Quality", exact: true }),
        ).not.toBeVisible();
        await page.locator(".recorded-measurement-filters > summary").click();
        await page
          .getByRole("combobox", { name: "Quality", exact: true })
          .selectOption("good");
        await expect(
          page.locator(".measurements tbody tr[data-signal]"),
        ).toHaveCount(20);
        await page
          .getByRole("button", { name: "Clear filters", exact: true })
          .click();
        await page.locator(".recorded-measurement-filters > summary").click();
      }
      if (tab === "System") {
        await expect(
          page.getByText("Recent ingest rate", { exact: true }),
        ).toHaveCount(0);
        await expect(
          page.locator(".recorded-architecture"),
        ).not.toHaveAttribute("open", "");
        await page.locator(".recorded-architecture > summary").click();
        await expect(page.locator(".delivery-node")).toHaveCount(7);
        await page.locator(".recorded-architecture > summary").click();
      }
      await screenshot(
        page,
        (prefix === "/" ? "03-" : "project-path-") + filename,
      );
      if (tab === "Map") {
        await page.locator(".route-legend").scrollIntoViewIfNeeded();
        const legendBox = await page.locator(".route-legend").boundingBox(),
          playerBox = await page.locator(".recorded-player").boundingBox();
        assert.ok(
          legendBox.y + legendBox.height <= playerBox.y,
          "Map legend hidden behind playback controls",
        );
        await screenshot(
          page,
          (prefix === "/" ? "03-" : "project-path-") + "map-legend",
        );
        await page.evaluate(() => window.scrollTo(0, 0));
      }
    }
    await page
      .getByRole("navigation")
      .getByRole("button", { name: "History", exact: true })
      .click();
    await page.getByText("Select signals to compare", { exact: false }).click();
    const search = page.getByRole("searchbox");
    await search.fill("battery");
    await expect(
      page.getByRole("checkbox", {
        name: "Compare battery.pack.soc",
        exact: true,
      }),
    ).toBeVisible();
    await search.fill("");
    const signals = page.getByRole("checkbox", { name: /^Compare / });
    assert.equal(await signals.count(), 20);
    for (let i = 0; i < 20; i++) await signals.nth(i).check();
    await page
      .getByRole("heading", { name: "Choose a recorded interval" })
      .click();
    assert.equal(
      await page.locator("details.history-signal-picker[open]").count(),
      0,
    );
    await expect(
      page.getByRole("heading", { name: "Vehicle speed", exact: true }),
    ).toBeVisible();
    assert.equal(await page.locator("main svg[role=img]").count(), 20);
    await page
      .getByRole("button", { name: "Use latest time", exact: true })
      .click();
    await screenshot(
      page,
      prefix === "/" ? "03-history" : "project-path-history",
    );
    const downloadPromise = page.waitForEvent("download");
    await page.locator(".recorded-history-export > summary").click();
    await page
      .getByRole("button", { name: "Download CSV", exact: true })
      .click();
    const downloaded = await downloadPromise;
    await downloaded.saveAs(resolve(output, "history-test.csv"));
    await seek(page, timelines.charge.summary.duration);
    await page
      .getByRole("button", { name: "Compare both outcomes", exact: true })
      .click();
    await expect(page.locator(".recorded-results table")).toBeVisible();
    await screenshot(
      page,
      prefix === "/" ? "04-outcome-comparison" : "project-path-results",
    );
    await page
      .getByRole("button", { name: "Review alternative decision", exact: true })
      .click();
    await expect(
      page.getByRole("dialog", { name: "Charging stop recommended" }),
    ).toBeVisible();
    await page
      .getByRole("button", { name: "Continue without charging", exact: true })
      .click();
    await expect(
      page.getByRole("dialog", { name: "Charging stop recommended" }),
    ).not.toBeVisible();
    await seek(page, timelines.continue.summary.duration);
    await expect(page.locator(".recorded-results")).toContainText("0 kWh");
    await seek(page, 0);
    assert.equal(await page.locator(".recorded-results").count(), 0);
    assert.equal(await page.locator(".recorded-results table").count(), 0);
    await seek(page, timelines.continue.summary.duration);
    await page
      .getByRole("button", { name: "Restart recording", exact: true })
      .click();
    await expect(
      page.getByRole("slider", { name: "Recorded trip playhead" }),
    ).toHaveValue("0");
    await page.clock.fastForward(60000); // long scheduling interruption must not skip the intro
    await expect(
      page.getByRole("slider", { name: "Recorded trip playhead" }),
    ).toHaveValue("0");
    await page.clock.runFor(manifest.decision.at * 1000 + 100);
    await expect(
      page.getByRole("dialog", { name: "Charging stop recommended" }),
    ).toBeVisible();
    await page.setViewportSize({ width: 390, height: 844 });
    await screenshot(
      page,
      prefix === "/" ? "05-narrow-decision" : "project-path-narrow",
    );
    assert.ok(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
      "Narrow layout overflows",
    );
    await page.keyboard.press("Escape");
    for (const width of [390, 768, 1280]) {
      await page.setViewportSize({ width, height: 900 });
      for (const tab of [
        "Overview",
        "Map",
        "Measurements",
        "History",
        "Fleet",
        "Energy Forecast",
        "Activity",
        "System",
        "Charging Advisor",
      ]) {
        if (width <= 720)
          await page
            .getByRole("button", { name: "Open navigation", exact: true })
            .click();
        await page
          .getByRole("navigation")
          .getByRole("button", { name: tab, exact: true })
          .click();
        await page.clock.runFor(100);
        assert.ok(
          await page.evaluate(
            () => document.documentElement.scrollWidth <= innerWidth,
          ),
          `${tab} overflows at ${width}px`,
        );
        const footerOverlap = await page
          .locator(".recorded-player")
          .evaluate((el) => {
            const boxes = [...el.children]
              .filter((child) => child.tagName !== "INPUT")
              .map((child) => child.getBoundingClientRect());
            return boxes.some((a, i) =>
              boxes
                .slice(i + 1)
                .some(
                  (b) =>
                    Math.min(a.right, b.right) > Math.max(a.left, b.left) + 1 &&
                    Math.min(a.bottom, b.bottom) > Math.max(a.top, b.top) + 1,
                ),
            );
          });
        assert.equal(
          footerOverlap,
          false,
          `Playback controls overlap at ${width}px`,
        );
        assert.ok(
          await page
            .getByRole("combobox", { name: "Playback speed" })
            .evaluate((el) => parseFloat(getComputedStyle(el).fontSize) >= 10),
          "Playback speed label must remain readable",
        );
        if (prefix === "/")
          await screenshot(
            page,
            `06-${width}-${tab.toLowerCase().replaceAll(" ", "-")}`,
          );
      }
    }
    await context.close();
    report.checks.push(
      `Both choices, held decision, six rates, 20 charts, CSV, backward seek, reset, desktop/narrow at ${prefix}`,
    );
  }
  const page = await browser.newPage();
  page.on("pageerror", (e) => errors.push(e.message));
  for (const mode of ["missing", "corrupt"]) {
    failureMode = mode;
    await page.goto(origin + "/demo.html");
    await expect(
      page.getByRole("heading", {
        name: "The recorded trip could not be opened",
      }),
    ).toBeVisible();
    failureMode = "";
    await page.getByRole("button", { name: "Try again" }).click();
    await expect(
      page.getByRole("button", { name: "Start recorded trip" }),
    ).toBeEnabled();
  }
  report.checks.push(
    "Missing file and damaged data fail closed and recover on retry",
  );
  assert.deepEqual(errors, []);
  assert.ok(
    requests.every(
      (url) => url.startsWith(origin + "/") && !/\/(v1|v2|api)\//.test(url),
    ),
    "Operational or external request detected",
  );
  report.checks.push(
    "No page errors, sign-in, external data requests, or operational APIs",
  );
  assert.deepEqual(
    buildInventory(built),
    buildBefore,
    "Application changed during browser verification",
  );
  report.build_sha256 = buildBefore;
  report.passed = true;
} catch (error) {
  report.error = String(error);
  throw error;
} finally {
  writeFileSync(
    resolve(output, "browser-report.json"),
    JSON.stringify(report, null, 2),
  );
  await browser.close();
  await new Promise((r) => server.close(r));
  console.log(
    JSON.stringify({
      passed: report.passed,
      fixture,
      checks: report.checks,
      report: resolve(output, "browser-report.json"),
    }),
  );
}
