import { defineConfig } from '@playwright/test';
import path from 'node:path';
if (!process.env.EVDT_DASHBOARD_CONTROL || !process.env.EVDT_BASE_URL || !process.env.EVDT_BROWSER_OUTPUT) throw Error('Use the owned energy product qualifier');
export default defineConfig({
  testDir: './energy', workers: 1, retries: 0, timeout: 90_000,
  outputDir: path.join(process.env.EVDT_BROWSER_OUTPUT, 'artifacts'),
  reporter: [['line'], ['json', { outputFile: path.join(process.env.EVDT_BROWSER_OUTPUT, 'playwright.json') }]],
  use: { baseURL: process.env.EVDT_BASE_URL, viewport: { width: 1440, height: 1050 }, trace: 'retain-on-failure', headless: true, serviceWorkers: 'block' },
  projects: [{ name: 'chromium', use: { channel: 'chromium' } }],
});
