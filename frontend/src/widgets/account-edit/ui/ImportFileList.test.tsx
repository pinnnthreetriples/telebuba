import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { ImportFileList } from './ImportFileList';
import type { BulkFile } from './useBulkImport';

const FILES: BulkFile[] = [
  { name: 'a.session', state: 'ok', accountIds: ['a'] },
  { name: 'b.session', state: 'importing', accountIds: [] },
  { name: 'c.session', state: 'error', accountIds: [] },
  { name: 'many.zip', state: 'ok', accountIds: ['x', 'y', 'z'] },
];

test('renders the summary and one verdict per row', () => {
  render(<ImportFileList files={FILES} onRetry={vi.fn()} />);
  expect(screen.getByText('Добавлено 2 из 4')).toBeInTheDocument();
  expect(screen.getByText('a.session')).toBeInTheDocument();
  expect(screen.getByText('Аккаунт импортирован')).toBeInTheDocument();
  expect(screen.getByText('Импортируем…')).toBeInTheDocument();
  expect(screen.getByText('Не удалось импортировать')).toBeInTheDocument();
  expect(screen.getByText('Аккаунтов: 3')).toBeInTheDocument();
});

test('a single file has no summary line', () => {
  render(<ImportFileList files={FILES.slice(0, 1)} onRetry={vi.fn()} />);
  expect(screen.queryByText(/Добавлено/)).not.toBeInTheDocument();
});

test('retry appears only on the failed row and reports its index', async () => {
  const onRetry = vi.fn();
  render(<ImportFileList files={FILES} onRetry={onRetry} />);
  // getByText throws on more than one match: retry sits on the failed row only.
  await userEvent.click(screen.getByText('Повторить'));
  expect(onRetry).toHaveBeenCalledWith(2);
});
