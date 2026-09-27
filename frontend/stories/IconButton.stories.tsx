import type { Meta, StoryObj } from '@storybook/react-vite';

import { Icon, IconButton } from '../src/shared/ui';

const meta = {
  title: 'Shared/IconButton',
  component: IconButton,
  tags: ['autodocs'],
  args: { 'aria-label': 'Изменить', children: <Icon name="pencil" size={14} /> },
} satisfies Meta<typeof IconButton>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};
export const Action: Story = { args: { tone: 'action', 'aria-label': 'Добавить' } };
export const Danger: Story = { args: { tone: 'danger' } };
export const Circle: Story = { args: { shape: 'circle' } };
export const Disabled: Story = { args: { disabled: true } };
