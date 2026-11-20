import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';

const control = process.env.EVDT_DASHBOARD_CONTROL;
const fixture = JSON.parse(fs.readFileSync(path.join(control, 'ready.json'), 'utf8'));

test('one fleet page correlates every original reading of every simultaneous bus', async ({ page, context, browser }, info) => {
  test.setTimeout((fixture.total_samples + 400) * 1000);
  const assets = fixture.pairs[info.project.name], errors = [], external = [];
  const result = { passed: false, project: info.project.name, browser: browser.version(),
    viewer_policy: 'fleet-operator-table/1.0.0', browser_pages: 1,
    trace_policy: fixture.trace_policy, network_archives: [], asset_urls: fixture.asset_urls, assets: {} };
  let tracing = false;
  const saveAsset = async (asset, verified) => {
    const data = await page.evaluate(asset => {
      const { events, clocks, failures } = window.evdtFleetMeasurement.buses[asset];
      return { events, clocks, failures, clock_resolution_s: 0.002 };
    }, asset);
    const name = info.project.name + '-measurement-asset-' + asset + '.json';
    fs.writeFileSync(path.join(control, name), JSON.stringify(data));
    result.assets[asset] = { file: name, events: Object.keys(data.events).length, passed: verified };
    return data;
  };
  try {
    expect(fixture.profile.browser_viewer_policy).toBe(result.viewer_policy);
    expect(fixture.profile.browser_storage_contexts).toBe(1);
    expect(fixture.profile.browser_pages).toBe(1);
    expect(fixture.trace_policy).toBe('action-har/1.0.0');
    expect(new Set(Object.values(fixture.asset_urls))).toEqual(new Set([fixture.base_url]));
    expect(new URL(fixture.base_url).hostname).toBe('127.0.0.1');
    const archive = info.project.name + '-fleet.har';
    await context.tracing.startHar(path.join(process.env.EVDT_BROWSER_OUTPUT, archive), { content: 'omit', mode: 'full' });
    tracing = true;
    result.network_archives.push(archive);
    page.on('pageerror', error => errors.push(String(error)));
    page.on('request', request => {
      if (new URL(request.url()).origin !== new URL(fixture.base_url).origin) external.push(request.url());
    });
    await page.addInitScript(({ assets }) => {
      const baseUTC = Date.now() / 1000, baseMono = performance.now() / 1000;
      const now = () => baseUTC + performance.now() / 1000 - baseMono;
      const data = { now, pending: [], buses: Object.fromEntries(assets.map(asset => [asset, { events: {}, clocks: [], failures: [] }])) };
      window.evdtFleetMeasurement = data;
      const read = () => {
        for (const asset of assets) {
          const bus = data.buses[asset];
          const panel = document.querySelector(`[data-fleet-asset="${asset}"]`);
          if (!panel) continue;
          const rows = [...panel.querySelectorAll('[data-signal][data-event]')];
          if (rows.length !== 20) continue;
          const event = rows[0].dataset.event;
          if (!event || bus.events[event] || rows.some(row => row.dataset.event !== event)) continue;
          const values = {};
          for (const row of rows) {
            if (row.dataset.asset !== asset || row.dataset.run !== rows[0].dataset.run || row.dataset.source !== rows[0].dataset.source) { bus.failures.push('Cross-asset/run/source DOM identity'); return; }
            if (getComputedStyle(row).display === 'none' || !row.getClientRects().length) return;
            values[row.dataset.signal] = row.querySelectorAll('td')[1].textContent.trim();
          }
          bus.events[event] = { utc_s: now(), monotonic_s: performance.now() / 1000,
            wall_residual_s: Date.now() / 1000 - now(), run_id: rows[0].dataset.run,
            asset_id: asset, source_id: rows[0].dataset.source, event_id: event, values };
          if (data.exchange) data.pending.push(data.exchange(asset, event));
          else bus.failures.push('DOM event before witness collector readiness');
        }
      };
      new MutationObserver(read).observe(document, { attributes: true, childList: true, subtree: true, characterData: true });
    }, { assets });
    await page.goto(fixture.base_url + '/');
    expect(await page.evaluate(() => ({ local: localStorage.length, session: sessionStorage.length }))).toEqual({ local: 0, session: 0 });
    await page.getByLabel('Dashboard key').fill(fixture.reader_token);
    await page.getByRole('button', { name: 'Connect to monitoring' }).click();
    await page.getByRole('button', { name: 'Fleet', exact: true }).click();
    await page.getByLabel('Fleet vehicles').selectOption(assets);
    await expect(page.locator('[data-fleet-asset]')).toHaveCount(assets.length);
    for (const asset of assets) {
      const panel = page.locator(`[data-fleet-asset="${asset}"]`);
      await expect(panel.locator('[data-signal]')).toHaveCount(20);
      await expect(panel).toHaveAttribute('data-connection', 'live');
    }
    expect((await page.request.get(fixture.base_url + '/v1/time')).status()).toBe(401);
    await page.evaluate(({ token, assets }) => {
      const data = window.evdtFleetMeasurement;
      data.exchange = async (asset, event = null) => {
        const bus = data.buses[asset], send = data.now(), monoSend = performance.now() / 1000;
        try {
          const suffix = event ? '?asset_id=' + encodeURIComponent(asset) + '&event_id=' + encodeURIComponent(event) : '';
          const response = await fetch('/v1/time' + suffix, { headers: { Authorization: 'Bearer ' + token }, cache: 'no-store' });
          const server = await response.json(), receive = data.now(), monoReceive = performance.now() / 1000;
          if (!response.ok) throw Error('Clock authentication failed');
          const exchange = { client_send_s: send, client_receive_s: receive,
            client_send_monotonic_s: monoSend, client_receive_monotonic_s: monoReceive,
            wall_residual_s: Date.now() / 1000 - receive, ...server };
          if (event) bus.events[event].witness = exchange;
          else bus.clocks.push(exchange);
        } catch (error) { bus.failures.push(String(error)); }
      };
      data.timer = setInterval(() => { for (const asset of assets) data.pending.push(data.exchange(asset)); }, 5000);
    }, { token: fixture.reader_token, assets });
    for (let i = 0; i < 3; i++) for (const asset of assets)
      await page.evaluate(asset => window.evdtFleetMeasurement.exchange(asset), asset);
    expect(await context.cookies()).toEqual([]);
    result.per_asset_dom_isolation_passed = true;
    fs.writeFileSync(path.join(control, info.project.name + '-start'), 'ready');
    await page.waitForFunction(({ assets, total }) => assets.every(asset => Object.keys(window.evdtFleetMeasurement.buses[asset].events).length === total),
      { assets, total: fixture.total_samples }, { timeout: (fixture.total_samples + 150) * 1000 });
    const originalPath = path.join(control, info.project.name + '-originals.json');
    await expect.poll(() => fs.existsSync(originalPath), { timeout: 20_000 }).toBe(true);
    const originals = JSON.parse(fs.readFileSync(originalPath, 'utf8'));
    await page.evaluate(async assets => {
      const data = window.evdtFleetMeasurement;
      clearInterval(data.timer);
      await Promise.all(data.pending);
      for (const asset of assets) await data.exchange(asset);
    }, assets);
    const formatter = new Intl.NumberFormat('en-US', { maximumFractionDigits: 3 });
    for (const asset of assets) {
      const data = await saveAsset(asset, false);
      assert.deepEqual(data.failures, []);
      assert.equal(Object.keys(data.events).length, fixture.total_samples);
      const events = JSON.parse(fs.readFileSync(originals[asset], 'utf8')).events;
      for (const original of events) {
        const rendered = data.events[original.event_id];
        assert.ok(rendered, 'Missing rendered original event');
        assert.equal(rendered.run_id, original.body.run_id);
        assert.equal(rendered.asset_id, original.body.asset_id);
        assert.equal(rendered.source_id, original.body.source_id);
        for (const sample of original.body.samples)
          assert.equal(rendered.values[sample.signal_id], formatter.format(sample.value) + ' ' + sample.unit, 'Rendered value or unit differs');
        rendered.values_verified = true;
        rendered.wire_sha256 = original.wire_sha256;
      }
      fs.writeFileSync(path.join(control, result.assets[asset].file), JSON.stringify(data));
      result.assets[asset].passed = true;
    }
    expect(errors).toEqual([]); expect(external).toEqual([]);
    expect(await page.evaluate(() => ({ local: localStorage.length, session: sessionStorage.length }))).toEqual({ local: 0, session: 0 });
    expect(await context.cookies()).toEqual([]);
    result.empty_browser_storage_passed = true;
    await page.evaluate(() => scrollTo(0, 0));
    await page.screenshot({ path: path.join(process.env.EVDT_BROWSER_OUTPUT, info.project.name + '-fleet-measurement.png'), fullPage: true });
    result.passed = true;
  } finally {
    for (const asset of assets) if (!result.assets[asset]) {
      try { await saveAsset(asset, false); } catch { /* Preserve other assets even when the page failed. */ }
    }
    if (!result.passed) {
      try { await page.screenshot({ path: path.join(process.env.EVDT_BROWSER_OUTPUT, info.project.name + '-fleet-failed.png'), timeout: 5000 }); } catch { /* Partial event files remain. */ }
    }
    if (tracing) {
      try { await context.tracing.stopHar(); }
      catch (error) { result.passed = false; result.network_error = String(error); }
    }
    const resultPath = path.join(control, info.project.name + '-measurement-result.json');
    fs.writeFileSync(resultPath + '.tmp', JSON.stringify(result));
    fs.renameSync(resultPath + '.tmp', resultPath);
  }
});
