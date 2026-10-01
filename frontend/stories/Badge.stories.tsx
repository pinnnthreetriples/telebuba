import type { Meta, StoryObj } from '@storybook/react-vite';

import { Badge } from '../src/shared/ui';

const meta = {
  title: 'Design System/Components/Badge',
  component: Badge,
  tags: ['autodocs'],
  args: { children: 'Прогрет' },
} satisfies Meta<typeof Badge>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Neutral: Story = {};
export const Info: Story = { args: { tone: 'info', dot: true } };
export const Success: Story = { args: { tone: 'success', dot: true } };
export const Danger: Story = { args: { tone: 'danger' } };
