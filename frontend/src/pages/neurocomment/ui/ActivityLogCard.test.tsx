import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';

import '@/shared/i18n';

import type { LogEntry } from '@/shared/api';

import { ActivityLogCard } from './ActivityLogCard';

function entry(over: Partial<LogEntry>): LogEntry {
  return {
    id: 1,
    created_at: '2026-07-11T10:00:00+00:00',
    level: 'INFO',
    status: 'success',
    account_id: 'acc-1',
    event: 'neurocomment_posted',
    extra: {},
    ...over,
  };
}

test('shows the channel and the translated reason inline', () => {
  render(
    <ActivityLogCard
      logLines={[
        entry({
          id: 1,
          event: 'neurocomment_no_account_available',
          extra: { channel: '@Barca_Studio_News', reason: 'quota' },
        }),
      ]}
    />,
  );
  expect(screen.getByText('@Barca_Studio_News')).toBeInTheDocument();
  expect(screen.getByText('Нет доступного аккаунта')).toBeInTheDocument();
  expect(screen.getByText(/лимит исчерпан/)).toBeInTheDocument();
});

test('colours an attempted-but-failed event red even though it is logged INFO', () => {
  render(
    <ActivityLogCard
      logLines={[entry({ event: 'neurocomment_generation_exhausted', extra: { reason: 'gemini_error' } })]}
    />,
  );
  const label = screen.getByText('Не удалось сгенерировать текст');
  expect(label).toHaveStyle({ color: '#e5736b' });
});

test('attaches a what-to-do hint as a hover tooltip', () => {
  render(
    <ActivityLogCard
      logLines={[entry({ event: 'neurocomment_no_account_available', extra: { channel: '@x' } })]}
    />,
  );
  const row = screen.getByText('Нет доступного аккаунта').closest('div');
  expect(row?.getAttribute('title')).toMatch(/Добавьте аккаунтов/);
});
