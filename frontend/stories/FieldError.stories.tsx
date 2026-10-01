import type { Meta, StoryObj } from '@storybook/react-vite';

import { FieldError } from '../src/shared/ui';

const meta = {
  title: 'Design System/Components/FieldError',
  component: FieldError,
  tags: ['autodocs'],
  args: {
    field: {
      name: 'username',
      state: { value: '', meta: { isTouched: true, errors: ['Минимум 3 символа'] } },
      handleChange: () => undefined,
      handleBlur: () => undefined,
    },
  },
} satisfies Meta<typeof FieldError>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Visible: Story = {};
export const Untouched: Story = {
  args: {
    field: {
      ...meta.args.field,
      state: {
        ...meta.args.field.state,
        meta: { isTouched: false, errors: ['Минимум 3 символа'] },
      },
    },
  },
};
