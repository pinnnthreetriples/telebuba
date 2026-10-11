import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { AccountRead, MasstaggingSettingsRead } from '@/shared/api';

import { SETTINGS_READ } from '../../model/fixtures.test-helpers';
import { callsTo, FLEET, routeApi } from '../MasstaggingPage.testHelpers';

import { CHIPS_SHOWN } from './TargetsSection';
import { SettingsDialog } from './SettingsDialog';

function open(read: MasstaggingSettingsRead = SETTINGS_READ, onSaved = vi.fn()) {
  routeApi({ settings: read });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <SettingsDialog
        read={read}
        fleet={FLEET as AccountRead[]}
        onClose={vi.fn()}
        onSaved={onSaved}
      />
    </QueryClientProvider>,
  );
  return onSaved;
}

function withSettings(over: MasstaggingSettingsRead['settings']): MasstaggingSettingsRead {
  return { ...SETTINGS_READ, settings: { ...SETTINGS_READ.settings, ...over } };
}

test('an edit shows «Не сохранено» and Save writes the draft on the version read', async () => {
  const onSaved = open();
  expect(screen.queryByText('Не сохранено')).toBeNull();
  expect(screen.getByRole('button', { name: 'Сохранить' })).toBeDisabled();

  fireEvent.change(screen.getByLabelText('Кого добавить'), {
    target: { value: '@gamma\n+79001234567' },
  });
  fireEvent.click(screen.getByRole('button', { name: /Добавить 2/ }));

  expect(screen.getByText('Не сохранено')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));
  await waitFor(() => {
    expect(onSaved).toHaveBeenCalled();
  });
  const [put] = callsTo('/api/v1/masstagging/campaigns/c1/settings', 'PUT');
  const body = (await put?.clone().json()) as {
    expected_updated_at: string;
    settings: { targets: string[] };
  };
  expect(body.expected_updated_at).toBe(SETTINGS_READ.updated_at);
  expect(body.settings.targets).toEqual(['@alpha', '@beta', '@gamma', '+79001234567']);
});

test('a running campaign only shows its settings', () => {
  open({ ...SETTINGS_READ, status: 'running' });

  expect(
    screen.getByText('Кампания идёт. Поставьте на паузу, чтобы менять настройки.'),
  ).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Сохранить' })).toBeDisabled();
  expect(screen.getByLabelText('Кого добавить')).toBeDisabled();
});

test('priming has no «Сторис» section and its pace is people a day', () => {
  open(withSettings({ mode: 'priming' }));

  expect(screen.queryByText('Подпись')).toBeNull();
  expect(screen.getByText('Людей в сутки')).toBeInTheDocument();
  expect(screen.queryByText('Сторис в сутки')).toBeNull();
});

test('switching the mode hides and brings back the story section', () => {
  open();
  expect(screen.getByText('Подпись')).toBeInTheDocument();

  fireEvent.click(screen.getByRole('radio', { name: /Прайминг/ }));

  expect(screen.queryByText('Подпись')).toBeNull();
});

test('the chips show the first few and «ещё N»', () => {
  const targets = Array.from({ length: CHIPS_SHOWN + 3 }, (_, index) => `@person${String(index)}`);
  open(withSettings({ targets }));

  expect(screen.getByText('@person0')).toBeInTheDocument();
  expect(screen.queryByText(`@person${String(CHIPS_SHOWN)}`)).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'ещё 3' }));
  expect(screen.getByText(`@person${String(CHIPS_SHOWN)}`)).toBeInTheDocument();
});

test('the button text appears once a link is filled', () => {
  open();
  expect(screen.queryByText('Текст на кнопке')).toBeNull();

  fireEvent.change(screen.getByPlaceholderText('https://'), {
    target: { value: 'https://t.me/school' },
  });

  expect(screen.getByText('Текст на кнопке')).toBeInTheDocument();
});

test('the accounts strip counts the free and the spam-blocked accounts', () => {
  open({ ...SETTINGS_READ, account_ids: ['a1', 'a2', 'a3'] });

  const section = screen.getByText('Аккаунты').closest('section') as HTMLElement;
  expect(within(section).getByText('без Premium: 1')).toBeInTheDocument();
  expect(within(section).getByText('спамблок: 1')).toBeInTheDocument();
});

test('a list file and a parser base are copied into the list', async () => {
  open();

  const input = screen.getByLabelText('Из файла') as HTMLInputElement;
  const file = new File(['@filed'], 'list.txt', { type: 'text/plain' });
  fireEvent.change(input, { target: { files: [file] } });
  expect(await screen.findByText('@filed')).toBeInTheDocument();
  expect(screen.getByText('1 строку не поняли')).toBeInTheDocument();

  fireEvent.click(screen.getByRole('radio', { name: 'База парсера' }));
  fireEvent.click(await screen.findByRole('combobox', { name: 'База парсера' }));
  fireEvent.click(await screen.findByRole('option', { name: /Москва/ }));

  expect(await screen.findByText('@base1')).toBeInTheDocument();
  expect(screen.getByText('Скопировано из базы «Москва»')).toBeInTheDocument();
});

test('a stale save says the campaign changed elsewhere', async () => {
  open();
  routeApi({ refuse: { path: '/settings', status: 409, code: 'campaign_changed' } });

  fireEvent.change(screen.getByLabelText('Кого добавить'), { target: { value: '@gamma' } });
  fireEvent.click(screen.getByRole('button', { name: /Добавить 1/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить' }));

  expect(await screen.findByText(/изменились в другом окне/)).toBeInTheDocument();
});

test('an uploaded story file joins the media already in the draft', async () => {
  open();

  const input = screen.getByLabelText('Перетащите фото или видео') as HTMLInputElement;
  const file = new File(['png'], 'cover.png', { type: 'image/png' });
  fireEvent.change(input, { target: { files: [file] } });

  expect(await screen.findByText('cover.png')).toBeInTheDocument();
  expect(screen.getByText('lesson.mp4')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Убрать lesson.mp4' }));
  expect(screen.queryByText('lesson.mp4')).toBeNull();
  expect(screen.getByText('cover.png')).toBeInTheDocument();
});
