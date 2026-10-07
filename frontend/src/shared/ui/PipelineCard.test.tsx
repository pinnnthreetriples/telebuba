import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import { expectNoAxeViolations } from './axe.test-helpers';
import { PipelineCard } from './PipelineCard';

function renderCard(over: Partial<Parameters<typeof PipelineCard>[0]> = {}) {
  const onToggle = vi.fn();
  const view = render(
    <PipelineCard
      title="Конвейер"
      status={<span>Остановлен</span>}
      running={false}
      startLabel="Запустить"
      stopLabel="Остановить"
      onToggle={onToggle}
      steps={[
        { id: 'a', label: 'Слушает', state: 'done' },
        { id: 'b', label: 'Пост', state: 'current' },
      ]}
      notice={{ tone: 'info', text: 'Ждёт постов' }}
      stats={[{ label: 'Каналов', value: 4 }]}
      {...over}
    />,
  );
  return { onToggle, ...view };
}

test('stopped: Start toggles; running: Stop toggles', async () => {
  const { onToggle, rerender } = renderCard();
  await userEvent.click(screen.getByRole('button', { name: 'Запустить' }));
  expect(onToggle).toHaveBeenCalledTimes(1);

  rerender(
    <PipelineCard
      title="Конвейер"
      status={null}
      running
      startLabel="Запустить"
      stopLabel="Остановить"
      onToggle={onToggle}
      steps={[]}
      notice={{ tone: 'info', text: 'Работает' }}
      stats={[]}
    />,
  );
  expect(screen.queryByRole('button', { name: 'Запустить' })).toBeNull();
  await userEvent.click(screen.getByRole('button', { name: 'Остановить' }));
  expect(onToggle).toHaveBeenCalledTimes(2);
});

test('a disabled toggle cannot fire', () => {
  renderCard({ toggleDisabled: true });
  expect(screen.getByRole('button', { name: 'Запустить' })).toBeDisabled();
});

test('header, steps, notice and tiles are all on the card', async () => {
  const { container } = renderCard();
  expect(screen.getByText('Конвейер').className).toContain('type-h3');
  expect(screen.getByText('Остановлен')).toBeInTheDocument();
  expect(screen.getAllByRole('listitem')).toHaveLength(2);
  expect(screen.getByText('Ждёт постов')).toBeInTheDocument();
  expect(screen.getByText('Каналов')).toBeInTheDocument();
  await expectNoAxeViolations(container);
});

test('the status line breathes only while the pipeline runs', () => {
  const { container } = renderCard();
  expect(container.querySelector('.pl-pulse')).toBeNull();
  const { container: live } = renderCard({ running: true });
  expect(live.querySelector('.pl-pulse')).not.toBeNull();
});

test('what only one pipeline has goes under the tiles', () => {
  renderCard({ children: <p>Замен: 0</p> });
  const extra = screen.getByText('Замен: 0');
  expect(extra.parentElement?.className).toContain('mt-4');
});
