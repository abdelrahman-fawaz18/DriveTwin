import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
const control = process.env.EVDT_DASHBOARD_CONTROL;
const fixture = JSON.parse(
  fs.readFileSync(path.join(control, "ready.json"), "utf8"),
);
const signal = (name) => fs.writeFileSync(path.join(control, name), "ready");
const marker = async (name) =>
  expect
    .poll(() => fs.existsSync(path.join(control, name)), { timeout: 60_000 })
    .toBe(true);
async function login(page) {
  await page.goto("/");
  await page.getByLabel("Dashboard key").fill(fixture.token);
  await page.getByRole("button", { name: "Connect to monitoring" }).click();
  await page.getByRole("button", { name: "Energy", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Current forecast", exact: true }),
  ).toBeVisible();
}
test("complete advisory details, road assessments, unavailable cases and legitimate recovery", async ({
  page,
}) => {
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await login(page);
  signal("ready");
  await expect(page.getByTestId("energy-estimate")).toBeVisible({
    timeout: 45_000,
  });
  await expect(page.getByTestId("forecast-input-age")).toContainText("seconds");
  await expect(page.getByTestId("forecast-horizon")).toContainText("min");
  await expect(page.getByTestId("conditional-range")).toContainText("km");
  await expect(page.getByTestId("conditional-time")).toContainText("min");
  await expect(page.getByTestId("forecast-assumptions")).toContainText(
    "350 kWh",
  );
  await expect(
    page
      .getByRole("table", { name: "Station assessments" })
      .locator("tbody tr"),
  ).toHaveCount(2);
  await expect(
    page.getByRole("table", { name: "Station assessments" }),
  ).toContainText("reachable under assumptions");
  signal("available-seen");
  await page.screenshot({
    path: path.join(process.env.EVDT_BROWSER_OUTPUT, "available.png"),
    fullPage: true,
  });
  await expect(page.getByTestId("energy-estimate")).toHaveCount(0, {
    timeout: 12_000,
  });
  await expect(
    page.getByRole("table", { name: "Station assessments" }),
  ).toHaveCount(0);
  await expect(page.locator(".energy-panel tbody tr")).toHaveCount(1);
  await expect(page.locator(".energy-panel tbody")).toContainText("advisory");
  signal("stale-seen");
  await page.route("**/v1/energy-forecasts?*", (route) => route.abort());
  await expect(
    page.getByText(
      "Forecast connection unavailable. Saved history may be out of date.",
    ),
  ).toBeVisible();
  await page.unroute("**/v1/energy-forecasts?*");
  await expect(
    page.getByText(
      "Forecast connection unavailable. Saved history may be out of date.",
    ),
  ).toHaveCount(0);
  await expect(page.getByTestId("energy-estimate")).toHaveCount(0);
  await marker("missing-ready");
  await page.getByLabel("Vehicle", { exact: true }).selectOption("bus-002");
  await expect(
    page.getByText("Forecast unavailable — required observation invalid", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(page.getByTestId("energy-estimate")).toHaveCount(0);
  await expect(page.locator(".energy-panel tbody tr")).toHaveCount(0);
  signal("missing-seen");
  await marker("domain-ready");
  await expect(
    page.getByText(
      "Forecast unavailable — prediction cutoff outside evaluated domain",
      { exact: true },
    ),
  ).toBeVisible();
  await page.screenshot({
    path: path.join(process.env.EVDT_BROWSER_OUTPUT, "unavailable.png"),
    fullPage: true,
  });
  signal("domain-seen");
  await marker("new-run-ready");
  await login(page);
  await expect(page.locator(".energy-panel tbody tr")).toHaveCount(0);
  signal("recovery-browser-ready");
  await expect(page.getByTestId("energy-estimate")).toBeVisible({
    timeout: 45_000,
  });
  await expect(
    page
      .getByRole("table", { name: "Station assessments" })
      .locator("tbody tr"),
  ).toHaveCount(2);
  signal("recovery-seen");
  expect(errors).toEqual([]);
  fs.writeFileSync(
    path.join(process.env.EVDT_BROWSER_OUTPUT, "result.json"),
    JSON.stringify({
      passed: true,
      input_age_horizon_assumptions_displayed: true,
      conditional_range_time_displayed: true,
      two_station_assessments_displayed: true,
      stale_and_disconnected_values_hidden: true,
      immutable_history_visible: true,
      missing_required_input_visible: true,
      unsupported_cutoff_visible: true,
      connection_recovery_passed: true,
      new_registered_run_recovery_passed: true,
      asset_and_run_state_cleared: true,
      page_errors: 0,
    }),
  );
});
