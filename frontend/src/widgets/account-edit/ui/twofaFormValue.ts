import { z } from 'zod';

// Value type + empty default + zod schema for the 2FA set/change form. Kept out
// of TwoFactorForm.tsx so that file only exports a component
// (react-refresh/only-export-components).

export interface TwofaFormValue {
  // "generate" sends no password at all and lets the backend mint one; "custom"
  // sends what the operator typed. The mode is part of the form value (not a
  // sibling useState) because it is what decides whether `password` is required.
  mode: 'generate' | 'custom';
  password: string;
  hint: string;
}

export const EMPTY_TWOFA_FORM: TwofaFormValue = { mode: 'generate', password: '', hint: '' };

export const TWOFA_MIN_LENGTH = 8;
export const TWOFA_HINT_MAX_LENGTH = 100;

// Messages are i18n keys resolved by FormField via t(). Both rules are
// cross-field, hence superRefine rather than per-field validators: the length
// only applies in "custom" mode, and the hint check needs the password.
export const twofaFormSchema = z
  .object({
    mode: z.enum(['generate', 'custom']),
    password: z.string(),
    hint: z.string().max(TWOFA_HINT_MAX_LENGTH),
  })
  .superRefine((value, ctx) => {
    if (value.mode === 'custom' && value.password.trim().length < TWOFA_MIN_LENGTH) {
      ctx.addIssue({
        code: 'custom',
        path: ['password'],
        message: 'accounts.edit.twofaErrShort',
      });
    }
    // Telegram shows the hint to whoever is at the password prompt, so a hint
    // that quotes the password publishes it. Blocked rather than warned about:
    // the whole point of the password is that the prompt cannot answer itself.
    const password = value.password.trim();
    if (password && value.hint.toLowerCase().includes(password.toLowerCase())) {
      ctx.addIssue({
        code: 'custom',
        path: ['hint'],
        message: 'accounts.edit.twofaErrHintLeaks',
      });
    }
  });
