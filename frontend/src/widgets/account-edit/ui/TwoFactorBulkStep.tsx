import { useForm, useStore } from '@tanstack/react-form';
import { useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { accountDisplayName, allAccountsQueryOptions } from '@/entities/account';
import type { AccountTwoFactorUpdateRequest } from '@/shared/api';
import { mutationErrorText } from '@/shared/lib';
import {
  Button,
  FormField,
  Icon,
  Input,
  Notice,
  ProgressBar,
  SegmentedControl,
  Spinner,
  useModalDirty,
} from '@/shared/ui';

import { BulkAccountChecklist } from './BulkAccountChecklist';
import { TwoFactorBulkResults } from './TwoFactorBulkResults';
import {
  EMPTY_TWOFA_FORM,
  TWOFA_HINT_MAX_LENGTH,
  twofaFormSchema,
  type TwofaFormValue,
} from './twofaFormValue';
import { useBulkTwofa } from './useBulkTwofa';

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
  recorded = [],
  recordFailures = {},
  sources,
  onDone,
  onImported,
  onPhaseChange,
}: {
  accountIds: string[];
  // Accounts whose existing password the import already verified and stored (from the
  // seller's JSON): nothing to set on them, so they are listed as saved and skipped.
  recorded?: string[];
  // accountId → the refusal code of a seller-JSON password that could NOT be saved
  // (wrong password, no proxy…): shown so the operator knows to set it by hand.
  recordFailures?: Record<string, string>;
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
  const toSet = accountIds.filter((id) => !recorded.includes(id));
  const [selected, setSelected] = useState<string[]>(toSet);
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
  const typed = useStore(form.store, (state) => state.isDirty);
  // Inside the add wizard a typed password must not vanish with a stray click on the veil.
  useModalDirty(phase === 'select' && typed);

  if (phase === 'result') {
    return <TwoFactorBulkResults rows={bulk.rows} label={label} onDone={onDone} />;
  }

  if (phase === 'running') {
    const done = bulk.rows.filter((row) => row.state === 'ok' || row.state === 'error').length;
    return (
      <>
        <div className="mb-2 type-small">
          {t('accounts.addWizard.twofaRunning', { done, total: bulk.rows.length })}
        </div>
        <ProgressBar value={done} max={bulk.rows.length} className="mb-4 w-full" />
        <div className="overflow-hidden rounded-md border border-line">
          {bulk.rows.map((row) => (
            <div
              key={row.accountId}
              className="flex items-center gap-3 border-b border-canvas px-3 py-2 last:border-b-0"
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
              <span className="min-w-0 flex-1 truncate type-body-medium">
                {label(row.accountId)}
              </span>
              <span
                className={`shrink-0 type-small ${row.state === 'error' ? 'text-danger-deep' : ''}`}
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
        <div className="mt-3 type-small">{t('accounts.addWizard.twofaRunningNote')}</div>
        <div className="mt-6 flex justify-end gap-2">
          <Button onClick={bulk.stop}>{t('accounts.addWizard.twofaStop')}</Button>
          <Button variant="primary" loading disabled>
            {t('accounts.addWizard.twofaWorking')}
          </Button>
        </div>
      </>
    );
  }

  const failedIds = Object.keys(recordFailures);
  const savedList =
    recorded.length + failedIds.length > 0 ? (
      <div className="mb-4 overflow-hidden rounded-md border border-line">
        {failedIds.map((accountId) => (
          <div
            key={accountId}
            className="flex items-center gap-3 border-b border-canvas px-3 py-2 last:border-b-0"
          >
            <span className="min-w-0 flex-1 truncate type-body-medium">{label(accountId)}</span>
            <span role="alert" className="shrink-0 text-right type-small text-danger-deep">
              {t('accounts.addWizard.twofaRowRecordFailed')}:{' '}
              {t(`shell.code.${recordFailures[accountId] ?? ''}`, {
                defaultValue: recordFailures[accountId] ?? '',
              })}
            </span>
          </div>
        ))}
        {recorded.map((accountId) => (
          <div
            key={accountId}
            className="flex items-center gap-3 border-b border-canvas px-3 py-2 last:border-b-0"
          >
            <span className="min-w-0 flex-1 truncate type-body-medium">{label(accountId)}</span>
            <span className="shrink-0 type-small text-success-deep">
              {t('accounts.addWizard.twofaRowRecorded')}
            </span>
          </div>
        ))}
      </div>
    ) : null;

  // Every account already carries its password: nothing left to set in this step.
  if (toSet.length === 0) {
    return (
      <>
        {savedList}
        <div className="mt-6 flex justify-end gap-2">
          <Button variant="primary" onClick={onDone}>
            {t('accounts.edit.twofaDone')}
          </Button>
        </div>
      </>
    );
  }

  return (
    <>
      {savedList}
      <div className="mb-4 type-body text-content-subtle">{t('accounts.addWizard.twofaIntro')}</div>
      <BulkAccountChecklist
        accountIds={toSet}
        sources={sources}
        selected={selected}
        onChange={setSelected}
        label={label}
      />

      <div className="mb-2 mt-4 type-small-medium">{t('accounts.addWizard.twofaModeTitle')}</div>
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
      <div className="mt-2 type-small">
        {mode === 'custom'
          ? t('accounts.addWizard.twofaShared')
          : t('accounts.addWizard.twofaPerAccount')}
      </div>

      {/* The gap below lives on the wrapper, not as `mt-sm` on the Notice inside
          it: a caller may not hand Card/Notice an outer margin (classMerge.test). */}
      {mode === 'custom' ? (
        <div className="mt-3 flex flex-col gap-2">
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
                    className="absolute right-2 top-1/2 -translate-y-1/2 text-content-subtle"
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

      <div className="mt-3">
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
      <div className="mt-2 type-small">{t('accounts.edit.twofaHintWarn')}</div>

      <div className="mt-6 flex justify-end gap-2">
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
