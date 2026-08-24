import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import { Button } from './Button';

function classesOf(name: string): string {
  return screen.getByRole('button', { name }).className;
}

test('the shape comes from the size and the fill from the variant', () => {
  render(
    <>
      <Button>Отмена</Button>
      <Button variant="primary" size="sm">
        Запустить
      </Button>
      <Button variant="danger">Удалить</Button>
    </>,
  );

  expect(classesOf('Отмена')).toContain('px-2xl');
  expect(classesOf('Отмена')).toContain('text-lead');
  expect(classesOf('Отмена')).toContain('bg-white');
  expect(classesOf('Запустить')).toContain('px-xl');
  // The rung has to survive the variant's colour: both are `text-*`, and an
  // untaught tailwind-merge drops the size in favour of the colour (see cn.ts).
  expect(classesOf('Запустить')).toContain('text-body');
  expect(classesOf('Запустить')).toContain('bg-primary');
  expect(classesOf('Удалить')).toContain('bg-danger-tint');
});

// Every rung carries the same state vocabulary — before the component the app
// spelled `disabled` four ways and `focus-visible` three times in total.
test('every button carries the same disabled and focus treatment', () => {
  render(
    <>
      <Button size="xs">Проверить</Button>
      <Button variant="ghost">Ещё</Button>
    </>,
  );

  for (const name of ['Проверить', 'Ещё']) {
    expect(classesOf(name)).toContain('disabled:opacity-50');
    expect(classesOf(name)).toContain('focus-visible:shadow-focus');
  }
});

test('a caller class wins over the variant it collides with', () => {
  render(
    <Button variant="primary" className="bg-success">
      Сохранено
    </Button>,
  );

  const classes = classesOf('Сохранено').split(' ');
  expect(classes).toContain('bg-success');
  expect(classes).not.toContain('bg-primary');
});

// `loading` and `disabled` both stop the click, but a screen reader has to hear
// the difference: one is "wait", the other is "not available".
test('loading reports itself as busy and takes no clicks', async () => {
  const onClick = vi.fn();
  render(
    <Button loading onClick={onClick}>
      Сохраняю…
    </Button>,
  );

  const button = screen.getByRole('button', { name: 'Сохраняю…' });
  expect(button).toHaveAttribute('aria-busy', 'true');
  expect(button).toBeDisabled();
  await userEvent.click(button);
  expect(onClick).not.toHaveBeenCalled();
});

test('a disabled button is not busy', () => {
  render(<Button disabled>Нельзя</Button>);

  expect(screen.getByRole('button', { name: 'Нельзя' })).not.toHaveAttribute('aria-busy');
});

// The default matters: a bare <button> inside a <form> submits it, and half of
// these live in dialogs that wrap their fields in one.
test('the default type is button and a caller can still submit', () => {
  const onSubmit = vi.fn((event: React.FormEvent) => {
    event.preventDefault();
  });
  render(
    <form onSubmit={onSubmit}>
      <Button>Показать</Button>
      <Button type="submit">Сохранить</Button>
    </form>,
  );

  expect(screen.getByRole('button', { name: 'Показать' })).toHaveAttribute('type', 'button');
  expect(screen.getByRole('button', { name: 'Сохранить' })).toHaveAttribute('type', 'submit');
});
