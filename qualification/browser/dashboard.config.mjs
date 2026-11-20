import { defineConfig } from '@playwright/test';
import path from 'node:path';
if (!process.env.EVDT_DASHBOARD_CONTROL || !process.env.EVDT_BASE_URL || !process.env.EVDT_BROWSER_OUTPUT) throw Error('Run scripts/monitoring/qualify_dashboard.py');
const tracePolicy = process.env.EVDT_MEASUREMENT_TRACE;
if (tracePolicy && tracePolicy !== 'action-har/1.0.0') throw Error('Unsupported measurement tracing policy');
export default defineConfig({
  testDir: './dashboard', workers: 1, retries: 0, timeout: 150_000,
  outputDir: path.join(process.env.EVDT_BROWSER_OUTPUT, 'artifacts'),
  reporter: [['line'], ['json', { outputFile: path.join(process.env.EVDT_BROWSER_OUTPUT, 'results.json') }]],
  use: { baseURL: process.env.EVDT_BASE_URL, viewport: { width: 1440, height: 1050 }, trace: process.env.EVDT_OPERATOR_CASE ? 'off' : tracePolicy ? { mode: 'retain-on-failure', screenshots: false, snapshots: false, sources: false } : 'retain-on-failure', serviceWorkers: 'block' },
  projects: [{ name: 'windows-edge-visible', use: { channel: 'msedge', headless: false } }, { name: 'chromium-automated', use: { channel: 'chromium', headless: true } }],
});
