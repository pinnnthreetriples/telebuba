import type { Meta, StoryObj } from '@storybook/react-vite';

import { AccountAvatar } from '../src/entities/account';

const meta = {
  title: 'Design System/Patterns/Account avatar',
  component: AccountAvatar,
  tags: ['autodocs'],
  args: {
    account: { account_id: 'example', first_name: 'Иван', last_name: 'Петров' },
    className: 'size-tile shrink-0 rounded-full',
    fallbackClassName: 'bg-info-tint text-info-strong text-body font-semibold',
  },
} satisfies Meta<typeof AccountAvatar>;

export default meta;
type Story = StoryObj<typeof meta>;

export const InTable: Story = {};
export const InWarmingCard: Story = {
  args: {
    className: 'size-tile shrink-0 rounded-full ring-2 ring-success',
    fallbackClassName: 'bg-info-tint text-info-strong text-tiny font-bold',
  },
};
