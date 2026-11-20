// Browser-only launch-page verification. All mutation requests are intercepted;
// this check cannot start a vehicle or change the local runner's state.
import { chromium } from "playwright";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import assert from "node:assert/strict";

const root = process.cwd();
const output =
  process.env.EVDT_LAUNCH_OUTPUT ??
  path.join(root, ".private/design/launch-app");
await mkdir(output, { recursive: true });
const token =
  process.env.EVDT_LAUNCH_KEY ??
  JSON.parse(
    await readFile(path.join(root, ".runtime/launch-app/instance.json")),
  ).token;
const origin = process.env.EVDT_LAUNCH_ORIGIN ?? "http://127.0.0.1:18760";
const initial = await fetch(`${origin}/api/status`, {
  headers: { Authorization: `Bearer ${token}` },
}).then((r) => r.json());
assert.equal(
  initial.run?.active ?? false,
  false,
  "An active run must not be disturbed",
);
assert.equal(initial.trips.length, 10);
assert.ok(
  initial.trips.every((trip) => trip.available_services.includes("energy")),
);
let state = { ...initial, run: null };
let rejectStart = false,
  rejectedStarts = 0,
  dashboardOpens = 0,
  heldStatus,
  releaseStatus;
let started = null,
  stopRequests = 0,
  recoverRequests = 0,
  startRequests = 0,
  folderRequests = 0;
const historyEntries = Array.from({ length: 23 }, (_, index) => ({
  id: `20261113T184819Z-${index.toString(16).padStart(6, "0")}`,
  trip_id: initial.trips[index % 10].id,
  title: initial.trips[index % 10].title,
  services: ["monitoring", "energy", "replay"],
  state:
    index === 1
      ? "stopped_incomplete"
      : index === 2
        ? "blocked_by_host_preflight"
        : "arrived",
  active: false,
  folder_available: true,
  recording_saved: index !== 2,
  cleanup_verified: index !== 2 ? true : null,
  ...(index === 2
    ? { starting_charge_percent: 22.5, initial_charge_percent: 22.5 }
    : {}),
  created: Date.now() / 1000 - index * 3600,
  absolute_path: "C:\\DriveTwin\\evidence\\example-recording",
}));
const errors = [];
const browser = await chromium.launch({
  headless: true,
  executablePath: chromium.executablePath(),
});
try {
  const context = await browser.newContext({
    viewport: { width: 1920, height: 1080 },
    deviceScaleFactor: 1,
  });
  await context.route(`${origin}/api/**`, async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/map" && route.request().method() === "GET") {
      return route.continue();
    } else if (url.pathname === "/api/status" && heldStatus) {
      const delay = heldStatus;
      heldStatus = undefined;
      const body = JSON.stringify(state);
      delay.started();
      await delay.release;
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body,
      });
    } else if (url.pathname === "/api/start") {
      if (rejectStart) {
        rejectedStarts++;
        return route.fulfill({
          status: 400,
          contentType: "application/json",
          body: JSON.stringify({
            error:
              "The selected trip could not start. Review the saved profile.",
          }),
        });
      }
      startRequests++;
      started = route.request().postDataJSON();
      const trip = initial.trips.find((t) => t.id === started.trip_id);
      state.run = {
        id: `browser-fixture-${startRequests}`,
        trip_id: trip.id,
        title: trip.title,
        created: Date.now() / 1000,
        state: "checking",
        active: true,
        services: ["monitoring", ...started.services],
        time_scale: started.time_scale,
        charging_operator: started.charging_operator === true,
        charging_operator_ready: false,
        starting_charge_percent: started.starting_charge_percent,
        initial_charge_percent:
          started.starting_charge_percent ?? trip.initial_charge_percent,
        message: "Checking the host before starting services.",
        cleanup_verified: null,
        output: "evidence/energy-v2/browser-fixture",
        log: ".runtime/launch-app/browser-fixture.log",
        access: {},
        pause: { available: false },
      };
    } else if (url.pathname === "/api/pause") {
      const body = route.request().postDataJSON();
      assert.deepEqual(body, {
        id: state.run.id,
        revision: state.run.pause.revision,
        paused: !state.run.pause.paused,
      });
      state.run.pause = {
        ...state.run.pause,
        revision: body.revision + 1,
        pending: true,
        requested_paused: body.paused,
      };
    } else if (url.pathname === "/api/stop") {
      assert.deepEqual(route.request().postDataJSON(), { id: state.run.id });
      stopRequests++;
      state.run = {
        ...state.run,
        state: "stopped_incomplete",
        active: false,
        cleanup_verified: true,
        recording_saved: true,
        access: {},
        message: "Stopped early. Partial recording retained.",
      };
    } else if (url.pathname === "/api/recover") {
      assert.deepEqual(route.request().postDataJSON(), { id: state.run.id });
      recoverRequests++;
      state.run = { ...state.run, state: "recovering_cleanup" };
    } else if (url.pathname === "/api/history") {
      const filtered = historyEntries
        .filter((entry) =>
          entry.title
            .toLowerCase()
            .includes((url.searchParams.get("search") || "").toLowerCase()),
        )
        .filter((entry) =>
          url.searchParams.get("outcome") === "saved"
            ? entry.recording_saved
            : url.searchParams.get("outcome") === "attention"
              ? entry.state === "blocked_by_host_preflight"
              : true,
        );
      const offset = Number(url.searchParams.get("offset") || "0");
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: filtered.slice(offset, offset + 20),
          total: filtered.length,
          offset,
        }),
      });
    } else if (url.pathname === "/api/open-dashboard") {
      dashboardOpens++;
      assert.equal(route.request().postDataJSON().id, state.run.id);
      if (state.run.charging_operator) {
        assert.equal(state.run.charging_operator_ready, true);
        assert.equal(state.run.access.monitoring_token, undefined);
      } else {
        assert.ok(state.run.access.monitoring_token);
      }
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ opened: true, key_copied: true }),
      });
    } else if (url.pathname === "/api/charging-operator-key") {
      assert.equal(route.request().postDataJSON().id, state.run.id);
      assert.equal(state.run.charging_operator_ready, true);
      return route.fulfill({
        json: { charging_operator_token: "TEST-ONLY-APPROVAL-KEY" },
      });
    } else if (url.pathname === "/api/open-results") {
      folderRequests++;
      assert.ok(
        historyEntries.some(
          (entry) => entry.id === route.request().postDataJSON().id,
        ),
      );
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: '{"opened":true}',
      });
    } else if (url.pathname !== "/api/status") {
      return route.abort();
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(state),
    });
  });
  const page = await context.newPage();
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`${origin}/#${token}`);
  await page.locator("#workspace").waitFor({ state: "visible" });
  assert.equal(
    await page.locator("#launcher-update").isVisible(),
    false,
    "The page must accept the running launch service's API version",
  );
  await page
    .locator("#route-outline")
    .waitFor({ state: "visible", timeout: 50000 });
  assert.equal(
    await page.locator('#route-outline [data-map-layer="stops"]').count(),
    initial.trips[0].stops,
  );
  assert.equal(
    await page.locator('#route-outline [data-map-layer="endpoints"]').count(),
    2,
  );
  assert.equal(await page.locator("#route-outline canvas").count(), 1);
  assert.equal(await page.locator(".trip-choice").count(), 10);
  assert.equal(await page.locator("#trip-speed").inputValue(), "1");
  state.session_metadata_error =
    "DriveTwin cannot read the previous trip's saved status. Cleanup review is required.";
  await page.locator("#launcher-update").waitFor({ state: "visible" });
  assert.equal(await page.locator("#start").isDisabled(), true);
  assert.equal(
    await page.locator("#state").innerText(),
    "Cleanup needs review",
  );
  assert.equal(await page.locator("#idle").isVisible(), false);
  assert.equal(await page.locator("#run").isVisible(), false);
  assert.equal(startRequests, 0);
  await page.screenshot({ path: path.join(output, "saved-status-review.png") });
  state.session_metadata_error = null;
  await page.locator("#launcher-update").waitFor({ state: "hidden" });
  assert.equal(await page.locator("#start").isEnabled(), true);
  const charge = page.locator("#starting-charge");
  const chargeSlider = page.locator("#starting-charge-slider");
  assert.equal(
    await charge.inputValue(),
    String(initial.trips[0].initial_charge_percent),
  );
  await charge.fill("7.5");
  await page
    .locator("#charge-note")
    .filter({ hasText: "below the 10% reserve" })
    .waitFor();
  assert.equal(await chargeSlider.inputValue(), "7.5");
  assert.equal(await page.locator("#launch-charge").innerText(), "7.5%");
  await page.locator("#reset-charge").click();
  assert.equal(
    await charge.inputValue(),
    String(initial.trips[0].initial_charge_percent),
  );
  for (const speed of [0.5, 1, 2, 5, 10, 20]) {
    await page.locator("#trip-speed").selectOption(String(speed));
    for (const [index, trip] of initial.trips.entries()) {
      const choice = page.locator(".trip-choice").nth(index);
      const expected = `≈ ${trip.minutes.toLocaleString("en-US", { maximumFractionDigits: 1 })} min`;
      const real = `≈ ${(trip.minutes / speed).toLocaleString("en-US", { maximumFractionDigits: 1 })} min`;
      assert.deepEqual(await choice.locator(".trip-time").allTextContents(), [
        `Working time${expected}`,
        `Real-life driving${real}`,
      ]);
      await choice.click();
      assert.equal(await page.locator("#duration").textContent(), expected);
      assert.equal(await page.locator("#real-duration").textContent(), real);
      assert.equal(
        await page.locator("#launch-selection").textContent(),
        trip.title,
      );
      assert.equal(
        await page.locator("#launch-working").textContent(),
        expected,
      );
      assert.equal(
        await page.locator("#launch-real-life").textContent(),
        `${real} at ${speed}×`,
      );
      assert.ok(
        (
          await page.locator("#trip-select option").nth(index).textContent()
        ).includes(`Working time ${expected} · Real-life driving ${real}`),
      );
    }
  }
  await page.locator("#trip-speed").selectOption("1");
  await page.locator(".trip-choice").first().click();
  const chargeAdvisor = page.locator("#charging");
  if (initial.trips[0].available_services.includes("charging")) {
    await chargeAdvisor.check();
    assert.match(
      await page.locator("#launch-services").textContent(),
      /Charging Advisor/,
    );
    await page.locator("#energy").uncheck();
    assert.equal(await chargeAdvisor.isDisabled(), true);
    assert.equal(await chargeAdvisor.isChecked(), false);
    await page.locator("#energy").check();
    await chargeAdvisor.check();
    await page.locator("#trip-speed").selectOption("5");
    assert.equal(await chargeAdvisor.isDisabled(), true);
    assert.equal(await chargeAdvisor.isChecked(), false);
    await page.locator("#trip-speed").selectOption("1");
    assert.equal(await chargeAdvisor.isEnabled(), true);
  } else {
    assert.equal(await chargeAdvisor.isDisabled(), true);
  }
  await page.locator("#route-outline canvas").waitFor({ timeout: 50000 });
  assert.match(
    await page.locator(".timing-extra").textContent(),
    /startup.*five minutes.*three minutes/s,
  );
  await page.evaluate(() => scrollTo(0, 0));
  assert.equal(
    new URL(page.url()).hash,
    "",
    "Launch credential removed from address bar",
  );
  await page.screenshot({
    path: path.join(output, "01-launch-desktop.png"),
    fullPage: false,
  });
  await page.locator(".trip-choice").filter({ hasText: "Richmond" }).click();
  await page.locator("#distance").filter({ hasText: "58.98" }).waitFor();
  await page.locator("#route-outline canvas").waitFor({ timeout: 50000 });
  await page.evaluate(() => scrollTo(0, 0));
  await page.screenshot({
    path: path.join(output, "02-longest-trip-desktop.png"),
    fullPage: false,
  });
  await page.getByRole("button", { name: "Ottawa", exact: true }).click();
  assert.equal(await page.locator(".trip-choice").count(), 3);
  await page.locator(".trip-choice").filter({ hasText: "ByWard" }).click();
  assert.equal(await page.locator("#energy").isEnabled(), true);
  await page.locator("#energy").check();
  assert.equal(await page.locator("#energy-reason").isVisible(), false);
  await page.getByRole("button", { name: "All regions", exact: true }).click();
  await page.locator(".trip-choice").first().click();
  await page.locator("#select-all").click();
  await page.locator("#trip-speed").selectOption("5");
  await charge.fill("15.4");
  rejectStart = true;
  await page.locator("#start").click();
  await page.locator("#error").filter({ hasText: "could not start" }).waitFor();
  assert.equal(await page.evaluate(() => document.activeElement.id), "error");
  assert.equal(await page.locator("#run").isVisible(), false);
  assert.equal(dashboardOpens, 0);
  assert.equal(rejectedStarts, 1);
  await page.screenshot({
    path: path.join(output, "08-start-error-desktop.png"),
    fullPage: false,
  });
  rejectStart = false;
  await page.locator("#start").click();
  await page
    .locator("#dashboard-opening")
    .filter({ hasText: "automatically when ready" })
    .waitFor();
  assert.equal(await page.locator("#access").isVisible(), false);
  assert.equal(dashboardOpens, 0, "No dashboard opens before readiness");
  assert.equal(
    await page
      .locator("#run-stages [aria-current='step'] .loading-spinner")
      .count(),
    1,
  );
  // Refresh during startup; the pending run identity survives, without persisting reader keys.
  await page.reload();
  await page
    .locator("#dashboard-opening")
    .filter({ hasText: "automatically when ready" })
    .waitFor();
  state.run.state = "running";
  state.run.pause = {
    available: true,
    revision: 0,
    paused: false,
    pending: false,
  };
  state.run.message = "The bus is running.";
  state.run.access = {
    monitoring_url: "http://127.0.0.1:18765",
    monitoring_token: "TEST-ONLY-MONITORING-KEY",
    replay_url: "http://127.0.0.1:18766",
    replay_token: "TEST-ONLY-REPLAY-KEY",
  };
  await page.locator("#access").waitFor({ state: "visible" });
  await page
    .locator("#dashboard-opening")
    .filter({ hasText: "Paste the copied key into Dashboard key" })
    .waitFor();
  assert.equal(dashboardOpens, 1);
  assert.deepEqual(started.services, ["energy", "replay"]);
  assert.equal(started.time_scale, 5);
  assert.equal(started.starting_charge_percent, 15.4);
  assert.equal(await charge.isDisabled(), true);
  assert.equal(await page.locator("#trip-speed").isDisabled(), true);
  await page.reload();
  await page.locator("#access").waitFor({ state: "visible" });
  assert.equal(
    dashboardOpens,
    1,
    "Refresh does not open another tab or overwrite the clipboard",
  );
  assert.equal(await page.locator("#trip-speed").inputValue(), "5");
  assert.equal(await charge.inputValue(), "15.4");
  assert.equal(await page.locator("#start").isDisabled(), true);
  assert.equal(
    await page.locator("#monitor-key").inputValue(),
    "TEST-ONLY-MONITORING-KEY",
  );
  assert.equal(await page.locator("#replay-key").count(), 0);
  await page.screenshot({
    path: path.join(output, "03-session-controls-desktop.png"),
    fullPage: false,
  });
  await new Promise((started) => {
    heldStatus = {
      started,
      release: new Promise((resolve) => {
        releaseStatus = resolve;
      }),
    };
  });
  await page.getByRole("button", { name: "Pause trip", exact: true }).click();
  await page.getByRole("button", { name: "Pausing…", exact: true }).waitFor();
  const olderPoll = page.waitForResponse(
    (response) => response.url() === `${origin}/api/status`,
  );
  releaseStatus();
  await olderPoll;
  await page.evaluate(
    () => new Promise((resolve) => requestAnimationFrame(resolve)),
  );
  assert.equal(
    await page
      .getByRole("button", { name: "Pausing…", exact: true })
      .isDisabled(),
    true,
  );
  assert.equal(await page.locator("#state").innerText(), "Running");
  state.run.pause = {
    ...state.run.pause,
    pending: false,
    paused: true,
    working_s: 42,
  };
  await page
    .getByRole("button", { name: "Resume trip", exact: true })
    .waitFor();
  assert.equal(await page.locator("#state").innerText(), "Paused");
  await page.getByRole("button", { name: "Resume trip", exact: true }).click();
  await page.getByRole("button", { name: "Resuming…", exact: true }).waitFor();
  state.run.pause = { ...state.run.pause, pending: false, paused: false };
  await page.getByRole("button", { name: "Pause trip", exact: true }).waitFor();
  await page.locator("#stop").click();
  await page.locator("#stop-dialog").waitFor({ state: "visible" });
  assert.equal(stopRequests, 0);
  await page.screenshot({
    path: path.join(output, "05-stop-confirmation-desktop.png"),
    fullPage: false,
  });
  await page.locator("#cancel-stop").click();
  assert.equal(stopRequests, 0);
  await page.locator("#stop").click();
  await page.locator("#confirm-stop").click();
  await page
    .locator("#cleanup")
    .filter({ hasText: "Cleanup verified" })
    .waitFor();
  assert.equal(stopRequests, 1);
  assert.equal(await page.locator("#access").isVisible(), false);
  assert.equal(await page.locator("#start").isEnabled(), true);
  assert.equal(await page.locator("#completed-recording").isVisible(), false);
  const stoppedRun = state.run;
  state.run = {
    ...stoppedRun,
    state: "arrived",
    recording_saved: true,
    message: "Trip complete. Recording saved and services stopped.",
  };
  await page.locator("#completed-recording").waitFor();
  assert.equal(
    await page.locator("#completed-recording").getAttribute("href"),
    "#saved",
  );
  state.run = stoppedRun;
  await page.locator('a.nav[href="#saved"]').click();
  await page
    .locator("#history-count")
    .filter({ hasText: "23 sessions" })
    .waitFor();
  assert.equal(await page.locator(".history-entry").count(), 20);
  await page.locator(".history-location summary").first().click();
  await Promise.all([
    page.waitForResponse(
      (response) =>
        response.url().includes("/api/history?") &&
        response.request().url().includes("refresh=1"),
    ),
    page.locator("#refresh-history").click(),
  ]);
  assert.equal(
    await page.locator(".history-location").first().getAttribute("open"),
    "",
    "Unchanged history refresh keeps the folder details open",
  );
  await page.locator(".history-location summary").first().click();
  await page.evaluate(() => document.getElementById("saved").scrollIntoView());
  await page.screenshot({
    path: path.join(output, "06-saved-sessions-desktop.png"),
    fullPage: false,
  });
  await page.locator("#history-next").click();
  await page.locator("#history-page").filter({ hasText: "21–23" }).waitFor();
  assert.equal(await page.locator(".history-entry").count(), 3);
  await page.locator("#history-prev").click();
  await page.locator("#history-page").filter({ hasText: "1–20" }).waitFor();
  await page.locator("#history-search").fill("Richmond");
  await page
    .locator("#history-count")
    .filter({ hasText: "2 sessions" })
    .waitFor();
  await page.locator("#history-search").fill("");
  await page.locator("#history-outcome").selectOption("attention");
  await page
    .locator("#history-count")
    .filter({ hasText: "1 session" })
    .waitFor();
  assert.equal(
    await page.locator(".history-entry .pill").textContent(),
    "Host check blocked",
  );
  await page.getByRole("button", { name: "Open results folder" }).click();
  assert.equal(folderRequests, 1);
  await page.getByRole("button", { name: "Use these settings" }).click();
  assert.equal(startRequests, 1, "Selecting past settings never starts a run");
  assert.equal(await charge.inputValue(), "22.5");
  assert.equal(
    await page.locator("#trip-title").textContent(),
    historyEntries[2].title,
  );
  await page.locator("#energy").uncheck();
  await page.reload();
  await page.locator("#workspace").waitFor({ state: "visible" });
  assert.equal(
    await page.locator("#trip-title").textContent(),
    historyEntries[2].title,
  );
  assert.equal(
    await page.locator("#energy").isChecked(),
    false,
    "Optional choices persist",
  );
  const preferences = await page.evaluate(() =>
    JSON.parse(localStorage.getItem("drivetwin-launch-preferences")),
  );
  assert.deepEqual(Object.keys(preferences).sort(), [
    "charging",
    "charging_startup_delay_working_s",
    "energy",
    "replay",
    "trip",
  ]);
  assert.equal(preferences.charging_startup_delay_working_s, 0);
  state.launcher_api_version = 0;
  await page.reload();
  await page
    .locator("#launcher-update")
    .filter({ hasText: "needs an update" })
    .waitFor();
  assert.equal(await page.locator("#start").isDisabled(), true);
  assert.equal(startRequests, 1);
  state.launcher_api_version = initial.launcher_api_version;
  await page.reload();
  await page.locator("#workspace").waitFor({ state: "visible" });
  assert.equal(await page.locator("#start").isEnabled(), true);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => scrollTo(0, 0));
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
    true,
  );
  await page.locator("#trip-select").selectOption(initial.trips[9].id);
  await page.screenshot({
    path: path.join(output, "04-small-screen.png"),
    fullPage: false,
  });
  await page.evaluate(() => document.getElementById("saved").scrollIntoView());
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
    true,
  );
  await page.screenshot({
    path: path.join(output, "07-saved-sessions-small-screen.png"),
    fullPage: false,
  });
  await page.setViewportSize({ width: 1440, height: 960 });
  const operatorTrip = initial.trips.find(
    (trip) => trip.charging_operator_available,
  );
  assert.ok(operatorTrip);
  await page
    .locator(".trip-choice")
    .filter({ hasText: operatorTrip.title })
    .click();
  await page.locator("#trip-speed").selectOption("1");
  await page.locator("#energy").check();
  await page.locator("#charging").check();
  await page.locator("#charging-operator").check();
  await page.locator("#start").click();
  await page
    .locator("#dashboard-opening")
    .filter({ hasText: "key with charging approval" })
    .waitFor();
  assert.equal(started.charging_operator, true);
  state.run.state = "starting";
  state.run.access = {
    monitoring_url: "http://127.0.0.1:18765",
    monitoring_token: "TEST-ONLY-EARLY-READER",
  };
  await page
    .locator("#state")
    .filter({ hasText: "Starting services" })
    .waitFor();
  assert.equal(
    dashboardOpens,
    1,
    "Approval session must not auto-open with early reader access",
  );
  assert.equal(await page.locator("#monitor-access").isVisible(), false);
  state.run.state = "running";
  state.run.access = { monitoring_url: "http://127.0.0.1:18765" };
  state.run.charging_operator_ready = true;
  await page
    .locator("#dashboard-key-access")
    .filter({ hasText: "Includes charging approval" })
    .waitFor();
  await page
    .locator("#dashboard-opening")
    .filter({ hasText: "Paste the copied key into Dashboard key" })
    .waitFor();
  assert.equal(dashboardOpens, 2);
  await page.locator("#copy-monitor").click();
  await page.waitForFunction(
    () =>
      document.getElementById("monitor-key").value === "TEST-ONLY-APPROVAL-KEY",
  );
  state.run.state = "history";
  state.run.charging_operator_ready = false;
  state.run.access = {};
  await page.locator("#dashboard-history").waitFor();
  assert.equal(await page.locator("#monitor-key").inputValue(), "");
  state.run = {
    ...state.run,
    active: false,
    state: "cleanup_unverified",
    cleanup_verified: false,
    message: "Cleanup was not verified.",
  };
  const recover = page.getByRole("button", { name: "Recover previous trip" });
  await recover.waitFor();
  assert.equal(await page.locator("#start").isDisabled(), true);
  await recover.click();
  await page
    .locator("#state")
    .filter({ hasText: "Recovering interrupted trip" })
    .waitFor();
  assert.equal(recoverRequests, 1);
  assert.equal(await page.locator("#start").isDisabled(), true);
  const beforeRecovery = startRequests;
  state.run = {
    ...state.run,
    state: "failed_or_incomplete",
    cleanup_verified: true,
    cleanup_recovered: true,
    recording_saved: true,
    message:
      "Interrupted trip recording saved. Cleanup verified. You can start a new trip.",
  };
  await page
    .locator("#cleanup")
    .filter({ hasText: "Cleanup verified" })
    .waitFor();
  assert.equal(await page.locator("#start").isEnabled(), true);
  assert.equal(startRequests, beforeRecovery);
  assert.equal(await page.locator("#state").innerText(), "Recovered");
  assert.deepEqual(errors, []);
  await writeFile(
    path.join(output, "browser-result.json"),
    JSON.stringify(
      {
        passed: true,
        trips: 10,
        live_bus_started: false,
        checks: [
          "damaged saved status visibly blocks start without exposing a guessed run",
          "interrupted trip recovery verifies cleanup and enables a new start",
          "approval trip waits for the single dashboard key with approval",
          "region filters",
          "preset selection",
          "starting charge default, numeric field, synchronized slider, and reset",
          "starting charge override sent and restored with current and saved sessions",
          "working and real-life driving estimates for all ten presets",
          "separate startup, history, and saving time",
          "service compatibility",
          "real route outline",
          "local street map, passenger stops, and endpoints",
          "pause and resume wait for source confirmation",
          "visible reader keys",
          "start request",
          "failed start focuses visible error and opens no dashboard",
          "startup waits for readiness and survives refresh",
          "automatic dashboard open requested once with current run identity",
          "outdated service disables start with reopen instructions",
          "duplicate start disabled",
          "stop request",
          "stop confirmation and cancellation",
          "saved session outcomes, search, filters, and pagination",
          "known result folder request",
          "reuse settings without starting a bus",
          "preference persistence without credentials",
          "credential removal after cleanup",
          "small-screen layout",
          "no browser errors",
        ],
      },
      null,
      2,
    ),
  );
  console.log(
    "Launch page browser checks: PASSED. No bus or container started.",
  );
} finally {
  await browser.close();
}
