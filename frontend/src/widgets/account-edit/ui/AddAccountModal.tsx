import { useMutation } from '@tanstack/react-query';
import { useRef, useState, type ChangeEvent, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import { startPhoneLoginMutation } from '@/entities/account';
import { assignProxyMutation, createProxyMutation } from '@/entities/proxy';
import { FOCUS_RING, PRESS_FEEDBACK } from '@/shared/design-system';
import { Button, CloseButton, Icon, Modal, Stepper } from '@/shared/ui';

import { CodeLoginStep } from './CodeLoginStep';
import { ImportFileList } from './ImportFileList';
import { ProxyForm } from './ProxyForm';
import { EMPTY_PROXY_FORM, isProxyFormTouched, type ProxyFormValue } from './proxyFormValue';
import { ProxyPoolStep } from './ProxyPoolStep';
import { TwoFactorBulkStep } from './TwoFactorBulkStep';
import { useBulkImport } from './useBulkImport';

// The design's add-account wizard. STEP 1 provisions accounts: MANY .session /
// tdata.zip files at once, each imported by its own request (useBulkImport), or
// a bare phone number (start-login). STEP 2 assigns proxies to the just-created
// accounts. For the phone method a STEP 3 then requests + confirms the Telegram
// login code — run after the proxy is assigned so the first Telegram connection
// uses it. A LAST step then offers Telegram's cloud password for the whole batch.
// The created account ids thread across all steps.
type Method = 'session' | 'tdata' | 'phone' | null;
type ProxyStep = 'choice' | 'form' | 'pool';

// The wizard's five choice rows — three method cards on step 1 and the two proxy
// choices on step 2 — were byte-identical apart from the icon, the two strings
// and which of `selected` / `chevron` they carried.
function ChoiceCard({
  icon,
  title,
  desc,
  selected = false,
  chevron = false,
  onClick,
}: {
  icon: ReactNode;
  title: string;
  desc: string;
  selected?: boolean;
  chevron?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      // Background lives in both branches, never in the base: two `bg-*` utilities in
      // one class list are resolved by stylesheet order, where `bg-surface-card` comes last
      // and wins, so the picked method showed a blue border over a white row.
      className={`flex cursor-pointer items-center gap-3 rounded-md border px-4 py-4 text-left transition-colors hover:border-info-line ${PRESS_FEEDBACK} ${FOCUS_RING} ${selected ? 'border-action-primary bg-info-tint' : 'border-line bg-surface-card'}`}
    >
      <span className="flex size-thumbnail shrink-0 items-center justify-center rounded-md bg-info-tint">
        {icon}
      </span>
      <span className="flex-1">
        <span className="block type-h3">{title}</span>
        <span className="block type-small">{desc}</span>
      </span>
      {chevron && <Icon name="chevron-right" size={16} className="stroke-line-strong" />}
    </button>
  );
}

export function AddAccountModal({
  onClose,
  onImported,
}: {
  onClose: () => void;
  onImported: () => void;
}) {
  const { t } = useTranslation();
  const fileInput = useRef<HTMLInputElement>(null);
  const [step, setStep] = useState<1 | 2 | 3 | 4>(1);
  const [method, setMethod] = useState<Method>(null);
  const [phone, setPhone] = useState('');
  const [proxyStep, setProxyStep] = useState<ProxyStep>('choice');
  const [proxyValue, setProxyValue] = useState<ProxyFormValue>(EMPTY_PROXY_FORM);
  const [proxyValid, setProxyValid] = useState(false);
  // The id of the account the PHONE method created in step 1; file methods keep
  // theirs in the bulk import. `accountIds` below is what later steps act on.
  const [createdAccountId, setCreatedAccountId] = useState<string | null>(null);
  // The committed method, readable from a mutate-level callback that resolves
  // after the operator has moved on: those closures are never cancelled, so an
  // import/start-login that lands late must not re-provision the wizard for a
  // method it no longer holds — "Next" would unlock while afterProxy branches on
  // the NEW method, POSTing a phone login at an already-authorised .session
  // account. `selectMethod` owns both this and the state.
  const methodRef = useRef<Method>(null);
  // The cloud-password step's RESULT phase renames the dialog: the passwords it
  // shows are the point of that screen, and "Добавить аккаунт" over them is a
  // title for a wizard that is already finished. The step reports the phase up
  // because the title belongs to this component, which owns the chrome.
  const [twofaResult, setTwofaResult] = useState(false);

  const bulk = useBulkImport(method === 'tdata' ? 'tdata' : 'session', onImported);
  const startLogin = useMutation(startPhoneLoginMutation());
  const createProxy = useMutation(createProxyMutation());
  const assignProxy = useMutation(assignProxyMutation());

  const accountIds =
    method === 'phone' ? (createdAccountId ? [createdAccountId] : []) : bulk.accountIds;

  // What the operator recognises each just-created account by on the 2FA step:
  // the file it came out of, or the number they typed. The Telegram name is
  // usually not known yet — nothing has connected as this account.
  const sources: Record<string, string> =
    method === 'phone'
      ? createdAccountId
        ? { [createdAccountId]: phone }
        : {}
      : Object.fromEntries(
          bulk.files.flatMap((file) =>
            file.accountIds.map((id): [string, string] => [id, file.name]),
          ),
        );

  // Clear a FINISHED start-login only. `reset()` detaches the observer from the
  // mutation ("there is no way to get it back" — mutationObserver.ts), so
  // resetting one still in flight drops its mutate-level callbacks entirely,
  // including `onSettled: onImported`. The account is created server-side
  // regardless, so that left a real orphan the operator had to hunt down and
  // delete by hand — methodRef can only make the outcome correct when the
  // callback fires at all. A pending mutation has no error/success to clear, so
  // skipping the reset costs nothing.
  const clearFinishedStartLogin = () => {
    if (!startLogin.isPending) startLogin.reset();
  };

  const totalSteps = method === 'phone' ? 4 : 3;

  // Picking a method un-provisions the wizard, because an account created by an
  // earlier method would keep "Next" unlocked with nothing to show for it and
  // afterProxy would branch on the NEW method. Re-clicking the method ALREADY
  // selected must be a no-op: the account it provisioned really exists, and the
  // only in-wizard recovery is re-importing the same file, which the backend
  // refuses ("already exists. Delete it before importing.").
  const selectMethod = (next: Method) => {
    methodRef.current = next;
    if (method === next) return;
    bulk.reset();
    setCreatedAccountId(null);
    clearFinishedStartLogin();
    setMethod(next);
  };

  // Phone method, step 1: create the account from a bare number; success unlocks
  // "Next" exactly like a file import does.
  const onStartPhone = () => {
    startLogin.reset();
    setCreatedAccountId(null);
    const forMethod = method;
    startLogin.mutate(
      { body: { phone: phone.trim() } },
      {
        onSuccess: (account) => {
          if (methodRef.current !== forMethod) return;
          setCreatedAccountId(account.account_id);
        },
        onSettled: onImported,
      },
    );
  };

  // After proxy is assigned/skipped: step 3 either way — the code step for the
  // phone method, the cloud-password step for the file methods (which reach it
  // one step earlier, having no code to confirm).
  const afterProxy = () => {
    setStep(3);
  };

  // Every picked file becomes its own import request; the hook ignores results
  // that land after a method switch (bulk.reset()) while still refetching the
  // table for them, since the account exists server-side either way.
  const onFile = (event: ChangeEvent<HTMLInputElement>) => {
    if (event.target.files) bulk.add(event.target.files);
    event.target.value = '';
  };

  // Step 2 manual: create the entered proxy (idempotent), assign it to every
  // created account, then close. Sequential `mutateAsync` — one useMutation
  // observer is a single callback slot, and the pool has a capacity the server
  // enforces per assign. The close waits for the batch to settle so the observer
  // is still attached when `onImported` runs; a refused assign does not block
  // the close (the operator assigns the rest from the Proxies page).
  const createAndAssign = async () => {
    if (accountIds.length === 0) {
      onClose();
      return;
    }
    try {
      const created = await createProxy.mutateAsync({
        body: {
          proxy_type: proxyValue.proxy_type,
          host: proxyValue.host.trim(),
          port: Number(proxyValue.port),
          username: proxyValue.username.trim() || null,
          password: proxyValue.password || null,
        },
      });
      for (const accountId of accountIds) {
        try {
          await assignProxy.mutateAsync({
            path: { proxy_id: created.id },
            body: { account_id: accountId },
          });
        } catch {
          // Reported by the Proxies page; the remaining accounts still get theirs.
        }
      }
      onImported();
    } catch {
      // Create refused: close like the pre-bulk wizard did; the Proxies page reports it.
    } finally {
      afterProxy();
    }
  };

  // Input not yet handed to the server: a number typed but not started, a proxy typed
  // but not created. Imported files and a started login are already accounts on the
  // server, so closing loses nothing of theirs. The code and cloud-password steps hold
  // their own fields and report them through `useModalDirty`.
  const dirty =
    (step === 1 && method === 'phone' && phone.trim() !== '' && createdAccountId === null) ||
    (step === 2 && proxyStep === 'form' && isProxyFormTouched(proxyValue));

  return (
    <Modal onClose={onClose} dirty={dirty} size="form" label={t('accounts.addWizard.title')}>
      {(close) => (
        <div className="px-6 pb-6 pt-6">
          <div className="mb-4 flex items-start justify-between">
            <div>
              <div className="type-h2">
                {twofaResult
                  ? t('accounts.addWizard.twofaResultTitle')
                  : t('accounts.addWizard.title')}
              </div>
              <div className="mt-1 type-body text-content-subtle">
                {step === 1
                  ? t('accounts.addWizard.step1Label')
                  : step === 2
                    ? t('accounts.addWizard.step2Label')
                    : step === 3 && method === 'phone'
                      ? t('accounts.addWizard.step3Label')
                      : // The cloud password is always the LAST step, and which
                        // number that is depends on the method.
                        t('accounts.addWizard.stepTwofaLabel', { n: totalSteps })}
              </div>
            </div>
            <CloseButton onClick={close} aria-label={t('accounts.addWizard.close')} />
          </div>

          {/* stepper */}
          <Stepper
            className="mb-6"
            numbered
            steps={Array.from({ length: totalSteps }, (_, i) => ({
              id: String(i + 1),
              state: i + 1 < step ? 'done' : i + 1 === step ? 'current' : 'upcoming',
            }))}
          />

          {step === 1 ? (
            <>
              <div className="flex flex-col gap-3">
                <ChoiceCard
                  icon={<Icon name="file" size={18} className="stroke-action-primary" />}
                  title={t('accounts.addWizard.sessionTitle')}
                  desc={t('accounts.addWizard.sessionDesc')}
                  selected={method === 'session'}
                  onClick={() => {
                    selectMethod('session');
                  }}
                />
                <ChoiceCard
                  icon={
                    <svg
                      width="18"
                      height="18"
                      viewBox="0 0 24 24"
                      fill="none"
                      strokeWidth="1.8"
                      className="stroke-action-primary"
                    >
                      <path d="M21 8v13H3V8M1 3h22v5H1zM10 12h4" />
                    </svg>
                  }
                  title={t('accounts.addWizard.tdataTitle')}
                  desc={t('accounts.addWizard.tdataDesc')}
                  selected={method === 'tdata'}
                  onClick={() => {
                    selectMethod('tdata');
                  }}
                />
                <ChoiceCard
                  icon={
                    <svg
                      width="18"
                      height="18"
                      viewBox="0 0 24 24"
                      fill="none"
                      strokeWidth="1.8"
                      className="stroke-action-primary"
                    >
                      <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.96.36 1.9.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.9.34 1.85.57 2.81.7A2 2 0 0 1 22 16.92z" />
                    </svg>
                  }
                  title={t('accounts.addWizard.phoneTitle')}
                  desc={t('accounts.addWizard.phoneDesc')}
                  selected={method === 'phone'}
                  onClick={() => {
                    selectMethod('phone');
                  }}
                />

                {method === 'phone' && (
                  <div className="tb-fadeup flex flex-col gap-3 rounded-md border border-line bg-surface-card px-3 py-4">
                    <label className="block type-small-medium">
                      {t('accounts.addWizard.phoneLabel')}
                    </label>
                    <input
                      type="tel"
                      value={phone}
                      onChange={(event) => {
                        setPhone(event.target.value);
                        setCreatedAccountId(null);
                        clearFinishedStartLogin();
                      }}
                      placeholder={t('accounts.addWizard.phonePlaceholder')}
                      className="rounded-md border border-line bg-surface-card px-3 py-3 text-body outline-hidden focus:border-action-primary"
                    />
                    <Button
                      variant="primary"
                      size="sm"
                      className="self-start"
                      onClick={onStartPhone}
                      disabled={!phone.trim() || startLogin.isPending || Boolean(createdAccountId)}
                    >
                      {startLogin.isPending
                        ? t('accounts.addWizard.phoneCreating')
                        : createdAccountId
                          ? t('accounts.addWizard.phoneCreated')
                          : t('accounts.addWizard.phoneContinue')}
                    </Button>
                    {startLogin.isError && (
                      <div className="type-small text-danger-deep">
                        {t('accounts.addWizard.phoneError')}
                      </div>
                    )}
                  </div>
                )}

                {method && method !== 'phone' && (
                  <>
                    <input
                      ref={fileInput}
                      type="file"
                      accept={method === 'tdata' ? '.zip' : '.session'}
                      multiple
                      className="hidden"
                      onChange={onFile}
                    />
                    <button
                      type="button"
                      onClick={() => fileInput.current?.click()}
                      className="flex items-center gap-3 rounded-md border border-dashed border-line bg-surface-card px-4 py-4 text-left"
                    >
                      <span className="flex size-touch shrink-0 items-center justify-center rounded-md border border-line bg-surface-card text-action-primary">
                        <Icon name="upload-cloud" size={20} />
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block type-h3">{t('accounts.addWizard.dropTitle')}</span>
                        <span className="block type-small">
                          {method === 'tdata'
                            ? t('accounts.addWizard.dropDescTdata')
                            : t('accounts.addWizard.dropDescSession')}
                        </span>
                      </span>
                      <span className="shrink-0 rounded-full border border-line px-4 py-1 text-body font-medium text-content-primary">
                        {t('accounts.addWizard.browse')}
                      </span>
                    </button>
                    <ImportFileList files={bulk.files} onRetry={bulk.retry} />
                  </>
                )}
              </div>
              <div className="mt-6 flex justify-end gap-2">
                <Button onClick={close}>{t('accounts.addWizard.cancel')}</Button>
                {/* Locked until at least one account exists and no import is still
                  in flight: step 2 must see the whole batch, not its first half. */}
                <Button
                  variant="primary"
                  disabled={accountIds.length === 0}
                  loading={bulk.importing}
                  onClick={() => {
                    setStep(2);
                    setProxyStep('choice');
                  }}
                >
                  {t('accounts.addWizard.next')}
                </Button>
              </div>
            </>
          ) : step === 4 || (step === 3 && method !== 'phone') ? (
            <TwoFactorBulkStep
              accountIds={accountIds}
              sources={sources}
              onDone={onClose}
              onImported={onImported}
              onPhaseChange={setTwofaResult}
            />
          ) : step === 3 ? (
            <CodeLoginStep
              accountId={createdAccountId}
              phone={phone}
              onDone={() => {
                onImported();
                setStep(4);
              }}
            />
          ) : proxyStep === 'choice' ? (
            <>
              <div className="mb-4 flex items-center gap-2 rounded-md bg-success-tint px-3 py-3">
                <Icon name="check" size={16} className="stroke-success-deep" />
                <span className="type-body-medium text-success-deep">
                  {accountIds.length > 1
                    ? t('accounts.addWizard.addedMany', { count: accountIds.length })
                    : t('accounts.addWizard.added')}
                </span>
              </div>
              <div className="flex flex-col gap-3">
                <ChoiceCard
                  icon={<Icon name="plus" size={18} className="stroke-action-primary" />}
                  title={t('accounts.addWizard.proxyManual')}
                  desc={t('accounts.addWizard.proxyManualDesc')}
                  chevron
                  onClick={() => {
                    setProxyStep('form');
                  }}
                />
                <ChoiceCard
                  icon={
                    <svg
                      width="18"
                      height="18"
                      viewBox="0 0 24 24"
                      fill="none"
                      strokeWidth="1.8"
                      className="stroke-action-primary"
                    >
                      <path d="M3 6h18M3 12h18M3 18h18" />
                    </svg>
                  }
                  title={t('accounts.addWizard.proxyPool')}
                  desc={t('accounts.addWizard.proxyPoolDesc')}
                  chevron
                  onClick={() => {
                    setProxyStep('pool');
                  }}
                />
              </div>
              <div className="mt-6 flex justify-between gap-2">
                <Button
                  onClick={() => {
                    setStep(1);
                  }}
                >
                  {t('accounts.addWizard.back')}
                </Button>
                <Button className="text-content-muted" onClick={afterProxy}>
                  {t('accounts.addWizard.skip')}
                </Button>
              </div>
            </>
          ) : proxyStep === 'form' ? (
            <>
              <ProxyForm
                value={proxyValue}
                onChange={setProxyValue}
                onValidityChange={setProxyValid}
              />
              <div className="mt-6 flex justify-between gap-2">
                <Button
                  onClick={() => {
                    setProxyStep('choice');
                  }}
                >
                  {t('accounts.addWizard.back')}
                </Button>
                <Button
                  variant="primary"
                  onClick={() => {
                    void createAndAssign();
                  }}
                  disabled={!proxyValid || createProxy.isPending || assignProxy.isPending}
                >
                  {t('accounts.addWizard.done')}
                </Button>
              </div>
            </>
          ) : (
            <ProxyPoolStep
              accountIds={accountIds}
              onBack={() => {
                setProxyStep('choice');
              }}
              onDone={afterProxy}
              onImported={onImported}
            />
          )}
        </div>
      )}
    </Modal>
  );
}
