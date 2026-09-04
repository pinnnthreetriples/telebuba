import { useForm, useStore } from '@tanstack/react-form';
import { useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { accountDisplayName, allAccountsQueryOptions } from '@/entities/account';
import type { AccountTwoFactorUpdateRequest } from '@/shared/api';
import { mutationErrorText } from '@/shared/lib';
import { Button, FormField, Icon, Input, Notice, SegmentedControl, Spinner } from '@/shared/ui';

import { TwoFactorBulkResults } from './TwoFactorBulkResults';
import {
  EMPTY_TWOFA_FORM,
  TWOFA_HINT_MAX_LENGTH,
  twofaFormSchema,
  type TwofaFormValue,
} from './twofaFormValue';
import { useBulkTwofa } from './useBulkTwofa';

// The square box of the slice's own checkbox row (_CheckRow), copied rather than
// reused: these rows are two lines — a display name over the file or number the
// operator recognises it by — and _CheckRow takes a single `label: string`.
//
// Written out at both sites rather than hoisted into `BOX_ON`/`BOX_OFF` consts,
// and that is not a style preference: `contrast.test.ts` reads the fill and the
// ink out of the SAME class list, so a fill behind a hoisted name is invisible to
// it and `stroke-on-action` floats up to whatever the row sits on — the header's
// `bg-surface`, which it reads at 1.05:1. `_CheckRow.tsx` writes it inline for
// the same reason and is the shape the gate has a passing fixture for.

// Only what the operator filled in travels: a bare `{}` is the documented
// "generate one for me", and the backend forbids unknown keys. There is no
// previous hint to clear in this step (every account here was created minutes
// ago), so an empty hint stays omitted.
function twofaBody(value: TwofaFormValue): AccountTwoFactorUpdateRequest {
  const body: AccountTwoFactorUpdateRequest = {};
  if (value.mode === 'custom') body.password = value.password.trim();
  const hint = value.hint.trim();
  if (hint) body.hint = hint;
  return body;
}

// The wizard's LAST step: turn Telegram's cloud password on for the accounts
// just added. Three phases — pick the accounts and the mode, watch the batch go
// through one account at a time, then read the passwords off exactly once.
export function TwoFactorBulkStep({
  accountIds,
  sources,
  onDone,
  onImported,
  onPhaseChange,
}: {
  accountIds: string[];
  // accountId → the thing the operator recognises it by: the imported file name,
  // or the phone number they typed. The account's own Telegram name is usually
  // not known yet at this point in the wizard.
  sources: Record<string, string>;
  onDone: () => void;
  onImported: () => void;
  // The dialog title changes on the result phase ("Пароли созданы"), and the
  // title belongs to the parent, which owns the chrome.
  onPhaseChange?: (isResult: boolean) => void;
}) {
  const { t } = useTranslation();
  const [phase, setPhase] = useState<'select' | 'running' | 'result'>('select');
  const [selected, setSelected] = useState<string[]>(accountIds);
  const [showPass, setShowPass] = useState(false);
  const accounts = useQuery(allAccountsQueryOptions());
  const bulk = useBulkTwofa();

  useEffect(() => {
    onPhaseChange?.(phase === 'result');
  }, [phase, onPhaseChange]);

  const label = (accountId: string) => {
    const account = accounts.data?.items.find((item) => item.account_id === accountId);
    const name = account ? accountDisplayName(account).trim() : '';
    return name && name !== accountId ? name : accountId;
  };

  const form = useForm({
    defaultValues: { ...EMPTY_TWOFA_FORM },
    validators: { onChange: twofaFormSchema, onMount: twofaFormSchema },
    onSubmit: ({ value }) => {
      setPhase('running');
      void bulk.run(selected, twofaBody(value)).then(() => {
        // Every account's 2FA status just changed; the table and the cards read it.
        onImported();
        setPhase('result');
      });
    },
  });
  const mode = useStore(form.store, (state) => state.values.mode);
  const canSubmit = useStore(form.store, (state) => state.canSubmit);

  if (phase === 'result') {
    return <TwoFactorBulkResults rows={bulk.rows} label={label} onDone={onDone} />;
  }

  if (phase === 'running') {
    const done = bulk.rows.filter((row) => row.state === 'ok' || row.state === 'error').length;
    const percent = bulk.rows.length === 0 ? 0 : Math.round((done / bulk.rows.length) * 100);
    return (
      <>
        <div className="mb-sm type-caption">
          {t('accounts.addWizard.twofaRunning', { done, total: bulk.rows.length })}
        </div>
        <div className="mb-lg h-rail w-full overflow-hidden rounded-full bg-line">
          <div
            className="h-rail rounded-full bg-action-primary"
            style={{ width: `${String(percent)}%` }}
          />
        </div>
        <div className="overflow-hidden rounded-lg border border-line">
          {bulk.rows.map((row) => (
            <div
              key={row.accountId}
              className="flex items-center gap-md border-b border-line-row px-md py-sm last:border-b-0"
            >
              <span className="flex size-glyph shrink-0 items-center justify-center">
                {row.state === 'running' ? (
                  <Spinner />
                ) : row.state === 'ok' ? (
                  <span className="flex size-glyph items-center justify-center rounded-full bg-success-tint">
                    <Icon name="check" size={14} className="stroke-success-deep" />
                  </span>
                ) : row.state === 'error' ? (
                  <Icon name="x-circle" size={14} className="stroke-danger" />
                ) : (
                  <span className="size-dot rounded-full bg-line-strong" />
                )}
              </span>
              <span className="min-w-0 flex-1 truncate type-item-title">
                {label(row.accountId)}
              </span>
              <span
                className={`shrink-0 type-caption ${row.state === 'error' ? 'text-danger' : ''}`}
              >
                {row.state === 'queued'
                  ? t('accounts.addWizard.twofaQueued')
                  : row.state === 'running'
                    ? t('accounts.addWizard.twofaSending')
                    : row.state === 'ok'
                      ? t('accounts.addWizard.twofaRowOk')
                      : mutationErrorText(row.error)}
              </span>
            </div>
          ))}
        </div>
        <div className="mt-md type-caption">{t('accounts.addWizard.twofaRunningNote')}</div>
        <div className="mt-xl flex justify-end gap-sm">
          <Button onClick={bulk.stop}>{t('accounts.addWizard.twofaStop')}</Button>
          <Button variant="primary" loading disabled>
            {t('accounts.addWizard.twofaWorking')}
          </Button>
        </div>
      </>
    );
  }

  const allOn = selected.length === accountIds.length && accountIds.length > 0;
  const toggle = (accountId: string) => {
    setSelected((prev) =>
      prev.includes(accountId) ? prev.filter((id) => id !== accountId) : [...prev, accountId],
    );
  };

  return (
    <>
      <div className="mb-lg type-prose">{t('accounts.addWizard.twofaIntro')}</div>
      <div className="overflow-hidden rounded-lg border border-line">
        <div className="flex items-center gap-md border-b border-line bg-surface px-md py-sm">
          <button
            type="button"
            role="checkbox"
            aria-checked={allOn ? true : selected.length > 0 ? 'mixed' : false}
            onClick={() => {
              setSelected(allOn ? [] : accountIds);
            }}
            className="flex items-center gap-md text-left"
          >
            <span
              className={`flex size-glyph shrink-0 items-center justify-center rounded-sm border ${selected.length > 0 ? 'border-action-primary bg-action-primary' : 'border-line bg-surface-card'}`}
            >
              {allOn ? (
                <Icon name="check" size={14} className="stroke-on-action" />
              ) : selected.length > 0 ? (
                // Indeterminate is a bar, not a check: a check here would claim
                // the whole batch is picked when only part of it is.
                <svg
                  width="14"
                  height="14"
                  viewBox="0 0 24 24"
                  fill="none"
                  strokeWidth="2.5"
                  strokeLinecap="round"
                  className="stroke-on-action"
                  aria-hidden="true"
                >
                  <path d="M6 12h12" />
                </svg>
              ) : null}
            </span>
            <span className="type-label">{t('accounts.addWizard.twofaSelectAll')}</span>
          </button>
          <span className="ml-auto shrink-0 type-caption">
            {t('accounts.addWizard.twofaSelected', {
              done: selected.length,
              total: accountIds.length,
            })}
          </span>
        </div>
        {accountIds.map((accountId) => {
          const on = selected.includes(accountId);
          return (
            <button
              key={accountId}
              type="button"
              role="checkbox"
              aria-checked={on}
              onClick={() => {
                toggle(accountId);
              }}
              className="flex w-full items-center gap-md border-b border-line-row px-md py-sm text-left last:border-b-0"
            >
              <span
                className={`flex size-glyph shrink-0 items-center justify-center rounded-sm border ${on ? 'border-action-primary bg-action-primary' : 'border-line bg-surface-card'}`}
              >
                {on && <Icon name="check" size={14} className="stroke-on-action" />}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate type-item-title">{label(accountId)}</span>
                <span className="block truncate font-mono type-caption">
                  {sources[accountId] ?? ''}
                </span>
              </span>
            </button>
          );
        })}
      </div>

      <div className="mb-sm mt-lg type-eyebrow">{t('accounts.addWizard.twofaModeTitle')}</div>
      <SegmentedControl
        variant="tray"
        value={mode}
        options={(['generate', 'custom'] as const).map((option) => ({
          value: option,
          label:
            option === 'generate'
              ? t('accounts.edit.twofaGenerate')
              : t('accounts.edit.twofaCustom'),
        }))}
        onChange={(option) => {
          form.setFieldValue('mode', option);
          // Back to "generate" drops the typed password: it is no longer sent,
          // and leaving it would keep the hint-leak rule firing against a field
          // nobody can see.
          if (option === 'generate') form.setFieldValue('password', '');
        }}
      />
      <div className="mt-sm type-caption">
        {mode === 'custom'
          ? t('accounts.addWizard.twofaShared')
          : t('accounts.addWizard.twofaPerAccount')}
      </div>

      {/* The gap below lives on the wrapper, not as `mt-sm` on the Notice inside
          it: a caller may not hand Card/Notice an outer margin (classMerge.test). */}
      {mode === 'custom' ? (
        <div className="mt-md flex flex-col gap-sm">
          <form.Field name="password">
            {(field) => (
              // FormField's `children` slot rather than its default input: the
              // eye toggle needs a positioned wrapper, and the slot keeps the
              // shared FieldError (the zod key → t()).
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
                    // The ACCOUNTS' cloud password, never the operator's: `off`
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
                    className="absolute right-sm top-1/2 -translate-y-1/2 text-content-subtle"
                  >
                    {showPass ? <Icon name="eye-off" size={16} /> : <Icon name="eye" size={16} />}
                  </button>
                </div>
              </FormField>
            )}
          </form.Field>
          <Notice tone="warning">{t('accounts.addWizard.twofaSharedWarn')}</Notice>
        </div>
      ) : null}

      <div className="mt-md">
        <form.Field name="hint">
          {(field) => (
            <FormField
              field={field}
              label={t('accounts.edit.twofaHint')}
              maxLength={TWOFA_HINT_MAX_LENGTH}
              placeholder={t('accounts.addWizard.twofaHintPlaceholder')}
            />
          )}
        </form.Field>
      </div>
      <div className="mt-tight type-caption">{t('accounts.edit.twofaHintWarn')}</div>

      <div className="mt-xl flex justify-end gap-sm">
        <Button onClick={onDone}>{t('accounts.addWizard.skip')}</Button>
        <Button
          variant="primary"
          disabled={selected.length === 0 || !canSubmit}
          onClick={() => {
            void form.handleSubmit();
          }}
        >
          {t('accounts.addWizard.twofaEnableCount', { count: selected.length })}
        </Button>
      </div>
    </>
  );
}
