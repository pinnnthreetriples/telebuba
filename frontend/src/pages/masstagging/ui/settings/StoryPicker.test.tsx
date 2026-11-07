import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { AccountRead, MasstaggingSettingsRead, ProfileStoryView } from '@/shared/api';

import { SETTINGS_READ } from '../../model/fixtures.test-helpers';
import { callsTo, FLEET, routeApi, type Api } from '../MasstaggingPage.testHelpers';

import { SettingsDialog } from './SettingsDialog';

const story = (id: number, over: Partial<ProfileStoryView> = {}): ProfileStoryView => ({
  story_id: id,
  kind: 'image',
  privacy_preset: 'all',
  is_pinned: false,
  thumb_url: null,
  ...over,
});

function open(over: Partial<Api> = {}, read: MasstaggingSettingsRead = SETTINGS_READ) {
  routeApi({ settings: read, ...over });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <SettingsDialog
        read={read}
        fleet={FLEET as AccountRead[]}
        onClose={vi.fn()}
        onSaved={vi.fn()}
      />
    </QueryClientProvider>,
  );
}

const picker = () => screen.getByRole('dialog', { name: 'Сторис аккаунта' });

function openPicker() {
  fireEvent.click(screen.getByRole('button', { name: 'Выбрать из сторис аккаунта' }));
}

test('a picked story joins the media already in the draft and the picker closes', async () => {
  open({ stories: { a1: [story(7), story(8, { kind: 'video', is_pinned: true })] } });
  openPicker();

  fireEvent.click(await screen.findByRole('checkbox', { name: 'Сторис 7' }));
  fireEvent.click(within(picker()).getByRole('button', { name: 'Добавить 1' }));

  expect(await screen.findByText('story-7.jpg')).toBeInTheDocument();
  expect(screen.getByText('lesson.mp4')).toBeInTheDocument();
  await waitFor(() => {
    expect(screen.queryByRole('dialog', { name: 'Сторис аккаунта' })).toBeNull();
  });
  const [call] = callsTo('/api/v1/masstagging/campaigns/c1/media/from-story', 'POST');
  expect(await call?.clone().json()).toEqual({ account_id: 'a1', story_id: 7 });
});

test('a story that did not come stays ticked while the others land', async () => {
  open({ stories: { a1: [story(7), story(8)] }, goneStories: [7] });
  openPicker();

  fireEvent.click(await screen.findByRole('checkbox', { name: 'Сторис 7' }));
  fireEvent.click(screen.getByRole('checkbox', { name: 'Сторис 8' }));
  fireEvent.click(within(picker()).getByRole('button', { name: 'Добавить 2' }));

  expect(await screen.findByText('story-8.jpg')).toBeInTheDocument();
  expect(screen.queryByText('story-7.jpg')).toBeNull();
  expect(screen.getByRole('checkbox', { name: 'Сторис 7' })).toBeChecked();
  expect(within(picker()).getByRole('button', { name: 'Добавить 1' })).toBeEnabled();
});

test('another account shows its own stories and drops the ticks', async () => {
  open({ stories: { a1: [story(7)], a2: [story(9)] } });
  openPicker();
  fireEvent.click(await screen.findByRole('checkbox', { name: 'Сторис 7' }));

  fireEvent.click(within(picker()).getByRole('combobox', { name: 'Аккаунт' }));
  fireEvent.click(await screen.findByRole('option', { name: /Юлия/ }));

  expect(await screen.findByRole('checkbox', { name: 'Сторис 9' })).not.toBeChecked();
  expect(screen.queryByRole('checkbox', { name: 'Сторис 7' })).toBeNull();
  expect(within(picker()).getByRole('button', { name: 'Добавить 0' })).toBeDisabled();
});

test('an account without stories says so', async () => {
  open();
  openPicker();

  expect(
    await screen.findByText('У аккаунта нет закреплённых или активных сторис.'),
  ).toBeInTheDocument();
});

test('a refused profile read is an error with a retry, not an empty list', async () => {
  open({ snapshotError: true });
  openPicker();

  expect(await screen.findByText('Не получилось прочитать сторис аккаунта.')).toBeInTheDocument();
  expect(screen.queryByText('У аккаунта нет закреплённых или активных сторис.')).toBeNull();
  expect(screen.getByRole('button', { name: 'Повторить' })).toBeInTheDocument();
});

test('without accounts there is nothing to pick from', () => {
  open({}, { ...SETTINGS_READ, account_ids: [] });

  expect(screen.getByRole('button', { name: 'Выбрать из сторис аккаунта' })).toBeDisabled();
  expect(screen.getByText('Сначала добавьте аккаунты.')).toBeInTheDocument();
});

test('a running campaign cannot pick', () => {
  open({}, { ...SETTINGS_READ, status: 'running' });

  expect(screen.getByRole('button', { name: 'Выбрать из сторис аккаунта' })).toBeDisabled();
});
