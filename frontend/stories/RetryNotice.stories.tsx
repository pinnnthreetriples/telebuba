import type { Meta, StoryObj } from '@storybook/react-vite';

import { RetryNotice } from '../src/widgets/account-edit/ui/RetryNotice';

const meta = {
  title: 'Design System/Patterns/Retry notice',
  component: RetryNotice,
  tags: ['autodocs'],
  args: {
    message: 'Не удалось загрузить настройки аккаунта.',
    label: 'Повторить',
    onRetry: () => undefined,
    role: 'alert',
  },
} satisfies Meta<typeof RetryNotice>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Error: Story = {};
export const Retrying: Story = { args: { disabled: true, label: 'Проверяю…' } };
