import { render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';

import { expectNoAxeViolations } from './axe.test-helpers';
import { ModalFooter } from './ModalFooter';
import { ModalHeader } from './ModalHeader';

test('заголовок — настоящий h2, рамка снизу по умолчанию', async () => {
  const { container } = render(<ModalHeader title="Лимиты аккаунта" />);
  const heading = screen.getByRole('heading', { level: 2, name: 'Лимиты аккаунта' });
  expect(heading.className).toContain('type-h2');
  const bar = container.firstElementChild as HTMLElement;
  expect(bar.className).toContain('border-b');
  expect(bar.className).toContain('px-6');
  await expectNoAxeViolations(container);
});

test('без рамки, когда под шапкой стоят свои вкладки', () => {
  const { container } = render(<ModalHeader divided={false} title="Слушатель" />);
  expect((container.firstElementChild as HTMLElement).className).not.toContain('border-b');
});

test('подзаголовок мельче без плитки и крупнее рядом с плиткой', () => {
  const { rerender } = render(<ModalHeader title="Медиа" subtitle="Ссылка на пост" />);
  expect(screen.getByText('Ссылка на пост').className).toContain('type-small');

  rerender(
    <ModalHeader title="Медиа" subtitle="Ссылка на пост" icon={<svg data-testid="icon" />} />,
  );
  const subtitle = screen.getByText('Ссылка на пост');
  expect(subtitle.className).toContain('type-body');
  expect(subtitle.className).not.toContain('type-small');
  expect(screen.getByTestId('icon').parentElement?.className).toContain('size-tile');
});

test('плитки нет без иконки, а хвост стоит после заголовка', () => {
  const { container } = render(
    <ModalHeader title="Кампания">
      <span>3 из 5</span>
    </ModalHeader>,
  );
  expect(container.querySelector('.size-tile')).toBeNull();
  const bar = container.firstElementChild as HTMLElement;
  expect(bar.lastElementChild?.textContent).toBe('3 из 5');
});

test('подвал: рамка сверху, кнопки справа', () => {
  const { container } = render(
    <ModalFooter>
      <button type="button">Сохранить</button>
    </ModalFooter>,
  );
  const bar = container.firstElementChild as HTMLElement;
  expect(bar.className).toContain('border-t');
  expect(bar.className).toContain('justify-end');
  expect(screen.getByRole('button', { name: 'Сохранить' })).toBeInTheDocument();
});
