import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import type { PipelineView } from '../model/pipeline';

import { BroadcastPipeline } from './BroadcastPipeline';

function view(over: Partial<PipelineView> = {}): PipelineView {
  return {
    badge: { label: 'Встала', tone: 'danger' },
    action: { kind: 'resume', label: 'Продолжить', disabled: false },
    nodes: [
      { id: 'accounts', label: 'Аккаунты', sub: '1 из 2 работают', done: true },
      { id: 'chats', label: 'Чаты', sub: 'Нет чатов', done: false },
    ],
    notice: { tone: 'danger', text: 'Рассылка встала' },
    extras: [{ tone: 'warning', text: 'Остановлены: Иван' }],
    stats: [{ id: 'sent', label: 'Отправлено', value: 4 }],
    progress: { sent: 4, total: 18 },
    chips: [{ label: 'Передано чатов: 2', tone: 'neutral' }],
    ...over,
  };
}

test('every broadcast part lands on the shared card', () => {
  const { container } = render(
    <BroadcastPipeline name="Крипто" view={view()} busy={false} onAction={vi.fn()} />,
  );
  expect(screen.getByText('— Крипто')).toBeInTheDocument();
  expect(screen.getByText('Встала')).toBeInTheDocument();
  expect(screen.getAllByRole('listitem')).toHaveLength(2);
  expect(screen.getByText('1 из 2 работают')).toBeInTheDocument();
  // A stall is the card's notice in its own tone, not a blue line that reads as fine.
  expect(screen.getByText('Рассылка встала').closest('[class*="bg-danger-tint"]')).not.toBeNull();
  expect(screen.getByText('Отправлено')).toBeInTheDocument();
  expect(screen.getByText('Передано чатов: 2')).toBeInTheDocument();
  expect(screen.getByText('4 из 18 сообщений')).toBeInTheDocument();
  // The bar is decorative: the count beside it is what a screen reader reads.
  expect(container.querySelector('.h-meter > div')?.getAttribute('style')).toContain('width');
  expect(screen.getByText('Остановлены: Иван')).toBeInTheDocument();
});

test('Resume is the start side of the toggle, Stop the other', async () => {
  const onAction = vi.fn();
  const { rerender } = render(
    <BroadcastPipeline name="Крипто" view={view()} busy={false} onAction={onAction} />,
  );
  await userEvent.click(screen.getByRole('button', { name: 'Продолжить' }));
  expect(onAction).toHaveBeenCalledTimes(1);

  rerender(
    <BroadcastPipeline
      name="Крипто"
      view={view({ action: { kind: 'stop', label: 'Остановить', disabled: false } })}
      busy
      onAction={onAction}
    />,
  );
  // A request in flight holds the button whichever side it is on.
  expect(screen.getByRole('button', { name: 'Остановить' })).toBeDisabled();
});

test('a draft with nothing sent yet has nothing under the tiles', () => {
  const { container } = render(
    <BroadcastPipeline
      name="Крипто"
      view={view({ progress: null, extras: [] })}
      busy={false}
      onAction={vi.fn()}
    />,
  );
  expect(container.querySelector('.h-meter')).toBeNull();
  // The chips belong to a run in progress, so they go with its bar.
  expect(screen.queryByText('Передано чатов: 2')).toBeNull();
});
