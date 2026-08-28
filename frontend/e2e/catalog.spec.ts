// Снимки каталога — гейт «визуальные тесты не показывают случайных изменений».
//
// Снимается по разделу, а не одной страницей целиком: страница на 4000 пикселей даёт
// один эталон, в котором сдвиг кнопки и сдвиг плашки выглядят одинаково — «файл
// отличается». Раздел — это наименьшая единица, о которой можно сказать, что именно
// поехало.
//
// Состояния hover, active и focus вызываются здесь, а не рисуются классами на странице:
// они принадлежат браузеру, и страница, которая «показывает hover» своими средствами,
// показывает догадку о нём. Образцы помечены `data-probe`, и цикл ниже — единственное
// место, которое знает, как эти состояния получить.
import { expect, test, type Page } from '@playwright/test';

const SECTIONS = ['controls', 'feedback', 'surfaces', 'typography'] as const;

async function open(page: Page) {
  await page.goto('/catalog/');
  // Шрифты — часть снимка: Inter грузится из `@fontsource`, и снимок до её загрузки
  // отличается от снимка после метриками каждой строки.
  await page.evaluate(async () => {
    await document.fonts.ready;
  });
  await expect(page.locator('#controls')).toBeVisible();
}

test.describe('каталог дизайн-системы', () => {
  for (const id of SECTIONS) {
    test(`раздел «${id}» выглядит как эталон`, async ({ page }) => {
      await open(page);
      await expect(page.locator(`#${id}`)).toHaveScreenshot(`${id}.png`);
    });
  }

  test('состояния, которые задаёт браузер', async ({ page }) => {
    await open(page);
    const probes = page.locator('[data-probe]');
    const total = await probes.count();
    // Пустой список пометок выглядел бы как зелёный прогон: цикл ниже ничего бы не
    // сделал, и гейт молча перестал бы проверять состояния.
    expect(total).toBeGreaterThan(10);

    for (let i = 0; i < total; i += 1) {
      const probe = probes.nth(i);
      const kind = await probe.getAttribute('data-probe');
      const cell = (await probe.getAttribute('data-cell')) ?? String(i);
      const target = probe.locator('button, input, textarea, [tabindex]').first();
      const name = `probe-${String(i)}-${kind ?? 'none'}-${cell.replace(/[^\w-]+/g, '_')}.png`;

      if (kind === 'hover') await probe.hover();
      else if (kind === 'focus') await target.focus();
      else if (kind === 'active') await target.click();
      else continue;

      await expect(probe).toHaveScreenshot(name);
      // Состояние снимается и снимается назад: наведённая мышь или открытая панель,
      // оставленная на месте, попадает в снимок СЛЕДУЮЩЕГО образца.
      await page.mouse.move(0, 0);
      if (kind === 'active') await page.keyboard.press('Escape');
      await target.evaluate((node: HTMLElement) => {
        node.blur();
      });
    }
  });

  test('диалог поверх каталога', async ({ page }) => {
    await open(page);
    await page.locator('[data-catalog="open-modal"]').click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await expect(page).toHaveScreenshot('modal.png');
    await page.keyboard.press('Escape');

    await page.locator('[data-catalog="open-confirm"]').click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await expect(page).toHaveScreenshot('confirm.png');
  });
});
