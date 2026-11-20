import { expect } from '@playwright/test';
import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';

const output = process.argv[2];
const file = (name) => path.join(output, name);
const read = (name) => JSON.parse(fs.readFileSync(file(name), 'utf8'));
const mark = (name) => fs.writeFileSync(file(name), '', { flag: 'wx' });
let fixture;
await expect.poll(() => {
  try {
    fixture = read('ready.json');
    return Boolean(fixture.monitoring_token && fixture.replay_token);
  } catch {
    return false;
  }
}).toBe(true);

const origins = new Set([fixture.monitoring_url, fixture.replay_url]);
expect(origins).toEqual(new Set(['http://127.0.0.1:18765', 'http://127.0.0.1:18766']));
const browser = await chromium.launch({ channel: 'chromium', headless: true });
const review = fixture.review;
const result = { passed: false, chromium: browser.version(), checks: [], screenshots: [] };
let activePage;
const capture = async (page, name) => {
  if (!review) return;
  fs.mkdirSync(file('screenshots'), { recursive: true });
  const relative = `screenshots/${String(result.screenshots.length + 1).padStart(2, '0')}-${name}.png`;
  await page.screenshot({ path: file(relative), fullPage: true, timeout: 10000 });
  result.screenshots.push({ file: relative, label: name, captured_at: new Date().toISOString() });
  fs.writeFileSync(file('screenshots.json'), JSON.stringify(result.screenshots, null, 2));
};
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1050 }, serviceWorkers: 'block' });
  const unexpected = [];
  await context.route('**/*', (route) => {
    if (origins.has(new URL(route.request().url()).origin)) return route.continue();
    unexpected.push('External request');
    return route.abort();
  });
  const page = await context.newPage();
  activePage = page;
  page.on('pageerror', (error) => unexpected.push(error.message));
  const tab = (name) => page.getByRole('button', { name, exact: true }).click();
  const speed = page.locator('[data-signal="motion.speed"]');
  // The quality badge comes first. Both badges can say "unknown" before ingress.
  const freshness = speed.locator('.badge').last();
  const source = page.getByText(/^Source:/);
  const connected = page.getByRole('status').filter({ hasText: 'Dashboard connected' });

  await page.goto(fixture.monitoring_url);
  await page.getByLabel('Dashboard key').fill(fixture.monitoring_token);
  await page.getByRole('button', { name: 'Connect to monitoring' }).click();
  await expect(connected).toBeVisible();
  await tab('Measurements');
  await expect(page.locator('tbody tr[data-signal]')).toHaveCount(20);
  await expect(freshness).toHaveText('unknown');
  mark('start');
  await expect(freshness).toHaveText('fresh', { timeout: 25000 });
  const firstEvent = await speed.getAttribute('data-event');
  expect(firstEvent).toBeTruthy();
  await expect(speed).not.toHaveAttribute('data-event', firstEvent);
  await expect(freshness).toHaveText('fresh');
  result.checks.push('connected_before_start', 'fresh_observations_advanced');
  console.log('Verified live readings');
  await capture(page, 'live-measurements');

  await expect(source).toContainText('paused', { timeout: 15000 });
  await expect(freshness).toHaveText('paused');
  await expect.poll(async () => parseFloat(await speed.getByText(/ s old$/).innerText()), {
    timeout: 9000,
  }).toBeGreaterThan(5);
  const pausedEvent = await speed.getAttribute('data-event');
  expect(pausedEvent).toBeTruthy();
  await expect(freshness).toHaveText('paused');
  await tab('Map');
  await expect(page.getByTestId('vehicle-position')).toHaveAttribute('aria-label', /paused/);
  await expect(page.getByText(/paired observed points/)).toBeVisible();
  await capture(page, 'paused-map');
  await tab('Measurements');
  await expect(speed).toHaveAttribute('data-event', pausedEvent);
  result.checks.push('healthy_pause_beyond_freshness_deadline', 'paused_location_rendered');
  console.log('Verified healthy pause and map');

  mark('disconnect-source');
  await expect(source).toContainText('disconnected', { timeout: 10000 });
  await expect(freshness).toHaveText('stale');
  await expect(speed).toHaveAttribute('data-event', pausedEvent);
  await expect(connected).toBeVisible();
  await capture(page, 'source-disconnected');
  mark('reconnect-source');
  await expect(source).toContainText('running', { timeout: 30000 });
  await expect(speed).not.toHaveAttribute('data-event', pausedEvent);
  await expect(freshness).toHaveText('fresh');
  result.checks.push('source_disconnected_and_stale', 'source_reconnected_with_new_reading');
  console.log('Verified source disconnect and recovery');
  await capture(page, 'source-reconnected');

  await context.setOffline(true);
  await expect(page.getByRole('status').filter({ hasText: 'Disconnected · reconnecting' })).toBeVisible({ timeout: 15000 });
  await expect(freshness).toHaveText('stale');
  await capture(page, 'browser-offline');
  await context.setOffline(false);
  await expect(page.getByText(/Resynchronized ·/)).toBeVisible({ timeout: 20000 });
  await expect(connected).toBeVisible();
  result.checks.push('browser_disconnect_marks_stale', 'browser_reconnect_resynchronizes');
  console.log('Verified browser resynchronization');
  await capture(page, 'browser-resynchronized');

  if (review) {
    const screens = [
      ['Overview', 'Vehicle overview'], ['Fleet', 'Fleet measurements'],
      ['Map', 'Route and location'], ['Measurements', 'All measurements'],
      ['History', 'History'], ['Energy', 'Energy forecast'],
      ['Activity', 'Activity'], ['System', 'System health'], ['Operator', 'Vehicle operator'],
    ];
    // Use received working time, not an assumed wall-clock delay, to space live captures.
    for (let round = 0; round < review.live_capture_rounds; round++) {
      await tab('Measurements');
      await expect.poll(async () => {
        const response = await context.request.get(`${fixture.monitoring_url}/v1/snapshot?asset_id=bus-001`, {
          headers: { Authorization: `Bearer ${fixture.monitoring_token}` },
        });
        expect(response.ok()).toBe(true);
        const snapshot = await response.json();
        return Math.max(0, ...snapshot.items.map((item) => item.observation?.sim_time_s ?? 0));
      }, { timeout: 160000, intervals: [1000] }).toBeGreaterThanOrEqual([45, 135, 225][round]);
      await expect(freshness).toHaveText('fresh');
      const before = await speed.getAttribute('data-event');
      for (const [name, heading] of screens) {
        const endpoint = { Fleet: '/v1/snapshot', Energy: '/v1/energy-forecasts', Activity: '/v1/events', System: '/v1/system-health' }[name];
        const response = endpoint ? page.waitForResponse(r => new URL(r.url()).pathname === endpoint && r.ok(), { timeout: 15000 }) : null;
        await tab(name);
        if (response) await response;
        await expect(page.getByRole('heading', { level: 1, name: heading, exact: true })).toBeVisible();
        if (name === 'Map') await expect(page.getByTestId('vehicle-position')).toBeVisible();
        if (name === 'Measurements') await expect(page.locator('tbody tr[data-signal]')).toHaveCount(20);
        if (name === 'History') await expect(page.getByRole('img', { name: /history/ }).first()).toBeVisible();
        if (name === 'System') await expect(page.getByRole('heading', { name: 'Delivery queue' })).toBeVisible();
        // Give the browser two painted frames after the view's committed layout.
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        await capture(page, `live-${round + 1}-${name.toLowerCase()}`);
        if (name === 'Map') {
          await page.getByRole('button', { name: 'Center on vehicle', exact: true }).click();
          await page.getByLabel('Follow vehicle', { exact: true }).check();
          await capture(page, `live-${round + 1}-map-streets`);
        }
      }
      await tab('Measurements');
      await expect(freshness).toHaveText('fresh');
      await expect(speed).not.toHaveAttribute('data-event', before);
    }
    result.checks.push('all_nine_tabs_captured_during_live_source');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole('button', { name: 'Open navigation', exact: true }).click();
    await capture(page, 'mobile-navigation');
    await tab('Overview');
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    await capture(page, 'mobile-overview');
    await page.getByRole('button', { name: 'Open navigation', exact: true }).click();
    await tab('Map');
    await expect(page.getByTestId('vehicle-position')).toBeVisible();
    await capture(page, 'mobile-map');
    await page.setViewportSize({ width: 1440, height: 1050 });
    await tab('Measurements');
    result.checks.push('mobile_navigation_and_map');
  }

  await expect.poll(() => fs.existsSync(file('demo-recorded')), { timeout: review ? 120000 : 35000 }).toBe(true);
  const originals = read('source-report.json').events.map((entry) => entry.body);
  expect(originals).toHaveLength(review?.samples ?? 16);
  expect(originals.some((body) => body.event_id === firstEvent)).toBe(true);
  expect(originals.some((body) => body.event_id === pausedEvent)).toBe(true);
  await expect(speed).toHaveAttribute('data-event', originals.at(-1).event_id);
  await page.getByRole('button', { name: 'History for motion.speed', exact: true }).click();
  await expect(page.getByRole('img', { name: /Motion · Speed history/ })).toBeVisible();
  await page.getByText('Inspect recorded values and provenance').click();
  const history = page.locator('.raw tbody tr');
  await expect(history).toHaveCount(Math.min(100, originals.length));
  const lastSpeed = originals.at(-1).samples.find((sample) => sample.signal_id === 'motion.speed').value;
  await expect(history.last()).toContainText(new Intl.NumberFormat('en-US', { maximumFractionDigits: 3 }).format(lastSpeed));
  result.checks.push('history_rendered_original_drive');
  await capture(page, 'recorded-history');

  const replay = await context.newPage();
  replay.on('pageerror', (error) => unexpected.push(error.message));
  await replay.goto(fixture.replay_url + '/#access=' + encodeURIComponent(fixture.replay_token));
  await replay.getByLabel('From working seconds').fill('0');
  await replay.getByLabel('To working seconds').fill(String(review?.samples ?? 16));
  const response = replay.waitForResponse((r) => r.url().endsWith('/v1/replay/sessions') && r.ok());
  await replay.getByRole('button', { name: 'Load recording' }).click();
  const recording = await (await response).json();
  expect(recording.source_run_id).toBe(originals[0].run_id);
  expect(recording.replay_run_id).not.toBe(recording.source_run_id);
  expect(recording.packets.map((packet) => JSON.parse(Buffer.from(packet.raw_base64, 'base64')))).toEqual(originals);
  expect(recording.capabilities.dispatch).toBe(false);
  await expect(replay.locator('[data-replay-signal]')).toHaveCount(20);
  await replay.getByRole('button', { name: 'Next observation', exact: true }).click();
  await expect(replay.getByTestId('replay-time')).toHaveText(`${originals[0].sim_time_s.toFixed(2)} s`);
  await capture(replay, 'replay-first-observation');
  if (review) {
    await replay.getByRole('button', { name: 'Play', exact: true }).click();
    await expect.poll(async () => parseFloat(await replay.getByTestId('replay-time').innerText())).toBeGreaterThan(3);
    await replay.getByRole('button', { name: 'Pause', exact: true }).click();
    await capture(replay, 'replay-playback');
    await tab('Map');
    await capture(page, 'complete-observed-route');
    await tab('System');
    await capture(page, 'recorded-system-health');
    expect(result.screenshots.length).toBeGreaterThanOrEqual(40);
  }
  expect(unexpected).toEqual([]);
  result.checks.push('isolated_replay_originals_and_playhead');
  result.passed = true;
  console.log('Verified history and replay');
} catch (error) {
  result.error = String(error);
  if (activePage) await capture(activePage, 'failure').catch(() => {});
  process.exitCode = 1;
} finally {
  await browser.close();
  fs.writeFileSync(file('browser-result.tmp'), JSON.stringify(result), { flag: 'wx' });
  fs.renameSync(file('browser-result.tmp'), file('browser-result.json'));
  if (review) {
    const escape = value => value.replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
    fs.writeFileSync(file('screenshots.html'), `<!doctype html><html lang="en"><meta charset="utf-8"><title>DriveTwin dashboard review</title><style>body{font:16px system-ui;background:#f5f6f8;color:#252831;max-width:1300px;margin:40px auto}img{max-width:100%;border-radius:16px}figure{background:white;padding:20px;margin:30px 0}figcaption{margin-bottom:12px}</style><h1>DriveTwin dashboard review</h1><p>${result.screenshots.length} captures. Browser checks passed: ${result.passed}.</p>${result.screenshots.map(item => `<figure><figcaption>${escape(item.label)} · ${escape(item.captured_at)}</figcaption><a href="${item.file}"><img loading="lazy" src="${item.file}" alt="${escape(item.label)}"></a></figure>`).join('')}</html>`);
  }
}
