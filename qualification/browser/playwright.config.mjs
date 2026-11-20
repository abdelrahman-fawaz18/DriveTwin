import { defineConfig } from '@playwright/test';
import path from 'node:path';

if (!process.env.EVDT_BROWSER_OUTPUT || !process.env.EVDT_BASE_URL || !process.env.EVDT_EXPECTED_RUN) {
  throw new Error('Run through scripts/platform/qualify_browser.py to select an owned fixture and evidence folder.');
}
const output = process.env.EVDT_BROWSER_OUTPUT;
export default defineConfig({
  testDir: './tests',
  workers: 1,
  fullyParallel: false,
  retries: 0,
  timeout: 30_000,
  outputDir: path.join(output, 'test-artifacts'),
  reporter: [['line'], ['json', { outputFile: path.join(output, 'results.json') }],
             ['html', { outputFolder: path.join(output, 'html-report'), open: 'never' }]],
  use: {
    baseURL: process.env.EVDT_BASE_URL,
    viewport: { width: 1280, height: 900 },
    trace: 'on',
    screenshot: 'on',
    serviceWorkers: 'block',
  },
  projects: [
    { name: 'windows-edge-visible', use: { channel: 'msedge', headless: false } },
    { name: 'chromium-automated', use: { channel: 'chromium', headless: true } },
  ],
});
