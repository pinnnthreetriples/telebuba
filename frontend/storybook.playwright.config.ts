import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  testMatch: 'storybook.spec.ts',
  outputDir: './e2e/.artifacts/storybook',
  preserveOutput: 'always',
  timeout: 90_000,
  expect: { timeout: 30_000 },
  workers: 1,
  use: {
    baseURL: 'http://127.0.0.1:6106',
    screenshot: 'only-on-failure',
    launchOptions: process.env.PLAYWRIGHT_CHROME_PATH
      ? { executablePath: process.env.PLAYWRIGHT_CHROME_PATH }
      : undefined,
  },
  projects: [
    {
      name: 'desktop',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 900 } },
    },
    {
      name: 'mobile',
      use: { ...devices['Desktop Chrome'], viewport: { width: 375, height: 812 } },
    },
  ],
  webServer: {
    command: 'npm run storybook:test:server',
    url: 'http://127.0.0.1:6106/iframe.html?id=design-system-overview--patterns&viewMode=story',
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
  },
});
