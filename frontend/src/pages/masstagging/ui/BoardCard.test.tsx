import { fireEvent, render, screen } from '@testing-library/react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { MasstaggingBoard } from '@/shared/api';

import { board, campaign, row } from '../model/fixtures.test-helpers';

import { BoardCard } from './BoardCard';

const time = (iso: string) => iso.slice(11, 16);

function show(shown: MasstaggingBoard, onAddTargets = vi.fn()) {
  render(
    <BoardCard
      board={shown}
      fleet={new Map()}
      time={time}
      stories
      busy={false}
      onAction={vi.fn()}
      onPace={vi.fn()}
      onAddTargets={onAddTargets}
    />,
  );
  return onAddTargets;
}

test('an empty list says where people come from', () => {
  const onAdd = show(board({ rows: [] }));

  expect(screen.getByText('Здесь появятся люди из списка')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Добавить' }));
  expect(onAdd).toHaveBeenCalled();
});

test('a resting campaign shows one sentence until asked for the list', () => {
  show(
    board({
      campaign: campaign({ status: 'resting', rest_until: '2026-10-12T09:00:00Z' }),
    }),
  );

  expect(screen.getByText('Продолжим завтра в 09:00 · ждут 2 человека')).toBeInTheDocument();
  expect(screen.queryByRole('table')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Показать список' }));
  expect(screen.getByRole('table')).toBeInTheDocument();
  expect(screen.getAllByText('завтра')).toHaveLength(2);
});

test('a finished campaign opens on «Готово»', () => {
  show(
    board({
      campaign: campaign({ status: 'done' }),
      rows: [
        row({ state: 'tagged', active: false, when: '2026-10-11T10:05:00Z' }),
        row({
          target_key: 'u:beta',
          target: '@beta',
          state: 'skipped',
          reason: 'already_tagged',
          active: false,
        }),
      ],
    }),
  );

  expect(screen.getByRole('radio', { name: 'Готово · 2' })).toBeChecked();
  expect(screen.getByText('уже отмечен')).toBeInTheDocument();
  expect(screen.getByText('Отмечен')).toBeInTheDocument();
});

test('a failed person can go back to the queue', () => {
  const onAction = vi.fn();
  render(
    <BoardCard
      board={board({
        campaign: campaign({ status: 'paused' }),
        rows: [row({ state: 'failed', reason: 'privacy', active: false })],
      })}
      fleet={new Map()}
      time={time}
      stories
      busy={false}
      onAction={onAction}
      onPace={vi.fn()}
      onAddTargets={vi.fn()}
    />,
  );

  fireEvent.click(screen.getByRole('button', { name: 'Подробнее' }));
  fireEvent.click(screen.getByRole('button', { name: 'Вернуть в очередь' }));

  expect(onAction).toHaveBeenCalledWith(
    expect.objectContaining({ target_key: 'u:alpha' }),
    'requeue',
  );
});
