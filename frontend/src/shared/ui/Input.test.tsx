import { createRef } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import { expectNoAxeViolations } from './axe.test-helpers';
import { Button } from './Button';
import { Input, Textarea } from './Input';

// Высота ФИКСИРОВАННАЯ, а не сумма padding и интерлиньяжа. Разница не косметическая:
// пока она складывалась, смена рунга размера меняла высоту контрола, и `size="md"` значил
// 41px у поля против 40px у кнопки рядом.
test('the height and type size come from the size prop', async () => {
  const { container } = render(
    <>
      <Input aria-label="Ключ" />
      <Input aria-label="Порог" size="sm" />
      <Input aria-label="Лимит" size="xs" />
    </>,
  );

  expect(screen.getByLabelText('Ключ').className).toContain('h-control');
  expect(screen.getByLabelText('Порог').className).toContain('h-field');
  expect(screen.getByLabelText('Лимит').className).toContain('h-compact');
  // Ни одна ступень не набирает вертикальный padding: он и был тем, из чего высота
  // складывалась.
  expect(screen.getByLabelText('Ключ').className).not.toMatch(/py-/);
  await expectNoAxeViolations(container);
});

// Это и есть то, что рецепт контрола ДОБАВЛЯЕТ: имя ступени значит одно и то же у кнопки
// и у поля. Раньше не значило, и увидеть это можно было только линейкой на экране.
test('a size name means the same height in Button and Input', () => {
  render(
    <>
      <Button size="md">Сохранить</Button>
      <Input aria-label="Ключ" size="md" />
      <Button size="sm">Отмена</Button>
      <Input aria-label="Порог" size="sm" />
    </>,
  );

  const height = (node: Element) => /h-[\w-]+/.exec(node.className)?.[0];
  expect(height(screen.getByRole('button', { name: 'Сохранить' }))).toBe(
    height(screen.getByLabelText('Ключ')),
  );
  expect(height(screen.getByRole('button', { name: 'Отмена' }))).toBe(
    height(screen.getByLabelText('Порог')),
  );
});

// A red border alone is a colour carrying meaning, so the state is published to
// assistive tech as well — the message itself is `FieldError`'s job.
test('invalid paints the border and says so out loud', () => {
  render(<Input aria-label="Прокси" invalid />);

  const field = screen.getByLabelText('Прокси');
  expect(field).toHaveAttribute('aria-invalid', 'true');
  expect(field.className).toContain('border-danger');
  expect(field.className).not.toContain('border-line');
});

test('a valid field claims neither', () => {
  render(<Input aria-label="Прокси" />);

  const field = screen.getByLabelText('Прокси');
  expect(field).not.toHaveAttribute('aria-invalid');
  expect(field.className).toContain('border-line');
});

test('the flat tone drops the white fill for the inert one', () => {
  render(<Input aria-label="Модель" tone="flat" disabled value="iPhone 14" readOnly />);

  expect(screen.getByLabelText('Модель').className).toContain('bg-canvas');
});

test('a caller class wins over the tone it collides with', () => {
  render(<Input aria-label="Пароль" tone="flat" className="text-content-primary" />);

  expect(screen.getByLabelText('Пароль').className).toContain('text-content-primary');
});

test('Textarea starts at one row, prevents manual resize and keeps the field styling', async () => {
  const onChange = vi.fn();
  render(<Textarea aria-label="Промпт" size="sm" onChange={onChange} />);

  const area = screen.getByLabelText<HTMLTextAreaElement>('Промпт');
  expect(area.tagName).toBe('TEXTAREA');
  expect(area.getAttribute('rows')).toBe('1');
  expect(area.className).toContain('py-1');
  expect(area.className).toContain('resize-none');
  expect(area.className).not.toMatch(/h-/);
  await userEvent.type(area, 'ок');
  expect(onChange).toHaveBeenCalled();
});

test('Textarea grows and shrinks with text changes while forwarding input and ref', () => {
  const ref = createRef<HTMLTextAreaElement>();
  const onInput = vi.fn();
  const { rerender } = render(
    <Textarea aria-label="Промпт" value="одна" onChange={vi.fn()} onInput={onInput} ref={ref} />,
  );
  const area = screen.getByLabelText<HTMLTextAreaElement>('Промпт');
  expect(ref.current).toBe(area);
  Object.defineProperty(area, 'scrollHeight', {
    configurable: true,
    get: () => 16 + area.value.split('\n').length * 20,
  });

  rerender(
    <Textarea
      aria-label="Промпт"
      value={'одна\nдве\nтри'}
      onChange={vi.fn()}
      onInput={onInput}
      ref={ref}
    />,
  );
  expect(area.style.height).toBe('76px');

  rerender(
    <Textarea aria-label="Промпт" value="одна" onChange={vi.fn()} onInput={onInput} ref={ref} />,
  );
  expect(area.style.height).toBe('36px');

  fireEvent.input(area);
  expect(onInput).toHaveBeenCalledOnce();
  expect(area.style.height).toBe('36px');
});

// The focus ring is one shared recipe (`.tb-time` in index.css) rather than a
// per-field `focus:` class, which is why the fields turn the outline off.
test('every field wears the shared focus treatment', () => {
  render(<Input aria-label="Ключ" />);

  const classes = screen.getByLabelText('Ключ').className;
  expect(classes).toContain('tb-time');
  expect(classes).toContain('outline-none');
});
