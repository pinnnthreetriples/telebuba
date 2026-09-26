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
import { Button, CloseButton, Icon, IconButton, Input, Modal, Textarea } from '@/shared/ui';

import { BulkAccountPicker } from './BulkAccountPicker';

const MAX_ACCOUNTS = 50;
const MAX_RECIPIENTS = 50;
const MAX_SENDS = 500;
const MAX_DELAY_SECONDS = 300;

export type BulkMessageDraft = {
  ids: string[];
  recipients: string;
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
  const [recipients, setRecipients] = useState(initialDraft?.recipients ?? '');
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
    recipientList.length <= MAX_RECIPIENTS &&
    ids.length * recipientList.length <= MAX_SENDS &&
    message.trim() !== '' &&
    message.length <= 4096 &&
    delayReady;
  const started = jobId !== null;
  const complete = job.data?.status === 'completed' || job.data?.status === 'cancelled';
  const stale = job.isError && errorCode(job.error) === 'not_found';
  const attention = job.data?.results.filter((result) => result.status !== 'ok') ?? [];

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
        },
      })
      .then((result) => {
        onJobStarted(result.job_id);
      })
      .catch(() => undefined);
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
        recipients,
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
          <div className="flex items-center gap-lg border-b border-line-row px-xl py-xl">
            <div className="flex size-face shrink-0 items-center justify-center rounded-full bg-info-tint text-info-strong">
              <Icon name="users" size={20} />
            </div>
            <div className="min-w-0 flex-1">
              <h2 className="truncate type-dialog-title">{t('accounts.messages.title')}</h2>
              <p className="type-prose">{t('accounts.messages.subtitle')}</p>
            </div>
            <CloseButton onClick={close} aria-label={t('accounts.profile.close')} />
          </div>

          <div className="tb-scroll flex-1 space-y-xl overflow-y-auto p-xl">
            {started ? (
              stale ? (
                <div className="space-y-sm">
                  <h3 className="type-item-title">{t('accounts.messages.staleTitle')}</h3>
                  <p className="type-prose">{t('accounts.messages.staleHint')}</p>
                </div>
              ) : (
                <div className="space-y-lg">
                  <div>
                    <h3 className="type-item-title">
                      {t(
                        job.data?.status === 'cancelled'
                          ? 'accounts.messages.cancelled'
                          : complete
                            ? 'accounts.messages.completed'
                            : 'accounts.messages.running',
                      )}
                    </h3>
                    <p role="status" className="mt-sm type-prose tabular-nums">
                      {t('accounts.messages.progress', {
                        done: job.data?.completed ?? 0,
                        total: job.data?.total ?? ids.length * recipientList.length,
                      })}
                    </p>
                  </div>
                  {job.isError && (
                    <p role="alert" className="text-danger">
                      {t('accounts.messages.progressError')}
                    </p>
                  )}
                  {stopRequested && !complete && (
                    <p className="type-caption">{t('accounts.messages.stopping')}</p>
                  )}
                  {cancel.isError && (
                    <p role="alert" className="text-danger">
                      {t('accounts.messages.stopError')}
                    </p>
                  )}
                  {attention.length > 0 && (
                    <div>
                      <h3 className="type-label">
                        {t('accounts.messages.attention', { count: attention.length })}
                      </h3>
                      <ul className="mt-sm space-y-sm">
                        {attention.map((result) => (
                          <li
                            key={`${result.account_id}:${result.recipient}`}
                            className="rounded-md bg-danger-tint px-md py-sm type-caption"
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
                </div>
              )
            ) : (
              <>
                <section className="space-y-sm">
                  <div className="flex items-center justify-between gap-md">
                    <h3 className="type-label">{t('accounts.messages.accounts')}</h3>
                    <span
                      className={`type-caption tabular-nums ${ids.length > MAX_ACCOUNTS ? 'text-danger' : ''}`}
                    >
                      {ids.length}/{MAX_ACCOUNTS}
                    </span>
                  </div>
                  <div className="flex min-h-control items-center gap-sm rounded-lg border border-line bg-canvas p-sm">
                    <IconButton
                      size="touch"
                      aria-label={t('accounts.bulk.add')}
                      onClick={() => {
                        setPickerOpen(true);
                      }}
                    >
                      <Icon name="plus" size={18} />
                    </IconButton>
                    {ids.length === 0 ? (
                      <span className="type-prose">{t('accounts.messages.pickAccounts')}</span>
                    ) : (
                      <div className="tb-scroll flex items-center gap-sm overflow-x-auto py-hair">
                        {ids.map((id) => {
                          const account = byId.get(id);
                          return (
                            <span key={id} className="group relative shrink-0">
                              <AccountAvatar
                                account={account ?? { account_id: id }}
                                className="size-tile rounded-full"
                                fallbackClassName="bg-surface-card text-content-muted type-label"
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
                                className="absolute -right-hair -top-hair bg-surface-card sm:opacity-0 sm:transition-opacity sm:focus-visible:opacity-100 sm:group-hover:opacity-100"
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

                <section className="space-y-sm">
                  <div className="flex items-center justify-between gap-md">
                    <label htmlFor="bulk-message-recipients" className="type-label">
                      {t('accounts.messages.recipients')}
                    </label>
                    <span
                      className={`type-caption tabular-nums ${recipientList.length > MAX_RECIPIENTS ? 'text-danger' : ''}`}
                    >
                      {recipientList.length}/{MAX_RECIPIENTS}
                    </span>
                  </div>
                  <Textarea
                    id="bulk-message-recipients"
                    rows={3}
                    value={recipients}
                    placeholder={t('accounts.messages.recipientsPlaceholder')}
                    onChange={(event) => {
                      setRecipients(event.target.value);
                    }}
                  />
                  <p className="type-caption">{t('accounts.messages.recipientsHint')}</p>
                </section>

                <section className="space-y-sm">
                  <div className="flex items-center justify-between gap-md">
                    <label htmlFor="bulk-message-text" className="type-label">
                      {t('accounts.messages.text')}
                    </label>
                    <IconButton
                      size="touch"
                      tone="primary"
                      aria-label={t('accounts.messages.openGenerator')}
                      aria-expanded={generatorOpen}
                      onClick={() => {
                        generationRevision.current += 1;
                        setGeneratorOpen((open) => !open);
                      }}
                    >
                      <Icon name="sparkles" size={18} />
                    </IconButton>
                  </div>
                  {generatorOpen && (
                    <div className="space-y-sm rounded-lg bg-canvas p-md">
                      <label htmlFor="bulk-message-prompt" className="type-label">
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
                        <p role="alert" className="type-caption text-danger-deep">
                          {t(generateErrorKey)}
                        </p>
                      )}
                    </div>
                  )}
                  <Textarea
                    id="bulk-message-text"
                    rows={5}
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
                    <p className="type-caption">
                      {t('accounts.messages.generatedBy', {
                        provider: provider === 'deepseek' ? 'DeepSeek' : 'Gemini',
                      })}
                    </p>
                  )}
                </section>

                <section className="space-y-sm">
                  <h3 className="type-label">{t('accounts.messages.delay')}</h3>
                  <div className="grid grid-cols-2 gap-md">
                    <label className="space-y-sm type-caption">
                      <span>{t('accounts.messages.minDelay')}</span>
                      <Input
                        type="number"
                        min={0}
                        max={MAX_DELAY_SECONDS}
                        step={1}
                        value={minDelay}
                        onChange={(event) => {
                          setMinDelay(event.target.value);
                        }}
                      />
                    </label>
                    <label className="space-y-sm type-caption">
                      <span>{t('accounts.messages.maxDelay')}</span>
                      <Input
                        type="number"
                        min={0}
                        max={MAX_DELAY_SECONDS}
                        step={1}
                        value={maxDelay}
                        onChange={(event) => {
                          setMaxDelay(event.target.value);
                        }}
                      />
                    </label>
                  </div>
                  <p className={delayReady ? 'type-caption' : 'type-caption text-danger'}>
                    {t('accounts.messages.delayHint')}
                  </p>
                </section>
              </>
            )}
          </div>

          <div className="flex flex-wrap items-center justify-end gap-sm border-t border-line-row px-xl py-lg">
            {!started && (
              <div className="mr-auto min-w-0">
                <span className="type-caption tabular-nums">
                  {t('accounts.messages.total', { count: ids.length * recipientList.length })}
                </span>
                {ids.length * recipientList.length > MAX_SENDS && (
                  <p className="type-caption text-danger">{t('accounts.messages.tooManySends')}</p>
                )}
                {send.isError && (
                  <p role="alert" className="type-caption text-danger">
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
