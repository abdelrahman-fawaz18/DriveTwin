// Check the exact Pages upload locally or its anonymous public HTTPS deployment.
// No GUI browser, authentication, simulator, or operational services are used.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { createServer } from "node:http";
import { createRequire } from "node:module";
import { readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { resolve, relative, extname } from "node:path";

const args = process.argv.slice(2);
const option = (name) => args[args.indexOf(name) + 1];
if (!args.includes("--bundle"))
  throw Error("Use --bundle PRIVATE_UPLOAD_DIRECTORY");
const bundle = resolve(option("--bundle"));
const privateRoot = resolve(".private/browser-demo");
if (relative(privateRoot, bundle).startsWith("..") || bundle === privateRoot)
  throw Error("Use the prepared private upload directory");
const site = resolve(bundle, "site");
const receipt = JSON.parse(
  readFileSync(resolve(bundle, "upload-receipt.json")),
);
assert.equal(receipt.version, "browser-demo-cloudflare-upload/1.0.0");
const sha = (raw) => createHash("sha256").update(raw).digest("hex");
for (const [name, digest] of Object.entries(receipt.files_sha256)) {
  assert.ok(!relative(site, resolve(site, name)).startsWith(".."));
  assert.equal(sha(readFileSync(resolve(site, name))), digest, name);
}
assert.equal(
  sha(readFileSync(resolve(bundle, "drivetwin-demo.zip"))),
  receipt.archive_sha256,
);
const manifest = JSON.parse(
  readFileSync(resolve(site, "cases/montreal/manifest.json")),
);
const charge = JSON.parse(
  readFileSync(resolve(site, "cases/montreal", manifest.files.charge.path)),
);
const output = resolve(bundle, "checks-" + Date.now());
mkdirSync(output);
const report = {
  version: "browser-demo-hosting-check/1.0.0",
  mode: args.includes("--url") ? "public-https" : "local-pages-behavior",
  archive_sha256: receipt.archive_sha256,
  passed: false,
  checks: [],
};

// This small local harness implements only the Pages behaviors used by this
// bundle: extensionless HTML, declared headers, and a genuine not-found page.
// A local pass is never represented as verification of Cloudflare itself.
const headerRules = [];
for (const line of readFileSync(resolve(site, "_headers"), "utf8").split(
  /\r?\n/,
)) {
  if (!line.trim() || line.trim().startsWith("#")) continue;
  if (!/^\s/.test(line)) {
    headerRules.push({ pattern: line.trim(), headers: {} });
  } else {
    const colon = line.indexOf(":");
    assert.ok(colon > 0 && headerRules.length, "Invalid header rule");
    headerRules.at(-1).headers[line.slice(0, colon).trim().toLowerCase()] = line
      .slice(colon + 1)
      .trim();
  }
}
function headersFor(path) {
  return Object.assign(
    {},
    ...headerRules
      .filter(({ pattern }) =>
        pattern.endsWith("*")
          ? path.startsWith(pattern.slice(0, -1))
          : path === pattern,
      )
      .map(({ headers }) => headers),
  );
}
let server, origin, browser;
const errors = [],
  requests = [];
try {
  if (args.includes("--url")) {
    const url = new URL(option("--url"));
    assert.ok(
      url.protocol === "https:" &&
        url.pathname === "/" &&
        !url.username &&
        !url.password &&
        !url.search &&
        !url.hash,
      "Supply the public HTTPS site root",
    );
    origin = url.origin;
  } else {
    server = createServer((request, response) => {
      const path = new URL(request.url, "http://localhost").pathname;
      const headers = headersFor(path);
      if (path.endsWith(".html") && path !== "/404.html") {
        response
          .writeHead(301, {
            ...headers,
            Location: path === "/index.html" ? "/" : path.slice(0, -5),
          })
          .end();
        return;
      }
      const requested = path === "/" ? "index.html" : path.slice(1);
      const name = Object.hasOwn(receipt.files_sha256, requested + ".html")
        ? requested + ".html"
        : requested;
      const exists =
        name !== "_headers" && Object.hasOwn(receipt.files_sha256, name);
      const local = resolve(site, exists ? name : "404.html");
      const mime = {
        ".js": "text/javascript",
        ".css": "text/css",
        ".json": "application/json",
        ".html": "text/html",
        ".woff2": "font/woff2",
        ".txt": "text/plain",
      };
      response.writeHead(exists ? 200 : 404, {
        ...headers,
        "Content-Type": mime[extname(local)] ?? "application/octet-stream",
      });
      response.end(readFileSync(local));
    });
    await new Promise((done) => server.listen(0, "127.0.0.1", done));
    origin = `http://127.0.0.1:${server.address().port}`;
  }
  report.url = origin;
  async function get(path) {
    let url = new URL(path, origin);
    for (let i = 0; i < 6; i++) {
      assert.equal(url.origin, origin, "Unexpected cross-origin redirect");
      const response = await fetch(url, {
        redirect: "manual",
        signal: AbortSignal.timeout(20000),
      });
      if ([301, 302, 303, 307, 308].includes(response.status)) {
        url = new URL(response.headers.get("location"), url);
        continue;
      }
      return response;
    }
    throw Error("Too many redirects");
  }
  for (const [name, digest] of Object.entries(receipt.files_sha256)) {
    if (name === "_headers") continue; // Pages consumes rather than serves this file.
    const response = await get("/" + name);
    assert.equal(response.status, 200, name);
    assert.equal(sha(Buffer.from(await response.arrayBuffer())), digest, name);
    for (const [header, expected] of Object.entries(
      headersFor(new URL(response.url).pathname),
    ))
      assert.equal(
        response.headers.get(header),
        expected,
        `${name}: ${header}`,
      );
  }
  assert.equal(
    sha(Buffer.from(await (await get("/")).arrayBuffer())),
    receipt.files_sha256["index.html"],
  );
  for (const path of [
    "/missing-demo-page",
    "/cases/montreal/missing.json",
    "/_headers",
  ])
    assert.equal((await get(path)).status, 404, path);
  report.checks.push(
    "All deployed files match the upload fingerprints; root, HTML redirects, headers, JSON cache policy, and real 404s checked",
  );

  process.env.PLAYWRIGHT_BROWSERS_PATH = resolve(
    ".runtime/playwright-browsers",
  );
  const require = createRequire(resolve("qualification/browser/package.json"));
  const { chromium, expect } = require("@playwright/test");
  browser = await chromium.launch({ headless: true, channel: "chromium" });
  for (const choice of ["charge", "continue"]) {
    const context = await browser.newContext({
      viewport: { width: 1600, height: 900 },
      reducedMotion: "reduce",
    });
    const page = await context.newPage();
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("console", (message) => {
      if (
        message.type() === "error" &&
        /content security|violates|refused to/i.test(message.text())
      )
        errors.push(message.text());
    });
    page.on("request", (request) => requests.push(request.url()));
    await page.clock.install({ time: new Date("2026-11-17T12:00:00Z") });
    await page.clock.pauseAt(new Date("2026-11-17T12:00:01Z"));
    await page.goto(origin + "/");
    await expect(
      page.getByRole("button", { name: "Start recorded trip" }),
    ).toBeEnabled();
    assert.equal(await page.locator("input[type=password]").count(), 0);
    await page.getByRole("button", { name: "Start recorded trip" }).click();
    await page.clock.runFor(manifest.decision.at * 1000 + 1000);
    await expect(
      page.getByRole("dialog", { name: "Charging stop recommended" }),
    ).toBeVisible();
    await page.clock.runFor(120000);
    await expect(
      page.getByRole("button", { name: "Approve charging stop", exact: true }),
    ).toBeEnabled();
    await expect(
      page.getByRole("button", {
        name: "Continue without charging",
        exact: true,
      }),
    ).toBeEnabled();
    await page
      .getByRole("button", {
        name:
          choice === "charge"
            ? "Approve charging stop"
            : "Continue without charging",
        exact: true,
      })
      .click();
    await page
      .getByRole("button", { name: "Pause playback", exact: true })
      .click();
    if (choice === "charge") {
      await page
        .getByRole("combobox", { name: "Playback speed" })
        .selectOption("20");
      await page
        .getByRole("slider", { name: "Recorded trip playhead" })
        .press("Home");
      await page
        .getByRole("button", { name: "Resume playback", exact: true })
        .click();
      const charging = charge.transfers.find(
        (transfer) => transfer.state === "charging",
      ).t;
      await page.clock.runFor(((charging + 3) / 20) * 1000);
      await page
        .getByRole("button", { name: "Pause playback", exact: true })
        .click();
      for (const tab of [
        "Map",
        "Measurements",
        "History",
        "Energy Forecast",
        "Charging Advisor",
        "Fleet",
        "Activity",
        "System",
        "Overview",
      ]) {
        await page
          .getByRole("navigation")
          .getByRole("button", { name: tab, exact: true })
          .click();
        await expect(page.locator("main h1")).toBeVisible();
        // Flush Leaflet's canvas frame before a subsequent tab unmount. The
        // virtual playback clock otherwise holds a queued redraw indefinitely.
        await page.clock.runFor(100);
      }
      await page
        .getByRole("navigation")
        .getByRole("button", { name: "Map", exact: true })
        .click();
      await expect(
        page.locator("[data-testid='vehicle-position']"),
      ).toHaveAttribute(
        "aria-label",
        "Recorded vehicle charging at this position",
      );
      await page.clock.runFor(100);
      await page.screenshot({ path: resolve(output, "charging-map.png") });
    }
    await page
      .getByRole("slider", { name: "Recorded trip playhead" })
      .press("End");
    await expect(page.locator(".recorded-results")).toBeVisible();
    if (choice === "continue")
      await expect(page.locator(".recorded-results")).toContainText("0 kWh");
    await page.screenshot({ path: resolve(output, choice + "-finished.png") });
    await page.setViewportSize({ width: 390, height: 844 });
    assert.ok(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
      "Narrow layout overflow",
    );
    await context.close();
  }
  assert.deepEqual(errors, []);
  assert.ok(
    requests.every(
      (url) => url.startsWith(origin + "/") && !/\/(v1|v2|api)\//.test(url),
    ),
    "Unexpected external or operational request",
  );
  report.checks.push(
    "Anonymous isolated sessions: both decisions held at 30 seconds, measured charging, nine tabs, both endings, narrow layout, no CSP/script errors or external/service requests",
  );
  report.passed = true;
} catch (error) {
  report.error = String(error);
  throw error;
} finally {
  writeFileSync(
    resolve(output, "hosting-report.json"),
    JSON.stringify(report, null, 2) + "\n",
  );
  await browser?.close();
  if (server) await new Promise((done) => server.close(done));
  console.log(
    JSON.stringify({
      passed: report.passed,
      mode: report.mode,
      report: resolve(output, "hosting-report.json"),
    }),
  );
}
