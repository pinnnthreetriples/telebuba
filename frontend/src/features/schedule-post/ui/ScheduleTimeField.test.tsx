import { fireEvent, render, screen, within } from '@testing-library/react';
import { useState } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { MAX_LEAD_MS, toLocalInput } from '../model/runAt';
import { ScheduleModeControl } from './ScheduleModeControl';
import { ScheduleTimeField } from './ScheduleTimeField';

const NOW = Date.UTC(2026, 9, 1, 10, 0);

function Harness({ initial }: { initial: number | null }) {
  const [value, setValue] = useState<number | null>(initial);
  return <ScheduleTimeField value={value} onChange={setValue} now={NOW} label="Когда" />;
}

test('a valid time is echoed in words, with how far away it is', () => {
  render(<Harness initial={NOW + 2 * 3_600_000} />);

  const field = screen.getByLabelText('Когда');
  expect(field).toHaveValue(toLocalInput(NOW + 2 * 3_600_000));
  expect(field).not.toHaveAttribute('aria-invalid');
  expect(screen.getByText(/Не раньше .* · через 2 часа/)).toBeInTheDocument();
});

test('a time in the past is flagged on the field and explained under it', () => {
  render(<Harness initial={NOW + 3_600_000} />);

  fireEvent.change(screen.getByLabelText('Когда'), {
    target: { value: toLocalInput(NOW - 3_600_000) },
  });

  expect(screen.getByLabelText('Когда')).toHaveAttribute('aria-invalid', 'true');
  expect(screen.getByText(/Слишком рано/)).toBeInTheDocument();
});

test('clearing the field asks for a time instead of flagging an error', () => {
  render(<Harness initial={NOW + 3_600_000} />);

  fireEvent.change(screen.getByLabelText('Когда'), { target: { value: '' } });

  expect(screen.getByLabelText('Когда')).not.toHaveAttribute('aria-invalid');
  expect(screen.getByText('Выберите дату и время')).toBeInTheDocument();
});

test('a day picked on the calendar keeps the time of day and closes the popover', () => {
  const start = NOW + 2 * 3_600_000;
  render(<Harness initial={start} />);

  fireEvent.click(screen.getByRole('button', { name: 'Выбрать день в календаре' }));
  const calendar = screen.getByRole('dialog', { name: 'Выбрать день в календаре' });
  const target = new Date(start);
  target.setDate(target.getDate() + 3);
  fireEvent.click(
    within(calendar).getByRole('button', {
      name: target.toLocaleDateString('ru', { day: 'numeric', month: 'long', year: 'numeric' }),
    }),
  );

  expect(screen.getByLabelText('Когда')).toHaveValue(toLocalInput(target.getTime()));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

test('the calendar offers days up to a year ahead, and no further', () => {
  render(<Harness initial={NOW + 3_600_000} />);

  fireEvent.click(screen.getByRole('button', { name: 'Выбрать день в календаре' }));
  const next = screen.getByRole('button', { name: 'Следующий месяц' });
  for (let i = 0; i < 12; i += 1) fireEvent.click(next);

  expect(next).toBeDisabled();
  const last = new Date(NOW + MAX_LEAD_MS);
  const label = (date: Date) =>
    date.toLocaleDateString('ru', { day: 'numeric', month: 'long', year: 'numeric' });
  expect(screen.getByRole('button', { name: label(last) })).toBeEnabled();
  const after = new Date(last);
  after.setDate(after.getDate() + 1);
  if (after.getMonth() === last.getMonth()) {
    expect(screen.getByRole('button', { name: label(after) })).toBeDisabled();
  }
});

test('Escape closes the calendar without reaching the editor around it', () => {
  const onEscape = vi.fn();
  render(
    <div
      onKeyDown={(event) => {
        if (event.key === 'Escape') onEscape();
      }}
    >
      <Harness initial={NOW + 3_600_000} />
    </div>,
  );

  fireEvent.click(screen.getByRole('button', { name: 'Выбрать день в календаре' }));
  fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });

  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(onEscape).not.toHaveBeenCalled();
});

test('the mode control switches between now and on schedule', () => {
  const onChange = vi.fn();
  render(<ScheduleModeControl value="now" onChange={onChange} />);

  fireEvent.click(screen.getByText('По расписанию'));

  expect(onChange).toHaveBeenCalledWith('later');
});
