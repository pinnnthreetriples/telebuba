import { useState } from 'react';
import type { Meta, StoryObj } from '@storybook/react-vite';

import { FormField } from '../src/shared/ui';

function FormFieldExample({ initiallyTouched = false }: { initiallyTouched?: boolean }) {
  const [value, setValue] = useState('');
  const [touched, setTouched] = useState(initiallyTouched);

  return (
    <div className="w-menu">
      <FormField
        label="Имя пользователя"
        placeholder="Минимум 3 символа"
        field={{
          name: 'username',
          state: {
            value,
            meta: { isTouched: touched, errors: value.length < 3 ? ['Минимум 3 символа'] : [] },
          },
          handleChange: setValue,
          handleBlur: () => setTouched(true),
        }}
      />
    </div>
  );
}

const meta = {
  title: 'Shared/FormField',
  component: FormField,
  tags: ['autodocs'],
  args: {
    field: {
      name: 'username',
      state: { value: '', meta: { isTouched: false, errors: [] } },
      handleChange: () => undefined,
      handleBlur: () => undefined,
    },
  },
} satisfies Meta<typeof FormField>;

export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = { render: () => <FormFieldExample /> };
export const Invalid: Story = { render: () => <FormFieldExample initiallyTouched /> };
