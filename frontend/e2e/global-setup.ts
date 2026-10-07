import { chromium, type FullConfig } from '@playwright/test';

// Прогрев Vite до первого теста.
//
// На холодном кэше (CI) Vite собирает зависимости по мере того, как страницы их
// просят, и если параллельный воркер найдёт пакет, которого не было в первой сборке,
// сервер пересобирает их и ПЕРЕЗАГРУЖАЕТ все открытые страницы. Тест, у которого в этот
// момент шёл `page.evaluate`, падает с «Execution context was destroyed» — не по своей
// вине. Прогрев открывает каталог и приложение по одному разу и ждёт, пока сеть
// утихнет: пересборка, если она нужна, случается здесь, до того как тесты начались.
export default async function globalSetup(config: FullConfig): Promise<void> {
  const baseURL = config.projects[0]?.use.baseURL ?? 'http://127.0.0.1:8132';
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage();
    for (const path of ['/catalog/', '/']) {
      await page.goto(`${baseURL}${path}`, { waitUntil: 'networkidle', timeout: 120_000 });
    }
    // Второй заход по каталогу: если первый вызвал пересборку, страница после неё
    // должна загрузиться уже без новой.
    await page.goto(`${baseURL}/catalog/`, { waitUntil: 'networkidle', timeout: 120_000 });
    await page.locator('#controls').waitFor({ timeout: 60_000 });
  } finally {
    await browser.close();
  }
}
