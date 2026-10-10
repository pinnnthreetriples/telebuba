import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { expect, test } from 'vitest';

import { NumberField } from './fields';

// The settings dialog owns the number; the field only edits it.
function Owned({ initial, min, max }: { initial: number; min: number; max: number }) {
  const [value, setValue] = useState(initial);
  return (
    <>
      <NumberField value={value} label="Пауза" min={min} max={max} onChange={setValue} />
      <output>{value}</output>
    </>
  );
}

test('a zero can be erased and another number typed in its place', async () => {
  render(<Owned initial={0} min={0} max={10_000} />);
  const field = screen.getByRole('spinbutton', { name: 'Пауза' });

  await userEvent.clear(field);
  expect(field).toHaveValue(null);
  // Nothing typed yet, so the draft keeps the number it had.
  expect(screen.getByRole('status')).toHaveTextContent('0');

  await userEvent.type(field, '45');
  expect(field).toHaveValue(45);
  expect(screen.getByRole('status')).toHaveTextContent('45');
});

test('erasing "60" to type "180" does not leave "0180"', async () => {
  render(<Owned initial={60} min={1} max={10_000} />);
  const field = screen.getByRole('spinbutton', { name: 'Пауза' });

  await userEvent.clear(field);
  await userEvent.type(field, '180');
  expect(field).toHaveValue(180);
  expect(screen.getByRole('status')).toHaveTextContent('180');
});

test('a field left empty shows the saved number again', async () => {
  render(<Owned initial={7} min={1} max={10_000} />);
  const field = screen.getByRole('spinbutton', { name: 'Пауза' });

  await userEvent.clear(field);
  await userEvent.tab();
  expect(field).toHaveValue(7);
});

test('a number over the limit is clamped as it is typed', async () => {
  render(<Owned initial={1} min={1} max={500} />);
  const field = screen.getByRole('spinbutton', { name: 'Пауза' });

  await userEvent.clear(field);
  await userEvent.type(field, '9999');
  expect(field).toHaveValue(500);
  expect(screen.getByRole('status')).toHaveTextContent('500');
});
