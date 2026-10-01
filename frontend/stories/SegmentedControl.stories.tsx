import { useState } from 'react';

import type { Meta, StoryObj } from '@storybook/react-vite';

import { SegmentedControl } from '../src/shared/ui';

const options = [
  { value: 'all', label: 'Все' },
  { value: 'live', label: 'В работе' },
  { value: 'off', label: 'Стоп' },
] as const;

function SegmentedExample({
  variant = 'tray',
  disabled = false,
}: {
  variant?: 'tray' | 'pill' | 'outline';
  disabled?: boolean;
}) {
  const [value, setValue] = useState<'all' | 'live' | 'off'>('live');
  return (
    <SegmentedControl
      variant={variant}
      disabled={disabled}
      value={value}
      onChange={setValue}
      options={options}
      ariaLabel="Фильтр"
    />
  );
}

const meta = {
  title: 'Design System/Components/SegmentedControl',
  component: SegmentedControl,
  tags: ['autodocs'],
  args: { value: 'live', onChange: () => undefined, options, ariaLabel: 'Фильтр' },
} satisfies Meta<typeof SegmentedControl>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Tray: Story = { render: () => <SegmentedExample /> };
export const Pill: Story = {
  render: () => <SegmentedExample variant="pill" />,
};
export const Outline: Story = { render: () => <SegmentedExample variant="outline" /> };
export const Disabled: Story = { render: () => <SegmentedExample disabled /> };
