import { useForm, useStore } from '@tanstack/react-form';
import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { setAccountTwofaMutation } from '@/entities/account';
import type { AccountTwoFactorCreated, AccountTwoFactorUpdateRequest } from '@/shared/api';
import { FormField } from '@/shared/ui';

import {
  EMPTY_TWOFA_FORM,
  TWOFA_HINT_MAX_LENGTH,
  twofaFormSchema,
  type TwofaFormValue,
} from './twofaFormValue';
import { Spinner } from './_shared';
import { FIELD, SEG_WRAP, seg } from './_styles';

// Only the fields the operator actually filled in are sent: a bare `{}` is the
// documented "generate one for me", and the backend forbids unknown keys, so
// nulls buy nothing over omission.
function twofaBody(value: TwofaFormValue): AccountTwoFactorUpdateRequest {
  const body: AccountTwoFactorUpdateRequest = {};
  if (value.mode === 'custom') body.password = value.password.trim();
  const hint = value.hint.trim();
  if (hint) body.hint = hint;
  return body;
}

// The set/change form, shared by the 2FA-off and 2FA-on states — both end in the
// same reveal-once panel, so both hand the response up via `onCreated` and this
// component never renders the plaintext itself.
export function TwoFactorForm({
  accountId,
  submitLabel,
  initialHint = '',
  onCreated,
}: {
  accountId: string;
  submitLabel: string;
  // The hint Telegram currently shows, so an empty field means "no hint" instead
  // of "keep whatever is there": the backend always writes the field, so a change
  // submitted with a blank hint would otherwise erase the one already set.
  initialHint?: string;
  onCreated: (created: AccountTwoFactorCreated) => void;
}) {
  const { t } = useTranslation();
  const [showPass, setShowPass] = useState(false);
  // `gcTime: 0` next to the `reset()` below: reset() detaches the observer but
  // only SCHEDULES collection, so with the 5-minute default the returned
  // plaintext would sit in the mutation cache long after the card dropped it.
  const setTwofa = useMutation({ ...setAccountTwofaMutation(), gcTime: 0 });
  const twofaForm = useForm({
    defaultValues: { ...EMPTY_TWOFA_FORM, hint: initialHint },
    validators: { onChange: twofaFormSchema, onMount: twofaFormSchema },
    onSubmit: ({ value }) => {
      setTwofa.mutate(
        { path: { account_id: accountId }, body: twofaBody(value) },
        {
          onSuccess: (created) => {
            // useMutation keeps `variables` (the typed password) and `data` (the
            // returned plaintext) until mutation gc, minutes after unmount. The
            // card promises the plaintext does not outlive it, so drop both now.
            setTwofa.reset();
            onCreated(created);
          },
        },
      );
    },
  });
  const mode = useStore(twofaForm.store, (state) => state.values.mode);
  const canSubmit = useStore(twofaForm.store, (state) => state.canSubmit);

  return (
    <>
      <div className="mb-[10px] text-[12px] text-ink-subtle">{t('accounts.edit.twofaExplain')}</div>
      <div className={SEG_WRAP}>
        {(['generate', 'custom'] as const).map((option) => (
          <button
            key={option}
            type="button"
            onClick={() => {
              twofaForm.setFieldValue('mode', option);
              // Switching back to "generate" drops the typed password: it is no
              // longer sent, and leaving it behind would keep the hint-leak
              // check firing against a field nobody can see.
              if (option === 'generate') twofaForm.setFieldValue('password', '');
            }}
            className={seg(mode === option)}
          >
            {option === 'generate'
              ? t('accounts.edit.twofaGenerate')
              : t('accounts.edit.twofaCustom')}
          </button>
        ))}
      </div>
      {mode === 'custom' ? (
        <div className="mb-[10px]">
          <twofaForm.Field name="password">
            {(field) => (
              // FormField's `children` slot rather than its default input: the
              // eye toggle needs a positioned wrapper, and going through the
              // slot keeps the shared FieldError (the zod key → t()) instead of
              // hand-rolling a second error renderer.
              <FormField field={field} label={t('accounts.edit.twofaPassword')}>
                <div className="relative">
                  <input
                    id={field.name}
                    name={field.name}
                    value={field.state.value}
                    onChange={(event) => {
                      field.handleChange(event.target.value);
                    }}
                    onBlur={field.handleBlur}
                    type={showPass ? 'text' : 'password'}
                    // The ACCOUNT's cloud password, never the operator's: `off`
                    // is documented as ignored on password inputs, so only
                    // `new-password` keeps the browser's saved credential out.
                    autoComplete="new-password"
                    className={`${FIELD} pr-9 font-mono`}
                  />
                  <button
                    type="button"
                    onClick={() => {
                      setShowPass((value) => !value);
                    }}
                    aria-label={t('accounts.edit.twofaReveal')}
                    className="absolute right-2 top-1/2 -translate-y-1/2 text-ink-subtle"
                  >
                    {showPass ? (
                      <svg
                        width="16"
                        height="16"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="1.8"
                      >
                        <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 10 8 10 8a13.16 13.16 0 0 1-1.67 2.68" />
                        <path d="M6.61 6.61A13.5 13.5 0 0 0 2 12s3 8 10 8a9.12 9.12 0 0 0 5.39-1.61" />
                        <path d="M14.12 14.12A3 3 0 1 1 9.88 9.88" />
                        <path d="M1 1l22 22" />
                      </svg>
                    ) : (
                      <svg
                        width="16"
                        height="16"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="1.8"
                      >
                        <path d="M2 12s3-8 10-8 10 8 10 8-3 8-10 8-10-8-10-8z" />
                        <circle cx="12" cy="12" r="3" />
                      </svg>
                    )}
                  </button>
                </div>
              </FormField>
            )}
          </twofaForm.Field>
        </div>
      ) : null}
      <div className="mb-[6px]">
        <twofaForm.Field name="hint">
          {(field) => (
            <FormField
              field={field}
              label={t('accounts.edit.twofaHint')}
              maxLength={TWOFA_HINT_MAX_LENGTH}
            />
          )}
        </twofaForm.Field>
      </div>
      <div className="mb-[14px] text-[11.5px] text-ink-subtle">
        {t('accounts.edit.twofaHintWarn')}
      </div>
      <button
        type="button"
        onClick={() => {
          void twofaForm.handleSubmit();
        }}
        disabled={setTwofa.isPending || !canSubmit}
        className="w-full rounded-[10px] border border-line-input bg-white py-[9px] text-[13px] font-medium disabled:opacity-50"
      >
        {setTwofa.isPending ? <Spinner size={14} /> : submitLabel}
      </button>
    </>
  );
}
