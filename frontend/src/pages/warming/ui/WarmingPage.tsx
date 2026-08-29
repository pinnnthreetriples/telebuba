import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { AccountAvatar, accountDisplayName } from '@/entities/account';
import { proxyTypeLabel } from '@/entities/proxy';
import {
  addWarmingChannelsMutation,
  handoffToNeurocommentMutation,
  promoteToNeurocommentMutation,
  removeWarmingChannelMutation,
  startWarmingMutation,
  stopWarmingMutation,
  unpromoteFromNeurocommentMutation,
  warmingBoardQueryOptions,
} from '@/entities/warming';
import type { WarmingAccountState } from '@/shared/api';
import { useLogEventStream, useTransientFeedback } from '@/shared/lib';
import {
  Badge,
  Button,
  Card,
  CollapsibleCard,
  ConfirmModal,
  FeedbackMark,
  Icon,
} from '@/shared/ui';
import { DialogueFeed } from '@/widgets/dialogue-feed';
import { WarmDaysModal, WarmingBoard } from '@/widgets/warming-board';

// SSE drives live board updates; this poll is just the fallback safety net.
const FALLBACK_POLL_MS = 30000;

// The only queries this page reads (createQueryKey stamps _id on key[0]); a live
// event refreshes just these, never the whole cache. The warmed pool now rides
// the board payload, so there's no separate listWarmedAccounts fetch here.
// listLogs is included so the card terminals actually pick up new events.
const WARMING_QUERY_IDS = ['getWarmingBoard', 'listWarmingChannels', 'listLogs'];

// Trust 3-tier tone (design): healthy / watch / risk. Tokens, not hexes, so the
// tiers read the same as every other health signal in the dashboard.
function trustTone(trust: number): string {
  if (trust >= 70) return 'text-success-deep';
  if (trust >= 45) return 'text-warning-deep';
  return 'text-danger';
}

// Map a backend readiness reason (English, from evaluate_readiness) to its RU
// i18n key: "session <status>" / "no proxy" / "proxy failed" / "no channels" /
// "spam limited" / "trust critical".
const READINESS_REASON_KEY: Record<string, string> = {
  'no proxy': 'warming.notReady.noProxy',
  'proxy failed': 'warming.notReady.proxyFailed',
  'no channels': 'warming.notReady.noChannels',
  'spam limited': 'warming.notReady.spamLimited',
  'trust critical': 'warming.notReady.trustCritical',
};
function reasonKey(reason: string): string {
  return reason.startsWith('session ')
    ? 'warming.notReady.session'
    : (READINESS_REASON_KEY[reason] ?? '');
}

function Counter({ value, label, cls }: { value: number; label: string; cls: string }) {
  return (
    <div className="text-right">
      <div className={`type-stat ${cls}`}>{value}</div>
      <div className="type-caption">{label}</div>
    </div>
  );
}

export function WarmingPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  // A Set, not one id: five mutations (start / stop / promote / unpromote /
  // handoff) act per account across three surfaces, the bulk pool button fires N
  // of them at once, and any two can be in flight together. With a single string
  // the last click owned the spinner while every other card re-enabled mid-request,
  // and the first response to land cleared the whole board's busy state.
  const [busyIds, setBusyIds] = useState<ReadonlySet<string>>(new Set());
  // Guards the bulk pool button for the WHOLE batch: useMutation.isPending tracks
  // only the last-fired call, so it can re-enable mid-batch and re-fire on a
  // second click. bulkBusy stays true until every batched call settles.
  const [bulkBusy, setBulkBusy] = useState(false);
  const [channelInput, setChannelInput] = useState('');
  const [addingChannel, setAddingChannel] = useState(false);
  const [warmDaysFor, setWarmDaysFor] = useState<WarmingAccountState | null>(null);
  const [channelToRemove, setChannelToRemove] = useState<string | null>(null);
  const accountFeedback = useTransientFeedback();
  const channelFeedback = useTransientFeedback();

  const { data, isPending, isError } = useQuery({
    ...warmingBoardQueryOptions(),
    refetchInterval: FALLBACK_POLL_MS,
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({
      predicate: (query) => {
        const id = (query.queryKey[0] as { _id?: string } | undefined)?._id;
        return id != null && WARMING_QUERY_IDS.includes(id);
      },
    });
  };
  // Live status: any runtime event refreshes the board (event-driven, not timed).
  useLogEventStream(invalidate);
  const start = useMutation(startWarmingMutation());
  const stop = useMutation(stopWarmingMutation());
  const addChannels = useMutation(addWarmingChannelsMutation());
  const removeChannel = useMutation(removeWarmingChannelMutation());
  const promote = useMutation(promoteToNeurocommentMutation());
  const unpromote = useMutation(unpromoteFromNeurocommentMutation());
  const handoff = useMutation(handoffToNeurocommentMutation());

  const markBusy = (accountId: string, busy: boolean) => {
    setBusyIds((ids) => {
      const next = new Set(ids);
      if (busy) next.add(accountId);
      else next.delete(accountId);
      return next;
    });
  };
  // promote (graduate) / unpromote (return to warming) share the {account_id} body.
  // mutateAsync for the same reason as runOnAccount below: mutate's callbacks live
  // in ONE slot per hook, so graduating a second account dropped the first
  // account's feedback mark and invalidate. A promise per call also captures its
  // own accountId, not the hook's latest variables.
  const runGraduation = (mutation: typeof promote, accountId: string) => {
    markBusy(accountId, true);
    return mutation
      .mutateAsync({ body: { account_id: accountId } })
      .then(
        () => {
          accountFeedback.mark(accountId, true);
        },
        () => {
          accountFeedback.mark(accountId, false);
        },
      )
      .finally(() => {
        markBusy(accountId, false);
        invalidate();
      });
  };

  const cancelAddChannel = () => {
    setAddingChannel(false);
    setChannelInput('');
  };
  // The channel pills are a list too, so both handlers use mutateAsync for the
  // same reason as the account runners: one useMutation is ONE callback slot, and
  // a second add (the input stays open until its own settle) or a second removal
  // confirmed while the first was in flight dropped the first channel's feedback
  // mark and its invalidate — the pill sat there unmarked and the list stale.
  const addChannel = () => {
    if (!channelInput.trim()) return;
    const raw = channelInput.trim();
    void addChannels
      .mutateAsync({ body: { raw } })
      .then(
        () => channelFeedback.mark(raw, true),
        () => channelFeedback.mark(raw, false),
      )
      .finally(() => {
        cancelAddChannel();
        invalidate();
      });
  };
  const confirmRemoveChannel = () => {
    if (!channelToRemove) return;
    const channel = channelToRemove;
    setChannelToRemove(null);
    void removeChannel
      .mutateAsync({ body: { channel } })
      .then(
        () => channelFeedback.mark(channel, true),
        () => channelFeedback.mark(channel, false),
      )
      .finally(invalidate);
  };

  // Returns a never-rejecting promise so the bulk path can await the whole batch
  // (single-account callers ignore it). mutateAsync (not mutate) makes it awaitable.
  const runOnAccount = (mutation: typeof start | typeof stop, accountId: string) => {
    markBusy(accountId, true);
    return mutation
      .mutateAsync({ body: { account_id: accountId } })
      .then(
        () => {
          accountFeedback.mark(accountId, true);
        },
        () => {
          accountFeedback.mark(accountId, false);
        },
      )
      .finally(() => {
        markBusy(accountId, false);
        invalidate();
      });
  };

  if (isPending) return <p className="text-content-muted">{t('warming.loading')}</p>;
  if (isError) {
    return (
      <p role="alert" className="text-danger">
        {t('warming.error')}
      </p>
    );
  }

  const idle = data.idle ?? [];
  const warming = data.warming ?? [];
  // A handed-off account lives on the neurocomment page's idle pool from that
  // point on — the warmed card here shows only the not-yet-handed ones.
  const warmed = (data.warmed ?? []).filter((acc) => !acc.nc_handed_off);
  const channels = data.channels.channels ?? [];
  // handle → friendly label, so the board's activity log can name the channel a
  // join/read/react touched (the gateway logs the raw handle in extra.channel).
  const channelLabels = Object.fromEntries(
    channels.filter((c) => c.label).map((c) => [c.channel, c.label as string]),
  );
  const errors = [...idle, ...warming].filter((a) => a.state === 'error').length;
  const poolOn = warming.length > 0;

  return (
    <div className="tb-fadeup">
      <div className="mb-xl flex flex-wrap items-center justify-between gap-lg">
        <h1 className="m-0 type-page-title">{t('warming.titleFull')}</h1>
        <div className="flex items-center gap-lg">
          <div className="flex gap-lg">
            <Counter
              value={warming.length}
              label={t('warming.counter.warming')}
              cls="text-action-primary"
            />
            <Counter
              value={idle.length}
              label={t('warming.counter.ready')}
              cls="text-content-primary"
            />
            <Counter value={errors} label={t('warming.counter.errors')} cls="text-danger" />
          </div>
          <Button
            variant="primary"
            size="sm"
            disabled={bulkBusy || start.isPending || stop.isPending}
            onClick={() => {
              const mutation = poolOn ? stop : start;
              setBulkBusy(true);
              void Promise.allSettled(
                (poolOn ? warming : idle).map((a) => runOnAccount(mutation, a.account_id)),
              ).finally(() => {
                setBulkBusy(false);
              });
            }}
            className={`gap-sm ${poolOn ? 'bg-content-primary hover:bg-content-primary' : ''}`}
          >
            {poolOn ? <Icon name="pause" size={14} /> : <Icon name="play" size={14} />}
            {poolOn ? t('warming.pool.stop') : t('warming.pool.start')}
          </Button>
        </div>
      </div>

      {/* `minmax(0,1fr)`, not a bare `1fr`: an `fr` track keeps an automatic minimum
          (index.css says the same of `.tb-subrow` in the row direction). The board is
          capped by its own 320px track, but the dialogue feed prints whatever the
          accounts wrote — one unwrappable line measured the track open to 1227px and the
          page to scrollWidth 1607 against clientWidth 1024, a scroll the viewport-wide
          sticky header can't follow. With minmax the feed scrolls in its own card. */}
      <div className="grid items-start gap-lg lg:grid-cols-[340px_minmax(0,1fr)]">
        <div className="flex flex-col gap-lg">
          <Card className="p-lg">
            <div className="mb-md flex items-center justify-between">
              <span className="type-card-title">{t('warming.ready.title')}</span>
              <span className="rounded-full border border-line bg-surface-card px-sm py-hair text-tiny text-content-subtle">
                {idle.length}
              </span>
            </div>
            <div className="flex flex-col gap-sm">
              {idle.length === 0 ? (
                <div className="py-page text-center type-prose">{t('warming.ready.empty')}</div>
              ) : (
                idle.map((account) => {
                  const trust = account.trust_score;
                  const tTone = trust != null ? trustTone(trust) : 'text-content-subtle';
                  const cc = account.phone_country?.toLowerCase() ?? null;
                  const pc = account.proxy_country?.toLowerCase() ?? null;
                  const ptype = account.proxy_type;
                  const ready = account.readiness?.ready ?? false;
                  const blockers = (account.readiness?.reasons ?? [])
                    .map((reason) => {
                      const key = reasonKey(reason);
                      return key ? t(key) : reason;
                    })
                    .join(', ');
                  // Telegram name on top; the phone (with its country flag)
                  // drops to a subtitle. When the account has no name,
                  // accountDisplayName falls back to the phone, so skip the
                  // duplicate subtitle and keep the flag on the primary line.
                  const name = accountDisplayName(account);
                  const showPhone = account.phone != null && account.phone !== name;
                  const flag = cc ? (
                    <span
                      className={`fi fi-${cc} h-flag w-flag shrink-0 rounded-[2px] shadow-ring`}
                    />
                  ) : null;
                  return (
                    <div
                      key={account.account_id}
                      className="flex items-center gap-md rounded-lg border border-line bg-surface-card px-md py-md"
                    >
                      <AccountAvatar
                        account={account}
                        className="size-icon shrink-0 rounded-full"
                        fallbackClassName="text-body font-semibold bg-info-tint text-info-strong"
                      />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-tight">
                          <span className="truncate type-card-title">{name}</span>
                          {showPhone ? null : flag}
                        </div>
                        {showPhone ? (
                          <div className="mt-px flex items-center gap-tight">
                            <span className="truncate type-caption">{account.phone}</span>
                            {flag}
                          </div>
                        ) : null}
                        <div className="mt-hair flex items-center gap-sm">
                          <Icon name="shield-check" size={14} className={`shrink-0 ${tTone}`} />
                          <span className={`text-tiny font-semibold ${tTone}`}>{trust ?? '—'}</span>
                          {ptype ? (
                            <>
                              <span className="type-caption">·</span>
                              {pc ? (
                                <span
                                  className={`fi fi-${pc} h-flag w-flag shrink-0 rounded-[2px] shadow-ring`}
                                />
                              ) : null}
                              <span className="type-caption">{proxyTypeLabel(ptype)}</span>
                            </>
                          ) : null}
                        </div>
                      </div>
                      <button
                        type="button"
                        disabled={!ready || busyIds.has(account.account_id)}
                        title={ready ? undefined : blockers}
                        onClick={() => {
                          setWarmDaysFor(account);
                        }}
                        className={`rounded-full px-lg py-tight text-body font-medium disabled:opacity-50 ${ready ? 'bg-action-primary text-on-action' : 'cursor-not-allowed bg-canvas text-content-subtle'}`}
                      >
                        {ready ? t('warming.ready.start') : t('warming.ready.unavailable')}
                      </button>
                    </div>
                  );
                })
              )}
            </div>
          </Card>

          <CollapsibleCard
            wrapperClassName="rounded-lg border border-line bg-surface-card"
            header={<span className="type-card-title">{t('warming.channels.title')}</span>}
            label={t('warming.channels.title')}
          >
            <div className="mb-md type-caption">{t('warming.channels.hint')}</div>
            <div className="flex flex-wrap gap-sm">
              {channels.map((channel) => (
                <Badge
                  size="md"
                  className="gap-sm border border-line text-content-secondary"
                  key={channel.channel}
                >
                  <FeedbackMark result={channelFeedback.feedback[channel.channel]} />
                  {channel.channel}
                  <button
                    type="button"
                    aria-label={t('warming.channels.remove')}
                    onClick={() => {
                      setChannelToRemove(channel.channel);
                    }}
                    className="text-body leading-none text-content-subtle"
                  >
                    ×
                  </button>
                </Badge>
              ))}
              {addingChannel ? (
                <span className="inline-flex items-center gap-tight rounded-full border border-action-primary bg-surface-card py-xs pl-md pr-xs">
                  <input
                    autoFocus
                    value={channelInput}
                    onChange={(event) => {
                      setChannelInput(event.target.value);
                    }}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') addChannel();
                      if (event.key === 'Escape') cancelAddChannel();
                    }}
                    placeholder={t('warming.channels.placeholderSingle')}
                    aria-label={t('warming.channels.placeholderSingle')}
                    className="w-col border-none bg-transparent text-body outline-none"
                  />
                  <button
                    type="button"
                    title={t('warming.channels.add')}
                    aria-label={t('warming.channels.add')}
                    disabled={!channelInput.trim()}
                    onClick={addChannel}
                    className="flex size-chip shrink-0 items-center justify-center rounded-full bg-action-primary text-on-action disabled:opacity-50"
                  >
                    <Icon name="check" size={12} />
                  </button>
                  <button
                    type="button"
                    title={t('warming.channels.cancel')}
                    aria-label={t('warming.channels.cancel')}
                    onClick={cancelAddChannel}
                    className="flex size-chip shrink-0 items-center justify-center rounded-full bg-line-row text-body leading-none text-content-muted"
                  >
                    ×
                  </button>
                </span>
              ) : (
                <button
                  type="button"
                  // The muted inline adder, not `Button variant="dashed"` — see the
                  // note on its twin in neurocomment's CampaignsCard.
                  onClick={() => {
                    setAddingChannel(true);
                  }}
                  className="inline-flex items-center gap-tight rounded-full border border-dashed border-line-strong bg-surface-card px-md py-tight text-body text-content-muted hover:border-action-primary hover:text-action-primary"
                >
                  {t('warming.channels.addPill')}
                </button>
              )}
            </div>
          </CollapsibleCard>

          <CollapsibleCard
            // Auto-expand once there are warmed accounts so a just-graduated
            // account is visible where it landed (the key re-inits defaultOpen
            // when the pool crosses empty↔non-empty).
            key={warmed.length > 0 ? 'warmed-has' : 'warmed-none'}
            defaultOpen={warmed.length > 0}
            label={t('warming.warmed.title')}
            header={
              <>
                <span className="flex size-icon items-center justify-center rounded-lg bg-success-tint">
                  <Icon name="check" size={16} className="stroke-success" />
                </span>
                <span className="type-card-title">{t('warming.warmed.title')}</span>
                <Badge tone="success" className="font-bold">
                  {warmed.length}
                </Badge>
              </>
            }
          >
            <div className="flex flex-col gap-md">
              {warmed.map((acc) => {
                // Telegram name on top; the phone (with its country flag) drops
                // to a subtitle — same pattern as the ready card. When there is
                // no name, accountDisplayName falls back to the phone, so keep
                // the flag on the primary line and skip the duplicate subtitle.
                const name = accountDisplayName(acc);
                const showPhone = acc.phone != null && acc.phone !== name;
                const flag = acc.phone_country ? (
                  <span
                    className={`fi fi-${acc.phone_country.toLowerCase()} h-flag w-flag shrink-0 rounded-[2px] shadow-ring`}
                  />
                ) : null;
                return (
                  <div key={acc.account_id} className="rounded-lg border border-line p-lg">
                    <div className="flex items-start gap-md">
                      <AccountAvatar
                        account={acc}
                        className="size-tile shrink-0 rounded-full ring-2 ring-success"
                        fallbackClassName="text-tiny font-bold bg-info-tint text-info-strong"
                      />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-tight">
                          <span className="truncate type-card-title leading-stack">{name}</span>
                          {showPhone ? null : flag}
                        </div>
                        {showPhone ? (
                          <div className="mt-px flex items-center gap-tight">
                            <span className="truncate type-caption">{acc.phone}</span>
                            {flag}
                          </div>
                        ) : null}
                        <div className="mt-tight flex items-center gap-sm">
                          {acc.proxy_country ? (
                            <span
                              className={`fi fi-${acc.proxy_country.toLowerCase()} h-flag w-flag rounded-[2px]`}
                            />
                          ) : null}
                          <span className="type-caption">
                            {acc.proxy_type ? proxyTypeLabel(acc.proxy_type) : '—'}
                          </span>
                        </div>
                      </div>
                      {/* The other accent marker (see LaunchCard's LIVE): `micro`/`bold`
                          with letter-spacing because it is emphasis on a finished account,
                          not a neutral state. Deliberately outside the status-pill family. */}
                      <span className="inline-flex items-center gap-tight rounded-full bg-success-tint px-md py-xs text-tiny font-bold text-success-deep">
                        <Icon name="check" size={10} className="stroke-success" />
                        {t('warming.warmed.badge')}
                      </span>
                    </div>
                    <div className="mt-lg flex items-center rounded-lg bg-surface px-lg py-md">
                      <div className="flex-1">
                        <div className="type-caption">{t('warming.warmed.days')}</div>
                        <div className="text-body font-bold">
                          {t('warming.warmed.daysValue', {
                            days: acc.warming_days,
                            target: acc.target_days,
                          })}
                        </div>
                      </div>
                      <span className="h-compact w-px bg-line" />
                      <div className="flex-1 pl-lg">
                        <div className="type-caption">{t('warming.warmed.trust')}</div>
                        <div className="text-body font-bold text-success-deep">
                          {acc.trust_score ?? '—'}
                        </div>
                      </div>
                    </div>
                    <div className="mt-lg flex items-center gap-md">
                      <button
                        type="button"
                        disabled={busyIds.has(acc.account_id)}
                        onClick={() => {
                          runGraduation(handoff, acc.account_id);
                        }}
                        className="flex flex-1 items-center justify-center gap-sm rounded-full bg-content-primary px-lg py-md text-body font-semibold text-on-action disabled:opacity-50"
                      >
                        {t('warming.warmed.toNeuro')}
                        <Icon name="arrow-right" size={14} />
                      </button>
                      <FeedbackMark result={accountFeedback.feedback[acc.account_id]} />
                      <button
                        type="button"
                        title={t('warming.warmed.backToWarm')}
                        aria-label={t('warming.warmed.backToWarm')}
                        disabled={busyIds.has(acc.account_id)}
                        onClick={() => {
                          runGraduation(unpromote, acc.account_id);
                        }}
                        className="flex size-thumbnail shrink-0 items-center justify-center rounded-full border border-line bg-surface-card text-content-muted disabled:opacity-50"
                      >
                        <svg
                          width="15"
                          height="15"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2"
                        >
                          <path d="M3 12a9 9 0 1 0 3-6.7L3 8" />
                          <path d="M3 3v5h5" />
                        </svg>
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          </CollapsibleCard>

          <CollapsibleCard
            label={t('warming.howto.title')}
            wrapperClassName="rounded-card border border-line bg-canvas"
            header={<span className="type-card-title">{t('warming.howto.title')}</span>}
          >
            <div className="mb-lg type-caption">{t('warming.howto.hint')}</div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-x-lg gap-y-md">
              {[0, 1, 2, 3, 4, 5].map((index) => (
                <div key={index} className="flex items-start gap-md">
                  <span className="mt-px flex size-glyph shrink-0 items-center justify-center rounded-full bg-action-primary text-tiny font-semibold text-on-action">
                    {index + 1}
                  </span>
                  <span className="type-prose">{t(`warming.howto.steps.${String(index)}`)}</span>
                </div>
              ))}
            </div>
          </CollapsibleCard>
        </div>

        <div className="flex flex-col gap-lg">
          <WarmingBoard
            warming={warming}
            onStop={(id) => {
              runOnAccount(stop, id);
            }}
            onPromote={(id) => {
              runGraduation(promote, id);
            }}
            busyIds={busyIds}
            feedback={accountFeedback.feedback}
            logLimit={data.card_log_limit}
            channelLabels={channelLabels}
          />
          <DialogueFeed />
        </div>
      </div>

      {warmDaysFor ? (
        <WarmDaysModal
          accountId={warmDaysFor.account_id}
          phone={warmDaysFor.phone ?? warmDaysFor.label ?? warmDaysFor.account_id}
          onClose={() => {
            setWarmDaysFor(null);
          }}
          onConfirm={(days, persona) => {
            const accountId = warmDaysFor.account_id;
            markBusy(accountId, true);
            // mutateAsync: this shares the `start` hook with the bulk pool button,
            // which fires start.mutateAsync for every idle account. With
            // mutate+onSettled a pool start took this call's ONE callback slot
            // over, so busyId was never cleared — the account's Прогреть button
            // stayed disabled for good — and its feedback mark never appeared.
            void start
              .mutateAsync({
                body: {
                  account_id: accountId,
                  target_days: days,
                  activity_persona: persona,
                },
              })
              .then(
                () => accountFeedback.mark(accountId, true),
                () => accountFeedback.mark(accountId, false),
              )
              .finally(() => {
                markBusy(accountId, false);
                invalidate();
              });
          }}
        />
      ) : null}

      {channelToRemove ? (
        <ConfirmModal
          title={t('warming.channels.removeTitle', { channel: channelToRemove })}
          body={t('warming.channels.removeBody')}
          confirmLabel={t('warming.channels.removeConfirm')}
          cancelLabel={t('warming.channels.cancel')}
          onClose={() => {
            setChannelToRemove(null);
          }}
          onConfirm={confirmRemoveChannel}
        />
      ) : null}
    </div>
  );
}
