import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';

const control = process.env.EVDT_DASHBOARD_CONTROL;
const fixture = JSON.parse(fs.readFileSync(path.join(control, 'ready.json'), 'utf8'));

test('correlate actual rendered measurement rows and periodic clock exchanges', async ({ context, browser }, info) => {
  test.setTimeout(fixture.profile ? (fixture.total_samples + 400) * 1000 : 260_000);
  const assets = fixture.pairs[info.project.name];
  const pages = [], ownedContexts = [], networkContexts = [], errors = [], external = [];
  expect(fixture.trace_policy).toBe('action-har/1.0.0');
  const result = { passed: false, project: info.project.name, browser: browser.version(), trace_policy: fixture.trace_policy, network_archives: [], assets: {} };
  if (fixture.profile) {
    expect(fixture.profile.browser_viewer_policy).toBe('fleet-operator-tabs/1.0.0');
    expect(fixture.profile.browser_storage_contexts).toBe(1);
    expect(fixture.profile.independent_contexts).toBe(false);
    expect(new Set(Object.values(fixture.asset_urls)).size).toBe(assets.length);
    result.viewer_policy = fixture.profile.browser_viewer_policy;
    result.asset_urls = fixture.asset_urls;
  }
  try {
    for (const asset of assets) {
      const session = context;
      const baseURL = fixture.profile ? fixture.asset_urls[asset] : fixture.base_url;
      expect(new URL(baseURL).hostname).toBe('127.0.0.1');
      if (!networkContexts.includes(session)) {
        const name = info.project.name + '-' + (fixture.profile ? 'fleet' : 'paired') + '.har';
        await session.tracing.startHar(path.join(process.env.EVDT_BROWSER_OUTPUT, name), { content: 'omit', mode: 'full' });
        networkContexts.push(session);
        result.network_archives.push(name);
      }
      const page = await session.newPage();
      pages.push(page);
      page.on('pageerror', error => errors.push(String(error)));
      page.on('request', request => {
        if (new URL(request.url()).origin !== new URL(baseURL).origin) external.push(request.url());
      });
      await page.addInitScript(({ asset }) => {
        const baseUTC = Date.now() / 1000, baseMono = performance.now() / 1000;
        const now = () => baseUTC + performance.now() / 1000 - baseMono;
        const data = { events: {}, clocks: [], failures: [], asset, now };
        window.evdtMeasurement = data;
        const read = () => {
          const rows = [...document.querySelectorAll('[data-signal][data-event]')];
          if (rows.length !== 20 || rows.some(row => row.dataset.asset !== asset)) return;
          const event = rows[0].dataset.event;
          if (rows.some(row => row.dataset.event !== event) || data.events[event]) return;
          const values = {};
          for (const row of rows) {
            if (getComputedStyle(row).display === 'none' || !row.getClientRects().length) return;
            values[row.dataset.signal] = row.querySelectorAll('td')[1].textContent;
          }
          data.events[event] = { utc_s: now(), monotonic_s: performance.now() / 1000,
            wall_residual_s: Date.now() / 1000 - now(), run_id: rows[0].dataset.run,
            asset_id: asset, source_id: rows[0].dataset.source, event_id: event, values };
          if (data.witness) data.pending.push(data.witness(event));
          else data.failures.push('DOM event before witness collector readiness');
        };
        new MutationObserver(read).observe(document, { attributes: true, childList: true, subtree: true, characterData: true });
      }, { asset });
      await page.goto(baseURL + '/');
      expect(await page.evaluate(() => ({ local: localStorage.length, session: sessionStorage.length }))).toEqual({ local: 0, session: 0 });
      await page.getByLabel('Dashboard key').fill(fixture.reader_token);
      await page.getByRole('button', { name: 'Connect to monitoring' }).click();
      await page.getByLabel('Vehicle', { exact: true }).selectOption(asset);
      await page.getByRole('button', { name: 'Measurements', exact: true }).click();
      await expect(page.locator('[data-signal]')).toHaveCount(20);
      await expect(page.locator('.connection-dot.live')).toHaveCount(1);
      // The clock endpoint is reader-authenticated; no token enters a URL.
      expect((await page.request.get(baseURL + '/v1/time')).status()).toBe(401);
      await page.evaluate(({ token }) => {
        const data = window.evdtMeasurement;
        data.pending = [];
        data.exchange = async (event = null) => {
          const send = data.now(), monoSend = performance.now() / 1000;
          try {
            const suffix = event ? '?asset_id=' + encodeURIComponent(data.asset) + '&event_id=' + encodeURIComponent(event) : '';
            const response = await fetch('/v1/time' + suffix, { headers: { Authorization: 'Bearer ' + token }, cache: 'no-store' });
            const server = await response.json();
            const receive = data.now(), monoReceive = performance.now() / 1000;
            if (!response.ok) throw Error('Clock authentication failed');
            const exchange = { client_send_s: send, client_receive_s: receive,
              client_send_monotonic_s: monoSend, client_receive_monotonic_s: monoReceive,
              wall_residual_s: Date.now() / 1000 - receive, ...server };
            if (event) data.events[event].witness = exchange;
            else data.clocks.push(exchange);
          } catch (error) { data.failures.push(String(error)); }
        };
        data.witness = event => data.exchange(event);
      }, { token: fixture.reader_token });
      for (let i = 0; i < 3; i++) await page.evaluate(() => window.evdtMeasurement.exchange());
      await page.evaluate(() => { window.evdtMeasurement.timer = setInterval(() => { void window.evdtMeasurement.exchange(); }, 5000); });
    }
    if (fixture.profile) {
      await pages[0].evaluate(() => localStorage.setItem('evdt-test-isolation', 'temporary-marker'));
      for (const page of pages.slice(1)) expect(await page.evaluate(() => localStorage.getItem('evdt-test-isolation'))).toBe(null);
      await pages[0].evaluate(() => localStorage.removeItem('evdt-test-isolation'));
      expect(await context.cookies()).toEqual([]);
      result.origin_storage_isolation_passed = true;
    }
    fs.writeFileSync(path.join(control, info.project.name + '-start'), 'ready');
    for (const page of pages) {
      await page.waitForFunction(total => Object.keys(window.evdtMeasurement.events).length === total,
        fixture.total_samples, { timeout: fixture.profile ? (fixture.total_samples + 150) * 1000 : 130_000 });
    }
    const originalPath = path.join(control, info.project.name + '-originals.json');
    await expect.poll(() => fs.existsSync(originalPath), { timeout: 20_000 }).toBe(true);
    const originals = JSON.parse(fs.readFileSync(originalPath, 'utf8'));
    for (let i = 0; i < pages.length; i++) {
      const page = pages[i];
      await page.evaluate(async () => { clearInterval(window.evdtMeasurement.timer); await Promise.all(window.evdtMeasurement.pending); await window.evdtMeasurement.exchange(); });
      const data = await page.evaluate(() => {
        const { events, clocks, failures } = window.evdtMeasurement;
        return { events, clocks, failures, clock_resolution_s: 0.002 };
      });
      assert.deepEqual(data.failures, []);
      assert.equal(Object.keys(data.events).length, fixture.total_samples);
      const formatter = new Intl.NumberFormat('en-US', { maximumFractionDigits: 3 });
      const events = fixture.profile ? JSON.parse(fs.readFileSync(originals[assets[i]], 'utf8')).events : originals[assets[i]];
      for (const original of events) {
        const rendered = data.events[original.event_id];
        assert.ok(rendered, 'Missing rendered original event');
        assert.equal(rendered.run_id, original.body.run_id);
        assert.equal(rendered.asset_id, original.body.asset_id);
        for (const sample of original.body.samples) {
          assert.equal(rendered.values[sample.signal_id], formatter.format(sample.value) + ' ' + sample.unit, 'Rendered value or unit differs');
        }
        rendered.values_verified = true;
        rendered.wire_sha256 = original.wire_sha256;
      }
      if (fixture.profile) {
        const name = info.project.name + '-measurement-asset-' + assets[i] + '.json';
        fs.writeFileSync(path.join(control, name), JSON.stringify(data));
        result.assets[assets[i]] = { file: name, events: Object.keys(data.events).length, passed: true };
      } else result.assets[assets[i]] = data;
      if (!fixture.profile || i === 0 || i === pages.length - 1) {
        await page.screenshot({ path: path.join(process.env.EVDT_BROWSER_OUTPUT, info.project.name + '-' + assets[i] + '-measurement.png'), fullPage: true });
      }
    }
    expect(errors).toEqual([]); expect(external).toEqual([]);
    for (const page of pages) expect(await page.evaluate(() => ({ local: localStorage.length, session: sessionStorage.length }))).toEqual({ local: 0, session: 0 });
    expect(await context.cookies()).toEqual([]);
    result.passed = true;
  } finally {
    // Preserve partial timing evidence on failure; never change its denominator.
    for (let i = 0; i < pages.length; i++) {
      if (!result.assets[assets[i]]) {
        try {
          const data = await pages[i].evaluate(() => {
          const { events, clocks, failures } = window.evdtMeasurement; return { events, clocks, failures };
          });
          if (fixture.profile) {
            const name = info.project.name + '-measurement-asset-' + assets[i] + '.json';
            fs.writeFileSync(path.join(control, name), JSON.stringify(data));
            result.assets[assets[i]] = { file: name, events: Object.keys(data.events).length, passed: false };
          } else result.assets[assets[i]] = data;
        } catch { /* The failed page is identified by the failed test result. */ }
      }
    }
    if (!result.passed) for (let i = 0; i < pages.length; i++) {
      try { await pages[i].screenshot({ path: path.join(process.env.EVDT_BROWSER_OUTPUT, info.project.name + '-failed-' + assets[i] + '.png'), timeout: 5000 }); } catch { /* Preserve other failed-page records too. */ }
    }
    for (const page of pages) await page.close();
    for (const session of networkContexts) {
      try { await session.tracing.stopHar(); }
      catch (error) { result.passed = false; result.network_error = String(error); }
    }
    for (const session of ownedContexts) await session.close();
    const resultPath = path.join(control, info.project.name + '-measurement-result.json');
    fs.writeFileSync(resultPath + '.tmp', JSON.stringify(result));
    fs.renameSync(resultPath + '.tmp', resultPath);
  }
});
