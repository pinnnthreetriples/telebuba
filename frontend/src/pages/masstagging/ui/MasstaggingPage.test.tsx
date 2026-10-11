import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { expect, test } from 'vitest';

import { board, campaign, row, SETTINGS_READ } from '../model/fixtures.test-helpers';

import { callsTo, renderPage, routeApi } from './MasstaggingPage.testHelpers';

const BASE = '/api/v1/masstagging/campaigns/c1';

async function pipelineButton(name: string | RegExp) {
  return screen.findByRole('button', { name });
}

test.each([
  ['draft', 'Начать', true, 'Чтобы начать: Пул медиа пуст'],
  ['ready', 'Начать', false, 'Всё готово — можно начинать'],
  ['running', 'Пауза', false, 'Идёт'],
  ['paused', 'Продолжить', false, 'На паузе · отмечено 0 из 2'],
  ['resting', 'Пауза', false, 'Лимит на сегодня выбран · продолжим завтра в'],
  ['done', 'Запустить снова', false, 'Готово · отмечено 0 из 2'],
  ['failed', 'Продолжить', false, 'Остановлено из-за ошибки'],
] as const)('a %s campaign: «%s», its notice', async (status, label, disabled, notice) => {
  const missing = status === 'draft' ? (['no_media'] as const) : [];
  const shown = campaign({ status, missing: [...missing], rest_until: '2026-10-12T06:00:00Z' });
  routeApi({ campaigns: [shown], board: board({ campaign: shown }) });
  renderPage();

  const button = await pipelineButton(new RegExp(`^${label}$`));
  if (disabled) expect(button).toBeDisabled();
  else expect(button).toBeEnabled();
  expect((await screen.findAllByText(new RegExp(notice))).length).toBeGreaterThan(0);
});

test('Start sends the version the page read, Pause stops', async () => {
  routeApi();
  renderPage();

  fireEvent.click(await pipelineButton(/^Начать$/));
  await waitFor(() => {
    expect(callsTo(`${BASE}/start`, 'POST')).toHaveLength(1);
  });
  const [start] = callsTo(`${BASE}/start`, 'POST');
  expect(await start?.clone().json()).toEqual({ expected_updated_at: SETTINGS_READ.updated_at });

  const running = campaign({ status: 'running' });
  routeApi({ campaigns: [running], board: board({ campaign: running }) });
  renderPage();
  fireEvent.click((await screen.findAllByRole('button', { name: /^Пауза$/ }))[0] as HTMLElement);
  await waitFor(() => {
    expect(callsTo(`${BASE}/stop`, 'POST').length).toBeGreaterThan(0);
  });
});

test('a new campaign opens its settings', async () => {
  routeApi();
  renderPage();

  fireEvent.click(await screen.findByRole('button', { name: '+ Новая кампания' }));
  fireEvent.change(screen.getByLabelText('Название кампании'), { target: { value: 'Новая' } });
  fireEvent.click(screen.getByRole('button', { name: 'Создать' }));

  await waitFor(() => {
    expect(callsTo('/api/v1/masstagging/campaigns', 'POST')).toHaveLength(1);
  });
});

test('the free-account badge and the mode show under the tiles', async () => {
  routeApi();
  renderPage();

  expect(await screen.findByText('без Premium: 1')).toBeInTheDocument();
  expect(screen.getAllByText('Сторис').length).toBeGreaterThan(0);
});

test('a board action posts the person and the action', async () => {
  const running = campaign({ status: 'running' });
  routeApi({ campaigns: [running], board: board({ campaign: running }) });
  renderPage();

  const table = await screen.findByRole('table');
  fireEvent.click(within(table).getAllByRole('button', { name: 'Подробнее' })[0] as HTMLElement);
  fireEvent.click(await screen.findByRole('button', { name: /Отметить сейчас/ }));

  await waitFor(() => {
    expect(callsTo(`${BASE}/targets/action`, 'POST')).toHaveLength(1);
  });
  const [action] = callsTo(`${BASE}/targets/action`, 'POST');
  expect(await action?.clone().json()).toEqual({ target_key: 'u:alpha', action: 'now' });
});

test('a never-started campaign shows its list but offers no actions', async () => {
  routeApi({ board: board({ rows: [row()] }) });
  renderPage();

  const table = await screen.findByRole('table');
  fireEvent.click(within(table).getByRole('button', { name: 'Подробнее' }));
  expect(await screen.findByText('Ещё не трогали.')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /Отметить сейчас/ })).toBeNull();
});

test('deleting asks first', async () => {
  routeApi();
  renderPage();

  fireEvent.click(await screen.findByRole('button', { name: 'Действия с кампанией' }));
  fireEvent.click(screen.getByRole('button', { name: 'Удалить' }));
  const dialog = await screen.findByRole('dialog');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Удалить' }));

  await waitFor(() => {
    expect(callsTo(BASE, 'DELETE')).toHaveLength(1);
  });
});
