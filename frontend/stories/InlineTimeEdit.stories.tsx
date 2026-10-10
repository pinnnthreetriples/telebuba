import { useState } from 'react';
import type { Meta, StoryObj } from '@storybook/react-vite';

import { InlineTimeEdit } from '../src/shared/ui';

// Ported from Devigner UI (https://ui.devigner.cc/components/inline-time-edit). Press the
// pill: it splits into hours and minutes; ↑/↓ step (Shift ×10), Enter saves, Escape or
// clicking away discards.
function Controlled() {
  const [minutes, setMinutes] = useState(150);
  return (
    <div className="flex items-center gap-4">
      <InlineTimeEdit value={minutes} onValueChange={setMinutes} />
      <span className="type-small">сохранено: {minutes} мин</span>
    </div>
  );
}

const meta = {
  title: 'Shared/InlineTimeEdit',
  component: InlineTimeEdit,
  tags: ['autodocs'],
  decorators: [
    (Story) => (
      <div className="flex items-center rounded-lg border border-line bg-surface-card p-8">
        <Story />
      </div>
    ),
  ],
} satisfies Meta<typeof InlineTimeEdit>;

export default meta;
type Story = StoryObj<typeof meta>;

/** Closed: the duration reads as a clock beside a pen. */
export const Closed: Story = { args: { defaultValue: 150 } };

/** Editing: three tiles, hours and minutes with their units. */
export const Editing: Story = { args: { defaultValue: 150, defaultOpen: true } };

/** Closed with units instead of a clock. */
export const WithUnits: Story = { args: { defaultValue: 95, shortTime: false } };

export const Disabled: Story = { args: { defaultValue: 45, disabled: true } };

/** The owner holds the value and hears each save. */
export const ControlledValue: Story = { render: () => <Controlled /> };
