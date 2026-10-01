import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { toLocalInput } from '../model/runAt';
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

test('the mode control switches between now and on schedule', () => {
  const onChange = vi.fn();
  render(<ScheduleModeControl value="now" onChange={onChange} />);

  fireEvent.click(screen.getByText('По расписанию'));

  expect(onChange).toHaveBeenCalledWith('later');
});
