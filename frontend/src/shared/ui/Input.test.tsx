import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import { Input, Textarea } from './Input';

test('the vertical padding and type size come from the size prop', () => {
  render(
    <>
      <Input aria-label="Ключ" />
      <Input aria-label="Порог" size="sm" />
      <Input aria-label="Лимит" size="xs" />
    </>,
  );

  // On one rhythm the three rungs share their horizontal padding and differ
  // vertically, which is what they were reaching for at 12/11/9px apart.
  expect(screen.getByLabelText('Ключ').className).toContain('py-md');
  expect(screen.getByLabelText('Порог').className).toContain('py-sm');
  expect(screen.getByLabelText('Лимит').className).toContain('py-tight');
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
  render(<Input aria-label="Пароль" tone="flat" className="text-ink" />);

  expect(screen.getByLabelText('Пароль').className).toContain('text-ink');
});

test('Textarea takes the same shape and reaches its element by ref', async () => {
  const onChange = vi.fn();
  render(<Textarea aria-label="Промпт" size="sm" onChange={onChange} />);

  const area = screen.getByLabelText('Промпт');
  expect(area.tagName).toBe('TEXTAREA');
  expect(area.className).toContain('py-sm');
  await userEvent.type(area, 'ок');
  expect(onChange).toHaveBeenCalled();
});

// The focus ring is one shared recipe (`.tb-time` in index.css) rather than a
// per-field `focus:` class, which is why the fields turn the outline off.
test('every field wears the shared focus treatment', () => {
  render(<Input aria-label="Ключ" />);

  const classes = screen.getByLabelText('Ключ').className;
  expect(classes).toContain('tb-time');
  expect(classes).toContain('outline-none');
});
