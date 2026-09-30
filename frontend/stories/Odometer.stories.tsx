import type { Meta, StoryObj } from '@storybook/react-vite';

import { Odometer } from '../src/shared/ui';

const meta = {
  title: 'Shared/Odometer',
  component: Odometer,
  tags: ['autodocs'],
  args: { value: 14 },
  argTypes: {
    value: {
      control: { type: 'number', min: 0, step: 1 },
      description: 'Non-negative integer. Change the value to see the digits roll.',
    },
  },
} satisfies Meta<typeof Odometer>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};
export const StatTones: Story = {
  render: () => (
    <div className="flex flex-wrap gap-lg">
      <Odometer value={2} />
      <Odometer value={4} className="text-action-primary" />
      <Odometer value={14} className="text-success-deep" />
      <Odometer value={1} className="text-danger" />
    </div>
  ),
};
