import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import { expectNoAxeViolations } from './axe.test-helpers';
import { Button } from './Button';

function classesOf(name: string): string {
  return screen.getByRole('button', { name }).className;
}

test('the shape comes from the size and the fill from the variant', async () => {
  const { container } = render(
    <>
      <Button>Отмена</Button>
      <Button variant="primary" size="sm">
        Запустить
      </Button>
      <Button variant="danger">Удалить</Button>
    </>,
  );

  expect(classesOf('Отмена')).toContain('px-2xl');
  expect(classesOf('Отмена')).toContain('text-body');
  expect(classesOf('Отмена')).toContain('bg-surface-card');
  expect(classesOf('Запустить')).toContain('px-xl');
  // The rung has to survive the variant's colour: both are `text-*`, and an
  // untaught tailwind-merge drops the size in favour of the colour (see cn.ts).
  expect(classesOf('Запустить')).toContain('text-body');
  expect(classesOf('Запустить')).toContain('bg-action-primary');
  expect(classesOf('Удалить')).toContain('bg-danger-tint');
  await expectNoAxeViolations(container);
});

// `block` is the only rung that sets its own width, and the only one that is not
// inline: a `w-full` inline-level button still sits on a line and collects that
// line's leading underneath it, which is the gap six of its wearers used to carry.
test('the block rung spans its form and is not inline', () => {
  render(<Button size="block">Подтвердить</Button>);

  const classes = classesOf('Подтвердить').split(' ');
  expect(classes).toContain('w-full');
  expect(classes).toContain('flex');
  expect(classes).not.toContain('inline-flex');
  expect(classes).toContain('rounded-lg');
});

// `dashed` is a fill, so it has to compose with the rung rather than replace it —
// the three add-one-more buttons in the app are all `block`, but `block` is worn by
// three different fills and the two must not fuse into one name.
test('dashed is a fill that keeps whatever rung it is given', () => {
  render(
    <>
      <Button variant="dashed" size="block">
        Добавить кампанию
      </Button>
      <Button variant="dashed" size="sm">
        Добавить
      </Button>
    </>,
  );

  for (const name of ['Добавить кампанию', 'Добавить']) {
    expect(classesOf(name)).toContain('border-dashed');
    expect(classesOf(name)).toContain('text-info-strong');
  }
  expect(classesOf('Добавить кампанию')).toContain('w-full');
  expect(classesOf('Добавить')).toContain('px-xl');
  expect(classesOf('Добавить')).not.toContain('w-full');
});

// Every rung carries the same state vocabulary — before the component the app
// spelled `disabled` four ways and `focus-visible` three times in total.
test('every button carries the same disabled and focus treatment', () => {
  render(
    <>
      <Button size="xs">Проверить</Button>
      <Button variant="ghost">Ещё</Button>
      <Button size="block">Готово</Button>
      <Button variant="dashed">Добавить</Button>
    </>,
  );

  for (const name of ['Проверить', 'Ещё', 'Готово', 'Добавить']) {
    expect(classesOf(name)).toContain('disabled:opacity-50');
    expect(classesOf(name)).toContain('focus-visible:outline-action-primary');
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
  expect(classes).not.toContain('bg-action-primary');
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

// The indicator a keyboard operator navigates by. It was `shadow-focus` — 1.18:1 once
// composited, beside `outline-none` that removed the browser's own — so this asserts the
// two halves that were wrong: that the ring is an outline, and that nothing suppresses it.
test('focus is an outline, and the browser ring is not thrown away', () => {
  render(<Button>Сохранить</Button>);
  const cls = screen.getByRole('button').className;
  expect(cls).toContain('focus-visible:outline-2');
  expect(cls).toContain('focus-visible:outline-action-primary');
  expect(cls).not.toContain('outline-none');
  expect(cls).not.toContain('shadow-focus');
});
