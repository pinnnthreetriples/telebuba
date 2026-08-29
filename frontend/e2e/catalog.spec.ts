// Снимки каталога — гейт «визуальные тесты не показывают случайных изменений».
//
// Снимается по разделу, а не одной страницей целиком: страница на 4000 пикселей даёт
// один эталон, в котором сдвиг кнопки и сдвиг плашки выглядят одинаково — «файл
// отличается». Раздел — это наименьшая единица, о которой можно сказать, что именно
// поехало.
//
// Состояния, которыми владеет браузер, вызываются здесь, а не рисуются классами на
// странице: страница, которая «показывает hover» своими средствами, показывает догадку о
// нём. Образцы помечены `data-probe`, и цикл ниже — единственное место, которое знает, как
// каждое состояние получить и как снять его назад.
//
// Видов четыре, и `press` с `open` разделены не для красоты: `:active` живёт только пока
// кнопка ЗАЖАТА, поэтому его надо держать вокруг снимка (`mouse.down` → снимок →
// `mouse.up`), а выпадающему списку нужен полный клик. Пока оба звались `active` и
// вызывались через `click()`, снимок «active» показывал состояние покоя.
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
      const target = probe.locator('button, input, textarea, [tabindex]').first();
      // Индекс и вид, без подписи: подписи русские, а `\w` в имени файла их не пропускает
      // и складывает в `_`, то есть в имени всё равно оставался бы только индекс. Что за
      // образец — видно на самом снимке.
      const name = `probe-${String(i)}-${kind ?? 'none'}.png`;

      if (kind === 'hover') {
        // На самом контроле, а не на контейнере: `data-probe` висит на обёртке, и
        // наведение на обёртку попадает в контрол только потому, что тот её заполняет.
        // У кнопки `xs` или у переключателя это уже не так, и снимок «hover» получался бы
        // снимком покоя — то есть гейт проверял бы состояние, которого не вызвал.
        await target.hover();
      } else if (kind === 'focus') {
        await target.focus();
      } else if (kind === 'press') {
        // `:active` — это состояние ЗАЖАТОЙ кнопки, и `click()` его не оставляет: он
        // нажимает и отпускает, поэтому к моменту снимка кнопка уже в покое. Снимок
        // назывался «active» и показывал hover. Кнопку надо держать нажатой ВОКРУГ
        // снимка, а не до него.
        await target.hover();
        await page.mouse.down();
      } else if (kind === 'open') {
        // Отдельный вид, а не `press`: выпадающему списку нужен полный клик, чтобы
        // раскрыться. Пока оба назывались `active`, один и тот же вид означал «зажато» и
        // «раскрыто» — а это разные жесты и разные снимки.
        await target.click();
      } else {
        continue;
      }

      // Кадр вырезается по ОКРУГЛЁННОЙ рамке, а не элементом.
      //
      // `expect(locator).toHaveScreenshot()` режет по рамке элемента, а рамка дробная:
      // подпись под образцом набрана 12.5px с интерлиньяжем 1.45, то есть 18.125px, из
      // этого складывается дробная высота строки, а `items-center` у ряда превращает её в
      // дробный ВЕРХ у каждой ячейки. Клип от дробного верха даёт то 36 рядов, то 37 — и
      // расхождение размера Playwright считает провалом безусловно, порог расхождения к
      // размерам не применяется.
      //
      // Измерено на двух прогонах CI подряд: сначала упал `probe-0-hover` (37 → 36), после
      // переноса эталона — `probe-1-focus` (тот же 37 → 36) на другом раннере, при том же
      // коде. Дробная часть зависит от метрик шрифта раннера, поэтому пересъёмка эталона
      // тут не лечит, а переносит провал на соседний снимок — «перезапускают до зелёного»
      // в чистом виде.
      //
      // Целочисленный клип убирает саму зависимость: кадр всегда одного размера, а его
      // содержимое сдвинуто максимум на полпикселя — то, что и так лежит внутри порога
      // сорока пикселей. Округление, а не `Math.floor`/`ceil`, чтобы кадр не уползал в
      // одну сторону на каждой ступени.
      const box = await probe.boundingBox();
      if (box === null) throw new Error(`образец ${name} не имеет рамки`);
      await expect(page).toHaveScreenshot(name, {
        clip: {
          x: Math.round(box.x),
          y: Math.round(box.y),
          width: Math.round(box.width),
          height: Math.round(box.height),
        },
      });

      // Состояние снимается назад: зажатая кнопка, наведённая мышь или раскрытая панель,
      // оставленные на месте, попадают в снимок СЛЕДУЮЩЕГО образца.
      if (kind === 'press') await page.mouse.up();
      if (kind === 'open') await page.keyboard.press('Escape');
      await page.mouse.move(0, 0);
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
