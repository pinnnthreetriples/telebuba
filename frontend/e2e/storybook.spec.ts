import { expect, test } from '@playwright/test';

test('product patterns render and campaign dialogs work', async ({ page }, testInfo) => {
  await page.goto('/iframe.html?id=design-system-overview--patterns&viewMode=story');
  await page.evaluate(async () => {
    await document.fonts.ready;
  });

  const patterns = page.locator('#patterns');
  await expect(patterns.getByRole('heading', { name: 'Блок кампаний' })).toBeVisible();
  await expect(patterns.getByRole('heading', { name: 'Журнал событий' })).toBeVisible();

  // Wait until the number component has armed its roll; the screenshot then
  // fast-forwards CSS motion so the attached review image always shows real values.
  const firstDigit = patterns.locator('.type-stat.tabular-nums [style*="translateY"]').first();
  await expect(firstDigit).toHaveAttribute('style', /translateY\(-2\.2(?:0)?em\)/);

  // Keep a screenshot as a CI artifact without a platform-specific golden image.
  const screenshot = testInfo.outputPath('patterns.png');
  await page.screenshot({ path: screenshot, fullPage: true, animations: 'disabled' });
  await testInfo.attach('storybook-patterns', {
    path: screenshot,
    contentType: 'image/png',
  });

  const viewport = page.viewportSize();
  if (!viewport) throw new Error('Viewport is unavailable');
  const pageWidth = await page.evaluate(() => document.documentElement.scrollWidth);
  expect(pageWidth).toBeLessThanOrEqual(viewport.width);

  const choice = patterns.getByRole('button', { name: 'Путешествия', exact: true });
  await choice.click();
  await expect(choice).toHaveAttribute('aria-pressed', 'true');

  const actions = patterns.getByRole('button', { name: 'Действия', exact: true }).nth(1);
  await actions.click();
  await expect(actions).toHaveAttribute('aria-expanded', 'true');
  await actions.click();

  const campaignBlock = patterns.getByRole('heading', { name: 'Блок кампаний' }).locator('..');
  await campaignBlock.getByRole('button', { name: '+ Канал' }).click();
  const channelEditor = campaignBlock.getByRole('textbox', { name: '@канал' });
  await expect(channelEditor).toBeVisible();
  await channelEditor.fill('@new_channel');
  await campaignBlock.getByRole('button', { name: 'Добавить', exact: true }).click();
  await expect(campaignBlock.getByText('@new_channel')).toBeVisible();

  await campaignBlock.getByRole('button', { name: 'Убрать канал' }).last().click();
  const confirmRemove = page.getByRole('dialog');
  await expect(confirmRemove).toContainText('Удалить @new_channel?');
  await confirmRemove.getByRole('button', { name: 'Удалить' }).click();
  await expect(campaignBlock.getByText('@new_channel')).toHaveCount(0);

  await campaignBlock.getByRole('button', { name: 'Найти каналы' }).click();
  const discovery = page.getByRole('dialog');
  await expect(discovery).toContainText('Поиск каналов');
  await discovery.getByRole('button', { name: 'Закрыть' }).click();

  await campaignBlock.getByRole('button', { name: '+ Создать кампанию' }).click();
  const create = page.getByRole('dialog');
  await create.getByRole('textbox', { name: 'Название' }).fill('Тестовая кампания');
  await create.getByRole('textbox', { name: 'Промт для LLM' }).fill('Тестовый промпт');
  await create.getByRole('button', { name: 'Создать кампанию' }).click();
  await expect(campaignBlock.getByRole('button', { name: 'Тестовая кампания' })).toBeVisible();

  await campaignBlock.getByRole('button', { name: 'Действия' }).last().click();
  await campaignBlock.getByRole('button', { name: 'Редактировать промт' }).last().click();
  const prompt = page.getByRole('dialog');
  await prompt.getByRole('textbox', { name: 'Текст промта' }).fill('Изменённый промпт');
  await prompt.getByRole('button', { name: 'Сохранить' }).click();
  await expect(prompt).toBeHidden();

  await campaignBlock.getByRole('button', { name: 'Действия' }).last().click();
  await campaignBlock.getByRole('button', { name: 'Редактировать промт' }).last().click();
  await expect(page.getByRole('dialog').getByRole('textbox', { name: 'Текст промта' })).toHaveValue(
    'Изменённый промпт',
  );
  await page.getByRole('dialog').getByRole('button', { name: 'Отмена' }).click();

  await campaignBlock.getByRole('button', { name: 'Действия' }).last().click();
  await campaignBlock.getByRole('button', { name: 'Удалить кампанию' }).last().click();
  const confirmCampaignDelete = page.getByRole('dialog');
  await expect(confirmCampaignDelete).toContainText('Тестовая кампания');
  await confirmCampaignDelete.getByRole('button', { name: 'Удалить' }).click();
  await expect(campaignBlock.getByRole('button', { name: 'Тестовая кампания' })).toHaveCount(0);
});
