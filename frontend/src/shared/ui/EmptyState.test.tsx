import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';

import { EmptyState } from './EmptyState';

test('серая строка по центру с воздухом списка по умолчанию', () => {
  render(<EmptyState>Аккаунтов нет</EmptyState>);
  const line = screen.getByText('Аккаунтов нет');
  expect(line.className).toContain('text-center');
  expect(line.className).toContain('type-body');
  expect(line.className).toContain('text-content-subtle');
  expect(line.className).toContain('py-8');
});

test('ступень воздуха выбирает размер', () => {
  const { rerender } = render(<EmptyState size="sm">пусто</EmptyState>);
  expect(screen.getByText('пусто').className).toContain('py-4');
  rerender(<EmptyState size="xl">пусто</EmptyState>);
  expect(screen.getByText('пусто').className).toContain('py-16');
});

test('пунктирная рамка только по просьбе', () => {
  const { rerender } = render(<EmptyState>пусто</EmptyState>);
  expect(screen.getByText('пусто').className).not.toContain('border-dashed');
  rerender(
    <EmptyState boxed size="md">
      пусто
    </EmptyState>,
  );
  const box = screen.getByText('пусто');
  expect(box.className).toContain('border-dashed');
  expect(box.className).toContain('py-6');
});

test('ошибка загрузки — та же строка красным и с ролью, которую дал вызывающий', () => {
  render(
    <EmptyState role="alert" tone="danger">
      Не загрузилось
    </EmptyState>,
  );
  const alert = screen.getByRole('alert');
  expect(alert.className).toContain('text-danger');
  expect(alert.className).not.toContain('text-content-subtle');
});
