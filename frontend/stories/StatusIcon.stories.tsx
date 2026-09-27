import type { Meta, StoryObj } from '@storybook/react-vite';

import { StatusIcon } from '../src/shared/ui';

const meta = {
  title: 'Shared/StatusIcon',
  component: StatusIcon,
  tags: ['autodocs'],
  args: { kind: 'ok' },
} satisfies Meta<typeof StatusIcon>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Ok: Story = {};
export const Error: Story = { args: { kind: 'err' } };
