import type { Meta, StoryObj } from '@storybook/react-vite';

import { Spinner } from '../src/shared/ui';

const meta = {
  title: 'Shared/Spinner',
  component: Spinner,
  tags: ['autodocs'],
} satisfies Meta<typeof Spinner>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};
export const Large: Story = { args: { size: 'lg' } };
export const OnAction: Story = {
  render: () => (
    <span className="inline-flex rounded-sm bg-action-primary p-2">
      <Spinner tone="onAction" />
    </span>
  ),
};
