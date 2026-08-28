import { useMutation, useQuery } from '@tanstack/react-query';
import { Fragment, useRef, useState, type ChangeEvent, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';

import {
  importAccountSessionMutation,
  importAccountTdataMutation,
  startPhoneLoginMutation,
} from '@/entities/account';
import {
  assignProxyMutation,
  createProxyMutation,
  proxyPoolQueryOptions,
  proxyTypeLabel,
} from '@/entities/proxy';
import { Button, Icon, IconButton, Modal, Spinner } from '@/shared/ui';

import { CodeLoginStep } from './CodeLoginStep';
import { ProxyForm } from './ProxyForm';
import { EMPTY_PROXY_FORM, type ProxyFormValue } from './proxyFormValue';

// The design's add-account wizard. STEP 1 provisions an account: .session /
// tdata.zip import via the real import endpoints, or a bare phone number
// (start-login). STEP 2 assigns a proxy to the just-created account. For the
// phone method a STEP 3 then requests + confirms the Telegram login code — run
// after the proxy is assigned so the first Telegram connection uses it. The
// created account's id threads across all steps.
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
      className={`flex cursor-pointer items-center gap-md rounded-lg border px-lg py-lg text-left transition-colors hover:border-info-line ${selected ? 'border-action-primary bg-info-tint' : 'border-line bg-surface-card'}`}
    >
      <span className="flex size-thumbnail shrink-0 items-center justify-center rounded-lg bg-info-tint">
        {icon}
      </span>
      <span className="flex-1">
        <span className="block type-card-title">{title}</span>
        <span className="mt-px block type-caption">{desc}</span>
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
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [method, setMethod] = useState<Method>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [phone, setPhone] = useState('');
  const [proxyStep, setProxyStep] = useState<ProxyStep>('choice');
  const [proxyValue, setProxyValue] = useState<ProxyFormValue>(EMPTY_PROXY_FORM);
  const [proxyValid, setProxyValid] = useState(false);
  // The id of the account created in step 1, so later steps can act on it.
  const [createdAccountId, setCreatedAccountId] = useState<string | null>(null);
  // The committed method, readable from a mutate-level callback that resolves
  // after the operator has moved on: those closures are never cancelled, so an
  // import/start-login that lands late must not re-provision the wizard for a
  // method it no longer holds — "Next" would unlock while afterProxy branches on
  // the NEW method, POSTing a phone login at an already-authorised .session
  // account. `selectMethod` owns both this and the state.
  const methodRef = useRef<Method>(null);

  const importTdata = useMutation(importAccountTdataMutation());
  const importSession = useMutation(importAccountSessionMutation());
  const startLogin = useMutation(startPhoneLoginMutation());
  const createProxy = useMutation(createProxyMutation());
  const assignProxy = useMutation(assignProxyMutation());
  const pool = useQuery(proxyPoolQueryOptions());
  const freeProxies = (pool.data?.proxies ?? []).filter((proxy) => proxy.free > 0);

  const importing = importTdata.isPending || importSession.isPending;
  const importFailed = importTdata.isError || importSession.isError;

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

  const totalSteps = method === 'phone' ? 3 : 2;

  // Picking a method un-provisions the wizard, because an account created by an
  // earlier method would keep "Next" unlocked with nothing to show for it and
  // afterProxy would branch on the NEW method. Re-clicking the method ALREADY
  // selected must be a no-op: the account it provisioned really exists, and the
  // only in-wizard recovery is re-importing the same file, which the backend
  // refuses ("already exists. Delete it before importing.").
  const selectMethod = (next: Method) => {
    methodRef.current = next;
    if (method === next) return;
    setFileName(null);
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

  // After proxy is assigned/skipped: phone goes on to the code step, the file
  // methods are done and close.
  const afterProxy = () => {
    if (method === 'phone') {
      setStep(3);
    } else {
      onClose();
    }
  };

  const onFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setFileName(file.name);
    setCreatedAccountId(null);
    const forMethod = method;
    const adopt = (accountId: string | null) => {
      if (methodRef.current !== forMethod) return;
      setCreatedAccountId(accountId);
    };
    if (method === 'tdata') {
      importSession.reset();
      importTdata.mutate(
        { body: { file } },
        {
          onSuccess: (result) => {
            adopt(result.accounts?.[0]?.account_id ?? null);
          },
          onSettled: onImported,
        },
      );
    } else {
      importTdata.reset();
      importSession.mutate(
        { body: { file } },
        {
          onSuccess: (account) => {
            adopt(account.account_id);
          },
          onSettled: onImported,
        },
      );
    }
    event.target.value = '';
  };

  // Step 2: assign a pool proxy to the just-imported account, then advance.
  //
  // `afterProxy` must be the assign's OWN callback, not a synchronous call
  // beside it — the same defect this branch has now fixed three times over. Run
  // synchronously it advanced on a refusal exactly as on a success, handing the
  // operator a proxyless account with nothing on screen to say so; and for the
  // file methods `afterProxy` is `onClose()`, which unmounted the modal while
  // the assign was still in flight and DETACHED the observer, so
  // `onSettled: onImported` was dropped too and the accounts table never heard
  // about the assignment either.
  //
  // Both callbacks below do fire: the only thing that unmounts this modal in
  // this flow is `afterProxy` itself, so the observer is still attached when the
  // mutation settles — the unmount is now the callback's effect, not a race
  // against it. `onSuccess`, not `onSettled`, because a failed assign must stay
  // on this step; `onImported` stays on `onSettled` because a partial failure
  // can still have changed the account.
  const assignFromPool = (proxyId: string) => {
    if (!createdAccountId) {
      afterProxy();
      return;
    }
    assignProxy.mutate(
      { path: { proxy_id: proxyId }, body: { account_id: createdAccountId } },
      { onSuccess: afterProxy, onSettled: onImported },
    );
  };

  // Step 2 manual: create the entered proxy (idempotent), assign it, then close.
  const createAndAssign = () => {
    if (!createdAccountId) {
      onClose();
      return;
    }
    createProxy.mutate(
      {
        body: {
          proxy_type: proxyValue.proxy_type,
          host: proxyValue.host.trim(),
          port: Number(proxyValue.port),
          username: proxyValue.username.trim() || null,
          password: proxyValue.password || null,
        },
      },
      {
        onSuccess: (created) => {
          assignProxy.mutate(
            { path: { proxy_id: created.id }, body: { account_id: createdAccountId } },
            { onSettled: onImported },
          );
        },
        onSettled: afterProxy,
      },
    );
  };

  return (
    <Modal onClose={onClose} size="form" label={t('accounts.addWizard.title')}>
      <div className="px-2xl pb-xl pt-2xl">
        <div className="mb-lg flex items-start justify-between">
          <div>
            <div className="type-dialog-title">{t('accounts.addWizard.title')}</div>
            <div className="mt-hair type-prose">
              {step === 1
                ? t('accounts.addWizard.step1Label')
                : step === 2
                  ? t('accounts.addWizard.step2Label')
                  : t('accounts.addWizard.step3Label')}
            </div>
          </div>
          <IconButton
            size="md"
            onClick={onClose}
            aria-label={t('accounts.addWizard.close')}
            className="text-title"
          >
            ×
          </IconButton>
        </div>

        {/* stepper */}
        <div className="mb-xl flex items-center gap-md">
          {Array.from({ length: totalSteps }, (_, i) => i + 1).map((n) => (
            <Fragment key={n}>
              {n > 1 && (
                <span
                  className={`h-rail flex-1 rounded-full ${step >= n ? 'bg-action-primary' : 'bg-line'}`}
                />
              )}
              <span
                className={`flex size-icon items-center justify-center rounded-full text-body font-semibold ${step >= n ? 'bg-action-primary text-on-action' : 'border border-line bg-surface-card text-content-muted'}`}
              >
                {n}
              </span>
            </Fragment>
          ))}
        </div>

        {step === 1 ? (
          <>
            <div className="flex flex-col gap-md">
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
                <div className="tb-fadeup flex flex-col gap-md rounded-lg border border-line bg-surface-card px-md py-lg">
                  <label className="block type-caption font-medium">
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
                    className="rounded-lg border border-line bg-surface-card px-md py-md text-body outline-none focus:border-focus"
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
                    <div className="type-caption text-danger">
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
                    className="hidden"
                    onChange={onFile}
                  />
                  <button
                    type="button"
                    onClick={() => fileInput.current?.click()}
                    className="flex items-center gap-md rounded-lg border border-dashed border-line bg-surface-card px-lg py-lg text-left"
                  >
                    <span className="flex size-touch shrink-0 items-center justify-center rounded-lg border border-line bg-surface-card text-action-primary">
                      <Icon name="upload-cloud" size={20} />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block type-card-title">
                        {t('accounts.addWizard.dropTitle')}
                      </span>
                      <span className="mt-px block type-caption">
                        {method === 'tdata'
                          ? t('accounts.addWizard.dropDescTdata')
                          : t('accounts.addWizard.dropDescSession')}
                      </span>
                    </span>
                    <span className="shrink-0 rounded-full border border-line px-lg py-tight text-body font-medium text-content-primary">
                      {t('accounts.addWizard.browse')}
                    </span>
                  </button>
                  {fileName && (
                    <div className="tb-fadeup rounded-lg border border-line bg-surface-card px-md py-md">
                      <div className="flex items-center gap-md">
                        <div className="flex size-thumbnail shrink-0 items-center justify-center rounded-lg bg-canvas text-content-muted">
                          {method === 'tdata' ? (
                            <Icon name="alert-square" size={18} />
                          ) : (
                            <Icon name="file" size={18} />
                          )}
                        </div>
                        <div className="min-w-0 flex-1">
                          <div className="truncate type-item-title">{fileName}</div>
                          {/* Import verdict tone from the tokens the states MEAN. */}
                          <div
                            className={`mt-px text-tiny ${importFailed ? 'text-danger' : createdAccountId ? 'text-success-deep' : 'text-content-subtle'}`}
                          >
                            {importFailed
                              ? t('accounts.addWizard.importError')
                              : importing
                                ? t('accounts.addWizard.importing')
                                : createdAccountId
                                  ? t('accounts.addWizard.imported')
                                  : t('accounts.addWizard.fileReady')}
                          </div>
                        </div>
                        {importing ? (
                          <Spinner className="m-tight" />
                        ) : importFailed ? (
                          <span className="m-xs inline-flex text-danger">
                            <Icon name="x-circle" size={18} />
                          </span>
                        ) : createdAccountId ? (
                          <span className="tb-pop m-xs inline-flex text-success-deep">
                            <Icon name="check-circle" size={18} />
                          </span>
                        ) : null}
                      </div>
                    </div>
                  )}
                </>
              )}
            </div>
            <div className="mt-xl flex justify-end gap-sm">
              <Button onClick={onClose}>{t('accounts.addWizard.cancel')}</Button>
              <Button
                variant="primary"
                disabled={!createdAccountId}
                onClick={() => {
                  setStep(2);
                  setProxyStep('choice');
                }}
              >
                {t('accounts.addWizard.next')}
              </Button>
            </div>
          </>
        ) : step === 3 ? (
          <CodeLoginStep
            accountId={createdAccountId}
            phone={phone}
            onDone={() => {
              onImported();
              onClose();
            }}
          />
        ) : proxyStep === 'choice' ? (
          <>
            <div className="mb-lg flex items-center gap-sm rounded-lg bg-success-tint px-md py-md">
              <Icon name="check" size={16} className="stroke-success" />
              <span className="type-label text-success-deep">{t('accounts.addWizard.added')}</span>
            </div>
            <div className="flex flex-col gap-md">
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
            <div className="mt-xl flex justify-between gap-sm">
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
            <div className="mt-xl flex justify-between gap-sm">
              <Button
                onClick={() => {
                  setProxyStep('choice');
                }}
              >
                {t('accounts.addWizard.back')}
              </Button>
              <Button variant="primary" onClick={createAndAssign} disabled={!proxyValid}>
                {t('accounts.addWizard.done')}
              </Button>
            </div>
          </>
        ) : (
          <>
            <div className="flex flex-col gap-sm">
              {freeProxies.length === 0 ? (
                <div className="rounded-lg border border-dashed border-line bg-surface-card px-lg py-2xl text-center text-body text-content-subtle">
                  {t('accounts.addWizard.poolEmpty')}
                </div>
              ) : (
                freeProxies.map((proxy) => (
                  <button
                    key={proxy.id}
                    type="button"
                    // The step no longer closes on click, so without this a
                    // second press would fire a second assign on the SAME
                    // observer — whose callback slot the first one then loses.
                    disabled={assignProxy.isPending}
                    onClick={() => {
                      assignFromPool(proxy.id);
                    }}
                    className="flex items-center gap-md rounded-lg border border-line bg-surface-card px-lg py-md text-left transition-colors hover:border-info-line disabled:opacity-60"
                  >
                    {proxy.country_code ? (
                      <span
                        className={`fi fi-${proxy.country_code.toLowerCase()} block h-flag w-flag shrink-0 rounded-[3px] shadow-ring`}
                      />
                    ) : null}
                    <span className="flex-1">
                      <span className="block type-card-title">
                        {(proxy.country_code ?? '—').toUpperCase()} ·{' '}
                        {proxyTypeLabel(proxy.proxy_type)}
                      </span>
                      <span className="block font-mono type-caption">
                        {proxy.host}:{proxy.port}
                      </span>
                    </span>
                    <span className="type-label text-success-deep">
                      {t('accounts.addWizard.poolFree', { count: proxy.free })}
                    </span>
                  </button>
                ))
              )}
              {/* The wizard stays on this step when the assign is refused, so the
                  refusal has to be visible — otherwise the only signal is a
                  screen that did not change. */}
              {assignProxy.isError && (
                <div role="alert" className="type-caption text-danger">
                  {t('accounts.addWizard.proxyAssignError')}
                </div>
              )}
            </div>
            <div className="mt-xl flex justify-between gap-sm">
              <Button
                onClick={() => {
                  setProxyStep('choice');
                }}
              >
                {t('accounts.addWizard.back')}
              </Button>
              <Button variant="primary" onClick={afterProxy}>
                {t('accounts.addWizard.done')}
              </Button>
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}
