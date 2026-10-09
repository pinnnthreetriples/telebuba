import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MotionGlobalConfig } from 'motion/react';
import { afterAll, afterEach, beforeAll, expect, test, vi } from 'vitest';

import type { DateRange } from '@/shared/lib/dateRange';

import { expectNoAxeViolations } from './axe.test-helpers';
import { DateRangePicker } from './DateRangePicker';

beforeAll(() => {
  MotionGlobalConfig.skipAnimations = true;
});
afterAll(() => {
  MotionGlobalConfig.skipAnimations = false;
});
afterEach(() => {
  vi.restoreAllMocks();
});

// Tuesday, 6 October 2026.
const TODAY = new Date(2026, 9, 6);
const day = (m: number, d: number) => new Date(2026, m - 1, d);
const dayButton = (name: string) => screen.getByRole('button', { name });
const selected = () =>
  screen
    .getAllByRole('gridcell', { selected: true })
    .map((cell) => within(cell).getByRole('button').getAttribute('aria-label'));

test('a Russian, Monday-first month with today marked; axe is clean', async () => {
  const { container } = render(<DateRangePicker today={TODAY} />);

  expect(screen.getByRole('grid', { name: 'Октябрь 2026' })).toBeInTheDocument();
  const headers = screen.getAllByRole('columnheader').map((h) => h.textContent);
  expect(headers).toEqual(['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']);
  expect(dayButton('6 октября')).toHaveAttribute('aria-current', 'date');
  // Roving tabindex: today is the one day reachable by Tab.
  expect(dayButton('6 октября')).toHaveAttribute('tabindex', '0');
  expect(dayButton('7 октября')).toHaveAttribute('tabindex', '-1');
  expect(screen.getByText('Добавьте даты')).toBeInTheDocument();
  await expectNoAxeViolations(container);
});

test('two presses pick a range, in either order, and announce it', async () => {
  const user = userEvent.setup();
  const onValueChange = vi.fn();
  const { container } = render(<DateRangePicker today={TODAY} onValueChange={onValueChange} />);

  await user.click(dayButton('12 октября'));
  // The first press is a pending check-in, not yet a value.
  expect(onValueChange).not.toHaveBeenCalled();
  await waitFor(() => expect(screen.getByText('Выберите выезд')).toBeInTheDocument());

  await user.click(dayButton('9 октября'));
  expect(onValueChange).toHaveBeenLastCalledWith({ start: day(10, 9), end: day(10, 12) });
  expect(selected()).toEqual(['9 октября', '10 октября', '11 октября', '12 октября']);
  expect(screen.getByText('Заезд 9 октября, выезд 12 октября, 3 ночи')).toBeInTheDocument();
  await waitFor(() => expect(screen.getByText('3 ночи', { selector: 'span' })).toBeInTheDocument());
  await expectNoAxeViolations(container);
});

test('a hovered day previews the range before it is committed', async () => {
  // happy-dom's pointer cannot hover; this one can.
  vi.spyOn(window, 'matchMedia').mockImplementation(
    (query: string) =>
      ({
        matches: query.includes('hover'),
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
      }) as unknown as MediaQueryList,
  );
  const user = userEvent.setup();
  const onValueChange = vi.fn();
  render(<DateRangePicker today={TODAY} onValueChange={onValueChange} />);

  await user.click(dayButton('6 октября'));
  fireEvent.pointerEnter(dayButton('11 октября').parentElement as HTMLElement);

  expect(selected()).toHaveLength(6);
  expect(onValueChange).not.toHaveBeenCalled();
  await waitFor(() =>
    expect(screen.getByText('5 ночей', { selector: 'span' })).toBeInTheDocument(),
  );

  fireEvent.pointerLeave(dayButton('11 октября').parentElement as HTMLElement);
  expect(selected()).toEqual(['6 октября']);
});

test('disabled days cannot be picked, and a range stops short of one', async () => {
  const user = userEvent.setup();
  const onValueChange = vi.fn();
  render(
    <DateRangePicker
      today={TODAY}
      minDate={TODAY}
      isDateDisabled={(d) => d.getDate() === 15}
      onValueChange={onValueChange}
    />,
  );

  expect(dayButton('5 октября')).toBeDisabled();
  expect(dayButton('15 октября')).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Предыдущий месяц' })).toBeDisabled();

  await user.click(dayButton('12 октября'));
  await user.click(dayButton('20 октября'));
  expect(onValueChange).toHaveBeenLastCalledWith({ start: day(10, 12), end: day(10, 14) });
});

test('presets fill a range from the check-in or today and light up when they match', async () => {
  const user = userEvent.setup();
  const onValueChange = vi.fn();
  render(<DateRangePicker today={TODAY} onValueChange={onValueChange} />);

  await user.click(screen.getByRole('button', { name: '3 ночи' }));
  expect(onValueChange).toHaveBeenLastCalledWith({ start: day(10, 6), end: day(10, 9) });
  expect(screen.getByRole('button', { name: '3 ночи' })).toHaveAttribute('aria-pressed', 'true');

  // From a committed check-in on Tuesday, the weekend snaps to that week's Friday.
  await user.click(screen.getByRole('button', { name: 'Выходные' }));
  expect(onValueChange).toHaveBeenLastCalledWith({ start: day(10, 9), end: day(10, 11) });
  expect(screen.getByRole('button', { name: '3 ночи' })).toHaveAttribute('aria-pressed', 'false');

  // A range picked by hand that happens to be a week lights «1 неделя» too.
  await user.click(dayButton('20 октября'));
  await user.click(dayButton('27 октября'));
  expect(screen.getByRole('button', { name: '1 неделя' })).toHaveAttribute('aria-pressed', 'true');
});

test('«Сбросить» clears the range', async () => {
  const user = userEvent.setup();
  const onValueChange = vi.fn();
  const onClear = vi.fn();
  const value: DateRange = { start: day(10, 6), end: day(10, 9) };
  render(
    <DateRangePicker
      today={TODAY}
      defaultValue={value}
      onValueChange={onValueChange}
      onClear={onClear}
    />,
  );

  expect(selected()).toHaveLength(4);
  await user.click(screen.getByRole('button', { name: 'Сбросить' }));
  expect(onValueChange).toHaveBeenLastCalledWith(null);
  expect(onClear).toHaveBeenCalledTimes(1);
  expect(screen.queryAllByRole('gridcell', { selected: true })).toEqual([]);
});

test('pressing «Выезд» re-opens the check-out of a committed range', async () => {
  const user = userEvent.setup();
  const onValueChange = vi.fn();
  render(
    <DateRangePicker
      today={TODAY}
      defaultValue={{ start: day(10, 6), end: day(10, 9) }}
      onValueChange={onValueChange}
    />,
  );

  await user.click(screen.getByRole('button', { name: /Выезд/ }));
  await user.click(dayButton('13 октября'));
  expect(onValueChange).toHaveBeenLastCalledWith({ start: day(10, 6), end: day(10, 13) });
});

test('month buttons and the keyboard move through months', async () => {
  const user = userEvent.setup();
  const onMonthChange = vi.fn();
  render(<DateRangePicker today={TODAY} onMonthChange={onMonthChange} />);

  await user.click(screen.getByRole('button', { name: 'Следующий месяц' }));
  expect(screen.getByRole('grid', { name: 'Ноябрь 2026' })).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'Предыдущий месяц' }));
  expect(onMonthChange).toHaveBeenLastCalledWith(day(10, 1));

  dayButton('6 октября').focus();
  await user.keyboard('{ArrowRight}');
  expect(dayButton('7 октября')).toHaveFocus();
  await user.keyboard('{ArrowDown}');
  expect(dayButton('14 октября')).toHaveFocus();
  await user.keyboard('{End}');
  expect(dayButton('18 октября')).toHaveFocus();
  await user.keyboard('{Home}');
  expect(dayButton('12 октября')).toHaveFocus();
  await user.keyboard('{PageDown}');
  expect(screen.getByRole('grid', { name: 'Ноябрь 2026' })).toBeInTheDocument();
  expect(dayButton('12 ноября')).toHaveFocus();
  await user.keyboard('{Enter}');
  await waitFor(() => expect(screen.getByText('Выберите выезд')).toBeInTheDocument());
});

test('arrow keys skip disabled days', async () => {
  const user = userEvent.setup();
  render(
    <DateRangePicker today={TODAY} isDateDisabled={(d) => d.getDay() === 0 || d.getDay() === 6} />,
  );

  dayButton('9 октября').focus();
  await user.keyboard('{ArrowRight}');
  expect(dayButton('12 октября')).toHaveFocus();
});

test('without presets the row keeps only «Сбросить»', () => {
  render(<DateRangePicker today={TODAY} presets={[]} />);
  expect(screen.queryByRole('button', { name: '3 ночи' })).toBeNull();
  expect(screen.getByRole('button', { name: 'Сбросить' })).toBeInTheDocument();
});
