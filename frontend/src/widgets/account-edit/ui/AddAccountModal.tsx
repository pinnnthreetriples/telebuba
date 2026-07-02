import { useMutation, useQuery } from '@tanstack/react-query';
import { useRef, useState, type ChangeEvent } from 'react';
import { useTranslation } from 'react-i18next';

import { importAccountSessionMutation, importAccountTdataMutation } from '@/entities/account';
import {
  assignProxyMutation,
  createProxyMutation,
  proxyPoolQueryOptions,
  proxyTypeLabel,
} from '@/entities/proxy';
import { Modal } from '@/shared/ui';

import { ProxyForm } from './ProxyForm';
import { EMPTY_PROXY_FORM, type ProxyFormValue } from './proxyFormValue';

// The design's add-account wizard: a two-step stepper. STEP 1 imports an account
// (.session or tdata.zip) via the real import endpoints; STEP 2 assigns a proxy
// to the just-imported account — pick from the pool, or enter one manually
// (create + assign). The created account's id threads from step 1 into step 2.
type Method = 'session' | 'tdata' | null;
type ProxyStep = 'choice' | 'form' | 'pool';

export function AddAccountModal({
  onClose,
  onImported,
}: {
  onClose: () => void;
  onImported: () => void;
}) {
  const { t } = useTranslation();
  const fileInput = useRef<HTMLInputElement>(null);
  const [step, setStep] = useState<1 | 2>(1);
  const [method, setMethod] = useState<Method>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [proxyStep, setProxyStep] = useState<ProxyStep>('choice');
  const [proxyValue, setProxyValue] = useState<ProxyFormValue>(EMPTY_PROXY_FORM);
  const [proxyValid, setProxyValid] = useState(false);
  // The id of the account imported in step 1, so step 2 can assign a proxy to it.
  const [createdAccountId, setCreatedAccountId] = useState<string | null>(null);

  const importTdata = useMutation(importAccountTdataMutation());
  const importSession = useMutation(importAccountSessionMutation());
  const createProxy = useMutation(createProxyMutation());
  const assignProxy = useMutation(assignProxyMutation());
  const pool = useQuery(proxyPoolQueryOptions());
  const freeProxies = (pool.data?.proxies ?? []).filter((proxy) => proxy.free > 0);

  const importing = importTdata.isPending || importSession.isPending;
  const importFailed = importTdata.isError || importSession.isError;

  const onFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setFileName(file.name);
    setCreatedAccountId(null);
    if (method === 'tdata') {
      importSession.reset();
      importTdata.mutate(
        { body: { file } },
        {
          onSuccess: (result) => {
            setCreatedAccountId(result.accounts?.[0]?.account_id ?? null);
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
            setCreatedAccountId(account.account_id);
          },
          onSettled: onImported,
        },
      );
    }
    event.target.value = '';
  };

  // Step 2: assign a pool proxy to the just-imported account, then close.
  const assignFromPool = (proxyId: string) => {
    if (createdAccountId) {
      assignProxy.mutate(
        { path: { proxy_id: proxyId }, body: { account_id: createdAccountId } },
        { onSettled: onImported },
      );
    }
    onClose();
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
        onSettled: onClose,
      },
    );
  };

  const choiceCard =
    'flex cursor-pointer items-center gap-[11px] rounded-[12px] border border-line-input bg-white px-[14px] py-[13px] text-left transition-colors hover:border-[#bfd6ff]';

  return (
    <Modal onClose={onClose} z={70} className="w-[480px]">
      <div className="px-6 pb-5 pt-[22px]">
        <div className="mb-4 flex items-start justify-between">
          <div>
            <div className="text-[16px] font-bold">{t('accounts.addWizard.title')}</div>
            <div className="mt-[2px] text-[12px] text-ink-subtle">
              {step === 1 ? t('accounts.addWizard.step1Label') : t('accounts.addWizard.step2Label')}
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
          <span className="flex h-7 w-7 items-center justify-center rounded-full bg-primary text-[12px] font-semibold text-white">
            1
          </span>
          <span
            className="h-[2px] flex-1 rounded-full"
            style={{ background: step === 2 ? '#0066ff' : '#e6e5e3' }}
          />
          <span
            className={`flex h-7 w-7 items-center justify-center rounded-full text-[12px] font-semibold ${step === 2 ? 'bg-primary text-white' : 'border border-line bg-white text-ink-muted'}`}
          >
            2
          </span>
        </div>

        {step === 1 ? (
          <>
            <div className="flex flex-col gap-[10px]">
              <button
                type="button"
                onClick={() => {
                  setMethod('session');
                  setFileName(null);
                }}
                className={`${choiceCard} ${method === 'session' ? 'border-primary bg-primary-tint' : ''}`}
              >
                <span className="flex h-[38px] w-[38px] shrink-0 items-center justify-center rounded-[10px] bg-[#e8f0ff]">
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
                </span>
                <span className="flex-1">
                  <span className="block text-[13.5px] font-semibold">
                    {t('accounts.addWizard.sessionTitle')}
                  </span>
                  <span className="mt-px block text-[11.5px] text-ink-subtle">
                    {t('accounts.addWizard.sessionDesc')}
                  </span>
                </span>
              </button>
              <button
                type="button"
                onClick={() => {
                  setMethod('tdata');
                  setFileName(null);
                }}
                className={`${choiceCard} ${method === 'tdata' ? 'border-primary bg-primary-tint' : ''}`}
              >
                <span className="flex h-[38px] w-[38px] shrink-0 items-center justify-center rounded-[10px] bg-[#e8f0ff]">
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
                </span>
                <span className="flex-1">
                  <span className="block text-[13.5px] font-semibold">
                    {t('accounts.addWizard.tdataTitle')}
                  </span>
                  <span className="mt-px block text-[11.5px] text-ink-subtle">
                    {t('accounts.addWizard.tdataDesc')}
                  </span>
                </span>
              </button>

              {method && (
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
              <button
                type="button"
                onClick={() => {
                  setProxyStep('form');
                }}
                className={choiceCard}
              >
                <span className="flex h-[38px] w-[38px] shrink-0 items-center justify-center rounded-[10px] bg-[#e8f0ff]">
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
                </span>
                <span className="flex-1">
                  <span className="block text-[13.5px] font-semibold">
                    {t('accounts.addWizard.proxyManual')}
                  </span>
                  <span className="mt-px block text-[11.5px] text-ink-subtle">
                    {t('accounts.addWizard.proxyManualDesc')}
                  </span>
                </span>
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
              </button>
              <button
                type="button"
                onClick={() => {
                  setProxyStep('pool');
                }}
                className={choiceCard}
              >
                <span className="flex h-[38px] w-[38px] shrink-0 items-center justify-center rounded-[10px] bg-[#e8f0ff]">
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
                </span>
                <span className="flex-1">
                  <span className="block text-[13.5px] font-semibold">
                    {t('accounts.addWizard.proxyPool')}
                  </span>
                  <span className="mt-px block text-[11.5px] text-ink-subtle">
                    {t('accounts.addWizard.proxyPoolDesc')}
                  </span>
                </span>
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
              </button>
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
                onClick={onClose}
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
                    onClick={() => {
                      assignFromPool(proxy.id);
                    }}
                    className="flex items-center gap-[11px] rounded-[12px] border border-line-input bg-white px-[14px] py-3 text-left transition-colors hover:border-[#bfd6ff]"
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
                onClick={onClose}
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
