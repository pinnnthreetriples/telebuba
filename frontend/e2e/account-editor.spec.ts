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
      : path.endsWith('/health')
        ? fx.health
        : path.endsWith('/events')
          ? { events: [] }
          : path.endsWith('/accounts/stats')
            ? fx.accountStats
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
