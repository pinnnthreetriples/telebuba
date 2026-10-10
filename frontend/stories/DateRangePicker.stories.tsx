import { useState } from 'react';
import type { Meta, StoryObj } from '@storybook/react-vite';

import { DateRangePicker } from '../src/shared/ui';
import { addDays, startOfDay, type DateRange } from '../src/shared/lib/dateRange';

// Ported from Devigner UI (https://ui.devigner.cc/components/date-range-picker). Press a
// check-in, hover to preview, press a check-out; presets fill a range from the check-in.
const today = startOfDay(new Date());
const weekend = (date: Date) => date.getDay() === 0 || date.getDay() === 6;

function Controlled() {
  const [range, setRange] = useState<DateRange | null>({
    start: addDays(today, 2),
    end: addDays(today, 5),
  });
  return (
    <div className="flex flex-col gap-4">
      <DateRangePicker value={range} onValueChange={setRange} minDate={today} />
      <span className="type-small">
        {range
          ? `${range.start.toLocaleDateString('ru-RU')} — ${range.end.toLocaleDateString('ru-RU')}`
          : 'не выбрано'}
      </span>
    </div>
  );
}

const meta = {
  title: 'Shared/DateRangePicker',
  component: DateRangePicker,
  tags: ['autodocs'],
  decorators: [
    (Story) => (
      <div className="flex justify-center p-8">
        <Story />
      </div>
    ),
  ],
} satisfies Meta<typeof DateRangePicker>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Nothing picked yet; today is marked. */
export const Empty: Story = {};

/** A committed three-night range. */
export const WithRange: Story = {
  args: { defaultValue: { start: addDays(today, 3), end: addDays(today, 6) } },
};

/** Past days and weekends cannot be booked; a range stops short of a closed day. */
export const DisabledDates: Story = { args: { minDate: today, isDateDisabled: weekend } };

/** No presets row, only «Сбросить». */
export const WithoutPresets: Story = { args: { presets: [] } };

/** The owner holds the range. */
export const ControlledRange: Story = { render: () => <Controlled /> };
