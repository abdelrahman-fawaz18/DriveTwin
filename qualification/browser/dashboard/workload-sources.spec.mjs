import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
const control = process.env.EVDT_DASHBOARD_CONTROL;
const fixture = JSON.parse(fs.readFileSync(path.join(control, 'ready.json'), 'utf8'));

test('explicit ten-asset sustained catalog through the existing product dashboard', async ({ page, browser }, info) => {
  const errors = [], external = [];
  page.on('pageerror', error => errors.push(String(error)));
  await page.route('**/*', route => {
    if (new URL(route.request().url()).origin !== new URL(fixture.base_url).origin) {
      external.push(route.request().url());
      return route.abort();
    }
    return route.continue();
  });
  await page.goto('/');
  await page.getByLabel('Dashboard key').fill(fixture.reader_token);
  await page.getByRole('button', { name: 'Connect to monitoring' }).click();
  await expect(page.getByLabel('Vehicle', { exact: true }).locator('option')).toHaveCount(10);
  await page.getByRole('button', { name: 'Measurements', exact: true }).click();
  for (let i = 1; i <= 10; i++) {
    const asset = 'bus-' + String(i).padStart(3, '0');
    await page.getByLabel('Vehicle', { exact: true }).selectOption(asset);
    await expect(page.locator('[data-signal]')).toHaveCount(20);
    const response = await page.request.get('/v1/snapshot?asset_id=' + asset, { headers: { Authorization: 'Bearer ' + fixture.reader_token } });
    expect(response.status()).toBe(200);
    const snapshot = await response.json();
    expect(snapshot.asset_id).toBe(asset);
    for (const item of snapshot.items) {
      const row = page.locator('[data-signal="' + item.signal_id + '"]');
      await expect(row).toHaveCount(1);
      await expect(row.locator('td').nth(1)).toContainText(item.definition.unit);
      await expect(row.locator('td').nth(1)).toContainText(new Intl.NumberFormat('en-US', { maximumFractionDigits: 3 }).format(item.observation.sample.value));
      await expect(row.locator('td').nth(1)).not.toContainText('—');
      expect(item.observation.stream_seq).toBe(29);
    }
  }
  await page.getByRole('button', { name: 'Map', exact: true }).click();
  await expect(page.getByText(/30 paired observed points/)).toBeVisible();
  const context = await (await page.request.get('/v1/map-context?asset_id=bus-010', { headers: { Authorization: 'Bearer ' + fixture.reader_token } })).json();
  expect(context.mission_id).toBe('montreal-sustained-v1');
  expect((await page.request.get('/v1/snapshot?asset_id=bus-010', { headers: { Authorization: 'Bearer ' + fixture.scoped_token } })).status()).toBe(403);
  await page.getByRole('button', { name: 'Measurements', exact: true }).click();
  await page.screenshot({ path: path.join(process.env.EVDT_BROWSER_OUTPUT, info.project.name + '-sources-desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: path.join(process.env.EVDT_BROWSER_OUTPUT, info.project.name + '-sources-mobile.png'), fullPage: true });
  expect(errors).toEqual([]);
  expect(external).toEqual([]);
  fs.writeFileSync(path.join(control, info.project.name + '-sources-result.json'), JSON.stringify({ passed: true, project: info.project.name, browser: browser.version(), checks: ['ten asset choices', '20 existing signal definitions and values per asset', 'asset-scoped snapshots and reader denial', 'sustained mission map', 'desktop/mobile', 'no external requests'], scope: 'final recorded-state UI compatibility; no live latency/DOM timing claim' }));
});
