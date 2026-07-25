import { useForm } from '@tanstack/react-form';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test } from 'vitest';
import { z } from 'zod';

import '@/shared/i18n';

import { FormField } from './FormField';

const schema = z.object({ name: z.string().trim().min(1, 'accounts.profile.errFirstName') });

// Two issues on one field, the shape `proxyFormValue.port` really produces: in zod
// a failed `regex` is dirty rather than aborting, so the `refine` runs anyway.
const twoIssueSchema = z.object({
  name: z
    .string()
    .regex(/^\d+$/, 'accounts.proxyForm.errPort')
    .refine(
      (value) => Number(value) >= 1 && Number(value) <= 65535,
      'accounts.proxyForm.errPortRange',
    ),
});

function Harness({ validator = schema }: { validator?: z.ZodTypeAny }) {
  const form = useForm({
    defaultValues: { name: '' },
    validators: { onChange: validator },
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

// Pins the contract react-form 1.x changed. 0.x comma-joined a field's issues into
// ONE string, so `errors[0]` was "accounts.proxyForm.errPort, …errPortRange" — not
// an i18n key, so i18next echoed it and the operator saw raw dotted identifiers.
// 1.x keeps the issues separate, so errors[0] resolves. This is the only migration
// behaviour that reached a user-visible string, and it was untested: assert the
// first message renders translated and the joined form is nowhere on screen.
test('a field with two validation issues renders the first message, translated', async () => {
  render(<Harness validator={twoIssueSchema} />);
  const input = screen.getByLabelText('Name');

  await userEvent.type(input, 'abcd');
  await waitFor(() => {
    expect(screen.getByText('Порт должен быть числом')).toBeInTheDocument();
  });
  // Not the 0.x comma-joined key string, and not a leaked raw key.
  expect(screen.queryByText(/accounts\.proxyForm/)).not.toBeInTheDocument();
});
