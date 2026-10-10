import { expect, test, type Page } from '@playwright/test';

import * as fx from './fixtures';

const routes: [RegExp, unknown][] = [
  [/\/auth\/me$/, fx.me],
  [/\/health$/, fx.health],
  [/\/accounts\/bulk-messages\/(active|latest)$/, null],
  [/\/accounts\/stats$/, fx.accountStats],
  [/\/accounts\/filter-options$/, fx.accountFilterOptions],
  [/\/account-folders$/, fx.accountFolders],
  [/\/accounts(\?|$)/, fx.accounts],
  [/\/proxies(\?|$)/, fx.proxies],
  [/\/warming\/board/, fx.warmingBoard],
  [/\/warming\/settings/, fx.warmingSettings],
  [/\/warming\/dialogues/, fx.warmingDialogues],
  [/\/warming\/(channels|warmed)/, {}],
  [/\/neurocomment\/campaigns\/[^/]+\/board/, fx.neurocommentBoard],
  [/\/neurocomment\/campaigns\/[^/]+\/(challenges|comments|discovery)/, fx.challenges],
  [/\/neurocomment\/campaigns$/, fx.neurocommentCampaigns],
  [/\/neurocomment\/runtime/, fx.neurocommentRuntime],
  [/\/neurocomment\/settings/, fx.neurocommentSettings],
  [/\/neuroshilling\/campaigns\/[^/]+\/board/, fx.neuroshillingBoard],
  [/\/neuroshilling\/campaigns\/[^/]+\/scenario/, fx.neuroshillingScenario],
  [/\/neuroshilling\/campaigns\/[^/]+\/settings/, fx.neuroshillingSettings],
  [/\/neuroshilling\/campaigns$/, fx.neuroshillingCampaigns],
  [/\/logs/, fx.logs],
  [/\/events/, { events: [] }],
];

async function setup(page: Page) {
  await page.route('**/api/v1/**', async (route) => {
    const url = route.request().url();
    const match = routes.find(([pattern]) => pattern.test(url));
    if (!match) throw new Error(`Missing fixture for ${url}`);
    if (!url.endsWith('/auth/me')) await new Promise((resolve) => setTimeout(resolve, 350));
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(match[1]),
    });
  });
}

async function goTo(page: Page, name: string) {
  if ((page.viewportSize()?.width ?? 1280) < 1024) {
    await page.getByRole('button', { name: 'Меню' }).click();
    await page.getByRole('dialog', { name: 'Меню' }).getByRole('link', { name }).click();
  } else {
    await page.getByRole('navigation').getByRole('link', { name }).click();
  }
}

test('navigation reveals each page with its primary data already in place', async ({
  page,
}, testInfo) => {
  await setup(page);
  await page.goto('/');
  await page.getByRole('heading', { name: 'Аккаунты' }).waitFor();
  const destinations = [
    { link: 'Нейрокомментинг', title: 'Нейрокомментинг', data: 'Доска работ' },
    { link: 'Нейрошиллинг', title: 'Нейрошиллинг', data: 'Конвейер' },
    { link: 'Прогрев', title: 'Прогрев аккаунтов', data: 'Готовы к прогреву' },
    { link: 'Логи', title: 'Логи', data: 'Комментарий опубликован' },
    { link: 'Настройки', title: 'Настройки', data: 'API-ключи' },
    { link: 'Аккаунты', title: 'Аккаунты', data: '@user_0' },
  ];
  for (const destination of destinations) {
    await goTo(page, destination.link);
    await expect(page.getByRole('heading', { name: destination.title })).toBeVisible();
    const visible = await page.evaluate(
      (text) => document.querySelector('main')?.innerText.includes(text) ?? false,
      destination.data,
    );
    expect(visible, `${destination.title} must not paint a partial page`).toBe(true);
    if (destination.link === 'Нейрошиллинг') {
      await page.screenshot({ path: testInfo.outputPath('neuroshilling-after.png') });
    }
  }
});

test('navigation keeps the active tab aligned with the visible page while loading', async ({
  page,
}) => {
  test.skip((page.viewportSize()?.width ?? 1280) < 1024, 'Desktop active rail');
  await setup(page);
  await page.goto('/');
  await expect(page.getByText('@user_0')).toBeVisible();
  await page.getByRole('navigation').getByRole('link', { name: 'Нейрокомментинг' }).click();
  const state = await page.evaluate(() => ({
    title: document.querySelector('main h1')?.textContent,
    active: document.querySelector('nav a.text-content-primary')?.textContent,
  }));
  expect(state).toEqual({ title: 'Аккаунты', active: 'Аккаунты' });
  await expect(page.getByRole('heading', { name: 'Нейрокомментинг' })).toBeVisible();
});

test('switching from a scrolled page starts the next page at the top', async ({ page }) => {
  test.skip((page.viewportSize()?.width ?? 1280) < 1024, 'Desktop sticky navigation');
  await setup(page);
  await page.goto('/warming');
  await expect(page.getByText('Готовы к прогреву')).toBeVisible();
  await page.evaluate(() => window.scrollTo(0, 700));
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(100);
  await goTo(page, 'Нейрошиллинг');
  await expect(page.getByRole('heading', { name: 'Нейрошиллинг' })).toBeVisible();
  expect(await page.evaluate(() => window.scrollY)).toBe(0);
});

test('return navigation waits for an invalidated campaign list', async ({ page }) => {
  await setup(page);
  let changed = false;
  await page.route('**/api/v1/neurocomment/campaigns', async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 350));
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(changed ? { campaigns: [] } : fx.neurocommentCampaigns),
    });
  });
  await page.goto('/neurocomment');
  await expect(page.getByText('Крипта', { exact: true }).first()).toBeVisible();
  await goTo(page, 'Аккаунты');
  await expect(page.getByRole('heading', { name: 'Аккаунты' })).toBeVisible();
  changed = true;
  await page.waitForTimeout(1_100);
  await goTo(page, 'Нейрокомментинг');
  await expect(page.getByRole('heading', { name: 'Нейрокомментинг' })).toBeVisible();
  expect(await page.getByText('Крипта', { exact: true }).count()).toBe(0);
});

test('a stalled logs request cannot hold neurocomment navigation indefinitely', async ({
  page,
}) => {
  await setup(page);
  await page.route(
    (url) =>
      url.pathname.endsWith('/logs') && url.searchParams.get('event_prefix') === 'neurocomment',
    async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 5_000));
      await route.abort();
    },
  );
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Аккаунты' })).toBeVisible();
  await goTo(page, 'Нейрокомментинг');
  await expect(page.getByRole('heading', { name: 'Нейрокомментинг' })).toBeVisible({
    timeout: 3_000,
  });
});

test('a stalled board request yields to the page loading state', async ({ page }) => {
  await setup(page);
  await page.route('**/api/v1/neurocomment/campaigns/*/board', async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 5_000));
    await route.abort();
  });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Аккаунты' })).toBeVisible();
  await goTo(page, 'Нейрокомментинг');
  await expect(page.getByRole('heading', { name: 'Нейрокомментинг' })).toBeVisible({
    timeout: 3_000,
  });
});

test('neurocomment counters expose their values while rolling without moving the tiles', async ({
  page,
}) => {
  await setup(page);
  await page.goto('/neurocomment');
  await expect(page.getByRole('heading', { name: 'Нейрокомментинг' })).toBeVisible();
  const tile = page.locator('.type-h1.tabular-nums').first().locator('..');
  await expect(tile.locator('.sr-only')).toHaveText('2');
  // Сначала шрифт: если Inter подгрузится между двумя замерами, плитка вырастет на
  // разницу метрик запасного шрифта, и тест примет это за сдвиг от прокрутки цифр.
  await page.evaluate(() => document.fonts.ready);
  const before = await tile.boundingBox();
  await expect
    .poll(async () =>
      page.evaluate(() => {
        const clip = document.querySelector<HTMLElement>(
          '.type-h1.tabular-nums [aria-hidden="true"] > span',
        );
        const digits = clip?.firstElementChild;
        if (!clip || !digits) return Number.POSITIVE_INFINITY;
        const actual = new DOMMatrix(getComputedStyle(digits).transform).m42;
        return Math.abs(actual + 2 * clip.getBoundingClientRect().height);
      }),
    )
    .toBeLessThan(2);
  const after = await tile.boundingBox();
  expect(before).not.toBeNull();
  expect(after).not.toBeNull();
  expect(Math.abs(after!.height - before!.height)).toBeLessThan(1);
});

test('add-channel chip hugs its neighbors and matches their height', async ({ page }, testInfo) => {
  await setup(page);
  await page.goto('/neurocomment');
  const channel = page
    .locator('div.flex.flex-wrap.items-start.gap-2 > span')
    .filter({ hasText: '@defi_news' });
  const add = page.getByRole('button', { name: '+ Канал' });
  await expect(channel).toBeVisible();
  await expect(add).toBeVisible();
  const channelBox = await channel.boundingBox();
  const addBox = await add.boundingBox();
  const rowBox = await add.locator('xpath=..').boundingBox();
  expect(channelBox).not.toBeNull();
  expect(addBox).not.toBeNull();
  expect(rowBox).not.toBeNull();
  expect(Math.abs(addBox!.height - channelBox!.height)).toBeLessThanOrEqual(2);
  if (Math.abs(addBox!.y - channelBox!.y) <= 2) {
    expect(addBox!.x - channelBox!.x - channelBox!.width).toBeLessThanOrEqual(10);
  } else {
    expect(addBox!.x - rowBox!.x).toBeLessThanOrEqual(2);
    expect(addBox!.y - channelBox!.y - channelBox!.height).toBeLessThanOrEqual(10);
  }
  await add.scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath('channel-chip-after.png') });
});
