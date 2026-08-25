import { useForm, useStore } from '@tanstack/react-form';
import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { setAccountTwofaMutation } from '@/entities/account';
import type { AccountTwoFactorCreated, AccountTwoFactorUpdateRequest } from '@/shared/api';
import { Button, FormField, Icon, Input } from '@/shared/ui';

import {
  EMPTY_TWOFA_FORM,
  TWOFA_HINT_MAX_LENGTH,
  twofaFormSchema,
  type TwofaFormValue,
} from './twofaFormValue';
import { Spinner } from './_shared';
import { SEG_WRAP, seg } from './_styles';

// Only the fields the operator actually filled in are sent: a bare `{}` is the
// documented "generate one for me", and the backend forbids unknown keys, so
// nulls buy nothing over omission.
//
// An omitted `hint` means KEEP the one Telegram shows, so an emptied field has to
// travel as `''` — that is the deliberate clear, and the prefill is what makes it
// expressible. Without `initialHint` in the test there was nothing to clear, so an
// empty hint stays omitted there and a fresh set still posts `{}`.
function twofaBody(value: TwofaFormValue, initialHint: string): AccountTwoFactorUpdateRequest {
  const body: AccountTwoFactorUpdateRequest = {};
  if (value.mode === 'custom') body.password = value.password.trim();
  const hint = value.hint.trim();
  if (hint || initialHint) body.hint = hint;
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
  // The hint Telegram currently shows. It is what lets the operator CLEAR a hint
  // deliberately: the body then carries an explicit `''`, which the backend
  // distinguishes from an omitted field ("keep the live one").
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
        { path: { account_id: accountId }, body: twofaBody(value, initialHint) },
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
      <div className="mb-md type-prose">{t('accounts.edit.twofaExplain')}</div>
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
        <div className="mb-md">
          <twofaForm.Field name="password">
            {(field) => (
              // FormField's `children` slot rather than its default input: the
              // eye toggle needs a positioned wrapper, and going through the
              // slot keeps the shared FieldError (the zod key → t()) instead of
              // hand-rolling a second error renderer.
              <FormField field={field} label={t('accounts.edit.twofaPassword')}>
                <div className="relative">
                  <Input
                    className="pr-[36px] font-mono"
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
                  />
                  <button
                    type="button"
                    onClick={() => {
                      setShowPass((value) => !value);
                    }}
                    aria-label={t('accounts.edit.twofaReveal')}
                    className="absolute right-sm top-1/2 -translate-y-1/2 text-ink-subtle"
                  >
                    {showPass ? <Icon name="eye-off" size={16} /> : <Icon name="eye" size={16} />}
                  </button>
                </div>
              </FormField>
            )}
          </twofaForm.Field>
        </div>
      ) : null}
      <div className="mb-tight">
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
      <div className="mb-lg type-caption">{t('accounts.edit.twofaHintWarn')}</div>
      <Button
        size="block"
        onClick={() => {
          void twofaForm.handleSubmit();
        }}
        disabled={setTwofa.isPending || !canSubmit}
      >
        {setTwofa.isPending ? <Spinner size={14} /> : submitLabel}
      </Button>
    </>
  );
}
