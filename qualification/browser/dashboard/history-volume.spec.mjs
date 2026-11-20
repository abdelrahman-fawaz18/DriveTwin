import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const control = process.env.EVDT_DASHBOARD_CONTROL;
const fixture = JSON.parse(fs.readFileSync(path.join(control, 'ready.json'), 'utf8'));

test('five-signal historical plots and complete original JSON/CSV downloads', async ({ context, browser }, info) => {
  test.setTimeout(360_000);
  const output = process.env.EVDT_BROWSER_OUTPUT;
  const downloads = path.join(output, 'downloads');
  fs.mkdirSync(downloads, { recursive: true });
  const result = { passed: false, project: info.project.name, browser: browser.version(), assets: {} };
  const pages = [], errors = [], external = [];
  try {
    for (const asset of fixture.assets) {
      const page = await context.newPage();
      pages.push(page);
      const requests = [];
      page.on('pageerror', error => errors.push(String(error)));
      await page.route('**/*', route => {
        const url = new URL(route.request().url());
        if (url.origin !== new URL(fixture.base_url).origin) {
          external.push(url.href);
          return route.abort();
        }
        if (url.pathname === '/v1/export-page') {
          requests.push({ asset: url.searchParams.get('asset_id'), as_of: url.searchParams.get('as_of'),
            signals: url.searchParams.getAll('signals'), after: url.searchParams.get('after'),
            from_s: Number(url.searchParams.get('from_s')), to_s: Number(url.searchParams.get('to_s')) });
        }
        return route.continue();
      });
      await page.setViewportSize({ width: 1440, height: 1050 });
      await page.goto('/');
      await page.getByLabel('Dashboard key').fill(fixture.reader_token);
      await page.getByRole('button', { name: 'Connect to monitoring' }).click();
      await page.getByLabel('Vehicle', { exact: true }).selectOption(asset);
      await page.getByRole('button', { name: 'Measurements', exact: true }).click();
      await expect(page.locator('tbody tr[data-signal]')).toHaveCount(20);
      await page.getByRole('button', { name: 'History for motion.speed', exact: true }).click();
      await page.getByText('Select signals to compare').click();
      for (const signal of fixture.signals) await page.getByLabel('Compare ' + signal, { exact: true }).check();
      await expect(page.getByLabel('Compare battery.pack.voltage', { exact: true })).toBeDisabled();
      await page.getByLabel('From working seconds').fill(String(fixture.from_s));
      await page.getByLabel('To working seconds').fill(String(fixture.to_s));
      await page.getByRole('button', { name: 'Apply historical query' }).click();
      await expect(page.locator('.chart svg')).toHaveCount(5, { timeout: 20000 });
      for (const chart of await page.locator('.chart svg').all()) await expect(chart).toBeVisible();
      await page.getByLabel('Shared time cursor').fill(String(fixture.to_s / 2));
      await expect(page.getByTestId('cursor-reading')).toHaveCount(5);
      const record = { passed: false, downloads: {} };
      result.assets[asset] = record;
      const pageCount = Math.ceil(fixture.expected_rows / 1000);
      for (const format of ['json', 'csv']) {
        const before = requests.length;
        const pending = page.waitForEvent('download', { timeout: 130000 });
        await page.getByRole('button', { name: 'Download ' + format.toUpperCase() }).click();
        const download = await pending;
        expect(download.suggestedFilename()).toBe('evdt-' + asset + '.' + format);
        const filename = info.project.name + '-' + asset + '.' + format;
        const destination = path.join(downloads, filename);
        await download.saveAs(destination);
        expect(await download.failure()).toBe(null);
        const bytes = fs.statSync(destination).size;
        expect(bytes).toBeGreaterThan(0);
        expect(bytes).toBeLessThanOrEqual(64 * 1024 * 1024);
        await expect.poll(() => requests.length - before).toBe(pageCount);
        const chain = requests.slice(before);
        expect(chain[0].after).toBe(null);
        for (const [index, request] of chain.entries()) {
          expect(request.asset).toBe(asset);
          expect(request.signals.slice().sort()).toEqual(fixture.signals.slice().sort());
          expect(request.from_s).toBe(fixture.from_s);
          expect(request.to_s).toBe(fixture.to_s);
          expect(request.as_of).toBe(chain[0].as_of);
          if (index) expect(typeof request.after).toBe('string');
        }
        if (format === 'json') {
          const body = JSON.parse(fs.readFileSync(destination, 'utf8'));
          expect(body.items).toHaveLength(fixture.expected_rows);
          expect(body.metadata.asset_id).toBe(asset);
          expect(body.metadata.run_id).toBe(fixture.run_id);
          expect(body.metadata.download_complete).toBe(true);
          expect(body.metadata.page_count).toBe(pageCount);
        }
        record.downloads[format] = { filename, bytes, requests: chain.length, page_count: pageCount };
      }
      await page.getByLabel('Bucket width seconds').fill('60');
      await page.getByRole('button', { name: 'Calculate summary' }).click();
      await expect(page.locator('.summaries tbody tr')).toHaveCount(5 * Math.ceil((fixture.to_s - fixture.from_s) / 60));
      await expect(page.getByRole('alert')).toHaveCount(0);
      await page.screenshot({ path: path.join(output, info.project.name + '-' + asset + '-history-desktop.png'), fullPage: true });
      await page.setViewportSize({ width: 390, height: 844 });
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2)).toBe(true);
      await expect(page.locator('.chart svg')).toHaveCount(5);
      await page.screenshot({ path: path.join(output, info.project.name + '-' + asset + '-history-mobile.png'), fullPage: true });
      expect(await page.evaluate(() => ({ local: localStorage.length, session: sessionStorage.length }))).toEqual({ local: 0, session: 0 });
      record.passed = true;
      await page.close();
    }
    expect(errors).toEqual([]);
    expect(external).toEqual([]);
    expect(await context.cookies()).toEqual([]);
    result.passed = true;
  } finally {
    for (const page of pages) if (!page.isClosed()) {
      if (!result.passed) try {
        await page.screenshot({ path: path.join(output, info.project.name + '-failed-history.png'), fullPage: true, timeout: 5000 });
      } catch { /* Preserve the other diagnostics if a page has crashed. */ }
      await page.close();
    }
    fs.writeFileSync(path.join(control, info.project.name + '-history-result.json'), JSON.stringify(result));
  }
});
