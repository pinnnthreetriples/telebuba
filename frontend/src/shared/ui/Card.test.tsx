import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';

import { expectNoAxeViolations } from './axe.test-helpers';
import { Card } from './Card';

test('the surface is the card and the padding is the caller`s', () => {
  render(
    <Card data-testid="plain" className="p-lg">
      тело
    </Card>,
  );

  const card = screen.getByTestId('plain');
  expect(card.className).toContain('rounded-card');
  expect(card.className).toContain('border-line');
  expect(card.className).toContain('bg-surface-card');
  expect(card.className).toContain('p-lg');
  expect(card.className).not.toContain('px-xl');
});

test('the title and subtitle slots render only when given', async () => {
  const { container, rerender } = render(<Card data-testid="c">тело</Card>);
  expect(screen.getByTestId('c').textContent).toBe('тело');

  rerender(
    <Card data-testid="c" title="Ключи" subtitle="откуда берутся">
      тело
    </Card>,
  );
  expect(screen.getByText('Ключи')).toBeInTheDocument();
  expect(screen.getByText('откуда берутся')).toBeInTheDocument();
  await expectNoAxeViolations(container);
});

// Карточка не носит внешнего отступа НИКОГДА — ни своего, ни по просьбе. Проп `mb` был
// ровно такой просьбой: карточка ставила расстояние до того, что стоит под ней, ничего об
// этом не зная, и настройки просили `lg` четыре раза и `xl` один. Утверждение осталось на
// месте, но стало безусловным.
test('карточка не носит внешнего отступа', () => {
  render(<Card data-testid="c">тело</Card>);
  expect(screen.getByTestId('c').className).not.toContain('mb-');
});
