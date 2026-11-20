import { expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

export async function checkFleet(page, fixture, info) {
  await page.goto(fixture.base_url);
  await page.getByLabel('Dashboard key').fill(fixture.reader_token);
  await page.getByRole('button', { name: 'Connect to monitoring' }).click();
  await page.getByRole('button', { name: 'Fleet', exact: true }).click();
  await expect(page.locator('[data-fleet-asset]')).toHaveCount(2);
  const formatter = new Intl.NumberFormat('en-US', { maximumFractionDigits: 3 });
  for (const [asset, count] of [['bus-001', 20], ['bus-002', 21]]) {
    const panel = page.locator(`[data-fleet-asset="${asset}"]`);
    await expect(panel.locator('[data-signal]')).toHaveCount(count);
    await expect(panel).toHaveAttribute('data-connection', 'live');
    const snap = await (await page.request.get(`/v1/snapshot?asset_id=${asset}`, {
      headers: { Authorization: 'Bearer ' + fixture.reader_token },
    })).json();
    for (const reading of snap.items) {
      const row = panel.locator(`[data-signal="${reading.signal_id}"]`);
      const original = reading.observation?.sample.value;
      await expect(row.locator('td').nth(1)).toHaveText((original == null ? '—' : formatter.format(original)) + ' ' + reading.definition.unit);
      await expect(row).toHaveAttribute('data-asset', asset);
      await expect(row).toHaveAttribute('data-run', snap.run_id);
      await expect(row).toHaveAttribute('data-source', reading.source_id);
      await expect(row.locator('.badge').first()).toHaveText(reading.quality);
    }
  }
  const added = page.locator('[data-fleet-asset="bus-002"] [data-signal="coolant.inlet.temperature"]');
  await added.getByText('Measurement metadata', { exact: true }).click();
  await expect(added).toContainText('conditioned-coolant-inlet/1.0.0');
  await expect(added).toContainText('conditioned coolant supply');
  const onlySecond = '**/v1/snapshot?asset_id=bus-002';
  await page.route(onlySecond, route => route.abort());
  await expect(page.locator('[data-fleet-asset="bus-002"]')).toHaveAttribute('data-connection', 'disconnected');
  await expect(page.locator('[data-fleet-asset="bus-002"] .badge.stale')).toHaveCount(21);
  await expect(page.locator('[data-fleet-asset="bus-001"]')).toHaveAttribute('data-connection', 'live');
  await expect(added.locator('td').nth(1)).not.toContainText('—');
  await page.unroute(onlySecond);
  await expect(page.locator('[data-fleet-asset="bus-002"]')).toHaveAttribute('data-connection', 'live');
  await page.context().setOffline(true);
  await expect(page.locator('.fleet-vehicle[data-connection="disconnected"]')).toHaveCount(2);
  await expect(page.locator('.fleet-vehicle .badge.stale')).toHaveCount(41);
  await page.context().setOffline(false);
  await expect(page.locator('.fleet-vehicle[data-connection="live"]')).toHaveCount(2);
  await page.getByLabel('Fleet vehicles').selectOption('bus-002');
  await expect(page.locator('[data-fleet-asset]')).toHaveCount(1);
  await expect(page.locator('[data-signal]')).toHaveCount(21);
  await page.getByRole('button', { name: 'Open bus-002 details' }).click();
  await expect(page.getByLabel('Vehicle', { exact: true })).toHaveValue('bus-002');
  await expect(page.locator('[data-signal]')).toHaveCount(21);
  await page.getByRole('button', { name: 'Fleet', exact: true }).click();
  await page.evaluate(() => scrollTo(0, 0));
  await page.screenshot({ path: path.join(process.env.EVDT_BROWSER_OUTPUT, info.project.name + '-fleet-desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.evaluate(() => scrollTo(0, 0));
  await page.screenshot({ path: path.join(process.env.EVDT_BROWSER_OUTPUT, info.project.name + '-fleet-mobile.png'), fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1050 });
  await page.route(onlySecond, route => route.fulfill({ status: 403, contentType: 'application/json', body: '{}' }));
  await expect(page.getByLabel('Dashboard key')).toBeVisible();
  await expect(page.locator('[data-fleet-asset]')).toHaveCount(0);
  await page.unroute(onlySecond);
  await page.getByLabel('Dashboard key').fill(fixture.second_token);
  await page.getByRole('button', { name: 'Connect to monitoring' }).click();
  // View preference can remain Fleet, while its scope must come from the new credential.
  await expect(page.locator('[data-fleet-asset]')).toHaveCount(1);
  await expect(page.locator('[data-fleet-asset="bus-002"] [data-signal]')).toHaveCount(21);
  await expect(page.getByLabel('Fleet vehicles').locator('option')).toHaveCount(1);
  expect(await page.evaluate(() => ({ local: localStorage.length, session: sessionStorage.length }))).toEqual({ local: 0, session: 0 });
  expect(await page.context().cookies()).toEqual([]);
  const record = { passed: true, checks: ['generic 20/21 signals', 'every value/unit/quality/asset/run/source', 'added metadata', 'single-asset failure and recovery', 'offline stale and resync', 'selection/detail navigation', 'desktop/mobile document overflow', '403 clears fleet', 'scoped reauthentication', 'empty storage/cookies'] };
  fs.writeFileSync(path.join(process.env.EVDT_DASHBOARD_CONTROL, info.project.name + '-fleet-functional.json'), JSON.stringify(record));
  return record;
}
