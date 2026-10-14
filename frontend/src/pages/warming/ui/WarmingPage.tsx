import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { AccountAvatar, accountDisplayName, deleteAccountMutation } from '@/entities/account';
import { proxyTypeLabel } from '@/entities/proxy';
import {
  addWarmingChannelsMutation,
  handoffToNeurocommentMutation,
  keepSpamBlockedAccountMutation,
  promoteToNeurocommentMutation,
  removeWarmingChannelMutation,
  rewarmAccountMutation,
  SpamBlockBadge,
  startWarmingMutation,
  stopWarmingMutation,
  SurvivalBadge,
  warmingBoardQueryOptions,
  WaveChip,
} from '@/entities/warming';
import type { StartWarmingRequest, WarmedAccount, WarmingAccountState } from '@/shared/api';
import { errorMessage, mutationErrorText, useTransientFeedback } from '@/shared/lib';
import {
  Badge,
  Button,
  Card,
  CardHeader,
  ChipAddButton,
  CollapsibleCard,
  ConfirmModal,
  EmptyState,
  FeedbackMark,
  HowItWorksCard,
  Icon,
  InlineChipEditor,
  StatGrid,
  toastError,
} from '@/shared/ui';
import { DeleteAccountModal, type DeleteReason } from '@/widgets/accounts-table';
import { DialogueFeed } from '@/widgets/dialogue-feed';
import {
  ActionTuningCard,
  ChannelLanguageShortages,
  ChannelPoolFill,
  DmPauseBanner,
  GeoMismatchBanner,
  HandoffModal,
  type HandoffMode,
  needsGeoConsent,
  PaceCard,
  readinessReasonKey,
  SpamRetryModal,
  type StartDialogChoice,
  trustTone,
  WarmDaysModal,
  WarmingBoard,
  WarmingChannelPill,
} from '@/widgets/warming-board';
import { SurvivalPanel, SurvivalTile } from '@/widgets/warming-survival';

import { invalidateWarmingQueries, useWarmingLiveRefresh } from '../model/liveRefresh';

// SSE drives live board updates; this poll is just the fallback safety net.
const FALLBACK_POLL_MS = 30000;

// «Как работает» in cycle order. A key from the board's `mechanics` is a step the
// settings can switch off; `null` is one the engine always runs.
// What the start dialog hands back: one length, cadence and first-cycle delay.
type StartChoice = Pick<
  StartWarmingRequest,
  | 'target_days'
  | 'activity_persona'
  | 'start_delay_hours'
  | 'spread_start'
  | 'accept_geo_mismatch'
  | 'device_mode'
>;

// The dialog's answer as the start request's fields.
function startChoice(choice: StartDialogChoice): StartChoice {
  return {
    target_days: choice.days,
    activity_persona: choice.persona,
    start_delay_hours: choice.delayHours,
    accept_geo_mismatch: choice.acceptGeo,
    device_mode: choice.device,
  };
}

// The start dialog's geo warning for one account, when its pair is not accepted yet.
function geoOf(account: {
  geo_status?: WarmingAccountState['geo_status'];
  geo_accepted?: boolean;
  phone_country?: string | null;
  proxy_country?: string | null;
}): { phoneCountry: string; proxyCountry: string } | undefined {
  if (!needsGeoConsent(account) || !account.phone_country || !account.proxy_country) {
    return undefined;
  }
  return { phoneCountry: account.phone_country, proxyCountry: account.proxy_country };
}

const SURVIVAL_PANEL_ID = 'warming-survival-panel';

const HOWTO_STEPS = [
  ['join', 'join'],
  ['read', null],
  ['reactions', 'reactions'],
  ['stories', 'stories'],
  ['dialogue', 'dialogue'],
  ['ramp', null],
  ['pauses', null],
] as const;

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
  // "Запустить пул" opens the start dialog for every idle account instead of firing.
  const [poolStartOpen, setPoolStartOpen] = useState(false);
  // «Вернуть в прогрев» opens the same start dialog; the account warms again from zero
  // as its next wave.
  const [rewarmFor, setRewarmFor] = useState<WarmedAccount | null>(null);
  // «Удалить» on a spam-blocked card opens the ordinary account delete dialog.
  const [deletingId, setDeletingId] = useState<string | null>(null);
  // «В нейрокомментинг» asks for the start mode first (item 2.4).
  const [handoffFor, setHandoffFor] = useState<string | null>(null);
  // The survival tile in the header opens its panel under the banners (stage 3).
  const [survivalOpen, setSurvivalOpen] = useState(false);
  const [channelToRemove, setChannelToRemove] = useState<string | null>(null);
  const accountFeedback = useTransientFeedback();
  const channelFeedback = useTransientFeedback();

  const { data, isPending, isError } = useQuery({
    ...warmingBoardQueryOptions(),
    refetchInterval: FALLBACK_POLL_MS,
  });

  const invalidate = () => {
    invalidateWarmingQueries(queryClient, 'all');
  };
  // Live status: warming events refresh the board and the affected cards' logs.
  useWarmingLiveRefresh();
  const start = useMutation(startWarmingMutation());
  const stop = useMutation(stopWarmingMutation());
  const addChannels = useMutation(addWarmingChannelsMutation());
  const removeChannel = useMutation(removeWarmingChannelMutation());
  const promote = useMutation(promoteToNeurocommentMutation());
  const rewarm = useMutation(rewarmAccountMutation());
  const keep = useMutation(keepSpamBlockedAccountMutation());
  const remove = useMutation(deleteAccountMutation());
  // Inline: "@SpamBot did not answer" opens the retry dialog rather than a toast;
  // runHandoff toasts every other refusal itself.
  const handoff = useMutation({ ...handoffToNeurocommentMutation(), meta: { inlineError: true } });
  const [spamRetryFor, setSpamRetryFor] = useState<{ id: string; mode: HandoffMode } | null>(null);

  const markBusy = (accountId: string, busy: boolean) => {
    setBusyIds((ids) => {
      const next = new Set(ids);
      if (busy) next.add(accountId);
      else next.delete(accountId);
      return next;
    });
  };
  // promote (graduate) takes the {account_id} body.
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

  // The hand-off refuses without an answer from @SpamBot. That refusal opens (or keeps
  // open) the retry dialog — its promise rejects so the dialog stays — while every
  // other outcome resolves, closing it.
  const runHandoff = (accountId: string, mode: HandoffMode) => {
    markBusy(accountId, true);
    return handoff
      .mutateAsync({ body: { account_id: accountId, start_mode: mode } })
      .then(
        () => {
          accountFeedback.mark(accountId, true);
        },
        (error: unknown) => {
          accountFeedback.mark(accountId, false);
          if (errorMessage(error) !== 'spam_check_unavailable') {
            toastError(mutationErrorText(error));
            return;
          }
          setSpamRetryFor({ id: accountId, mode });
          throw error;
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
  // (single-account callers ignore it). mutateAsync (not mutate): this shares the
  // `start`/`stop` hooks across every card and the pool, and mutate+onSettled let a
  // later call take an earlier one's ONE callback slot over — its busy mark was never
  // cleared and its feedback mark never appeared.
  const runOnAccount = (accountId: string, call: () => Promise<unknown>) => {
    markBusy(accountId, true);
    return call()
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
  const stopAccount = (accountId: string) =>
    runOnAccount(accountId, () => stop.mutateAsync({ body: { account_id: accountId } }));
  const startAccount = (accountId: string, choice: StartChoice) =>
    runOnAccount(accountId, () =>
      start.mutateAsync({ body: { account_id: accountId, ...choice } }),
    );
  const rewarmAccount = (accountId: string, choice: StartChoice) =>
    runOnAccount(accountId, () =>
      rewarm.mutateAsync({ body: { account_id: accountId, ...choice } }),
    );
  const keepAccount = (accountId: string) =>
    runOnAccount(accountId, () => keep.mutateAsync({ body: { account_id: accountId } }));
  const deleteAccount = (accountId: string, reason: DeleteReason | undefined) =>
    runOnAccount(accountId, () =>
      remove.mutateAsync({ path: { account_id: accountId }, query: { reason } }),
    );
  // A batch keeps the pool button disabled until its LAST call settles: isPending
  // tracks only the last-fired call and would re-enable it mid-batch.
  const runBatch = (calls: Promise<unknown>[]) => {
    setBulkBusy(true);
    void Promise.allSettled(calls).finally(() => {
      setBulkBusy(false);
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
  // A confirmed spam block waits for the operator just like an error does.
  const errors = [...idle, ...warming].filter(
    (a) => a.state === 'error' || a.state === 'spam_blocked',
  ).length;
  const poolOn = warming.length > 0;
  // With the readiness check switched off the engine starts a not-ready account, so
  // the button does too; the reasons stay on the row as a warning, not a block.
  const enforceReadiness = data.settings.enforce_readiness ?? true;
  // The pool starts what each card's own Start would: a not-ready account is left
  // out while the readiness check is on, and the dialog says how many.
  const poolIdle = idle.filter((a) => (a.readiness?.ready ?? false) || !enforceReadiness);
  const poolGeoMismatch = poolIdle.filter(needsGeoConsent).length;
  // Every card on the page whose phone/proxy pair still waits for a consent.
  const geoMismatches = [...idle, ...warming].filter(needsGeoConsent).length;
  const howtoSteps = HOWTO_STEPS.map(([step, mechanic]) => {
    const label = t(`warming.howto.steps.${step}`);
    const on = mechanic === null || (data.mechanics?.[mechanic] ?? true);
    return on ? label : t('warming.howto.off', { step: label });
  });

  return (
    <div className="tb-fadeup">
      <div className="mb-6 flex flex-wrap items-center justify-between gap-4">
        <h1 className="m-0 type-h1">{t('warming.titleFull')}</h1>
        <div className="flex w-full flex-wrap items-center justify-between gap-3 sm:w-auto sm:gap-4 lg:flex-nowrap">
          <StatGrid
            className="w-full sm:w-auto"
            stats={[
              { value: warming.length, label: t('warming.counter.warming'), tone: 'primary' },
              { value: idle.length, label: t('warming.counter.ready') },
              { value: errors, label: t('warming.counter.errors'), tone: 'danger' },
            ]}
          />
          <SurvivalTile
            className="w-full sm:w-auto"
            expanded={survivalOpen}
            controls={SURVIVAL_PANEL_ID}
            onToggle={() => {
              setSurvivalOpen((open) => !open);
            }}
          />
          <Button
            variant={poolOn ? 'neutral' : 'primary'}
            size="sm"
            disabled={
              bulkBusy || start.isPending || stop.isPending || (!poolOn && !poolIdle.length)
            }
            onClick={() => {
              if (poolOn) runBatch(warming.map((a) => stopAccount(a.account_id)));
              else setPoolStartOpen(true);
            }}
            className="gap-2"
          >
            {poolOn ? <Icon name="pause" size={14} /> : <Icon name="play" size={14} />}
            {poolOn ? t('warming.pool.stop') : t('warming.pool.start')}
          </Button>
        </div>
      </div>

      {/* Page banners: the fleet DM pause and unaccepted geo mismatches, then the
          survival panel the header tile opens. */}
      <DmPauseBanner pausedUntil={data.settings.dm_paused_until} />
      <GeoMismatchBanner count={geoMismatches} />
      {survivalOpen ? (
        <div className="mb-4">
          <SurvivalPanel id={SURVIVAL_PANEL_ID} />
        </div>
      ) : null}

      {/* `minmax(0,1fr)`, not a bare `1fr`: an `fr` track keeps an automatic minimum
          (index.css says the same of `.tb-subrow` in the row direction), so a track
          holding text the accounts wrote can be measured open past the viewport — one
          unwrappable line took it to 1227px and the page to scrollWidth 1607 against
          clientWidth 1024, a scroll the viewport-wide sticky header can't follow. The
          feed that proved it has since moved to the left column; the floor stays,
          because the board's own log prints the same kind of line. */}
      <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-[340px_minmax(0,1fr)]">
        <div className="flex min-w-0 flex-col gap-4">
          <Card className="p-4">
            <CardHeader
              className="mb-3"
              title={t('warming.ready.title')}
              badge={<Badge>{idle.length}</Badge>}
            />
            <div className="flex flex-col gap-2">
              {idle.length === 0 ? (
                <EmptyState>{t('warming.ready.empty')}</EmptyState>
              ) : (
                idle.map((account) => {
                  const trust = account.trust_score;
                  const tTone = trust != null ? trustTone(trust) : 'text-content-subtle';
                  const cc = account.phone_country?.toLowerCase() ?? null;
                  const pc = account.proxy_country?.toLowerCase() ?? null;
                  const ptype = account.proxy_type;
                  const ready = account.readiness?.ready ?? false;
                  const startable = ready || !enforceReadiness;
                  const blockers = (account.readiness?.reasons ?? [])
                    .map((reason) => {
                      const key = readinessReasonKey(reason);
                      return key ? t(key) : reason;
                    })
                    .join(', ');
                  const blockersId = `warm-blockers-${account.account_id}`;
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
                      className="rounded-md border border-line bg-surface-card px-3 py-3"
                    >
                      <div className="flex items-center gap-3">
                        <AccountAvatar
                          account={account}
                          className="size-icon shrink-0 rounded-full"
                          fallbackClassName="text-body font-medium bg-info-tint text-info-strong"
                        />
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-1">
                            <span className="truncate type-h3">{name}</span>
                            {showPhone ? null : flag}
                          </div>
                          {showPhone ? (
                            <div className="flex items-center gap-1">
                              <span className="truncate type-small">{account.phone}</span>
                              {flag}
                            </div>
                          ) : null}
                          <div className="mt-1 flex flex-wrap items-center gap-2">
                            <Icon name="shield-check" size={14} className={`shrink-0 ${tTone}`} />
                            <span className={`text-small font-medium ${tTone}`}>
                              {trust ?? '—'}
                            </span>
                            {ptype ? (
                              <>
                                <span className="type-small">·</span>
                                {pc ? (
                                  <span
                                    className={`fi fi-${pc} h-flag w-flag shrink-0 rounded-[2px] shadow-ring`}
                                  />
                                ) : null}
                                <span className="type-small">{proxyTypeLabel(ptype)}</span>
                              </>
                            ) : null}
                            {account.geo_accepted ? (
                              <Badge>{t('warming.geo.accepted')}</Badge>
                            ) : null}
                            <SpamBlockBadge blockedAt={account.spam_blocked_at} />
                          </div>
                        </div>
                        <Button
                          type="button"
                          variant="primary"
                          size="lg"
                          disabled={!startable || busyIds.has(account.account_id)}
                          title={ready ? undefined : blockers}
                          aria-describedby={!ready && blockers ? blockersId : undefined}
                          onClick={() => {
                            setWarmDaysFor(account);
                          }}
                          className={
                            startable
                              ? undefined
                              : 'cursor-not-allowed bg-canvas text-content-subtle hover:bg-canvas'
                          }
                        >
                          {startable ? t('warming.ready.start') : t('warming.ready.unavailable')}
                        </Button>
                      </div>
                      {/* Spelled out, not only in the button's `title`: a disabled button
                          never shows its tooltip to keyboard or touch. Full width under the
                          row, so a long list does not squeeze the name beside it. */}
                      {!ready && blockers ? (
                        <div id={blockersId} className="mt-2 type-small text-warning-deep">
                          {blockers}
                        </div>
                      ) : null}
                    </div>
                  );
                })
              )}
            </div>
          </Card>

          {/* Второй сверху, а не под складными карточками: живая лента не должна
              отодвигать «Прогреть» вниз, но и стоять ниже закрытого чрома ей нечего. */}
          <DialogueFeed />

          <CollapsibleCard
            wrapperClassName="rounded-md border border-line bg-surface-card"
            title={t('warming.channels.title')}
            label={t('warming.channels.title')}
          >
            <div className="mb-3 type-small">{t('warming.channels.hint')}</div>
            <ChannelLanguageShortages shortages={data.language_shortages ?? []} />
            <div className="flex flex-wrap gap-2">
              {channels.map((channel) => (
                <WarmingChannelPill
                  key={channel.channel}
                  channel={channel}
                  feedback={channelFeedback.feedback[channel.channel]}
                  onRemove={setChannelToRemove}
                />
              ))}
              {addingChannel ? (
                <InlineChipEditor
                  value={channelInput}
                  onChange={setChannelInput}
                  onConfirm={addChannel}
                  onCancel={cancelAddChannel}
                  placeholder={t('warming.channels.placeholderSingle')}
                  inputLabel={t('warming.channels.placeholderSingle')}
                  confirmLabel={t('warming.channels.add')}
                  cancelLabel={t('warming.channels.cancel')}
                />
              ) : (
                <ChipAddButton
                  onClick={() => {
                    setAddingChannel(true);
                  }}
                >
                  {t('warming.channels.addPill')}
                </ChipAddButton>
              )}
            </div>
            <ChannelPoolFill
              defaultLanguage={data.language_shortages?.[0]?.language}
              onPoolChanged={invalidate}
            />
          </CollapsibleCard>

          <CollapsibleCard
            // Auto-expand once there are warmed accounts so a just-graduated
            // account is visible where it landed (the key re-inits defaultOpen
            // when the pool crosses empty↔non-empty).
            key={warmed.length > 0 ? 'warmed-has' : 'warmed-none'}
            defaultOpen={warmed.length > 0}
            label={t('warming.warmed.title')}
            // Плитка `success` красит галочку `success-deep`, а не базовым зелёным: базовый
            // на своём тоне мерит 2.97:1, а 1.4.11 просит 3:1 у графики, которая несёт
            // смысл. `success-deep` даёт 5.85:1 — см. `contrast.test.ts`.
            icon={<Icon name="check" size={16} />}
            tone="success"
            title={t('warming.warmed.title')}
            badge={<Badge tone="success">{warmed.length}</Badge>}
          >
            <div className="flex flex-col gap-3">
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
                  <div key={acc.account_id} className="rounded-md border border-line p-4">
                    <div className="flex items-start gap-3">
                      <AccountAvatar
                        account={acc}
                        className="size-tile shrink-0 rounded-full ring-2 ring-success"
                        fallbackClassName="text-small font-medium bg-info-tint text-info-strong"
                      />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-1">
                          <span className="truncate type-h3">{name}</span>
                          {showPhone ? null : flag}
                        </div>
                        {showPhone ? (
                          <div className="flex items-center gap-1">
                            <span className="truncate type-small">{acc.phone}</span>
                            {flag}
                          </div>
                        ) : null}
                        {acc.survival || acc.spam_blocked_at || (acc.wave ?? 1) > 1 ? (
                          <div className="mt-2 flex flex-wrap items-center gap-2">
                            {acc.survival ? <SurvivalBadge survival={acc.survival} /> : null}
                            <SpamBlockBadge blockedAt={acc.spam_blocked_at} />
                            <WaveChip
                              accountId={acc.account_id}
                              name={name}
                              wave={acc.wave}
                              startedAt={acc.started_at}
                            />
                          </div>
                        ) : null}
                        <div className="mt-2 flex items-center gap-2">
                          {acc.proxy_country ? (
                            <span
                              className={`fi fi-${acc.proxy_country.toLowerCase()} h-flag w-flag rounded-[2px]`}
                            />
                          ) : null}
                          <span className="type-small">
                            {acc.proxy_type ? proxyTypeLabel(acc.proxy_type) : '—'}
                          </span>
                        </div>
                      </div>
                      {/* The other accent marker (see LaunchCard's LIVE): `micro`/`bold`
                          with letter-spacing because it is emphasis on a finished account,
                          not a neutral state. Deliberately outside the status-pill family. */}
                      <span className="inline-flex items-center gap-1 rounded-full bg-success-tint px-3 py-1 text-small font-medium text-success-deep">
                        <Icon name="check" size={10} className="stroke-success-deep" />
                        {t('warming.warmed.badge')}
                      </span>
                    </div>
                    <div className="mt-4 flex items-center rounded-md bg-surface px-4 py-3">
                      <div className="flex-1">
                        <div className="type-small">{t('warming.warmed.days')}</div>
                        <div className="text-body font-medium">
                          {t('warming.warmed.daysValue', {
                            days: acc.warmed_days ?? 0,
                            target: acc.target_days,
                          })}
                        </div>
                      </div>
                      <span className="h-compact border-l border-line" />
                      <div className="flex-1 pl-4">
                        <div className="type-small">{t('warming.warmed.trust')}</div>
                        <div className="text-body font-medium text-success-deep">
                          {acc.trust_score ?? '—'}
                        </div>
                      </div>
                    </div>
                    <div className="mt-4 flex items-center gap-3">
                      <Button
                        variant="neutral"
                        disabled={busyIds.has(acc.account_id)}
                        onClick={() => {
                          setHandoffFor(acc.account_id);
                        }}
                        className="flex-1 shrink gap-2"
                      >
                        {t('warming.warmed.toNeuro')}
                        <Icon name="arrow-right" size={14} />
                      </Button>
                      <FeedbackMark result={accountFeedback.feedback[acc.account_id]} />
                      <button
                        type="button"
                        title={t('warming.warmed.backToWarm')}
                        aria-label={t('warming.warmed.backToWarm')}
                        disabled={busyIds.has(acc.account_id)}
                        onClick={() => {
                          setRewarmFor(acc);
                        }}
                        className="flex size-touch shrink-0 items-center justify-center rounded-full border border-line bg-surface-card text-content-muted disabled:opacity-50"
                      >
                        <svg
                          aria-hidden
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

          <HowItWorksCard
            title={t('warming.howto.title')}
            hint={t('warming.howto.hint')}
            columns={2}
            steps={howtoSteps}
          />
        </div>

        <div className="flex min-w-0 flex-col gap-4">
          <WarmingBoard
            warming={warming}
            onStop={(id) => {
              void stopAccount(id);
            }}
            onPromote={(id) => {
              runGraduation(promote, id);
            }}
            onDelete={setDeletingId}
            onKeep={(id) => {
              void keepAccount(id);
            }}
            busyIds={busyIds}
            feedback={accountFeedback.feedback}
            logLimit={data.card_log_limit}
            channelLabels={channelLabels}
            defaultTargetDays={data.default_target_days}
          />

          {/* Настройки прогрева ОБЩИЕ, а не по аккаунту, поэтому карточка одна:
              шестерёнка на карточке аккаунта обещала «настройку этого аккаунта» и
              писала всем (#194-#196 оставили те же четыре поля глобальными).
              Стоит она ПОД доской, в её же колонке, а не во всю ширину под сеткой:
              полосой она оставляла под левой колонкой пустое поле в рост складных
              карточек, а тумблерам левая колонка мала — 340px не держат и двух
              столбцов. */}
          <ActionTuningCard />
          {/* Темп всего прогрева (5.2) — тоже общий, поэтому рядом с тумблерами. */}
          <PaceCard />
        </div>
      </div>

      {warmDaysFor ? (
        <WarmDaysModal
          accountId={warmDaysFor.account_id}
          phone={warmDaysFor.phone ?? warmDaysFor.label ?? warmDaysFor.account_id}
          defaultDays={data.default_target_days}
          cadence={data.cadence_preview}
          devices={warmDaysFor.devices}
          onClose={() => {
            setWarmDaysFor(null);
          }}
          geo={geoOf(warmDaysFor)}
          onConfirm={(choice) => {
            void startAccount(warmDaysFor.account_id, startChoice(choice));
          }}
        />
      ) : null}

      {rewarmFor ? (
        <WarmDaysModal
          accountId={rewarmFor.account_id}
          phone={rewarmFor.phone ?? rewarmFor.label}
          nextWave={(rewarmFor.wave ?? 1) + 1}
          defaultDays={data.default_target_days}
          cadence={data.cadence_preview}
          geo={geoOf(rewarmFor)}
          devices={rewarmFor.devices}
          onClose={() => {
            setRewarmFor(null);
          }}
          onConfirm={(choice) => {
            void rewarmAccount(rewarmFor.account_id, startChoice(choice));
          }}
        />
      ) : null}

      {deletingId ? (
        <DeleteAccountModal
          accountId={deletingId}
          phone={
            warming.find((a) => a.account_id === deletingId)?.phone ??
            warming.find((a) => a.account_id === deletingId)?.label ??
            deletingId
          }
          onClose={() => {
            setDeletingId(null);
          }}
          onConfirm={(reason) => {
            void deleteAccount(deletingId, reason);
          }}
        />
      ) : null}

      {handoffFor ? (
        <HandoffModal
          plan={data.soft_start}
          onClose={() => {
            setHandoffFor(null);
          }}
          onConfirm={(mode) => {
            runHandoff(handoffFor, mode).catch(() => undefined);
          }}
        />
      ) : null}

      {poolStartOpen ? (
        <WarmDaysModal
          poolAccountIds={poolIdle.map((a) => a.account_id)}
          poolSkipped={idle.length - poolIdle.length}
          poolGeoMismatch={poolGeoMismatch}
          defaultDays={data.default_target_days}
          cadence={data.cadence_preview}
          onClose={() => {
            setPoolStartOpen(false);
          }}
          onConfirm={(choice) => {
            // Same length, cadence and window for all; the backend draws each account
            // its own moment inside the window. «Только совпадающие» leaves out every
            // account whose geo still waits for a consent.
            const request = { ...startChoice(choice), spread_start: true };
            const targets = choice.onlyGeoMatching
              ? poolIdle.filter((a) => !needsGeoConsent(a))
              : poolIdle;
            // Each account warms through its primary device: a phone-only account has no
            // desktop sign-in to fall back on.
            runBatch(
              targets.map((a) =>
                startAccount(a.account_id, { ...request, device_mode: a.devices?.[0] }),
              ),
            );
          }}
        />
      ) : null}

      {spamRetryFor ? (
        <SpamRetryModal
          onClose={() => {
            setSpamRetryFor(null);
          }}
          onRetry={() => runHandoff(spamRetryFor.id, spamRetryFor.mode)}
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
