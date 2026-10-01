import type { Meta, StoryObj } from '@storybook/react-vite';

import { Notice } from '../src/shared/ui';

const meta = {
  title: 'Design System/Components/Notice',
  component: Notice,
  tags: ['autodocs'],
  args: {
    tone: 'warning',
    children: 'Прокси отвечает медленнее порога — аккаунт снят с очереди.',
  },
} satisfies Meta<typeof Notice>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Bordered: Story = {};
export const Nested: Story = { args: { bordered: false } };
