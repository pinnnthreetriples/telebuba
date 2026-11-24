import { expect, test } from '@playwright/test';

import * as fx from './fixtures';

test('account editors keep their position and show a subtle tab hover', async ({
  page,
}, testInfo) => {
  const unmatched: string[] = [];
  await page.route('**/api/v1/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    const body = path.endsWith('/auth/me')
      ? fx.me
      : path.endsWith('/session-guard')
        ? fx.sessionGuard
        : path.endsWith('/health')
          ? fx.health
          : path.endsWith('/events')
            ? { events: [] }
            : path.endsWith('/accounts/stats')
              ? fx.accountStats
              : path.endsWith('/accounts/web-windows')
                ? fx.accountWebWindows
                : path.endsWith('/accounts/spam-budget')
                  ? fx.spamBudget
                  : path.endsWith('/accounts/emulator')
                    ? fx.accountEmulator
                    : path.endsWith('/accounts/filter-options')
                      ? fx.accountFilterOptions
                      : path.endsWith('/account-folders')
                        ? fx.accountFolders
                        : path.endsWith('/proxies')
                          ? fx.proxies
                          : path.endsWith('/accounts/bulk-messages/active') ||
                              path.endsWith('/accounts/bulk-messages/latest')
                            ? null
                            : path.endsWith('/profile-snapshot')
                              ? {
                                  first_name: 'Иван',
                                  photos: [],
                                  stories: [],
                                  music: [],
                                  music_supported: true,
                                }
                              : path.endsWith('/privacy')
                                ? {
                                    settings: {
                                      profile_photo: 'everybody',
                                      bio: 'everybody',
                                      last_seen: 'contacts',
                                    },
                                    error: null,
                                  }
                                : path.endsWith('/channels')
                                  ? { items: [], next_cursor: null }
                                  : path.endsWith('/scheduled')
                                    ? { items: [], server_now: new Date().toISOString() }
                                    : path.endsWith('/accounts')
                                      ? fx.accounts
                                      : undefined;
    if (body === undefined) unmatched.push(path);
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(body ?? {}),
    });
  });

  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Аккаунты' })).toBeVisible();
  await page.getByRole('button', { name: 'Редактировать профиль' }).first().click();
  const profile = page.getByRole('dialog', { name: 'Профиль аккаунта' });
  await expect(profile).toBeVisible();

  await profile.getByRole('button', { name: 'Массовое редактирование' }).click();
  const bulk = page.getByRole('dialog', { name: 'Массовое редактирование' });
  await expect(bulk).toBeVisible();
  await page.waitForTimeout(250);
  const initial = await bulk.boundingBox();
  expect(initial).not.toBeNull();

  for (const name of ['Фото', 'Сторис', 'Музыка', 'Каналы', 'Приватность', 'Текст']) {
    await bulk.getByRole('tab', { name }).click();
    const current = await bulk.boundingBox();
    expect(current).not.toBeNull();
    expect(Math.abs(current!.y - initial!.y), name).toBeLessThan(1);
    expect(Math.abs(current!.height - initial!.height), name).toBeLessThan(1);
  }

  const photo = bulk.getByRole('tab', { name: 'Фото' });
  const rest = await photo.evaluate((node) => ({
    line: getComputedStyle(node).borderBottomColor,
    fill: getComputedStyle(node).backgroundColor,
  }));
  await photo.hover();
  await expect
    .poll(() => photo.evaluate((node) => getComputedStyle(node).borderBottomColor))
    .not.toBe(rest.line);
  await page.waitForTimeout(200);
  const hover = await photo.evaluate((node) => ({
    line: getComputedStyle(node).borderBottomColor,
    fill: getComputedStyle(node).backgroundColor,
  }));
  expect(hover.fill).toBe(rest.fill);
  await photo.click();
  await expect
    .poll(() => photo.evaluate((node) => getComputedStyle(node).borderBottomColor))
    .not.toBe(hover.line);
  const cancelBulk = bulk.getByRole('button', { name: 'Отмена' });
  const applyBulk = bulk.getByRole('button', { name: 'Применить к 1 аккаунту' });
  const cancelBulkBox = await cancelBulk.boundingBox();
  const applyBulkBox = await applyBulk.boundingBox();
  const bulkBox = await bulk.boundingBox();
  expect(cancelBulkBox).not.toBeNull();
  expect(applyBulkBox).not.toBeNull();
  expect(bulkBox).not.toBeNull();
  expect(cancelBulkBox!.x).toBeGreaterThanOrEqual(bulkBox!.x);
  expect(applyBulkBox!.x + applyBulkBox!.width).toBeLessThanOrEqual(bulkBox!.x + bulkBox!.width);
  const bulkPhotoPath = testInfo.outputPath('bulk-photo.png');
  await bulk.screenshot({ path: bulkPhotoPath });
  await testInfo.attach('bulk-photo', { path: bulkPhotoPath, contentType: 'image/png' });

  await bulk.getByRole('button', { name: 'Закрыть' }).first().click();
  const emptyHover: { line: string; fill: string }[] = [];
  for (const [tabName, actionName] of [
    ['Фото', 'Загрузить'],
    ['Сторис', 'Добавить'],
    ['Музыка', 'Добавить музыку'],
    ['Каналы', 'Создать канал'],
  ]) {
    await profile.getByRole('tab', { name: tabName }).click();
    const action = profile.getByRole('button', { name: actionName, exact: true });
    await action.hover();
    await page.waitForTimeout(200);
    emptyHover.push(
      await action.evaluate((node) => ({
        line: getComputedStyle(node).borderColor,
        fill: getComputedStyle(node).backgroundColor,
      })),
    );
  }
  expect(emptyHover.every((paint) => paint.line === emptyHover[0]?.line)).toBe(true);
  expect(emptyHover.every((paint) => paint.fill === emptyHover[0]?.fill)).toBe(true);
  await profile.getByRole('tab', { name: 'Каналы' }).click();
  await expect(profile.getByRole('button', { name: 'Создать канал' })).toBeVisible();
  const channelsPath = testInfo.outputPath('channels-empty.png');
  await profile.screenshot({ path: channelsPath });
  await testInfo.attach('channels-empty', { path: channelsPath, contentType: 'image/png' });
  await profile.getByRole('button', { name: 'Создать канал' }).hover();
  await expect(profile.getByText('У аккаунта пока нет каналов')).toHaveCSS('opacity', '0');
  const channelsHoverPath = testInfo.outputPath('channels-hover.png');
  await profile.screenshot({ path: channelsHoverPath });
  await testInfo.attach('channels-hover', { path: channelsHoverPath, contentType: 'image/png' });
  await profile.getByRole('tab', { name: 'Музыка' }).click();
  await expect(profile.getByRole('button', { name: 'Добавить музыку' })).toBeVisible();
  await profile.getByRole('tab', { name: 'Приватность' }).click();
  const applyAll = profile.getByRole('button', { name: 'Применить ко всем аккаунтам' });
  await expect(applyAll).toBeVisible();
  const applyBox = await applyAll.boundingBox();
  const profileBox = await profile.boundingBox();
  expect(applyBox).not.toBeNull();
  expect(profileBox).not.toBeNull();
  expect(applyBox!.x + applyBox!.width).toBeLessThanOrEqual(profileBox!.x + profileBox!.width);
  const photoLabel = profile.getByRole('group', { name: 'Фото профиля' }).getByText('Фото профиля');
  expect(await photoLabel.evaluate((node) => node.scrollWidth <= node.clientWidth)).toBe(true);
  const applyCurrent = profile.getByRole('button', { name: 'Применить', exact: true });
  const applyCurrentBox = await applyCurrent.boundingBox();
  expect(applyCurrentBox).not.toBeNull();
  expect(Math.abs(applyCurrentBox!.y - applyBox!.y)).toBeLessThan(1);
  await applyAll.scrollIntoViewIfNeeded();
  const privacyPath = testInfo.outputPath('privacy.png');
  await profile.screenshot({ path: privacyPath });
  await testInfo.attach('privacy', { path: privacyPath, contentType: 'image/png' });
  expect(unmatched).toEqual([]);
});

// The account card's spam block: the verdict with its source, the check that asks before an
// out-of-turn click, and the appeal that is only ever opened and recorded, never typed.
test('the account card words the spam verdict and asks before an out-of-turn check', async ({
  page,
}) => {
  const day = 24 * 3_600_000;
  const limited = {
    ...fx.accounts.items[0],
    spam_status: 'limited',
    spam_detail: null,
    spam_verdict: 'limited',
    spam_confidence: 'high',
    spam_source: 'spambot',
    spam_observed_at: new Date(Date.now() - 3 * day).toISOString(),
    spam_valid_until: null,
    spam_next_probe_at: null,
    spam_last_probe_at: new Date(Date.now() - 3 * 3_600_000).toISOString(),
    spam_probes_7d: 2,
    spam_out_of_turn: true,
    spam_tos_restricted: false,
    spam_appeal_status: 'none',
  };
  const checks: unknown[] = [];
  const written: string[] = [];
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() !== 'GET') written.push(`${request.method()} ${path}`);
    let body: unknown = {};
    if (path.endsWith('/auth/me')) body = fx.me;
    else if (path.endsWith('/health')) body = fx.health;
    else if (path.endsWith('/events')) body = { events: [] };
    else if (path.endsWith('/accounts/stats')) body = fx.accountStats;
    else if (path.endsWith('/accounts/web-windows')) body = fx.accountWebWindows;
    else if (path.endsWith('/accounts/spam-budget')) body = fx.spamBudget;
    else if (path.endsWith('/accounts/emulator')) body = fx.accountEmulator;
    else if (path.endsWith('/accounts/filter-options')) body = fx.accountFilterOptions;
    else if (path.endsWith('/account-folders')) body = fx.accountFolders;
    else if (path.endsWith('/proxies')) body = fx.proxies;
    else if (path.endsWith('/accounts/bulk-messages/active')) body = null;
    else if (path.endsWith('/accounts/bulk-messages/latest')) body = null;
    else if (path.endsWith('/accounts/acc-0/spam-appeal'))
      body = { account_id: 'acc-0', status: 'none' };
    else if (path.endsWith('/accounts/acc-0/spam-check')) {
      checks.push(request.postDataJSON());
      body = {
        account_id: 'acc-0',
        status: 'limited',
        checked_at: new Date().toISOString(),
        served_from: 'probe',
        probed: true,
      };
    } else if (path.endsWith('/accounts')) body = { items: [limited], next_cursor: null };
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(body),
    });
  });

  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Аккаунты' })).toBeVisible();
  await page.getByText('@user_0').first().click();
  await page.getByText('Спам/бан-сигналы').click();

  await expect(page.getByText('Ограничен', { exact: true })).toBeVisible();
  await expect(page.getByText('уверенность: высокая')).toBeVisible();
  await expect(page.getByText('@SpamBot, 3 дня назад')).toBeVisible();
  await expect(page.getByText('бессрочно')).toBeVisible();
  await expect(page.getByText('Проверок за 7 дней')).toBeVisible();

  // The appeal block opens a window and records a filing; it has nothing to type into.
  const appeal = page.getByTestId('spam-appeal');
  await expect(appeal.getByRole('button', { name: 'Подать апелляцию' })).toBeVisible();
  await expect(appeal.getByRole('button', { name: 'Апелляция подана' })).toBeVisible();
  await expect(appeal.locator('input, textarea')).toHaveCount(0);

  // Out of turn the check asks first: nothing is sent until the operator confirms.
  await page.locator('button[aria-describedby]').filter({ hasText: 'Проверить' }).last().click();
  await expect(page.getByRole('dialog', { name: 'Проверка вне очереди' })).toBeVisible();
  expect(checks).toEqual([]);
  await page.getByRole('button', { name: 'Проверить сейчас' }).click();
  await expect.poll(() => checks.length).toBe(1);
  expect(checks[0]).toEqual({ confirm_out_of_turn: true });
  // The only write the whole card made is that one check.
  expect(written).toEqual(['POST /api/v1/accounts/acc-0/spam-check']);
});
