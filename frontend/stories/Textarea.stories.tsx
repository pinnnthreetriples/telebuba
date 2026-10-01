import type { Meta, StoryObj } from '@storybook/react-vite';

import { Textarea } from '../src/shared/ui';

const meta = {
  title: 'Design System/Components/Textarea',
  component: Textarea,
  tags: ['autodocs'],
  args: { placeholder: 'Промпт для генерации комментария' },
  parameters: {
    docs: {
      description: {
        component: 'Высота следует за текстом: поле растёт при вводе и уменьшается после удаления.',
      },
    },
  },
} satisfies Meta<typeof Textarea>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};
export const Invalid: Story = { args: { invalid: true } };
export const Multiline: Story = {
  args: { defaultValue: 'Первая строка промпта.\nВторая строка с уточнением.\nТретья строка.' },
};
