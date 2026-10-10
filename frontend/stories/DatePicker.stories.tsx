import { useState } from 'react';
import type { Meta, StoryObj } from '@storybook/react-vite';

import { defaultRunAt, MAX_LEAD_MS, ScheduleTimeField } from '../src/features/schedule-post';
import { DatePicker } from '../src/shared/ui';
import { startOfDay } from '../src/shared/lib/dateRange';

// One day on the DateRangePicker's calendar. On screen it is the popover behind the
// calendar button of the publish-time field: photo and story scheduling, up to a year out.
const today = startOfDay(new Date());

function Controlled() {
  const [day, setDay] = useState<Date | null>(null);
  return (
    <DatePicker
      value={day}
      onChange={setDay}
      minDate={today}
      maxDate={new Date(Date.now() + MAX_LEAD_MS)}
    />
  );
}

function InScheduleField() {
  const [now] = useState(() => Date.now());
  const [value, setValue] = useState<number | null>(() => defaultRunAt(now));
  return (
    <div>
      <ScheduleTimeField value={value} onChange={setValue} now={now} label="Когда опубликовать" />
    </div>
  );
}

const meta = {
  title: 'Shared/DatePicker',
  component: Controlled,
} satisfies Meta<typeof Controlled>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};
export const PublishTimeField: Story = { render: () => <InScheduleField /> };
