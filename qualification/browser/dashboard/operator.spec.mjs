import { expect, test } from '@playwright/test';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';

const control = process.env.EVDT_DASHBOARD_CONTROL;
const scenario = process.env.EVDT_OPERATOR_CASE;

test('operator controls preserve the scoped command record', async ({ page, request }) => {
  test.skip(!scenario, 'Requires the Phase 3 command fixture');
  const fixture = JSON.parse(readFileSync(path.join(control, 'ready.json'), 'utf8'));
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  const capture = name => page.screenshot({ path: path.join(process.env.EVDT_BROWSER_OUTPUT, `${name}.png`), fullPage: true });
  const endpoint = `/v1/operator/${fixture.run}/${fixture.asset}`;
  const auth = (credential) => ({ Authorization: `Bearer ${credential}` });
  for (const credential of [fixture.reader, fixture.replay_credential]) {
    expect((await request.get(`${endpoint}/timeline`, { headers: auth(credential) })).status()).toBe(401);
    expect((await request.post(`${endpoint}/commands`, { headers: auth(credential), data: {} })).status()).toBe(401);
  }
  expect((await request.post(`/v1/operator/${fixture.run}/bus-002/commands`, { headers: auth(fixture.operator), data: {} })).status()).toBe(403);
  expect((await request.post(`${endpoint}/internal/claim`, { headers: auth(fixture.operator), data: {} })).status()).toBe(404);
  expect((await request.post(`${fixture.replay}${endpoint}/commands`, { headers: auth(fixture.replay_credential), data: {} })).status()).toBe(404);
  const replay = await page.context().newPage();
  await replay.goto(fixture.replay);
  await expect(replay.getByRole('button', { name: 'Operator', exact: true })).toHaveCount(0);
  await replay.close();

  await page.goto('/');
  await page.getByLabel('Dashboard key').fill(fixture.reader);
  await page.getByRole('button', { name: 'Connect to monitoring' }).click();
  await page.getByRole('button', { name: 'Operator', exact: true }).click();
  const panel = page.getByRole('region', { name: 'Operator controls and command timeline' });
  await expect(panel).toHaveCount(0);
  await page.getByRole('button', { name: 'Disconnect', exact: true }).click();
  await page.getByLabel('Dashboard key').fill(fixture.operator);
  await page.getByRole('button', { name: 'Connect to monitoring' }).click();
  await expect(panel).toBeVisible();
  await expect(page.locator('input[type=password]')).toHaveCount(0);

  if (scenario === 'sealed') {
    await expect(panel.getByText(/Selected role operator · Authority number/)).toBeVisible();
    await expect(panel.getByRole('button', { name: 'Request speed cap' })).toHaveCount(0);
    await panel.getByRole('button', { name: 'Cancel pending command' }).click();
    await expect(panel.getByText(/^canceled · Accepted/)).toBeVisible();
    await expect(panel.getByText('No effect assessment', { exact: true })).toBeVisible();
  } else {
    await panel.getByLabel('Speed cap (m/s)').fill('5');
    await panel.getByLabel('Speed request reason').fill('Operator browser check');
    await panel.getByRole('button', { name: 'Request speed cap' }).click();
    await expect(panel.getByText(/^queued · Accepted/)).toBeVisible();
    await capture('queued');
    if (scenario === 'confirmed') {
      await panel.getByLabel('Speed cap (m/s)').fill('4');
      await panel.getByLabel('Speed request reason').fill('Replacement browser check');
      await panel.getByRole('button', { name: 'Replace pending cap' }).click();
      await expect(panel.getByText(/^superseded · Accepted/)).toBeVisible();
      await expect(panel.getByRole('link', { name: 'View replacement' })).toBeVisible();
      await panel.getByLabel('Mission station').selectOption('depot-a');
      await panel.getByLabel('Mission request reason').fill('Recorded for review');
      await panel.getByRole('button', { name: 'Record mission intent' }).click();
      await expect(panel.getByRole('heading', { name: 'depot-a · Intent recorded for review' })).toBeVisible();
      writeFileSync(path.join(control, 'act'), '');
      await expect(panel.getByText('confirmed · Peak 4.00 m/s', { exact: true })).toBeVisible({ timeout: 65_000 });
      await expect(panel.getByText(/^acknowledged at/)).toBeVisible();
    } else {
      await page.route('**/commands/*/cancel', async (route) => {
        writeFileSync(path.join(control, 'act'), '');
        await expect.poll(() => existsSync(path.join(control, 'acted'))).toBe(true);
        await route.continue();
      });
      await panel.getByRole('button', { name: 'Cancel pending command' }).click();
      await expect(panel.getByRole('alert')).toContainText('conflicts with the current command');
      await expect(panel.locator('.operator-entry').getByText('Outcome unknown', { exact: true })).toBeVisible();
      await expect(panel.locator('.command-lifecycle').getByText('Outcome unknown', { exact: true })).toBeVisible();
      await expect(panel.getByText('Awaiting measured evidence', { exact: true })).toBeVisible();
      await expect(panel.getByRole('button', { name: 'Cancel pending command' })).toHaveCount(0);
    }
  }
  await expect(panel.getByRole('heading', { name: 'Command lifecycle', exact: true })).toBeVisible();
  await capture('operator');
  await page.setViewportSize({ width: 390, height: 844 });
  await capture('operator-mobile');
  expect(errors).toEqual([]);
  writeFileSync(path.join(process.env.EVDT_BROWSER_OUTPUT, 'browser-result.json'), JSON.stringify({ passed: true, case: scenario, permission_boundaries_checked: true, actual_service_actions: true, lifecycle_displayed: true, page_errors: 0 }));
  await page.getByRole('button', { name: 'Disconnect', exact: true }).click();
  await expect(page.getByLabel('Dashboard key')).toBeVisible();
  await expect(panel.getByRole('heading', { name: 'Speed command history' })).toHaveCount(0);
});
