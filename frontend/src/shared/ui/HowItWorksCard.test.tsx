import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test } from 'vitest';

import { HowItWorksCard } from './HowItWorksCard';

const STEPS = ['Создайте кампанию', 'Добавьте аккаунты', 'Запустите прогон'];

test('свёрнута, а раскрытая показывает пронумерованные шаги', async () => {
  render(<HowItWorksCard title="Как это работает" steps={STEPS} />);
  const [toggle] = screen.getAllByRole('button', { name: 'Как это работает' });
  if (toggle === undefined) throw new Error('нет кнопки раскрытия');
  expect(toggle).toHaveAttribute('aria-expanded', 'false');

  await userEvent.click(toggle);
  expect(toggle).toHaveAttribute('aria-expanded', 'true');
  STEPS.forEach((step, index) => {
    expect(screen.getByText(step)).toBeInTheDocument();
    expect(screen.getByText(String(index + 1))).toBeInTheDocument();
  });
});

test('подсказка над шагами и две колонки — только по просьбе', () => {
  const { container, rerender } = render(<HowItWorksCard title="Т" steps={STEPS} />);
  expect(container.querySelector('.md\\:grid-cols-2')).toBeNull();
  expect(screen.queryByText('Подсказка')).toBeNull();

  rerender(<HowItWorksCard title="Т" steps={STEPS} hint="Подсказка" columns={2} />);
  expect(container.querySelector('.md\\:grid-cols-2')).not.toBeNull();
  expect(screen.getByText('Подсказка')).toBeInTheDocument();
});
