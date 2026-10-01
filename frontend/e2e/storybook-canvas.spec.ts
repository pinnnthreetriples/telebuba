import { expect, test, type Page } from '@playwright/test';

const previewFailures = new WeakMap<Page, string[]>();

test.beforeEach(async ({ page }) => {
  const failures: string[] = [];
  page.on('console', (message) => {
    if (message.type() === 'error' && message.text().includes('Missing design-system fixture:'))
      failures.push(message.text());
  });
  page.on('pageerror', (error) => failures.push(error.message));
  await page.route('**/api/v1/**', (route) => {
    failures.push(`Unexpected network request: ${route.request().url()}`);
    return route.abort();
  });
  test.info().annotations.push({
    type: 'offline-preview',
    description: 'API network blocked; fixtures and JS errors checked',
  });
  previewFailures.set(page, failures);
});

test.afterEach(async ({ page }) => {
  expect(previewFailures.get(page)).toEqual([]);
});

test('foundations shows settings from code and editing locations', async ({ page }) => {
  await page.goto('/iframe.html?id=design-system-foundations--all&viewMode=story');
  await expect(page.getByRole('heading', { name: 'Настройки дизайн-системы' })).toBeVisible();
  await expect(page.getByText('components.ts', { exact: true }).first()).toBeVisible();
  await expect(page.getByText('card.padding.default.x', { exact: true })).toBeVisible();
  await expect(page.getByText('controls.height.md', { exact: true })).toBeVisible();
});

const screens = [
  ['accounts', 'Аккаунты'],
  ['warming', 'Прогрев аккаунтов'],
  ['neurocomment', 'Нейрокомментинг'],
  ['neuroshilling', 'Нейрошиллинг'],
  ['logs', 'Логи'],
  ['settings', 'Настройки'],
  ['login', 'Вход'],
] as const;

for (const [id, title] of screens) {
  test(`canvas renders real ${id} screen without backend requests`, async ({ page }) => {
    const unexpected: string[] = [];
    await page.route('**/api/v1/**', (route) => {
      unexpected.push(new URL(route.request().url()).pathname);
      return route.abort();
    });
    await page.goto(`/iframe.html?id=design-system-screens--${id}&viewMode=story`);
    await expect(page.getByRole('heading', { name: title, exact: true }).first()).toBeVisible();
    expect(unexpected).toEqual([]);
  });
}

test('canvas language follows its global control', async ({ page }) => {
  await page.goto(
    '/iframe.html?id=design-system-screens--settings&viewMode=story&globals=locale:en',
  );
  await expect(page.getByRole('heading', { name: 'Settings', exact: true })).toBeVisible();
});

test('screen fixtures expose a genuine empty state', async ({ page }) => {
  await page.goto(
    '/iframe.html?id=design-system-screens--accounts&viewMode=story&args=state:empty',
  );
  await expect(page.getByText('Аккаунтов нет', { exact: true })).toBeVisible();
});

test('settings loading and error states come from preview transport', async ({ page }) => {
  await page.goto(
    '/iframe.html?id=design-system-screens--settings&viewMode=story&args=state:loading',
  );
  await expect(page.getByText('Загрузка…', { exact: true })).toBeVisible();
  await expect(page.getByRole('textbox')).toHaveCount(0);
  await page.goto(
    '/iframe.html?id=design-system-screens--settings&viewMode=story&args=state:error',
  );
  await expect(page.getByRole('alert')).toHaveText('Не удалось загрузить настройки');
});

for (const id of ['campaign-details', 'campaign-settings', 'advanced-limits', 'media', 'approve']) {
  test(`real ${id} dialog supports narrow viewport and keyboard close`, async ({
    page,
  }, testInfo) => {
    await page.goto(
      `/iframe.html?id=design-system-patterns-campaign-dialogs--${id}&viewMode=story`,
    );
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();
    await page.evaluate(async () => document.fonts.ready);
    const viewport = page.viewportSize();
    if (!viewport) throw new Error('Missing viewport');
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
      viewport.width,
    );
    for (let index = 0; index < 4; index++) {
      await page.keyboard.press('Tab');
      expect(await dialog.evaluate((element) => element.contains(document.activeElement))).toBe(
        true,
      );
    }
    const screenshot = testInfo.outputPath(`${id}.png`);
    await page.screenshot({ path: screenshot, fullPage: true });
    await testInfo.attach(id, { path: screenshot, contentType: 'image/png' });
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
  });
}

test('campaign settings conflict disables saving', async ({ page }) => {
  await page.goto(
    '/iframe.html?id=design-system-patterns-campaign-dialogs--campaign-settings&viewMode=story&args=state:conflict',
  );
  await expect(page.getByRole('dialog').getByRole('alert')).toBeVisible();
  await expect(
    page.getByRole('dialog').getByRole('button', { name: 'Сохранить настройки', exact: true }),
  ).toBeDisabled();
});

test('media draft edits persist locally after apply', async ({ page }) => {
  await page.goto('/iframe.html?id=design-system-patterns-campaign-dialogs--media&viewMode=story');
  const textbox = page.getByRole('dialog').getByRole('textbox');
  await textbox.fill('https://t.me/example_channel/42');
  await page.getByRole('dialog').getByRole('button', { name: 'Прикрепить', exact: true }).click();
  await expect(page.getByRole('dialog')).toBeHidden();
  await page.getByRole('button').click();
  await expect(page.getByRole('dialog').getByRole('textbox')).toHaveValue(
    'https://t.me/example_channel/42',
  );
});

test('segmented control arrow and Home keys update its real state', async ({ page }) => {
  await page.goto('/iframe.html?id=design-system-components-segmentedcontrol--tray&viewMode=story');
  const selected = page.getByRole('radio', { name: 'В работе', exact: true });
  await selected.focus();
  await page.keyboard.press('ArrowRight');
  await expect(page.getByRole('radio', { name: 'Стоп', exact: true })).toHaveAttribute(
    'aria-checked',
    'true',
  );
  await page.keyboard.press('Home');
  await expect(page.getByRole('radio', { name: 'Все', exact: true })).toBeFocused();
  await expect(page.getByRole('radio', { name: 'Все', exact: true })).toHaveAttribute(
    'aria-checked',
    'true',
  );
});

test('API key field masks only locally entered demo text', async ({ page }) => {
  await page.goto('/iframe.html?id=design-system-patterns-apikeyfield--stored&viewMode=story');
  const input = page.locator('input');
  await input.fill('preview-only-example');
  await expect(input).toHaveAttribute('type', 'password');
  await page.getByRole('button', { name: 'Показать/скрыть ключ', exact: true }).click();
  await expect(input).toHaveAttribute('type', 'text');
  await page.getByRole('button', { name: 'Очистить ключ', exact: true }).click();
  await expect(input).toHaveValue('');
  await expect(page.getByRole('button', { name: 'Очистить ключ', exact: true })).toHaveCount(0);
});

test('multiple consumers share padding, field height and label spacing', async ({
  page,
}, testInfo) => {
  await page.goto(
    '/iframe.html?id=design-system-components-managed-geometry--propagation&viewMode=story',
  );
  await expect(page.getByRole('heading', { name: 'Propagation canvas' })).toBeVisible();
  const geometry = await page.evaluate(() => {
    const read = (selector: string) => {
      const element = document.querySelector<HTMLElement>(selector);
      if (!element) throw new Error(`Missing propagation consumer: ${selector}`);
      const css = getComputedStyle(element);
      return {
        height: element.getBoundingClientRect().height,
        padding: css.padding,
        marginBottom: css.marginBottom,
        gap: css.gap,
      };
    };
    return {
      stack: read('[data-testid="propagation-stack"]'),
      cards: [1, 2].map((index) => read(`[data-testid="propagation-card-${index}"]`)),
      inputs: [1, 2].map((index) => read(`[data-testid="propagation-input-${index}"]`)),
      buttons: [1, 2].map((index) => read(`[data-testid="propagation-button-${index}"]`)),
      labels: [1, 2].map((index) => read(`[data-testid="propagation-card-${index}"] label > span`)),
    };
  });
  expect(geometry.cards[0]?.padding).toBe(geometry.cards[1]?.padding);
  expect(parseFloat(geometry.cards[0]?.padding ?? '')).toBeGreaterThan(0);
  expect(geometry.inputs.map(({ height }) => height)).toEqual(
    geometry.buttons.map(({ height }) => height),
  );
  expect(geometry.labels[0]?.marginBottom).toBe(geometry.labels[1]?.marginBottom);
  expect(parseFloat(geometry.labels[0]?.marginBottom ?? '')).toBeGreaterThan(0);
  expect(parseFloat(geometry.stack.gap)).toBeGreaterThan(0);
  await testInfo.attach('rendered-geometry.json', {
    body: JSON.stringify(geometry, null, 2),
    contentType: 'application/json',
  });
  await page.screenshot({ path: testInfo.outputPath('propagation.png'), fullPage: true });
});

test('action and refresh text keep 4.5 contrast on canvas including hover', async ({ page }) => {
  await page.goto(
    '/iframe.html?id=design-system-components-button--contrast-canvas&viewMode=story',
  );
  for (const name of ['Ghost action', 'Refresh success', 'Refresh danger']) {
    const button = page.getByRole('button', { name, exact: true });
    const contrast = () =>
      button.evaluate((element) => {
        const channels = (color: string) => (color.match(/[\d.]+/g) ?? []).map(Number);
        const luminance = (rgb: number[]) =>
          rgb.slice(0, 3).reduce((sum, channel, index) => {
            const value = channel / 255;
            const linear = value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
            return sum + linear * ([0.2126, 0.7152, 0.0722][index] ?? 0);
          }, 0);
        const layers: number[][] = [];
        let current: Element | null = element;
        while (current) {
          layers.unshift(channels(getComputedStyle(current).backgroundColor));
          current = current.parentElement;
        }
        const background = layers.reduce(
          (base, layer) => {
            const alpha = layer[3] ?? 1;
            return base.map(
              (channel, index) => (layer[index] ?? 0) * alpha + channel * (1 - alpha),
            );
          },
          [255, 255, 255],
        );
        const foreground = channels(getComputedStyle(element).color);
        const ink = luminance(foreground);
        const fill = luminance(background);
        return (Math.max(ink, fill) + 0.05) / (Math.min(ink, fill) + 0.05);
      });
    await expect.poll(contrast).toBeGreaterThanOrEqual(4.5);
    await button.hover();
    await expect.poll(contrast).toBeGreaterThanOrEqual(4.5);
    await page.mouse.move(0, 0);
  }
});

test('real neuroshilling page opens full settings from its atomic fixture', async ({ page }) => {
  await page.goto('/iframe.html?id=design-system-screens--neuroshilling&viewMode=story');
  await page.getByRole('button', { name: 'Действия кампании', exact: true }).first().click();
  await page.getByRole('button', { name: 'Настройки кампании', exact: true }).first().click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText('Конфигурация сценария', { exact: true })).toBeVisible();
  await expect(dialog.getByText('Цели', { exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(dialog).toBeHidden();
});

test('approve empty state clearly disables approval', async ({ page }) => {
  await page.goto(
    '/iframe.html?id=design-system-patterns-campaign-dialogs--approve&viewMode=story&args=state:empty',
  );
  await expect(
    page
      .getByRole('dialog')
      .getByText('Превью пока нечего показать. Сгенерируйте диалог или напишите его руками.', {
        exact: true,
      }),
  ).toBeVisible();
  await expect(
    page.getByRole('dialog').getByRole('button', { name: 'Утвердить', exact: true }),
  ).toBeDisabled();
});

test('draft approval is enabled and stays local', async ({ page }) => {
  await page.goto(
    '/iframe.html?id=design-system-patterns-campaign-dialogs--approve-draft&viewMode=story',
  );
  const approve = page.getByRole('dialog').getByRole('button', { name: 'Утвердить', exact: true });
  await expect(approve).toBeEnabled();
  await approve.click();
  await expect(page.getByRole('dialog')).toBeHidden();
});

test('Storybook server rejects API traffic instead of proxying production', async ({ request }) => {
  const response = await request.get('/api/v1/health');
  expect(response.status()).toBe(503);
  expect(await response.json()).toMatchObject({ error: { code: 'storybook_offline' } });
});
