import { useMutation, useQuery } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  accountDisplayName,
  AccountAvatar,
  allAccountsQueryOptions,
  cancelBulkMessageJobMutation,
  generateBulkMessageMutation,
  getBulkMessageJobQueryOptions,
  sendBulkMessagesMutation,
} from '@/entities/account';
import {
  Button,
  CloseButton,
  HelpHint,
  Icon,
  IconButton,
  Input,
  Modal,
  SegmentedControl,
  Textarea,
} from '@/shared/ui';

import { BulkAccountPicker } from './BulkAccountPicker';
import { ContactLookupPanel } from './ContactLookupPanel';

const MAX_ACCOUNTS = 50;
const MAX_EACH_RECIPIENTS = 50;
const MAX_SENDS = 500;
const MAX_DELAY_SECONDS = 300;

type SendMode = 'each' | 'split';

export type BulkMessageDraft = {
  ids: string[];
  mode: SendMode;
  recipients: string;
  pins: Record<string, string[]>;
  message: string;
  minDelay: string;
  maxDelay: string;
  generatorOpen: boolean;
  prompt: string;
  provider: 'deepseek' | 'gemini' | null;
};

function recipientsFrom(value: string): string[] {
  return [
    ...new Set(
      value
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter(Boolean),
    ),
  ];
}

function errorMessage(error: unknown): string | null {
  if (typeof error !== 'object' || error === null || !('error' in error)) return null;
  const detail = error.error;
  if (typeof detail !== 'object' || detail === null || !('message' in detail)) return null;
  return typeof detail.message === 'string' ? detail.message : null;
}

function errorCode(error: unknown): string | null {
  if (typeof error !== 'object' || error === null || !('error' in error)) return null;
  const detail = error.error;
  if (typeof detail !== 'object' || detail === null || !('code' in detail)) return null;
  return typeof detail.code === 'string' ? detail.code : null;
}

export function BulkMessageModal({
  jobId,
  initialDraft,
  onJobStarted,
  onNewJob,
  onDraftSaved,
  onClose,
}: {
  jobId: string | null;
  initialDraft?: BulkMessageDraft | null;
  onJobStarted: (jobId: string) => void;
  onNewJob: () => void;
  onDraftSaved?: (draft: BulkMessageDraft) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [ids, setIds] = useState<string[]>(initialDraft?.ids ?? []);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [mode, setMode] = useState<SendMode>(initialDraft?.mode ?? 'each');
  const [recipients, setRecipients] = useState(initialDraft?.recipients ?? '');
  // A raw user_id found by phone lookup is addressable only by the accounts that found it.
  const [pins, setPins] = useState<Record<string, string[]>>(initialDraft?.pins ?? {});
  const [message, setMessage] = useState(initialDraft?.message ?? '');
  const [minDelay, setMinDelay] = useState(initialDraft?.minDelay ?? '0');
  const [maxDelay, setMaxDelay] = useState(initialDraft?.maxDelay ?? '0');
  const [generatorOpen, setGeneratorOpen] = useState(initialDraft?.generatorOpen ?? false);
  const [prompt, setPrompt] = useState(initialDraft?.prompt ?? '');
  const [provider, setProvider] = useState<'deepseek' | 'gemini' | null>(
    initialDraft?.provider ?? null,
  );
  const [stopRequested, setStopRequested] = useState(false);
  const generationRevision = useRef(0);

  const fleet = useQuery(allAccountsQueryOptions());
  const send = useMutation(sendBulkMessagesMutation());
  const generate = useMutation(generateBulkMessageMutation());
  const cancel = useMutation(cancelBulkMessageJobMutation());
  const job = useQuery({
    ...getBulkMessageJobQueryOptions({ path: { job_id: jobId ?? '' } }),
    enabled: jobId !== null,
    retry: false,
    refetchInterval: (query) =>
      query.state.data?.status === 'completed' ||
      query.state.data?.status === 'cancelled' ||
      errorCode(query.state.error) === 'not_found'
        ? false
        : 1500,
  });

  const byId = new Map((fleet.data?.items ?? []).map((row) => [row.account_id, row]));
  const recipientList = recipientsFrom(recipients);
  const maxRecipients = mode === 'each' ? MAX_EACH_RECIPIENTS : MAX_SENDS;
  const sendCount = mode === 'each' ? ids.length * recipientList.length : recipientList.length;
  const minSeconds = Number(minDelay);
  const maxSeconds = Number(maxDelay);
  const delayReady =
    /^\d+$/.test(minDelay) &&
    /^\d+$/.test(maxDelay) &&
    minSeconds <= maxSeconds &&
    maxSeconds <= MAX_DELAY_SECONDS;
  const batchReady =
    ids.length > 0 &&
    ids.length <= MAX_ACCOUNTS &&
    recipientList.length > 0 &&
    recipientList.length <= maxRecipients &&
    sendCount <= MAX_SENDS &&
    message.trim() !== '' &&
    message.length <= 4096 &&
    delayReady;
  const started = jobId !== null;
  const complete = job.data?.status === 'completed' || job.data?.status === 'cancelled';
  const stale = job.isError && errorCode(job.error) === 'not_found';
  const results = job.data?.results ?? [];
  const attention = results.filter((result) => result.status !== 'ok' && !result.handed_over);
  const handedOver = results.filter((result) => result.handed_over);

  const onSend = () => {
    if (!batchReady) return;
    void send
      .mutateAsync({
        body: {
          account_ids: ids,
          recipients: recipientList,
          text: message.trim(),
          min_delay_seconds: minSeconds,
          max_delay_seconds: maxSeconds,
          mode,
          ...(mode === 'split' && {
            recipient_accounts: Object.fromEntries(
              Object.entries(pins).flatMap(([recipient, finders]) => {
                const accountId = finders.find((id) => ids.includes(id));
                return recipientList.includes(recipient) && accountId
                  ? [[recipient, accountId]]
                  : [];
              }),
            ),
          }),
        },
      })
      .then((result) => {
        onJobStarted(result.job_id);
      })
      .catch(() => undefined);
  };

  const appendRecipients = (lines: string[], found: Record<string, string>) => {
    setRecipients((prev) => [...new Set([...recipientsFrom(prev), ...lines])].join('\n'));
    setPins((prev) => {
      const next = { ...prev };
      for (const [recipient, accountId] of Object.entries(found)) {
        const finders = next[recipient] ?? [];
        if (!finders.includes(accountId)) next[recipient] = [...finders, accountId];
      }
      return next;
    });
  };

  const onGenerate = () => {
    if (prompt.trim() === '') return;
    const revision = ++generationRevision.current;
    void generate
      .mutateAsync({ body: { prompt: prompt.trim() } })
      .then((result) => {
        if (generationRevision.current !== revision) return;
        setMessage(result.text);
        setProvider(result.provider);
        setGeneratorOpen(false);
      })
      .catch(() => undefined);
  };

  const onStop = () => {
    if (jobId === null) return;
    void cancel
      .mutateAsync({ path: { job_id: jobId } })
      .then(() => {
        setStopRequested(true);
        void job.refetch();
      })
      .catch(() => undefined);
  };

  const sendError = errorMessage(send.error);
  const sendErrorKey =
    sendError === 'bulk_message_run_active'
      ? 'accounts.messages.sendActive'
      : sendError === 'duplicate recipients'
        ? 'accounts.messages.sendDuplicate'
        : sendError === 'invalid recipient'
          ? 'accounts.messages.sendInvalidRecipient'
          : 'accounts.messages.sendError';
  const generateErrorKey =
    errorMessage(generate.error) === 'generator_unavailable'
      ? 'accounts.messages.generatorUnavailable'
      : 'accounts.messages.generateError';
  const failureText = (code: string | null | undefined) => {
    switch (code) {
      case 'account_not_found':
      case 'session_dead':
      case 'account_deactivated':
      case 'account_frozen':
      case 'flood_wait':
      case 'slow_mode_wait':
      case 'premium_wait':
      case 'peer_flood':
      case 'unavailable':
        return t(`accounts.messages.code.${code}`);
      default:
        return t('accounts.messages.code.failed');
    }
  };

  const close = () => {
    if (!started) {
      onDraftSaved?.({
        ids,
        mode,
        recipients,
        pins,
        message,
        minDelay,
        maxDelay,
        generatorOpen,
        prompt,
        provider,
      });
    }
    onClose();
  };

  return (
    <>
      <Modal onClose={close} size="panel" label={t('accounts.messages.title')}>
        <div className="flex max-h-dialog flex-col overflow-hidden">
          <div className="flex items-center gap-4 border-b border-canvas px-6 py-4">
            <div className="flex size-face shrink-0 items-center justify-center rounded-full bg-info-tint text-info-strong">
              <Icon name="users" size={20} />
            </div>
            <div className="min-w-0 flex-1">
              <h2 className="truncate type-h2">{t('accounts.messages.title')}</h2>
            </div>
            <CloseButton onClick={close} aria-label={t('accounts.profile.close')} />
          </div>

          <div className="tb-scroll flex-1 space-y-4 overflow-y-auto p-6">
            {started ? (
              stale ? (
                <div className="space-y-2">
                  <h3 className="type-body-medium">{t('accounts.messages.staleTitle')}</h3>
                  <p className="type-body text-content-subtle">
                    {t('accounts.messages.staleHint')}
                  </p>
                </div>
              ) : (
                <div className="space-y-4">
                  <div>
                    <h3 className="type-body-medium">
                      {t(
                        job.data?.status === 'cancelled'
                          ? 'accounts.messages.cancelled'
                          : complete
                            ? 'accounts.messages.completed'
                            : 'accounts.messages.running',
                      )}
                    </h3>
                    <p role="status" className="mt-2 type-body text-content-subtle tabular-nums">
                      {t('accounts.messages.progress', {
                        done: job.data?.completed ?? 0,
                        total: job.data?.total ?? sendCount,
                      })}
                    </p>
                  </div>
                  {job.isError && (
                    <p role="alert" className="text-danger">
                      {t('accounts.messages.progressError')}
                    </p>
                  )}
                  {stopRequested && !complete && (
                    <p className="type-small">{t('accounts.messages.stopping')}</p>
                  )}
                  {cancel.isError && (
                    <p role="alert" className="text-danger">
                      {t('accounts.messages.stopError')}
                    </p>
                  )}
                  {attention.length > 0 && (
                    <div>
                      <h3 className="type-body-medium text-content-secondary">
                        {t('accounts.messages.attention', { count: attention.length })}
                      </h3>
                      <ul className="mt-2 space-y-2">
                        {attention.map((result) => (
                          <li
                            key={`${result.account_id}:${result.recipient}`}
                            className="rounded-sm bg-danger-tint px-3 py-2 type-small"
                          >
                            {accountDisplayName(
                              byId.get(result.account_id) ?? {
                                account_id: result.account_id,
                              },
                            )}{' '}
                            → {result.recipient}:{' '}
                            {result.status === 'unconfirmed'
                              ? t('accounts.messages.unconfirmed')
                              : result.status === 'skipped'
                                ? t('accounts.messages.skipped', {
                                    reason: failureText(result.error_code),
                                  })
                                : failureText(result.error_code)}
                            {result.retry_after_seconds != null &&
                              result.retry_after_seconds > 0 && (
                                <span>
                                  {' '}
                                  {t('accounts.messages.retryAfter', {
                                    seconds: result.retry_after_seconds,
                                  })}
                                </span>
                              )}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {handedOver.length > 0 && (
                    <div>
                      <h3 className="type-body-medium text-content-secondary">
                        {t('accounts.messages.handedOverTitle', { count: handedOver.length })}
                      </h3>
                      <ul className="mt-2 space-y-2">
                        {handedOver.map((result) => (
                          <li
                            key={`${result.account_id}:${result.recipient}`}
                            className="rounded-sm bg-canvas px-3 py-2 type-small"
                          >
                            {t('accounts.messages.handedOver', {
                              account: accountDisplayName(
                                byId.get(result.account_id) ?? { account_id: result.account_id },
                              ),
                              recipient: result.recipient,
                              reason: failureText(result.error_code),
                            })}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )
            ) : (
              <>
                <section className="space-y-2">
                  <div className="flex items-center justify-between gap-3">
                    <h3 className="type-body-medium text-content-secondary">
                      {t('accounts.messages.accounts')}
                    </h3>
                    <span
                      className={`type-small tabular-nums ${ids.length > MAX_ACCOUNTS ? 'text-danger' : ''}`}
                    >
                      {ids.length}/{MAX_ACCOUNTS}
                    </span>
                  </div>
                  <div className="flex min-h-control items-center gap-2 rounded-md border border-line bg-canvas px-2 py-1">
                    <IconButton
                      size="sm"
                      aria-label={t('accounts.bulk.add')}
                      onClick={() => {
                        setPickerOpen(true);
                      }}
                    >
                      <Icon name="plus" size={16} />
                    </IconButton>
                    {ids.length === 0 ? (
                      <span className="type-small">{t('accounts.messages.pickAccounts')}</span>
                    ) : (
                      <div className="tb-scroll flex items-center gap-2 overflow-x-auto py-1">
                        {ids.map((id) => {
                          const account = byId.get(id);
                          return (
                            <span key={id} className="group relative shrink-0">
                              <AccountAvatar
                                account={account ?? { account_id: id }}
                                className="size-tile rounded-full"
                                fallbackClassName="bg-surface-card text-content-muted type-body-medium"
                              />
                              <IconButton
                                size="sm"
                                shape="circle"
                                aria-label={t('accounts.bulk.remove', {
                                  name: account ? accountDisplayName(account) : id,
                                })}
                                onClick={() => {
                                  setIds((prev) => prev.filter((value) => value !== id));
                                }}
                                className="absolute -right-1 -top-1 bg-surface-card sm:opacity-0 sm:transition-opacity sm:focus-visible:opacity-100 sm:group-hover:opacity-100"
                              >
                                <Icon name="close" size={16} />
                              </IconButton>
                            </span>
                          );
                        })}
                      </div>
                    )}
                  </div>
                </section>

                <section className="space-y-2">
                  <div className="flex items-center gap-2">
                    <h3 className="type-body-medium text-content-secondary">
                      {t('accounts.messages.mode')}
                    </h3>
                    <HelpHint text={t('accounts.messages.modeHint')} />
                  </div>
                  <SegmentedControl
                    variant="tray"
                    ariaLabel={t('accounts.messages.mode')}
                    value={mode}
                    onChange={setMode}
                    options={[
                      { value: 'each', label: t('accounts.messages.modeEach') },
                      {
                        value: 'split',
                        title: t('accounts.messages.modeSplitHint'),
                        label: t('accounts.messages.modeSplit'),
                      },
                    ]}
                  />
                </section>

                <section className="space-y-2">
                  <div className="flex items-center justify-between gap-3">
                    <div className="flex items-center gap-2">
                      <label
                        htmlFor="bulk-message-recipients"
                        className="type-body-medium text-content-secondary"
                      >
                        {t('accounts.messages.recipients')}
                      </label>
                      <HelpHint text={t('accounts.messages.recipientsHint')} />
                    </div>
                    <span
                      className={`type-small tabular-nums ${recipientList.length > maxRecipients ? 'text-danger' : ''}`}
                    >
                      {recipientList.length}/{maxRecipients}
                    </span>
                  </div>
                  <Textarea
                    id="bulk-message-recipients"
                    value={recipients}
                    placeholder={t('accounts.messages.recipientsPlaceholder')}
                    onChange={(event) => {
                      setRecipients(event.target.value);
                    }}
                  />
                  <ContactLookupPanel accountIds={ids} onAppendRecipients={appendRecipients} />
                </section>

                <section className="space-y-2">
                  <div className="flex items-center justify-between gap-3">
                    <label
                      htmlFor="bulk-message-text"
                      className="type-body-medium text-content-secondary"
                    >
                      {t('accounts.messages.text')}
                    </label>
                    <IconButton
                      size="sm"
                      tone="primary"
                      aria-label={t('accounts.messages.openGenerator')}
                      aria-expanded={generatorOpen}
                      onClick={() => {
                        generationRevision.current += 1;
                        setGeneratorOpen((open) => !open);
                      }}
                    >
                      <Icon name="sparkles" size={16} />
                    </IconButton>
                  </div>
                  {generatorOpen && (
                    <div className="space-y-2 rounded-md bg-canvas p-3">
                      <label
                        htmlFor="bulk-message-prompt"
                        className="type-body-medium text-content-secondary"
                      >
                        {t('accounts.messages.prompt')}
                      </label>
                      <Input
                        id="bulk-message-prompt"
                        maxLength={2000}
                        value={prompt}
                        placeholder={t('accounts.messages.promptPlaceholder')}
                        onChange={(event) => {
                          generationRevision.current += 1;
                          setPrompt(event.target.value);
                        }}
                      />
                      <Button
                        size="sm"
                        loading={generate.isPending}
                        disabled={prompt.trim() === ''}
                        onClick={onGenerate}
                      >
                        {t('accounts.messages.generate')}
                      </Button>
                      {generate.isError && (
                        <p role="alert" className="type-small text-danger-deep">
                          {t(generateErrorKey)}
                        </p>
                      )}
                    </div>
                  )}
                  <Textarea
                    id="bulk-message-text"
                    maxLength={4096}
                    value={message}
                    placeholder={t('accounts.messages.textPlaceholder')}
                    onChange={(event) => {
                      generationRevision.current += 1;
                      setMessage(event.target.value);
                      setProvider(null);
                    }}
                  />
                  {provider && (
                    <p className="type-small">
                      {t('accounts.messages.generatedBy', {
                        provider: provider === 'deepseek' ? 'DeepSeek' : 'Gemini',
                      })}
                    </p>
                  )}
                </section>

                <section className="space-y-2">
                  <div className="flex items-center gap-2">
                    <h3 className="type-body-medium text-content-secondary">
                      {t('accounts.messages.delay')}
                    </h3>
                    <HelpHint text={t('accounts.messages.delayHint')} />
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    {(
                      [
                        ['delayFrom', 'minDelay', minDelay, setMinDelay],
                        ['delayTo', 'maxDelay', maxDelay, setMaxDelay],
                      ] as const
                    ).map(([labelKey, ariaKey, value, setValue]) => (
                      <label key={labelKey} className="flex items-center gap-2 type-small">
                        <span>{t(`accounts.messages.${labelKey}`)}</span>
                        <Input
                          type="number"
                          size="sm"
                          min={0}
                          max={MAX_DELAY_SECONDS}
                          step={1}
                          aria-label={t(`accounts.messages.${ariaKey}`)}
                          value={value}
                          onChange={(event) => {
                            setValue(event.target.value);
                          }}
                        />
                        <span>{t('accounts.messages.secondsShort')}</span>
                      </label>
                    ))}
                  </div>
                  {!delayReady && (
                    <p role="alert" className="type-small text-danger">
                      {t('accounts.messages.delayInvalid')}
                    </p>
                  )}
                </section>
              </>
            )}
          </div>

          <div className="flex flex-wrap items-center justify-end gap-2 border-t border-canvas px-6 py-4">
            {!started && (
              <div className="mr-auto min-w-0">
                <span className="type-small tabular-nums">
                  {t('accounts.messages.total', { count: sendCount })}
                </span>
                {sendCount > MAX_SENDS && (
                  <p className="type-small text-danger">{t('accounts.messages.tooManySends')}</p>
                )}
                {send.isError && (
                  <p role="alert" className="type-small text-danger">
                    {t(sendErrorKey)}
                  </p>
                )}
              </div>
            )}
            <Button onClick={close}>
              {t(
                complete
                  ? 'accounts.messages.done'
                  : started
                    ? 'accounts.messages.close'
                    : 'accounts.profile.cancel',
              )}
            </Button>
            {started && !complete && !stale && (
              <Button
                variant="danger"
                loading={cancel.isPending}
                disabled={stopRequested}
                onClick={onStop}
              >
                {t('accounts.messages.stop')}
              </Button>
            )}
            {(complete || stale) && (
              <Button
                variant="primary"
                onClick={() => {
                  setStopRequested(false);
                  cancel.reset();
                  onNewJob();
                }}
              >
                {t('accounts.messages.newJob')}
              </Button>
            )}
            {!started && (
              <Button
                variant="primary"
                loading={send.isPending}
                disabled={!batchReady}
                onClick={onSend}
              >
                {t('accounts.messages.send')}
              </Button>
            )}
          </div>
        </div>
      </Modal>
      {pickerOpen && (
        <BulkAccountPicker
          selected={ids}
          onApply={(next) => {
            setIds(next);
            setPickerOpen(false);
          }}
          onClose={() => {
            setPickerOpen(false);
          }}
        />
      )}
    </>
  );
}
