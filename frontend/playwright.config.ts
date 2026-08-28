import { defineConfig, devices } from '@playwright/test';

// Визуальный гейт каталога, и он держит один порт — 8132.
//
// Порт назван, а не выбран автоматически, по причине из `.mex/context`: :8080 занят
// живым стендом, и снимать надо не его. Vite поднимается самим Playwright и гасится
// после прогона; `reuseExistingServer` оставлен включённым только вне CI, чтобы
// локальные повторные прогоны не ждали холодный старт.
//
// Один браузер и две ширины. Chromium — потому что снимок сравнивается сам с собой, а
// не с другим движком: цель гейта — заметить, что своя же правка сдвинула пиксель, и
// второй движок к этому вопросу ничего не добавляет, зато добавляет 250 МБ и второй
// набор эталонов. `deviceScaleFactor: 1` — чтобы эталоны не зависели от DPI машины.
export default defineConfig({
  testDir: './e2e',
  outputDir: './e2e/.artifacts',
  // Имя проекта в пути обязательно: без него desktop и mobile пишут в один файл, и второй
  // проект молча перезаписывает эталон первого — гейт остаётся зелёным, проверяя одну
  // ширину вместо двух.
  snapshotPathTemplate: '{testDir}/__screenshots__/{projectName}/{arg}{ext}',
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  reporter: process.env.CI ? 'line' : 'list',
  expect: {
    // Сглаживание шрифтов даёт единицы отличающихся пикселей на одинаковой странице,
    // и нулевой порог превратил бы гейт в источник ложных падений. 0.2% от площади —
    // это заметно меньше, чем любой сдвиг ступени токена, и заметно больше, чем шум.
    toHaveScreenshot: { maxDiffPixelRatio: 0.002, animations: 'disabled' },
  },
  use: {
    baseURL: 'http://127.0.0.1:8132',
    deviceScaleFactor: 1,
    trace: 'retain-on-failure',
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
    command: 'npm run catalog',
    url: 'http://127.0.0.1:8132/catalog/',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
