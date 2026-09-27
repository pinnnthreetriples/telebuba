import { useState } from 'react';

import type { Meta, StoryObj } from '@storybook/react-vite';

import { SegmentedControl } from '../src/shared/ui';

const options = [
  { value: 'all', label: 'Все' },
  { value: 'live', label: 'В работе' },
  { value: 'off', label: 'Стоп' },
] as const;

function SegmentedExample() {
  const [value, setValue] = useState<'all' | 'live' | 'off'>('live');
  return (
    <SegmentedControl value={value} onChange={setValue} options={options} ariaLabel="Фильтр" />
  );
}

const meta = {
  title: 'Shared/SegmentedControl',
  component: SegmentedControl,
  tags: ['autodocs'],
  args: { value: 'live', onChange: () => undefined, options, ariaLabel: 'Фильтр' },
} satisfies Meta<typeof SegmentedControl>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Tray: Story = { render: () => <SegmentedExample /> };
export const Pill: Story = {
  render: () => (
    <SegmentedControl
      variant="pill"
      value="live"
      onChange={() => {}}
      options={options}
      ariaLabel="Фильтр"
    />
  ),
};
