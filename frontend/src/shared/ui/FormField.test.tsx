import { useForm } from '@tanstack/react-form';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test } from 'vitest';
import { z } from 'zod';

import '@/shared/i18n';

import { FormField } from './FormField';

const schema = z.object({ name: z.string().trim().min(1, 'accounts.profile.errFirstName') });

function Harness() {
  const form = useForm({
    defaultValues: { name: '' },
    validators: { onChange: schema },
  });
  return <form.Field name="name">{(field) => <FormField field={field} label="Name" />}</form.Field>;
}

test('renders the label and the input, and shows the translated error once touched', async () => {
  render(<Harness />);
  const input = screen.getByLabelText('Name');
  expect(input).toBeInTheDocument();
  // Untouched: no error yet.
  expect(screen.queryByText('Укажите имя')).not.toBeInTheDocument();

  // Type then clear to leave an empty (invalid) touched field.
  await userEvent.type(input, 'a');
  await userEvent.clear(input);
  await waitFor(() => {
    expect(screen.getByText('Укажите имя')).toBeInTheDocument();
  });
  // The invalid input carries the error border.
  expect(input.className).toContain('border-[#c0473f]');
});
