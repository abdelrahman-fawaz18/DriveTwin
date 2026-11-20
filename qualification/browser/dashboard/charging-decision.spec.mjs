import { expect, test } from '@playwright/test';

test('charging alerts work across tabs and safely reuse the signed-in decision controls', async ({ page }) => {
  const run = '80bac57f-2aa0-476c-8c43-7d91d5ad4fb3';
  const asset = 'bus-001';
  const candidate = 'a'.repeat(64);
  const stamp = new Date().toISOString();
  const position = (signal_id, value) => ({
    signal_id, source_id: 'sumo-adapter', quality: 'good', freshness: 'fresh', source_state: 'running', age_s: 0,
    definition: { unit: 'deg', component_id: 'navigation', physical_location: 'Vehicle',
      classification: 'sensor', provenance: 'simulated-observation', sample_period_s: 1, freshness_s: 5 },
    observation: { event_id: '1', sim_time_s: 10, observed_at: stamp, received_at: stamp,
      sample: { value, provenance: 'simulated-observation' } },
  });
  const snapshot = {
    version: '1.0.0', mode: 'simulation', run_id: run, asset_id: asset,
    as_of: stamp, cursor: `${run}:${asset}:1`,
    items: [position('location.latitude', 45.503), position('location.longitude', -73.577)],
  };
  await page.addInitScript((snapshot) => {
    window.testNotificationTones = 0;
    const createOscillator = AudioContext.prototype.createOscillator;
    AudioContext.prototype.createOscillator = function () {
      window.testNotificationTones += 1;
      return createOscillator.call(this);
    };
    const fetch = window.fetch.bind(window);
    window.fetch = (input, init) => {
      if (!String(input).startsWith('/v1/stream?')) return fetch(input, init);
      if (window.testMonitoringUnavailable) return Promise.resolve(new Response(null, { status: 409 }));
      return Promise.resolve(new Response(new ReadableStream({
        start(controller) {
          const write = () => {
            const next = window.testMonitoringEnded ? { ...snapshot, monitoring_closed: true } : snapshot;
            controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(next)}\n\n`));
          };
          write();
          const timer = setInterval(write, 1000);
          init.signal.addEventListener('abort', () => {
            clearInterval(timer);
            controller.close();
          }, { once: true });
        },
      })));
    };
  }, snapshot);
  let available = false;
  let bay = 'Bay 2';
  let needsCharge = false;
  let forecastReads = 0;
  let crossingJunction = false;
  let stopped = false;
  let command = null;
  let observed = null;
  let digest = candidate;
  let target = 0.125;
  let fallbackExpiry;
  let continuing = false;
  const approvals = [];
  await page.route('**/v1/catalog', (route) => route.fulfill({ json: { assets: { [asset]: {} } } }));
  await page.route('**/v1/snapshot?*', (route) => route.fulfill({ json: snapshot }));
  await page.route('**/v1/map-context?*', (route) => route.fulfill({ json: {
    type: 'FeatureCollection', context_version: '1.0.0', run_id: run, asset_id: asset,
    mission_id: 'review', bbox: [-73.58, 45.5, -73.57, 45.51],
    attribution: 'Fixture', input_sha256: {},
    features: ['Bay 2', 'Bay 3'].map((id, i) => ({
      type: 'Feature', properties: { layer: 'stations', id, name: `Station ${i + 2}` },
      geometry: { type: 'Point', coordinates: [-73.58 + i * .003, 45.5 + i * .003] },
    })),
  } }));
  await page.route('**/v2/energy-forecasts?*', (route) => {
    forecastReads++;
    const created = new Date().toISOString();
    const charge = needsCharge ? 0.09 : 0.13;
    const ledger = Object.fromEntries([
      'initial_energy_j', 'final_energy_j', 'minimum_energy_j', 'capacity_j', 'battery_draw_j',
      'battery_recovery_j', 'net_battery_j', 'acceleration_work_j', 'grade_work_j',
      'rolling_work_j', 'air_work_j', 'propulsion_required_j', 'auxiliary_required_j',
      'regeneration_potential_j', 'regeneration_used_j', 'rejected_regeneration_j',
      'unserved_energy_j', 'drive_conversion_loss_j', 'braking_to_heat_j', 'energy_balance_error_j',
    ].map((field) => [field, 0]));
    const current = {
      version: 'route-aware-result/2.0.0', model_version: 'route-aware-energy/2.0.0-dev',
      input_sha256: candidate, route_sha256: candidate, vehicle_sha256: candidate,
      scope: { run_id: run, asset_id: asset, source_id: 'adapter', source_epoch: 1, session_id: 'demo' },
      generation: forecastReads, cutoff_working_s: 10, requested_real_life_s: Date.now() / 1000,
      status: 'available', reason: null, uncertainty_method: 'not_established',
      summary: {
        remaining_distance_m: 1400, duration_working_s: 200, arrival_working_s: 210,
        arrival_soc: charge, minimum_soc: charge,
        ledger: { ...ledger, reserve_breached: needsCharge, motion_feasible: true, assumptions: [] },
      },
    };
    return route.fulfill({
      json: {
        version: 'route-energy-view/2.0.0', run_id: run, asset_id: asset, configured: true,
        as_of_real_life_s: Date.now() / 1000, status: 'available', reason: null,
        current, current_fence: forecastReads, working_age_s: 0, real_life_age_s: 0,
        lifecycle: null, next: null, comparisons: [],
        comparison_status: { status: 'insufficient_data', intervals: 0, threshold_j: 900000 },
        automatic_dispatch_approved: false,
        items: [{ fence: forecastReads, created_at: created, payload: current, classification: 'current', trigger: 'periodic', reserve_soc: 0.10 }],
      },
    });
  });
  await page.route('**/v2/charging-advisory?*', (route) => route.fulfill({
    json: {
      configured: true, operator_control: true, can_respond: true,
      status: available ? 'provisional_plan' : continuing ? 'continue' : 'historical',
      observed_effect: observed,
      estimate: available ? {
        bay_id: bay, job_fence: 9, candidate_sha256: digest, station_state: 'available',
        target_soc: target, minimum_soc: 0.107, wait_working_s: 10, charging_working_s: 90,
        arrival_working_s: 520, crossing_working_s: 402,
        fallback_bay_id: fallbackExpiry === undefined ? null : 'Bay 3',
        fallback_sha256: fallbackExpiry === undefined ? undefined : 'c'.repeat(64),
        fallback_station_state: 'available',
        fallback_details: fallbackExpiry === undefined ? undefined : {
          target_soc: 0.13, minimum_soc: 0.105, wait_working_s: 15,
          charging_working_s: 100, arrival_working_s: 540, working_expires: fallbackExpiry,
        },
        expires_in_real_life_s: 7, working_expires: 50,
        approval_waiting_for_road: crossingJunction,
        approval_waiting_for_motion: stopped,
      } : null,
    },
  }));
  await page.route('**/v2/operator/**/charging/*', (route) => {
    expect(route.request().headers().authorization).toBe('Bearer demo-operator');
    if (route.request().method() === 'POST') {
      const body = route.request().postDataJSON();
      approvals.push(body);
      command = { state: 'approved', command_id: body.command_id };
    }
    return route.fulfill({ json: command ?? { state: 'idle', command_id: null } });
  });
  await page.goto('/');
  await page.getByLabel('Dashboard key').fill('demo-operator');
  await page.getByRole('button', { name: 'Connect to monitoring' }).click();
  await page.getByRole('button', { name: 'Fleet', exact: true }).click();
  await expect(page.locator('[data-fleet-asset]')).toHaveAttribute('data-connection', 'live');
  await expect(page.getByLabel('Estimated time to arrival')).toHaveText('Arrival in 3 min 20 s working time');
  const sounds = page.getByRole('button', { name: 'Notification sounds' });
  await expect(sounds).toHaveAttribute('aria-pressed', 'true');
  await sounds.click();
  await expect(sounds).toHaveAttribute('aria-pressed', 'false');

  needsCharge = true;
  const warning = page.getByRole('alertdialog', { name: 'Battery reserve warning' });
  await expect(warning).toHaveAccessibleDescription('Predicted lowest charge is 9.0%, below the 10% reserve.');
  expect(await page.evaluate(() => window.testNotificationTones)).toBe(0);
  await expect(warning.getByRole('button', { name: 'View Energy Forecast' })).toBeVisible();
  await expect(warning.getByRole('button', { name: 'Approve charging stop' })).toHaveCount(0);
  await page.keyboard.press('Escape');
  await expect(warning).toHaveCount(0);
  const lastRead = forecastReads;
  await expect.poll(() => forecastReads).toBeGreaterThan(lastRead + 1);
  await expect(warning).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Reserve warning' })).toBeVisible();
  await sounds.click();
  await expect(sounds).toHaveAttribute('aria-pressed', 'true');

  fallbackExpiry = 9;
  available = true;
  const recommendation = page.getByRole('alertdialog', { name: 'Charging stop recommended' });
  await expect(recommendation.getByRole('button', { name: 'Approve charging stop' })).toBeEnabled();
  const charger = recommendation.getByRole('combobox', { name: 'Charging stop' });
  const approveStop = recommendation.getByRole('button', { name: 'Approve charging stop' });
  await expect(recommendation.getByText('40 working s', { exact: true })).toBeVisible();
  await charger.selectOption('Bay 3');
  await expect(recommendation.getByText('Refreshing station availability')).toBeVisible();
  await expect(approveStop).toBeDisabled();
  expect(approvals).toHaveLength(0);
  fallbackExpiry = 25;
  await expect(recommendation.getByText('15 working s', { exact: true })).toBeVisible();
  await expect(approveStop).toBeEnabled();
  await charger.selectOption('Bay 2');
  fallbackExpiry = undefined;
  await expect.poll(() => page.evaluate(() => window.testNotificationTones)).toBe(2);
  await expect(page.locator('nav button[aria-current="page"]')).toHaveText('Fleet');
  await recommendation.getByRole('button', { name: 'Keep driving' }).click();
  await expect(recommendation).toHaveCount(0);
  expect(approvals).toHaveLength(0);
  await page.getByRole('button', { name: 'Activity', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Trip decisions and observations' }).getByText('Chose to keep driving · Bay 2')).toBeVisible();
  await page.getByRole('button', { name: 'Review charging stop' }).click();
  await expect(recommendation.getByText('Continuing on the planned route.')).toBeVisible();
  await recommendation.getByRole('button', { name: 'Show on map' }).click();
  await expect(page.getByRole('region', { name: 'Charging stop under review' }).getByRole('heading', { name: 'Station 2 · Bay 2' })).toBeVisible();
  await expect(page.locator('[data-charging-marker=preferred]')).toBeVisible();
  await page.getByRole('button', { name: 'Return to charging decision' }).click();
  await expect(recommendation.getByText('Continuing on the planned route.')).toBeVisible();
  await recommendation.getByRole('button', { name: 'Hide alert' }).click();
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('button', { name: 'Charging Advisor' }).click();
  const advisor = page.getByRole('region', { name: 'Charging Advisor' });
  await expect(advisor.getByText('Recommended charging stop')).toBeVisible();
  await expect(advisor.locator('.charge-summary').getByText('12.5%', { exact: true })).toBeVisible();
  await expect(page.locator('input[type=password]')).toHaveCount(0);
  await expect(advisor.getByText('Continuing on the planned route.')).toBeVisible();
  expect(approvals).toHaveLength(0);
  await advisor.getByRole('button', { name: 'Review stop again' }).click();
  await expect(advisor.getByRole('button', { name: 'Approve charging stop' })).toBeEnabled();
  crossingJunction = true;
  await expect(advisor.getByText('Bus crossing a junction')).toBeVisible();
  await expect(advisor.getByRole('button', { name: 'Approve charging stop' })).toBeEnabled();
  await expect(advisor.getByText('Recommended charging stop')).toBeVisible();
  crossingJunction = false;
  stopped = true;
  await expect(advisor.getByText('Waiting for the bus to move')).toBeVisible();
  await expect(advisor.getByRole('button', { name: 'Approve charging stop' })).toBeEnabled();
  stopped = false;
  await expect(advisor.getByRole('button', { name: 'Approve charging stop' })).toBeEnabled();
  await page.screenshot({ path: test.info().outputPath('charging-decision-desktop.png') });

  available = false;
  await expect(advisor.getByText('Last recommendation', { exact: true })).toBeVisible();
  await expect(advisor.locator('.charge-summary').getByText('12.5%', { exact: true })).toBeVisible();
  await expect(advisor.getByRole('button', { name: 'Approve charging stop', exact: true })).toBeEnabled();
  available = true;
  await expect(advisor.getByRole('button', { name: 'Approve charging stop' })).toBeEnabled();
  await expect(recommendation).toHaveCount(0);
  available = false;
  continuing = true;
  needsCharge = false;
  await expect(page.getByRole('button', { name: 'Review charging stop' })).toHaveCount(0);
  continuing = false;
  needsCharge = true;
  await expect(warning).toBeVisible();
  await expect(recommendation).toHaveCount(0);
  await warning.getByRole('button', { name: 'Hide alert' }).click();
  await page.getByRole('button', { name: 'Measurements', exact: true }).click();
  bay = 'Bay 3';
  available = true;
  await expect(recommendation.getByRole('heading', { name: /Bay 3/ })).toBeVisible();
  await expect(recommendation.getByRole('button', { name: 'Approve charging stop' })).toBeEnabled();

  available = false;
  await expect(recommendation.getByText('Last recommendation', { exact: true })).toBeVisible();
  await expect(recommendation.getByRole('heading', { name: /Bay 3/ })).toBeVisible();
  await recommendation.getByRole('button', { name: 'Hide alert' }).click();
  await expect(recommendation).toHaveCount(0);
  await page.getByRole('button', { name: 'Review charging stop' }).click();
  await expect(recommendation.getByText('Last recommendation', { exact: true })).toBeVisible();
  await expect(recommendation.locator('.charge-summary').getByText('12.5%', { exact: true })).toBeVisible();
  await expect(recommendation.getByRole('button', { name: 'Approve charging stop', exact: true })).toBeEnabled();
  await page.evaluate(() => {
    window.testMonitoringUnavailable = true;
    window.dispatchEvent(new Event('offline'));
  });
  await expect(recommendation.getByText('Waiting for a live update. Approval is unavailable.')).toBeVisible();
  await expect(recommendation).toHaveAccessibleDescription('Earlier forecast. Predicted lowest charge is 9.0%, below the 10% reserve.');
  await expect(recommendation.getByRole('heading', { name: /Bay 3/ })).toBeVisible();
  await expect(recommendation.getByRole('button', { name: 'Approve charging stop', exact: true })).toBeDisabled();
  await page.evaluate(() => { window.testMonitoringUnavailable = false; });
  available = true;
  await expect(recommendation.getByRole('button', { name: 'Approve charging stop' })).toBeEnabled();
  await page.screenshot({ path: test.info().outputPath('charging-alert-desktop.png') });
  stopped = true;
  const wait = recommendation.getByRole('button', { name: 'Approve charging stop' });
  await expect(recommendation.getByText('Waiting for the bus to move', { exact: true })).toBeVisible();
  await expect(recommendation.getByRole('timer')).toHaveText(/real-life s/);
  await wait.click();
  await expect(recommendation.getByText(/Approval pending for/)).toBeVisible();
  await expect(recommendation.getByText('Queued approval ends in')).toBeVisible();
  const queuedTimer = recommendation.getByRole('timer');
  const initialSeconds = Number.parseInt(await queuedTimer.innerText(), 10);
  await expect.poll(async () => Number.parseInt(await queuedTimer.innerText(), 10)).toBeLessThan(initialSeconds);
  await recommendation.getByRole('button', { name: 'Cancel pending approval' }).click();
  expect(approvals).toHaveLength(0);
  await wait.click();
  target = 0.13;
  await expect(recommendation.getByText(/no longer confirmed on the terms/)).toBeVisible();
  expect(approvals).toHaveLength(0);
  available = false;
  await expect(recommendation.getByText('Last recommendation', { exact: true })).toBeVisible();
  await wait.click();
  await expect(recommendation.getByText(/Approval pending for/)).toBeVisible();
  await page.screenshot({ path: test.info().outputPath('charging-approval-pending.png') });
  expect(approvals).toHaveLength(0);
  available = true;
  stopped = false;
  digest = 'b'.repeat(64);
  await expect(recommendation.getByText(/Approved · Awaiting handoff/)).toBeVisible();
  expect(approvals).toHaveLength(1);
  expect(approvals[0]).toMatchObject({
    run_id: run, asset_id: asset, job_fence: 9, candidate_sha256: digest,
  });
  observed = { state: 'charging', bay_id: 'Bay 3', delivered_j: 370000, observed_working_s: 19 };
  await expect(recommendation.getByRole('heading', { name: 'Observed charging' })).toBeVisible();
  await expect(recommendation.getByText('Charging observed', { exact: true })).toBeVisible();
  await recommendation.getByRole('button', { name: 'Hide alert' }).click();
  await expect(page.getByRole('button', { name: 'Vehicle charging' })).toBeVisible();
  await expect(page.getByLabel('Estimated time to arrival')).toHaveText(/estimated at approval/);
  await page.getByRole('button', { name: 'Map', exact: true }).click();
  await expect(page.getByLabel('Vehicle charging at this position')).toBeVisible();
  await page.screenshot({ path: test.info().outputPath('charging-map-arrival.png') });
  await page.getByRole('button', { name: 'Vehicle charging', exact: true }).click();
  observed = null;
  await expect(recommendation.getByRole('heading', { name: /Bay 3/ })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2)).toBe(true);
  await page.evaluate(() => { window.testMonitoringEnded = true; });
  await expect(recommendation.getByText('The trip has ended.')).toBeVisible();
  await expect(recommendation.getByRole('heading', { name: /Bay 3/ })).toBeVisible();
  await expect(recommendation.getByRole('button', { name: 'Approve charging stop', exact: true })).toBeDisabled();
  await recommendation.getByRole('button', { name: 'Hide alert' }).click();
  await expect(recommendation).toHaveCount(0);
  await page.setViewportSize({ width: 1440, height: 1050 });
  await page.getByRole('button', { name: 'Activity', exact: true }).click();
  await expect(page.getByRole('region', { name: 'Trip decisions and observations' }).getByText('Charging observed · Bay 3')).toBeVisible();
});
