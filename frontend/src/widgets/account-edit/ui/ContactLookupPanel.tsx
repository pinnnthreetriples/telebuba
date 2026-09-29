import { useMutation, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  activeContactLookupJobQueryOptions,
  cancelContactLookupJobMutation,
  getContactLookupJobQueryOptions,
  startContactLookupMutation,
} from '@/entities/account';
import { Button, HelpHint, Icon, IconButton, Input, Textarea } from '@/shared/ui';

const MAX_PHONES = 1000;
const MAX_DELAY_SECONDS = 300;

// Pasted lists are often comma-, semicolon- or tab-separated (a spreadsheet column
// pair); split on those too, so two numbers on one line are never sent fused.
function phonesFrom(value: string): string[] {
  return [
    ...new Set(
      value
        .split(/[\r\n\t,;]+/)
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

// A found user is reachable from the send step by public @username where it has one,
// and otherwise by raw user_id (only the account that found it can address that id).
function recipientToken(username: string | null | undefined, userId: number | null | undefined) {
  if (username) return `@${username}`;
  return userId != null ? String(userId) : null;
}

export function ContactLookupPanel({
  accountIds,
  onAppendRecipients,
}: {
  accountIds: string[];
  onAppendRecipients: (lines: string[], pins: Record<string, string>) => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [phones, setPhones] = useState('');
  const [minDelay, setMinDelay] = useState('1');
  const [maxDelay, setMaxDelay] = useState('3');
  const [jobId, setJobId] = useState<string | null>(null);
  const [added, setAdded] = useState<number | null>(null);
  const [resumeActive, setResumeActive] = useState(true);

  const start = useMutation(startContactLookupMutation());
  const cancel = useMutation(cancelContactLookupJobMutation());
  const active = useQuery({
    ...activeContactLookupJobQueryOptions(),
    enabled: open && jobId === null && resumeActive,
    refetchOnWindowFocus: false,
  });
  // A lookup keeps running on the server after the modal closes; pick it back up
  // instead of offering a start the server would refuse as already active.
  // Only a successful fetch from this mount counts: a failed refetch keeps stale data.
  const activeJobId =
    active.isSuccess && active.isFetchedAfterMount ? active.data?.job_id : undefined;
  if (jobId === null && resumeActive && activeJobId) {
    setJobId(activeJobId);
  }
  const job = useQuery({
    ...getContactLookupJobQueryOptions({ path: { job_id: jobId ?? '' } }),
    enabled: jobId !== null,
    retry: false,
    refetchInterval: (query) =>
      query.state.data?.status === 'completed' ||
      query.state.data?.status === 'cancelled' ||
      errorCode(query.state.error) === 'not_found'
        ? false
        : 1500,
  });

  const phoneList = phonesFrom(phones);
  const minSeconds = Number(minDelay);
  const maxSeconds = Number(maxDelay);
  const delayReady =
    /^\d+$/.test(minDelay) &&
    /^\d+$/.test(maxDelay) &&
    minSeconds <= maxSeconds &&
    maxSeconds <= MAX_DELAY_SECONDS;
  const canFind =
    accountIds.length > 0 && phoneList.length > 0 && phoneList.length <= MAX_PHONES && delayReady;

  const started = jobId !== null;
  const complete = job.data?.status === 'completed' || job.data?.status === 'cancelled';
  const results = job.data?.results ?? [];
  const found = results.filter((row) => row.status === 'found');
  const notFound = results.filter((row) => row.status === 'not_found').length;
  const skipped = results.filter((row) => row.status === 'skipped').length;
  const addable = [
    ...new Set(
      found
        .map((row) => recipientToken(row.username, row.user_id))
        .filter((token): token is string => token !== null),
    ),
  ];
  // A user without @username is addressable only by the account that found it, so
  // every other selected account will fail to message them.
  const idOnly = found.filter(
    (row) => !row.username && accountIds.some((id) => id !== row.account_id),
  ).length;
  // Split mode sends each raw user_id from the account that found it.
  const pins = Object.fromEntries(
    found
      .filter((row) => !row.username && row.user_id != null)
      .map((row) => [String(row.user_id), row.account_id]),
  );

  const onFind = () => {
    if (!canFind) return;
    setAdded(null);
    setResumeActive(false);
    void start
      .mutateAsync({
        body: {
          account_ids: accountIds,
          phones: phoneList,
          min_delay_seconds: minSeconds,
          max_delay_seconds: maxSeconds,
        },
      })
      .then((result) => {
        setJobId(result.job_id);
      })
      .catch(() => undefined);
  };

  const onStop = () => {
    if (jobId === null) return;
    void cancel
      .mutateAsync({ path: { job_id: jobId } })
      .then(() => job.refetch())
      .catch(() => undefined);
  };

  const onAdd = () => {
    if (addable.length === 0) return;
    onAppendRecipients(addable, pins);
    setAdded(addable.length);
  };

  const onReset = () => {
    setJobId(null);
    setAdded(null);
    setResumeActive(false);
    start.reset();
    cancel.reset();
  };

  const startErrorKey =
    errorMessage(start.error) === 'contact_lookup_run_active'
      ? 'accounts.messages.lookup.startActive'
      : errorMessage(start.error) === 'no valid phones'
        ? 'accounts.messages.lookup.startNoValid'
        : 'accounts.messages.lookup.startError';

  return (
    <div className="border-t border-line-row pt-sm">
      <button
        type="button"
        className="flex w-full items-center gap-sm type-label text-content-muted hover:text-content-primary"
        aria-expanded={open}
        onClick={() => {
          setOpen((value) => !value);
        }}
      >
        <Icon name={open ? 'chevron-down' : 'chevron-right'} size={18} />
        {t('accounts.messages.lookup.toggle')}
      </button>

      {open && (
        <div className="mt-md space-y-md">
          {!started ? (
            <>
              <section className="space-y-sm">
                <div className="flex items-center justify-between gap-md">
                  <div className="flex items-center gap-sm">
                    <label htmlFor="contact-lookup-phones" className="type-label">
                      {t('accounts.messages.lookup.phones')}
                    </label>
                    <HelpHint text={t('accounts.messages.lookup.phonesHint')} />
                  </div>
                  <span
                    className={`type-caption tabular-nums ${phoneList.length > MAX_PHONES ? 'text-danger-deep' : ''}`}
                  >
                    {phoneList.length}/{MAX_PHONES}
                  </span>
                </div>
                <Textarea
                  id="contact-lookup-phones"
                  value={phones}
                  placeholder={t('accounts.messages.lookup.phonesPlaceholder')}
                  onChange={(event) => {
                    setPhones(event.target.value);
                  }}
                />
              </section>

              <section className="space-y-sm">
                <h4 className="type-label">{t('accounts.messages.delay')}</h4>
                <div className="grid grid-cols-2 gap-md">
                  <label className="flex items-center gap-sm type-caption">
                    <span>{t('accounts.messages.delayFrom')}</span>
                    <Input
                      type="number"
                      size="xs"
                      aria-label={t('accounts.messages.minDelay')}
                      min={0}
                      max={MAX_DELAY_SECONDS}
                      step={1}
                      value={minDelay}
                      onChange={(event) => {
                        setMinDelay(event.target.value);
                      }}
                    />
                    <span>{t('accounts.messages.secondsShort')}</span>
                  </label>
                  <label className="flex items-center gap-sm type-caption">
                    <span>{t('accounts.messages.delayTo')}</span>
                    <Input
                      type="number"
                      size="xs"
                      aria-label={t('accounts.messages.maxDelay')}
                      min={0}
                      max={MAX_DELAY_SECONDS}
                      step={1}
                      value={maxDelay}
                      onChange={(event) => {
                        setMaxDelay(event.target.value);
                      }}
                    />
                    <span>{t('accounts.messages.secondsShort')}</span>
                  </label>
                </div>
              </section>

              {accountIds.length === 0 && (
                <p className="type-caption text-danger-deep">
                  {t('accounts.messages.lookup.needAccounts')}
                </p>
              )}
              {start.isError && (
                <p role="alert" className="type-caption text-danger-deep">
                  {t(startErrorKey)}
                </p>
              )}
              <Button
                variant="primary"
                loading={start.isPending}
                disabled={!canFind}
                onClick={onFind}
              >
                {t('accounts.messages.lookup.find')}
              </Button>
            </>
          ) : (
            <div className="space-y-sm">
              <p role="status" className="type-prose tabular-nums">
                {complete
                  ? [
                      t('accounts.messages.lookup.found', { count: found.length }),
                      t('accounts.messages.lookup.notFound', { count: notFound }),
                      ...(skipped > 0
                        ? [t('accounts.messages.lookup.skipped', { count: skipped })]
                        : []),
                    ].join(' · ')
                  : `${t('accounts.messages.lookup.running')} ${t('accounts.messages.progress', {
                      done: job.data?.completed ?? 0,
                      total: job.data?.total ?? phoneList.length,
                    })}`}
              </p>
              {job.isError && errorCode(job.error) !== 'not_found' && (
                <p role="alert" className="type-caption text-danger-deep">
                  {t('accounts.messages.progressError')}
                </p>
              )}
              {complete && idOnly > 0 && (
                <p className="type-caption">
                  {t('accounts.messages.lookup.idOnly', { count: idOnly })}
                </p>
              )}
              {added !== null && (
                <p className="type-caption">
                  {t('accounts.messages.lookup.added', { count: added })}
                </p>
              )}
              <div className="flex flex-wrap items-center gap-sm">
                {!complete && (
                  <Button variant="danger" loading={cancel.isPending} onClick={onStop}>
                    {t('accounts.messages.stop')}
                  </Button>
                )}
                {complete && added === null && (
                  <Button variant="primary" disabled={addable.length === 0} onClick={onAdd}>
                    {addable.length > 0
                      ? t('accounts.messages.lookup.addFound', { count: addable.length })
                      : t('accounts.messages.lookup.noneToAdd')}
                  </Button>
                )}
                {complete && (
                  <Button onClick={onReset}>{t('accounts.messages.lookup.newSearch')}</Button>
                )}
                {/* While a search runs (even through a failed poll), Stop ends it; closing would orphan it. */}
                {(complete || errorCode(job.error) === 'not_found') && (
                  <IconButton
                    size="touch"
                    aria-label={t('accounts.messages.close')}
                    onClick={onReset}
                    className="ml-auto"
                  >
                    <Icon name="close" size={16} />
                  </IconButton>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
