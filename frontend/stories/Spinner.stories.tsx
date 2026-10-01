import type { Meta, StoryObj } from '@storybook/react-vite';

import { Spinner } from '../src/shared/ui';

const meta = {
  title: 'Design System/Components/Spinner',
  component: Spinner,
  tags: ['autodocs'],
} satisfies Meta<typeof Spinner>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};
export const Large: Story = { args: { size: 'lg' } };
export const OnAction: Story = {
  render: () => (
    <span className="inline-flex rounded-md bg-action-primary p-sm">
      <Spinner tone="onAction" />
    </span>
  ),
};
