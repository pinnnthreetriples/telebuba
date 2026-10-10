import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { ImportFileList } from './ImportFileList';
import type { BulkFile } from './useBulkImport';

const FILES: BulkFile[] = [
  { id: 0, name: 'a.session', state: 'ok', accountIds: ['a'] },
  { id: 1, name: 'b.session', state: 'importing', accountIds: [] },
  { id: 2, name: 'c.session', state: 'error', accountIds: [], failure: 'duplicate' },
  { id: 3, name: 'many.zip', state: 'ok', accountIds: ['x', 'y', 'z'] },
];

test('renders the summary and one verdict per row', () => {
  render(<ImportFileList files={FILES} onRetry={vi.fn()} onRemove={vi.fn()} />);
  expect(screen.getByText('Импортировано 2 из 4')).toBeInTheDocument();
  expect(screen.getByText('a.session')).toBeInTheDocument();
  expect(screen.getByText('Аккаунт импортирован')).toBeInTheDocument();
  expect(screen.getByText('Импортируем…')).toBeInTheDocument();
  expect(screen.getByText('Этот аккаунт уже добавлен')).toBeInTheDocument();
  expect(screen.getByText('Импортировано 3 аккаунта')).toBeInTheDocument();
});

test('a single file has no summary line', () => {
  render(<ImportFileList files={FILES.slice(0, 1)} onRetry={vi.fn()} onRemove={vi.fn()} />);
  expect(screen.queryByText(/Импортировано/)).not.toBeInTheDocument();
});

test('retry and remove appear only on the failed row and report its id', async () => {
  const onRetry = vi.fn();
  const onRemove = vi.fn();
  render(<ImportFileList files={FILES} onRetry={onRetry} onRemove={onRemove} />);
  // getByRole throws on more than one match: both icons sit on the failed row only.
  await userEvent.click(screen.getByRole('button', { name: 'Повторить' }));
  expect(onRetry).toHaveBeenCalledWith(2);
  // Removal is immediate: no confirmation step in between.
  await userEvent.click(screen.getByRole('button', { name: 'Убрать из списка' }));
  expect(onRemove).toHaveBeenCalledWith(2);
});
