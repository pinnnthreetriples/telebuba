import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { newFolder } from '../model/folders';

import { FolderSettingsModal } from './FolderSettingsModal';

// The folder's settings say what they really do: length, types, device and first visit
// reach accounts started into the folder; pace and «когда догрет» are a prototype the
// server does not read, and running accounts keep what they started with.

function renderEdit(members: number) {
  render(
    <FolderSettingsModal
      initial={{ ...newFolder(14), id: 'f1', name: 'Пачка' }}
      members={members}
      onClose={vi.fn()}
      onSave={vi.fn()}
    />,
  );
}

test('the settings say what applies at start and mark pace and finish as a prototype', () => {
  renderEdit(0);
  expect(screen.getByText(/в момент запуска/)).toBeInTheDocument();
  expect(screen.getByText(/хранится только в этом браузере/)).toBeInTheDocument();
  expect(screen.getAllByText('прототип: пока не применяется')).toHaveLength(2);
  expect(screen.getByText(/автоматической передачи пока нет/)).toBeInTheDocument();
  expect(screen.queryByText(/сам уходит в нейрокомментинг/)).toBeNull();
});

test('an edit with running accounts says they keep the settings they started with', async () => {
  renderEdit(3);
  await userEvent.click(screen.getByRole('radio', { name: '21 день' }));
  expect(
    screen.getByText(/продолжат с теми настройками, с которыми стартовали/),
  ).toBeInTheDocument();
  expect(screen.queryByText(/со следующего захода/)).toBeNull();
});
