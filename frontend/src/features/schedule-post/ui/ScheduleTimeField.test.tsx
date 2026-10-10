import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MotionGlobalConfig } from 'motion/react';
import { useState } from 'react';
import { afterAll, beforeAll, expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { MAX_LEAD_MS, toLocalInput } from '../model/runAt';
import { ScheduleModeControl } from './ScheduleModeControl';
import { ScheduleTimeField } from './ScheduleTimeField';

const NOW = Date.UTC(2026, 9, 1, 10, 0);

beforeAll(() => {
  MotionGlobalConfig.skipAnimations = true;
});
afterAll(() => {
  MotionGlobalConfig.skipAnimations = false;
});

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

test('a day and a time picked in the popover land in the field only on «Готово»', async () => {
  const user = userEvent.setup();
  const start = NOW + 2 * 3_600_000;
  render(<Harness initial={start} />);

  await user.click(screen.getByRole('button', { name: 'Выбрать дату и время' }));
  const popover = screen.getByRole('dialog', { name: 'Выбрать дату и время' });
  const target = new Date(start);
  target.setDate(target.getDate() + 3);
  await user.click(
    within(popover).getByRole('button', {
      name: target.toLocaleDateString('ru', { day: 'numeric', month: 'long', year: 'numeric' }),
    }),
  );
  await user.click(within(popover).getByRole('button', { name: 'Изменить время' }));
  await user.keyboard('{ArrowUp}');

  expect(screen.getByLabelText('Когда')).toHaveValue(toLocalInput(start));
  await user.click(within(popover).getByRole('button', { name: 'Готово' }));

  target.setHours(target.getHours() + 1);
  expect(screen.getByLabelText('Когда')).toHaveValue(toLocalInput(target.getTime()));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

test('a draft that is too soon cannot be confirmed, and says why', async () => {
  const user = userEvent.setup();
  render(<Harness initial={NOW + 3 * 3_600_000} />);

  await user.click(screen.getByRole('button', { name: 'Выбрать дату и время' }));
  const popover = screen.getByRole('dialog');
  await user.click(within(popover).getByRole('button', { name: 'Изменить время' }));
  for (let i = 0; i < 5; i += 1) await user.keyboard('{ArrowDown}');
  await user.keyboard('{Enter}');

  await user.click(within(popover).getByRole('button', { name: 'Готово' }));
  expect(within(popover).getByRole('button', { name: 'Готово' })).toHaveAttribute(
    'aria-disabled',
    'true',
  );
  expect(within(popover).getByRole('alert')).toHaveTextContent('Слишком рано');
});

test('the calendar offers days up to a year ahead, and no further', () => {
  render(<Harness initial={NOW + 3_600_000} />);

  fireEvent.click(screen.getByRole('button', { name: 'Выбрать дату и время' }));
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

test('the last day offered never lands past the year, whatever the time of day', async () => {
  const user = userEvent.setup();
  // Three hours past `now`'s time of day: on the last day, that would be past the year.
  render(<Harness initial={NOW + 3 * 3_600_000} />);

  await user.click(screen.getByRole('button', { name: 'Выбрать дату и время' }));
  const popover = screen.getByRole('dialog');
  const next = within(popover).getByRole('button', { name: 'Следующий месяц' });
  for (let i = 0; i < 12; i += 1) await user.click(next);
  const last = new Date(NOW + MAX_LEAD_MS);
  await user.click(
    within(popover).getByRole('button', {
      name: last.toLocaleDateString('ru', { day: 'numeric', month: 'long', year: 'numeric' }),
    }),
  );
  await user.click(within(popover).getByRole('button', { name: 'Готово' }));

  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.getByLabelText('Когда')).toHaveValue(toLocalInput(NOW + MAX_LEAD_MS));
});

test('focus dropping to nothing keeps the popover; Tab to a control outside closes it', async () => {
  const user = userEvent.setup();
  render(
    <>
      <Harness initial={NOW + 3_600_000} />
      <button type="button">снаружи</button>
    </>,
  );

  await user.click(screen.getByRole('button', { name: 'Выбрать дату и время' }));
  const day = within(screen.getByRole('dialog')).getAllByRole('button')[2];
  // Safari, or a press on the popover's padding: focus leaves for no element.
  fireEvent.blur(day as HTMLElement, { relatedTarget: null });
  expect(screen.getByRole('dialog')).toBeInTheDocument();

  fireEvent.blur(day as HTMLElement, { relatedTarget: screen.getByText('снаружи') });
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
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

  fireEvent.click(screen.getByRole('button', { name: 'Выбрать дату и время' }));
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
