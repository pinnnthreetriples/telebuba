import { fireEvent, render, screen } from '@testing-library/react';
import { expect, test, vi } from 'vitest';

import { DatePicker } from './DatePicker';

const TODAY = new Date(2026, 9, 6);
const day = (date: Date) =>
  screen.getByRole('button', {
    name: date.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' }),
  });

test('picking a day reports it and marks it selected', () => {
  const onChange = vi.fn();
  const { rerender } = render(<DatePicker value={null} onChange={onChange} today={TODAY} />);

  fireEvent.click(day(new Date(2026, 9, 9)));

  expect(onChange).toHaveBeenCalledWith(new Date(2026, 9, 9));
  rerender(<DatePicker value={new Date(2026, 9, 9)} onChange={onChange} today={TODAY} />);
  expect(day(new Date(2026, 9, 9)).parentElement).toHaveAttribute('aria-selected', 'true');
});

test('days outside min and max cannot be picked, nor months past them', () => {
  render(
    <DatePicker
      value={null}
      onChange={vi.fn()}
      today={TODAY}
      minDate={TODAY}
      maxDate={new Date(2026, 9, 20)}
    />,
  );

  expect(day(new Date(2026, 9, 5))).toBeDisabled();
  expect(day(new Date(2026, 9, 21))).toBeDisabled();
  expect(day(new Date(2026, 9, 20))).toBeEnabled();
  expect(screen.getByRole('button', { name: 'Предыдущий месяц' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Следующий месяц' })).toBeDisabled();
});

test('autoFocus lands on the selected day and arrows walk across months', () => {
  render(<DatePicker value={new Date(2026, 9, 31)} onChange={vi.fn()} today={TODAY} autoFocus />);

  expect(day(new Date(2026, 9, 31))).toHaveFocus();
  fireEvent.keyDown(day(new Date(2026, 9, 31)), { key: 'ArrowRight' });

  expect(screen.getByRole('grid', { name: 'Ноябрь 2026' })).toBeInTheDocument();
  expect(day(new Date(2026, 10, 1))).toHaveFocus();
});

test('a selection later in the day than maxDate is still pickable and takes focus', () => {
  render(
    <DatePicker
      value={new Date(2026, 9, 20, 15, 0)}
      onChange={vi.fn()}
      today={TODAY}
      maxDate={new Date(2026, 9, 20, 10, 0)}
      autoFocus
    />,
  );

  expect(day(new Date(2026, 9, 20))).toBeEnabled();
  expect(day(new Date(2026, 9, 20))).toHaveFocus();
});

test('month and weekday names follow the locale', () => {
  render(<DatePicker value={null} onChange={vi.fn()} today={TODAY} locale="en" />);

  expect(screen.getByRole('grid', { name: 'October 2026' })).toBeInTheDocument();
  expect(screen.getByRole('columnheader', { name: 'Monday' })).toBeInTheDocument();
});
