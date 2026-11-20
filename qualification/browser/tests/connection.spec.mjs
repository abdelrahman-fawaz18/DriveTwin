import { test, expect } from '@playwright/test';

test('read real stored observations, refresh, and show an empty query', async ({ page, browser }, testInfo) => {
  const origin = new URL(process.env.EVDT_BASE_URL).origin;
  const unexpected = [];
  const failures = [];
  const consoleErrors = [];
  page.on('requestfailed', request => failures.push({ url: request.url(), error: request.failure() }));
  page.on('pageerror', error => consoleErrors.push(String(error)));
  page.on('console', message => { if (message.type() === 'error') consoleErrors.push(message.text()); });
  await page.route('**/*', route => {
    if (new URL(route.request().url()).origin !== origin) {
      unexpected.push(route.request().url());
      return route.abort();
    }
    return route.continue();
  });
  const responses = [];
  page.on('response', response => responses.push({ url: response.url(), status: response.status() }));
  const navigation = await page.goto('/qualification/browser');
  expect(navigation.status()).toBe(200);
  await expect(page.getByRole('heading', { name: 'Browser connection check' })).toBeVisible();
  await expect(page.getByRole('status')).toHaveText('Connection verified');
  await expect(page.locator('#run')).toHaveText(process.env.EVDT_EXPECTED_RUN);
  await expect(page.locator('#mode')).toHaveText('qualification');
  await expect(page.locator('#signal-count')).toHaveText('2');
  await expect(page.locator('#observation-count')).toHaveText('3');
  await expect(page.locator('#readings tr')).toHaveCount(3);
  const expected = [0.71, 0.70, 0.69];
  for (let index = 0; index < expected.length; index++) {
    const row = page.locator('#readings tr').nth(index);
    await expect(row.locator('td').nth(1)).toHaveText('battery.pack.soc');
    const [number, unit] = (await row.locator('td').nth(2).textContent()).split(' ');
    expect(Number(number)).toBeCloseTo(expected[index], 10);
    expect(unit).toBe('1');
    await expect(row.locator('td').nth(3)).toHaveText(`${expected[index] * 100}%`);
  }
  const pageData = await page.evaluate(async () => {
    const result = await fetch('/v1/observations?asset_id=bus-001&limit=100', { cache: 'no-store' });
    if (!result.ok) throw new Error('Browser history fetch failed');
    return result.json();
  });
  expect(pageData.run_id).toBe(process.env.EVDT_EXPECTED_RUN);
  expect(new Set(pageData.items.map(item => item.event_id)).size).toBe(3);
  expect(await page.locator('#readings tr').evaluateAll(rows => rows.map(row => row.dataset.eventId)))
    .toEqual(pageData.items.map(item => item.event_id));
  await page.screenshot({ path: testInfo.outputPath('stored-readings.png'), fullPage: true });
  await testInfo.attach('browser-read-history', { body: JSON.stringify(pageData, null, 2), contentType: 'application/json' });
  await page.getByRole('button', { name: 'Refresh readings' }).click();
  await expect(page.getByRole('status')).toHaveText('Connection verified');
  await expect(page.locator('#readings tr')).toHaveCount(3);
  await page.getByLabel('Vehicle', { exact: true }).fill('bus-999');
  await page.getByRole('button', { name: 'Refresh readings' }).click();
  await expect(page.locator('#observation-count')).toHaveText('0');
  await expect(page.locator('#readings tr')).toHaveCount(0);
  await expect(page.getByText('No stored readings for this vehicle.')).toBeVisible();
  await expect(page.getByRole('alert')).toBeHidden();
  expect(unexpected).toEqual([]);
  expect(failures).toEqual([]);
  expect(consoleErrors).toEqual([]);
  expect(responses.filter(item => item.status >= 400)).toEqual([]);
  await testInfo.attach('browser-environment-and-network', {
    body: JSON.stringify({ platform: process.platform, node: process.version,
      browserVersion: browser.version(), project: testInfo.project.name,
      baseURL: process.env.EVDT_BASE_URL, responses, unexpected, failures, consoleErrors }, null, 2),
    contentType: 'application/json',
  });
});
