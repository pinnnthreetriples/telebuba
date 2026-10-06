import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MotionGlobalConfig } from 'motion/react';
import { useState } from 'react';
import { afterAll, beforeAll, expect, test, vi } from 'vitest';

import { expectNoAxeViolations } from './axe.test-helpers';
import { InlineTimeEdit } from './InlineTimeEdit';

beforeAll(() => {
  MotionGlobalConfig.skipAnimations = true;
});
afterAll(() => {
  MotionGlobalConfig.skipAnimations = false;
});

const hours = () => screen.getByRole('spinbutton', { name: 'Часы' });
const minutes = () => screen.getByRole('spinbutton', { name: 'Минуты' });

test('closed it reads as a clock and only the pen is reachable; axe is clean open and closed', async () => {
  const user = userEvent.setup();
  const { container } = render(<InlineTimeEdit defaultValue={125} />);

  expect(hours()).toHaveValue('2');
  // A clock pads its minutes.
  expect(minutes()).toHaveValue('05');
  expect(hours()).toHaveAttribute('readonly');
  expect(hours()).toHaveAttribute('tabindex', '-1');
  expect(screen.getByText(':')).toBeInTheDocument();
  await expectNoAxeViolations(container);

  await user.click(screen.getByRole('button', { name: 'Изменить время' }));

  expect(screen.getByRole('button', { name: 'Сохранить время' })).toBeInTheDocument();
  expect(hours()).toHaveFocus();
  expect(hours()).not.toHaveAttribute('readonly');
  // Open, the fields take bare numbers with their units.
  expect(minutes()).toHaveValue('5');
  expect(screen.getByText('ч')).toBeInTheDocument();
  expect(screen.getByText('мин')).toBeInTheDocument();
  await expectNoAxeViolations(container);
});

test('typing and Enter saves the new total in minutes and folds back', async () => {
  const user = userEvent.setup();
  const onValueChange = vi.fn();
  render(<InlineTimeEdit defaultValue={150} onValueChange={onValueChange} />);

  await user.click(screen.getByRole('button', { name: 'Изменить время' }));
  await user.clear(hours());
  await user.type(hours(), '1x');
  await user.click(minutes());
  await user.clear(minutes());
  await user.type(minutes(), '45{Enter}');

  expect(onValueChange).toHaveBeenCalledWith(105);
  expect(screen.getByRole('button', { name: 'Изменить время' })).toHaveFocus();
  expect(hours()).toHaveValue('1');
  expect(minutes()).toHaveValue('45');
});

test('the tick saves too, and minutes over 59 clamp', async () => {
  const user = userEvent.setup();
  const onValueChange = vi.fn();
  render(<InlineTimeEdit defaultValue={60} onValueChange={onValueChange} />);

  await user.click(screen.getByRole('button', { name: 'Изменить время' }));
  await user.click(minutes());
  await user.clear(minutes());
  await user.type(minutes(), '75');
  await user.click(screen.getByRole('button', { name: 'Сохранить время' }));

  expect(onValueChange).toHaveBeenCalledWith(60 + 59);
});

test('arrow keys step by one, Shift by ten, inside each field’s range', async () => {
  const user = userEvent.setup();
  render(<InlineTimeEdit defaultValue={2 * 60 + 55} maxHours={12} />);

  await user.click(screen.getByRole('button', { name: 'Изменить время' }));
  await user.keyboard('{ArrowUp}');
  expect(hours()).toHaveValue('3');
  await user.keyboard('{Shift>}{ArrowUp}{/Shift}');
  expect(hours()).toHaveValue('12');
  expect(hours()).toHaveAttribute('aria-valuenow', '12');

  await user.click(minutes());
  await user.keyboard('{Shift>}{ArrowUp}{/Shift}');
  expect(minutes()).toHaveValue('59');
  await user.keyboard('{ArrowDown}');
  expect(minutes()).toHaveValue('58');
});

test('Escape discards the edit and returns focus to the button', async () => {
  const user = userEvent.setup();
  const onValueChange = vi.fn();
  const onOpenChange = vi.fn();
  render(<InlineTimeEdit onValueChange={onValueChange} onOpenChange={onOpenChange} />);

  await user.click(screen.getByRole('button', { name: 'Изменить время' }));
  await user.keyboard('{ArrowUp}{ArrowUp}{Escape}');

  expect(onValueChange).not.toHaveBeenCalled();
  expect(onOpenChange.mock.calls).toEqual([[true], [false]]);
  expect(hours()).toHaveValue('2');
  expect(screen.getByRole('button', { name: 'Изменить время' })).toHaveFocus();
});

test('leaving the control discards the edit, the same as Escape', async () => {
  const user = userEvent.setup();
  const onValueChange = vi.fn();
  render(
    <>
      <InlineTimeEdit onValueChange={onValueChange} />
      <button type="button">снаружи</button>
    </>,
  );

  await user.click(screen.getByRole('button', { name: 'Изменить время' }));
  await user.keyboard('{ArrowUp}');
  await user.click(screen.getByRole('button', { name: 'снаружи' }));

  expect(onValueChange).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: 'Изменить время' })).toBeInTheDocument();
  expect(hours()).toHaveValue('2');
});

test('saving an unchanged time is not a change', async () => {
  const user = userEvent.setup();
  const onValueChange = vi.fn();
  render(<InlineTimeEdit onValueChange={onValueChange} />);

  await user.click(screen.getByRole('button', { name: 'Изменить время' }));
  await user.keyboard('{Enter}');
  expect(onValueChange).not.toHaveBeenCalled();
});

test('controlled: a value from outside shows through, and the owner gets the save', async () => {
  const user = userEvent.setup();
  function Harness() {
    const [value, setValue] = useState(30);
    return (
      <>
        <InlineTimeEdit value={value} onValueChange={setValue} />
        <output aria-label="итог">{value}</output>
        <button type="button" onClick={() => setValue(90)}>
          полтора часа
        </button>
      </>
    );
  }
  render(<Harness />);

  await user.click(screen.getByRole('button', { name: 'полтора часа' }));
  expect(hours()).toHaveValue('1');
  expect(minutes()).toHaveValue('30');

  await user.click(screen.getByRole('button', { name: 'Изменить время' }));
  await user.keyboard('{ArrowUp}{Enter}');
  expect(screen.getByLabelText('итог')).toHaveTextContent('150');
});

test('with shortTime off, closed shows the units too', () => {
  render(<InlineTimeEdit shortTime={false} defaultValue={65} />);
  expect(minutes()).toHaveValue('5');
  expect(screen.getByText('ч')).toBeInTheDocument();
  expect(screen.queryByText(':')).toBeNull();
});

test('disabled cannot be opened', async () => {
  const user = userEvent.setup();
  render(<InlineTimeEdit disabled />);
  const edit = screen.getByRole('button', { name: 'Изменить время' });
  expect(edit).toBeDisabled();
  await user.click(hours());
  expect(hours()).toHaveAttribute('readonly');
});
