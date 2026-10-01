import type { Meta, StoryObj } from '@storybook/react-vite';

import { Input } from '../src/shared/ui';

const meta = {
  title: 'Design System/Components/Input',
  component: Input,
  tags: ['autodocs'],
  args: { placeholder: 'Имя аккаунта' },
} satisfies Meta<typeof Input>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};
export const Invalid: Story = { args: { invalid: true, defaultValue: 'не телефон' } };
export const Disabled: Story = { args: { disabled: true, defaultValue: 'Недоступно' } };
