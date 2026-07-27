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
import { Modal } from '@/shared/ui';

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
      className={`flex cursor-pointer items-center gap-[11px] rounded-[12px] border bg-white px-[14px] py-[13px] text-left transition-colors hover:border-[#bfd6ff] ${selected ? 'border-primary bg-primary-tint' : 'border-line-input'}`}
    >
      <span className="flex h-[38px] w-[38px] shrink-0 items-center justify-center rounded-[10px] bg-[#e8f0ff]">
        {icon}
      </span>
      <span className="flex-1">
        <span className="block text-[13.5px] font-semibold">{title}</span>
        <span className="mt-px block text-[11.5px] text-ink-subtle">{desc}</span>
      </span>
      {chevron && (
        <svg
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          stroke="#c8c6c2"
          strokeWidth="2"
        >
          <path d="m9 18 6-6-6-6" />
        </svg>
      )}
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
    <Modal onClose={onClose} z={70} className="w-[480px]">
      <div className="px-6 pb-5 pt-[22px]">
        <div className="mb-4 flex items-start justify-between">
          <div>
            <div className="text-[16px] font-bold">{t('accounts.addWizard.title')}</div>
            <div className="mt-[2px] text-[12px] text-ink-subtle">
              {step === 1
                ? t('accounts.addWizard.step1Label')
                : step === 2
                  ? t('accounts.addWizard.step2Label')
                  : t('accounts.addWizard.step3Label')}
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label={t('accounts.addWizard.close')}
            className="h-[30px] w-[30px] rounded-full border border-line bg-white text-[16px] text-ink-muted"
          >
            ×
          </button>
        </div>

        {/* stepper */}
        <div className="mb-5 flex items-center gap-[10px]">
          {Array.from({ length: totalSteps }, (_, i) => i + 1).map((n) => (
            <Fragment key={n}>
              {n > 1 && (
                <span
                  className="h-[2px] flex-1 rounded-full"
                  style={{ background: step >= n ? '#0066ff' : '#e6e5e3' }}
                />
              )}
              <span
                className={`flex h-7 w-7 items-center justify-center rounded-full text-[12px] font-semibold ${step >= n ? 'bg-primary text-white' : 'border border-line bg-white text-ink-muted'}`}
              >
                {n}
              </span>
            </Fragment>
          ))}
        </div>

        {step === 1 ? (
          <>
            <div className="flex flex-col gap-[10px]">
              <ChoiceCard
                icon={
                  <svg
                    width="18"
                    height="18"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="#0066ff"
                    strokeWidth="1.8"
                  >
                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                    <path d="M14 2v6h6" />
                  </svg>
                }
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
                    stroke="#0066ff"
                    strokeWidth="1.8"
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
                    stroke="#0066ff"
                    strokeWidth="1.8"
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
                <div className="tb-fadeup flex flex-col gap-[10px] rounded-[12px] border border-line bg-white px-3 py-[13px]">
                  <label className="block text-[11.5px] font-medium text-ink-subtle">
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
                    className="rounded-[10px] border border-line-input bg-white px-3 py-[9px] text-[13px] outline-none focus:border-primary"
                  />
                  <button
                    type="button"
                    onClick={onStartPhone}
                    disabled={!phone.trim() || startLogin.isPending || Boolean(createdAccountId)}
                    className="self-start rounded-full bg-primary px-4 py-[8px] text-[12.5px] font-medium text-white disabled:opacity-50"
                  >
                    {startLogin.isPending
                      ? t('accounts.addWizard.phoneCreating')
                      : createdAccountId
                        ? t('accounts.addWizard.phoneCreated')
                        : t('accounts.addWizard.phoneContinue')}
                  </button>
                  {startLogin.isError && (
                    <div className="text-[11.5px] text-[#c0473f]">
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
                    className="flex items-center gap-[11px] rounded-[12px] border border-dashed border-line bg-white px-4 py-[14px] text-left"
                  >
                    <span className="flex h-[46px] w-[46px] shrink-0 items-center justify-center rounded-[13px] border border-line bg-white text-primary">
                      <svg
                        width="22"
                        height="22"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="1.7"
                      >
                        <path d="M16 16l-4-4-4 4M12 12v9" />
                        <path d="M20.39 18.39A5 5 0 0 0 18 9h-1.26A8 8 0 1 0 3 16.3" />
                      </svg>
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block text-[13px] font-semibold">
                        {t('accounts.addWizard.dropTitle')}
                      </span>
                      <span className="mt-px block text-[11.5px] text-ink-subtle">
                        {method === 'tdata'
                          ? t('accounts.addWizard.dropDescTdata')
                          : t('accounts.addWizard.dropDescSession')}
                      </span>
                    </span>
                    <span className="shrink-0 rounded-full border border-line-input px-[13px] py-[6px] text-[12px] font-medium text-ink">
                      {t('accounts.addWizard.browse')}
                    </span>
                  </button>
                  {fileName && (
                    <div className="tb-fadeup rounded-[12px] border border-line bg-white px-3 py-[11px]">
                      <div className="flex items-center gap-[11px]">
                        <div className="flex h-[38px] w-[38px] shrink-0 items-center justify-center rounded-[10px] bg-[#f4f3f0] text-ink-muted">
                          {method === 'tdata' ? (
                            <svg
                              width="17"
                              height="17"
                              viewBox="0 0 24 24"
                              fill="none"
                              stroke="currentColor"
                              strokeWidth="1.7"
                            >
                              <rect x="3" y="3" width="18" height="18" rx="2" />
                              <path d="M12 7v2M12 12v2M12 17v.5" />
                            </svg>
                          ) : (
                            <svg
                              width="17"
                              height="17"
                              viewBox="0 0 24 24"
                              fill="none"
                              stroke="currentColor"
                              strokeWidth="1.7"
                            >
                              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                              <path d="M14 2v6h6" />
                            </svg>
                          )}
                        </div>
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-[12.5px] font-semibold">{fileName}</div>
                          <div
                            className="mt-px text-[11px]"
                            style={{
                              color: importFailed
                                ? '#c0473f'
                                : createdAccountId
                                  ? '#2e9e64'
                                  : '#9a9893',
                            }}
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
                          <span className="tb-spin m-[5px] inline-block h-[14px] w-[14px] rounded-full border-2 border-line-input border-t-primary" />
                        ) : importFailed ? (
                          <span className="m-[3px] inline-flex text-[#c0473f]">
                            <svg
                              width="18"
                              height="18"
                              viewBox="0 0 24 24"
                              fill="none"
                              stroke="currentColor"
                              strokeWidth="2"
                            >
                              <circle cx="12" cy="12" r="10" />
                              <path d="m15 9-6 6M9 9l6 6" />
                            </svg>
                          </span>
                        ) : createdAccountId ? (
                          <span className="tb-pop m-[3px] inline-flex text-[#2e9e64]">
                            <svg
                              width="18"
                              height="18"
                              viewBox="0 0 24 24"
                              fill="none"
                              stroke="currentColor"
                              strokeWidth="2"
                            >
                              <circle cx="12" cy="12" r="10" />
                              <path d="m8 12 2.5 2.5L16 9" />
                            </svg>
                          </span>
                        ) : null}
                      </div>
                    </div>
                  )}
                </>
              )}
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                onClick={onClose}
                className="rounded-full border border-line-input bg-white px-[18px] py-[9px] text-[13px] font-medium text-ink"
              >
                {t('accounts.addWizard.cancel')}
              </button>
              <button
                type="button"
                disabled={!createdAccountId}
                onClick={() => {
                  setStep(2);
                  setProxyStep('choice');
                }}
                className="rounded-full bg-primary px-5 py-[9px] text-[13px] font-medium text-white disabled:opacity-50"
              >
                {t('accounts.addWizard.next')}
              </button>
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
            <div className="mb-[14px] flex items-center gap-2 rounded-[10px] bg-[#e7f2ec] px-3 py-[10px]">
              <svg
                width="15"
                height="15"
                viewBox="0 0 24 24"
                fill="none"
                stroke="#2e7d55"
                strokeWidth="2.2"
              >
                <path d="M20 6 9 17l-5-5" />
              </svg>
              <span className="text-[12.5px] font-medium text-[#2e7d55]">
                {t('accounts.addWizard.added')}
              </span>
            </div>
            <div className="flex flex-col gap-[10px]">
              <ChoiceCard
                icon={
                  <svg
                    width="18"
                    height="18"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="#0066ff"
                    strokeWidth="1.8"
                  >
                    <path d="M12 5v14M5 12h14" />
                  </svg>
                }
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
                    stroke="#0066ff"
                    strokeWidth="1.8"
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
            <div className="mt-5 flex justify-between gap-2">
              <button
                type="button"
                onClick={() => {
                  setStep(1);
                }}
                className="rounded-full border border-line-input bg-white px-[18px] py-[9px] text-[13px] font-medium text-ink"
              >
                {t('accounts.addWizard.back')}
              </button>
              <button
                type="button"
                onClick={afterProxy}
                className="rounded-full border border-line-input bg-white px-[18px] py-[9px] text-[13px] font-medium text-ink-muted"
              >
                {t('accounts.addWizard.skip')}
              </button>
            </div>
          </>
        ) : proxyStep === 'form' ? (
          <>
            <ProxyForm
              value={proxyValue}
              onChange={setProxyValue}
              onValidityChange={setProxyValid}
            />
            <div className="mt-5 flex justify-between gap-2">
              <button
                type="button"
                onClick={() => {
                  setProxyStep('choice');
                }}
                className="rounded-full border border-line-input bg-white px-[18px] py-[9px] text-[13px] font-medium text-ink"
              >
                {t('accounts.addWizard.back')}
              </button>
              <button
                type="button"
                onClick={createAndAssign}
                disabled={!proxyValid}
                className="rounded-full bg-primary px-5 py-[9px] text-[13px] font-medium text-white disabled:opacity-50"
              >
                {t('accounts.addWizard.done')}
              </button>
            </div>
          </>
        ) : (
          <>
            <div className="flex flex-col gap-2">
              {freeProxies.length === 0 ? (
                <div className="rounded-[12px] border border-dashed border-line bg-white px-4 py-6 text-center text-[12.5px] text-ink-subtle">
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
                    className="flex items-center gap-[11px] rounded-[12px] border border-line-input bg-white px-[14px] py-3 text-left transition-colors hover:border-[#bfd6ff] disabled:opacity-60"
                  >
                    {proxy.country_code ? (
                      <span
                        className={`fi fi-${proxy.country_code.toLowerCase()} block h-[17px] w-6 shrink-0 rounded-[3px] shadow-[0_0_0_1px_rgba(0,0,0,0.07)]`}
                      />
                    ) : null}
                    <span className="flex-1">
                      <span className="block text-[13px] font-semibold">
                        {(proxy.country_code ?? '—').toUpperCase()} ·{' '}
                        {proxyTypeLabel(proxy.proxy_type)}
                      </span>
                      <span className="block font-mono text-[11.5px] text-ink-subtle">
                        {proxy.host}:{proxy.port}
                      </span>
                    </span>
                    <span className="text-[12px] font-medium text-[#2e7d55]">
                      {t('accounts.addWizard.poolFree', { count: proxy.free })}
                    </span>
                  </button>
                ))
              )}
              {/* The wizard stays on this step when the assign is refused, so the
                  refusal has to be visible — otherwise the only signal is a
                  screen that did not change. */}
              {assignProxy.isError && (
                <div role="alert" className="text-[11.5px] text-[#c0473f]">
                  {t('accounts.addWizard.proxyAssignError')}
                </div>
              )}
            </div>
            <div className="mt-5 flex justify-between gap-2">
              <button
                type="button"
                onClick={() => {
                  setProxyStep('choice');
                }}
                className="rounded-full border border-line-input bg-white px-[18px] py-[9px] text-[13px] font-medium text-ink"
              >
                {t('accounts.addWizard.back')}
              </button>
              <button
                type="button"
                onClick={afterProxy}
                className="rounded-full bg-primary px-5 py-[9px] text-[13px] font-medium text-white"
              >
                {t('accounts.addWizard.done')}
              </button>
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}
