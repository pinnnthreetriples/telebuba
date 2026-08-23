import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { accountDisplayName, allAccountsQueryOptions } from '@/entities/account';
import {
  assignCampaignAccountMutation,
  campaignChallengesQueryOptions,
  CampaignDeleteModal,
  CampaignPromptModal,
  campaignsQueryOptions,
  checkCampaignChannelBansMutation,
  clearNeurocommentListenerMutation,
  createCampaignMutation,
  CreateCampaignModal,
  deleteCampaignMutation,
  linkCampaignChannelMutation,
  ListenerEditModal,
  NeuroAccountsModal,
  neurocommentBoardQueryOptions,
  neurocommentRuntimeQueryOptions,
  removeCampaignAccountMutation,
  removeCampaignChannelMutation,
  setCampaignAccountChannelMutation,
  setCampaignSolverMutation,
  setCampaignStatusMutation,
  setNeurocommentListenerMutation,
  startNeurocommentMutation,
  stopNeurocommentMutation,
  updateCampaignPromptMutation,
} from '@/entities/campaign';
import { clearLogsMutation, logCountQueryOptions, logsQueryOptions } from '@/entities/log';
import { ChannelDiscoveryButton } from '@/features/channel-discovery';
import { warmedAccountsQueryOptions, warmingBoardQueryOptions } from '@/entities/warming';
import type { NeurocommentCampaign } from '@/shared/api';
import { logSeverity, useLogEventStream, useTransientFeedback } from '@/shared/lib';
import { ConfirmModal, toastError } from '@/shared/ui';
import { NeurocommentBoard } from '@/widgets/neurocomment-board';

import { ActivityLogCard } from './ActivityLogCard';
import { CommentHistoryModal } from './CommentHistoryModal';
import { CampaignsCard } from './CampaignsCard';
import { CaptchaSolverCard } from './CaptchaSolverCard';
import { HowItWorksCard } from './HowItWorksCard';
import { IdleBanner } from './IdleBanner';
import { ListenerCard } from './ListenerCard';
import { PipelineCard } from './PipelineCard';

// SSE drives live runtime/board updates (onboarding now emits a transient bus
// frame per progress step, so the board refreshes live during it too); this poll
// is just the fallback net.
const FALLBACK_POLL_MS = 30000;
// Raised from 40 when the gateway started stamping its rows `neurocomment_telegram_*`:
// a comment attempt now writes two rows under this prefix (the transport outcome plus the
// service's classified one), and joins and solver clicks add gateway rows of their own, so
// row density at least doubled. 80 keeps the window — which the Errors tile is computed
// over — at least as deep as the 40 service-only rows it used to hold. Well under the
// `le=1000` ceiling on `LogFilter.limit` (schemas/logs.py).
const NEURO_LOG_LIMIT = 80;
const CAPTCHA_QUEUE_LIMIT = 20;

function initials(value: string): string {
  return value.replace(/\D/g, '').slice(-2) || value.slice(0, 2).toUpperCase();
}

// The query-key `_id`s this page owns. The shared SSE stream fires on every log
// row across the whole app, so the page only refetches its own queries instead
// of blowing away the entire cache (accounts, warming, settings, …).
const NEURO_QUERY_IDS = new Set([
  'listCampaigns',
  'getNeurocommentBoard',
  'getNeurocommentRuntime',
  'listAccounts',
  'listCampaignChallenges',
  'listLogs',
]);

// True when a failed start is the backend's warming-listener rejection, so the UI
// can show the warming banner rather than swallowing it. The generated client
// throws the parsed error envelope ({ error: { code, message } }) on non-2xx, and
// a 409 maps to code "conflict"; any other error (network, validation) is left alone.
function isWarmingConflict(error: unknown): boolean {
  return (
    typeof error === 'object' &&
    error !== null &&
    (error as { error?: { code?: string } }).error?.code === 'conflict'
  );
}

export function NeurocommentPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  // The page's ONE refresh scope — narrowed to the query keys above (finding
  // #11). Used by the SSE stream and by every mutation on this page, because
  // they need exactly the same thing: none of them touches an account row, a
  // proxy, the warming board or the settings, and a bare invalidateQueries()
  // refetched all of those plus every open profile snapshot.
  const invalidateNeuro = () => {
    void queryClient.invalidateQueries({
      predicate: (query) => {
        const id = (query.queryKey[0] as { _id?: string } | undefined)?._id;
        return id !== undefined && NEURO_QUERY_IDS.has(id);
      },
    });
  };
  useLogEventStream(invalidateNeuro);
  // One useMutation is ONE callback slot. A second .mutate() before the first
  // settles takes that slot over, so the first call's onSettled never runs — and
  // most buttons below are PER ROW on a shared hook (two captcha rows, two
  // accounts in the modal, two campaign run/pause toggles), which lost the first
  // row's feedback mark and its refresh. mutateAsync hands back one promise per
  // call, so what follows a call cannot be taken over by the next one.
  const afterSettle = (call: Promise<unknown>, mark?: (ok: boolean) => void) => {
    void call
      .then(
        () => mark?.(true),
        () => mark?.(false),
      )
      .finally(invalidateNeuro);
  };

  const [selected, setSelected] = useState<string | null>(null);
  const [listener, setListener] = useState('');
  const [listenerOpen, setListenerOpen] = useState(false);
  // Gear-driven action-row reveals (click fallback for hover; finding #6).
  const [listenerActionsOpen, setListenerActionsOpen] = useState(false);
  const [openCampaignActions, setOpenCampaignActions] = useState<string | null>(null);
  const [channelInput, setChannelInput] = useState('');
  const [addingChannel, setAddingChannel] = useState(false);
  const [channelToRemove, setChannelToRemove] = useState<string | null>(null);
  const [confirmClearLogs, setConfirmClearLogs] = useState(false);
  const channelFeedback = useTransientFeedback();
  const accountFeedback = useTransientFeedback();
  // "Проверить каналы" verdicts: banned channels stay red until the next check;
  // healthy ones flash green for 5s then revert (auto-clearing transient feedback).
  const [bannedChannels, setBannedChannels] = useState<string[]>([]);
  const okCheck = useTransientFeedback(5000);

  // Modal open state.
  const [showAccounts, setShowAccounts] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [showListenerEdit, setShowListenerEdit] = useState(false);
  const [promptFor, setPromptFor] = useState<NeurocommentCampaign | null>(null);
  const [deleteFor, setDeleteFor] = useState<NeurocommentCampaign | null>(null);
  // Set only when the backend rejects a start it thought was fine — the
  // client-known case is derived below (listenerIsWarming), not stored.
  const [startRejectedWarming, setStartRejectedWarming] = useState(false);

  const campaigns = useQuery(campaignsQueryOptions());
  const accounts = useQuery(allAccountsQueryOptions());
  // Only graduated accounts ("Прогреты" pool) are eligible for neurocommenting;
  // the idle counter and the assignable candidates come from here, not the full
  // account list.
  const warmed = useQuery({
    ...warmedAccountsQueryOptions(),
    refetchInterval: FALLBACK_POLL_MS,
  });
  // Accounts actively warming must not double as the neurocomment listener
  // (the two runtimes are mutually exclusive per account). The board's "warming"
  // bucket is exactly the backend's ``is_warming`` set, so we reuse it to both
  // hide those accounts from the picker and block a stale/persisted pick. Fetched
  // once (no poll) — /warming/board is the app's heaviest read-model and the
  // warming set changes rarely; the authoritative HTTP 409 backstops any staleness.
  const warmingBoard = useQuery(warmingBoardQueryOptions());
  const runtime = useQuery({
    ...neurocommentRuntimeQueryOptions(),
    refetchInterval: FALLBACK_POLL_MS,
  });

  const campaignList = campaigns.data?.campaigns ?? [];
  const campaignId = selected ?? campaignList[0]?.campaign_id ?? null;
  const activeCampaign = campaignList.find((c) => c.campaign_id === campaignId) ?? null;

  const onboarding = runtime.data?.onboarding ?? false;
  const board = useQuery({
    ...neurocommentBoardQueryOptions({ path: { campaign_id: campaignId ?? '' } }),
    refetchInterval: FALLBACK_POLL_MS,
    enabled: campaignId !== null,
  });
  // Real neurocomment activity feed (live-invalidated by the SSE stream above).
  const neuroLog = useQuery({
    ...logsQueryOptions({ query: { event_prefix: 'neurocomment', limit: NEURO_LOG_LIMIT } }),
    refetchInterval: FALLBACK_POLL_MS,
  });
  // How many rows the clear button would actually delete. Asked only while the
  // confirmation is open: the log panel shows one page, so its length is no guide to the
  // size of a purge that spans the whole retention window, and an operator who cleared on
  // that impression once lost a month of history without noticing.
  const logCount = useQuery({
    ...logCountQueryOptions({ query: { event_prefix: 'neurocomment' } }),
    enabled: confirmClearLogs,
  });
  // Real captcha queue — unsolved bot-challenges across the campaign's channels.
  const challenges = useQuery({
    ...campaignChallengesQueryOptions({
      path: { campaign_id: campaignId ?? '' },
      query: { limit: CAPTCHA_QUEUE_LIMIT },
    }),
    refetchInterval: FALLBACK_POLL_MS,
    enabled: campaignId !== null,
  });
  // ONE gateway row is dropped, and only because `_classify.py` writes
  // `neurocomment_onboard_join_by_request` for the very same join — translated, and
  // carrying the attempt ratio the raw row has no way to show. Every other `*_by_request`
  // row stays: the listener's own `join_channel` (`services/neurocomment/_join.py`) has no
  // service twin on this page, so that row is the only evidence the request went out.
  // They read properly now — `by_request` was simply missing from `TG_STATUS_SUFFIXES`.
  // Known gap, accepted: `_onboard_pair.py` runs `ensure_current()` between the gateway
  // call and the classify that twins it, and its `CancelledError` is a BaseException no
  // handler on that path catches — so a Stop landing in that window hides a request that
  // really was sent. The next pass re-sends and logs it, because the same window also
  // skips `stamp_join_request`.
  const logLines = (neuroLog.data?.items ?? []).filter(
    (line) => line.event !== 'neurocomment_telegram_join_discussion_group_by_request',
  );
  const captchaQueue = challenges.data?.rows ?? [];
  // The captcha solver toggle reflects the campaign's per-campaign solver_enabled
  // override (null/true = on, only off when explicitly disabled).
  const solverEnabled = board.data?.solver_enabled !== false;

  const createCampaign = useMutation(createCampaignMutation());
  const linkChannel = useMutation(linkCampaignChannelMutation());
  const assignAccount = useMutation(assignCampaignAccountMutation());
  const start = useMutation(startNeurocommentMutation());
  const stop = useMutation(stopNeurocommentMutation());
  const setSolver = useMutation(setCampaignSolverMutation());
  const setStatus = useMutation(setCampaignStatusMutation());
  const clearListener = useMutation(clearNeurocommentListenerMutation());
  const saveListener = useMutation(setNeurocommentListenerMutation());
  const deleteCampaign = useMutation(deleteCampaignMutation());
  const removeChannel = useMutation(removeCampaignChannelMutation());
  const removeAccount = useMutation(removeCampaignAccountMutation());
  const setAccountChannel = useMutation(setCampaignAccountChannelMutation());
  const updatePrompt = useMutation(updateCampaignPromptMutation());
  const clearLogs = useMutation(clearLogsMutation());
  const checkBans = useMutation(checkCampaignChannelBansMutation());

  const accountOptions = accounts.data?.items ?? [];
  const warmingIds = new Set((warmingBoard.data?.warming ?? []).map((a) => a.account_id));
  // Listener candidates exclude accounts that are actively warming.
  const listenerOptions = accountOptions.filter((a) => !warmingIds.has(a.account_id));
  // The hand-off pool — what neurocomment may actually put to work. Graduated
  // accounts stay on the warming page's warmed card until the operator presses
  // «в нейрокомментинг» there (nc_handed_off); only then do they count/appear here.
  const warmedAccounts = (warmed.data?.accounts ?? []).filter((a) => a.nc_handed_off);
  const running = runtime.data?.running ?? false;
  // The listener id survives reload/pause: it comes from the persisted runtime
  // status (returned even when paused) and only falls back to a fresh local pick.
  const listenerId = runtime.data?.listener_account_id ?? listener;
  // The picker already hides warming accounts; this derived flag additionally
  // catches a persisted/stale listener that is warming. showWarmingBlock also
  // lights up when the backend rejects a start the client thought was fine.
  const listenerIsWarming = listenerId !== '' && warmingIds.has(listenerId);
  const showWarmingBlock = listenerIsWarming || startRejectedWarming;
  const boardAccounts = board.data?.accounts ?? [];
  const boardChannels = board.data?.channels ?? [];
  const boardChannelNames = boardChannels.map((c) => c.channel);

  // Resolve an account's Telegram display name from the full account list (the
  // only place carrying first/last name), so the captcha queue and neuro-accounts
  // modal show the same label as the Accounts table — not the raw phone/id that
  // the neurocomment board card exposes.
  const displayNameById = (accountId: string, fallback: string): string => {
    const found = accountOptions.find((a) => a.account_id === accountId);
    return found ? accountDisplayName(found) : fallback;
  };
  const accountLabel = (accountId: string): string => displayNameById(accountId, accountId);

  // Ids already on the selected campaign's board (linked accounts).
  const linkedIds = new Set(boardAccounts.map((a) => a.account_id));

  // Rows for the neuro-accounts modal: the campaign's linked accounts (with a
  // channel-pin dropdown) PLUS every graduated ("Прогреты") account not yet
  // linked (linked: false → shows the "assign" button so an idle warmed account
  // can actually be added).
  const neuroAccountRows = [
    ...boardAccounts.map((a) => ({
      account_id: a.account_id,
      name: displayNameById(a.account_id, a.label),
      linked: true,
      pinned_channels: a.pinned_channels ?? [],
      // Per-pair bans (#30) are permanent and invisible on the channel row while a
      // sibling account still posts there, so the modal names them per account.
      banned_channels: (a.readiness ?? []).filter((r) => r.banned).map((r) => r.channel),
    })),
    ...warmedAccounts
      .filter((a) => !linkedIds.has(a.account_id))
      .map((a) => ({
        account_id: a.account_id,
        name: accountDisplayName(a),
        linked: false,
        pinned_channels: [],
      })),
  ];

  // Errors stat: failure-severity rows in today's loaded neuro activity log
  // (genuine gen/publish failures — skips and busy-account misses don't count).
  //
  // `<domain>_telegram_*` gateway rows are excluded from the COUNT but still shown (and
  // still red) in the list. Since the gateway started stamping its rows with the calling
  // domain they reach this feed, and for a comment or a channel join the service layer
  // writes its own classified row for the same outcome (`_generate.py` / `_join.py`) —
  // counting both would double every failure, and `_generate.py` deliberately classifies
  // some of them as amber (`neurocomment_post_access_lost` / `_post_gated` / `_post_cooldown`).
  // The tile measures domain failures; the list shows raw transport truth.
  //
  // Known residual, deliberately not fixed here: two gateway sites have NO service twin —
  // `_onboard_pair.py` (JoinDiscussionGroup) writes nothing on 4 of `_classify.py`'s 6
  // branches (invite-request, ban, gate, hard-failure tail), and `challenge.py`
  // (ClickButton / PostComment) has no `log_event` at all. Their failures stay visible and
  // red in the list but are not counted in this tile. Adding service rows on those paths is
  // the right fix and is out of scope for this change.
  const errorCount = logLines.filter(
    (line) => !line.event.includes('_telegram_') && logSeverity(line) === 'error',
  ).length;

  // Idle = graduated ("Прогреты") accounts not yet linked to the selected
  // campaign's board. Only warmed accounts count — a still-warming or un-graduated
  // account is not "idle neurocomment work".
  const idleCount = warmedAccounts.filter((a) => !linkedIds.has(a.account_id)).length;

  const stats: { label: string; value: number; color: string }[] = [
    { label: t('neurocomment.stat.campaigns'), value: campaignList.length, color: '#0b0b0c' },
    {
      label: t('neurocomment.stat.channels'),
      value: runtime.data?.active_channels ?? boardChannels.length,
      color: '#0066ff',
    },
    { label: t('neurocomment.stat.accounts'), value: boardAccounts.length, color: '#0b0b0c' },
    {
      label: t('neurocomment.stat.comments'),
      value: boardAccounts.reduce((sum, a) => sum + a.comments_today, 0),
      color: '#12a150',
    },
    // Deleted is a subset of comments, so it sums the SAME rows over the SAME cards —
    // both tiles read the account's 24h window. Summing the channels' `deleted_recent`
    // instead would drift both ways: an unlinked channel takes its deletions off the board
    // while its comments stay on the account, and an unlinked account does the reverse.
    // It also counts a different SET: this tile and the cards count deletions among
    // `posted` comments, while the channel chips in `CampaignsCard` count them among
    // DELIVERED ones (any row carrying a message id), so a comment mis-classified `failed`
    // mid-send shows on the chip only. That is deliberate on the chip's side — it is the
    // number that has to explain a channel back-off — and it means a channel may
    // legitimately read `deleted_recent` higher than its own `posted` count. Do not
    // "reconcile" them by narrowing the chip; the back-off reads its own scan set either
    // way. The board row's own chip is a third thing again: that one is per-ACCOUNT.
    {
      label: t('neurocomment.stat.deleted'),
      value: boardAccounts.reduce((sum, a) => sum + (a.deleted_today ?? 0), 0),
      color: '#c0473f',
    },
    // The design's red "ошибок" odometer (#E5372A): today's error-level events.
    { label: t('neurocomment.stat.errors'), value: errorCount, color: '#e5372a' },
  ];

  const activeCampaignCount = campaignList.filter((c) => c.status === 'active').length;

  // Start the listener, surfacing the authoritative backend rejection: if the
  // account began warming after the picker was populated (stale board), the client
  // pre-check misses it and the server returns 409 — reflect that in the banner.
  const startListener = (id: string) => {
    start.mutate(
      { body: { listener_account_id: id } },
      {
        onSuccess: () => {
          setStartRejectedWarming(false);
        },
        onError: (error) => {
          setStartRejectedWarming(isWarmingConflict(error));
        },
        onSettled: invalidateNeuro,
      },
    );
  };

  // Both doors onto the listener account — the card's picker and the edit modal's
  // "Сохранить" — go through here. A running engine is re-pointed live by /start; a
  // stopped one still has to persist the pick, which is what used to be dropped.
  const pickListener = (id: string) => {
    setStartRejectedWarming(false);
    if (running) {
      setListener(id);
      startListener(id);
      return;
    }
    // The local id is set only once the write lands: `listenerId` falls back to it when
    // nothing is persisted, so an optimistic set would paint an unsaved account as the
    // listener — the same lie this whole path exists to stop telling.
    afterSettle(saveListener.mutateAsync({ body: { listener_account_id: id } }), (ok) => {
      if (ok) {
        setListener(id);
      }
    });
  };

  // GLOBAL listener start/stop (the whole engine). Kept distinct from the
  // per-campaign run/pause below.
  const toggleRuntime = () => {
    if (running) {
      stop.mutate({}, { onSettled: invalidateNeuro });
    } else if (listenerId && !warmingIds.has(listenerId)) {
      startListener(listenerId);
    }
    // A warming listenerId is not started; showWarmingBlock already renders the banner.
  };

  // Per-campaign run/pause (finding #2): flips campaign.status via setCampaignStatus.
  // The engine skips paused campaigns; this never touches the global engine.
  const toggleCampaignStatus = (campaign: NeurocommentCampaign) => {
    const next = campaign.status === 'active' ? 'paused' : 'active';
    afterSettle(
      setStatus.mutateAsync({
        path: { campaign_id: campaign.campaign_id },
        body: { status: next },
      }),
    );
  };

  // Remove the listener entirely (finding #4) — distinct from pausing (stop).
  const removeListener = () => {
    setListener('');
    clearListener.mutate({}, { onSettled: invalidateNeuro });
  };

  const addChannel = () => {
    const value = channelInput.trim();
    if (!value || campaignId === null) return;
    afterSettle(
      linkChannel.mutateAsync({ path: { campaign_id: campaignId }, body: { channel: value } }),
      (ok) => {
        setChannelInput('');
        setAddingChannel(false);
        channelFeedback.mark(value, ok);
      },
    );
  };

  const confirmRemoveChannel = () => {
    if (!channelToRemove || campaignId === null) return;
    const channel = channelToRemove;
    // The modal closes on this tick, so a second pill's removal is one click away
    // while this one is still in flight — and it would take this call's ONE
    // callback slot, dropping this channel's mark and refresh.
    setChannelToRemove(null);
    afterSettle(
      removeChannel.mutateAsync({ path: { campaign_id: campaignId }, body: { channel } }),
      (ok) => {
        channelFeedback.mark(channel, ok);
      },
    );
  };

  // Stale red from another campaign must not linger after switching.
  useEffect(() => {
    setBannedChannels([]);
  }, [campaignId]);

  const checkChannels = () => {
    if (campaignId === null) return;
    checkBans.mutate(
      { path: { campaign_id: campaignId } },
      {
        onSuccess: (data) => {
          const items = data.items ?? [];
          setBannedChannels(
            items.filter((item) => item.status === 'banned').map((item) => item.channel),
          );
          for (const item of items) {
            if (item.status === 'ok') okCheck.mark(item.channel, true);
          }
        },
        onError: () => {
          toastError(t('neurocomment.channels.checkFailed'));
        },
      },
    );
  };

  // Per-chip colour: banned (persistent red) merged with the transient green marks.
  const channelCheckStatus: Record<string, 'banned' | 'ok'> = {};
  for (const channel of bannedChannels) channelCheckStatus[channel] = 'banned';
  for (const channel of Object.keys(okCheck.feedback)) channelCheckStatus[channel] = 'ok';

  return (
    <div className="tb-fadeup">
      <h1 className="m-0 mb-[18px] text-[22px] font-bold tracking-[-0.02em]">
        {t('neurocomment.title')}
      </h1>

      {/* The col-start pinning must stay `lg:`-scoped: unprefixed it would make the
          one-column grid sprout an implicit second column and sit both children side
          by side. Below `lg`, DOM order puts the board before the config rail. */}
      {/* `minmax(0,1fr)`, not a bare `1fr`, for the reason index.css gives for
          `.tb-subrow` in the row direction: an `fr` track keeps an automatic minimum.
          The board's table pinned it open (measured 882px against a 605px share) —
          `overflow-x-auto` on its card does not stop min-content propagating — and the
          page picked up a horizontal scroll the viewport-wide sticky header can't follow,
          which is every card hanging out past the top bar on the right. */}
      <div className="grid items-start gap-4 lg:grid-cols-[340px_minmax(0,1fr)]">
        {/* RIGHT column */}
        <div className="flex flex-col gap-4 lg:col-start-2 lg:row-start-1">
          <PipelineCard
            running={running}
            canStart={Boolean(listenerId)}
            stats={stats}
            events={logLines}
            onToggle={toggleRuntime}
          />

          {board.data ? (
            <NeurocommentBoard
              board={board.data}
              accountsCount={boardAccounts.length}
              displayName={displayNameById}
              onboarding={onboarding}
              onOpenAccounts={() => {
                setShowAccounts(true);
              }}
              onOpenHistory={() => {
                setShowHistory(true);
              }}
            />
          ) : null}

          <ActivityLogCard
            logLines={logLines}
            accountName={accountLabel}
            onClear={() => {
              setConfirmClearLogs(true);
            }}
          />
        </div>

        {/* LEFT column */}
        <div className="flex flex-col gap-4 lg:col-start-1 lg:row-start-1">
          {idleCount > 0 ? (
            <IdleBanner
              count={idleCount}
              onOpen={() => {
                setShowAccounts(true);
              }}
            />
          ) : null}

          <ListenerCard
            listenerId={listenerId}
            running={running}
            activeCampaignCount={activeCampaignCount}
            // The same number the «Каналов» tile shows, so the plaque and the tile cannot
            // disagree. `running` is already false until the runtime read lands, so the 0
            // fallback here can never paint a live listener as idle.
            activeChannelCount={runtime.data?.active_channels ?? 0}
            unwatchedChannels={runtime.data?.unwatched_channels ?? []}
            listenerActionsOpen={listenerActionsOpen}
            onToggleActions={() => {
              setListenerActionsOpen((v) => !v);
            }}
            onToggleRuntime={toggleRuntime}
            onEdit={() => {
              setShowListenerEdit(true);
            }}
            onRemove={() => {
              setListenerActionsOpen(false);
              removeListener();
            }}
            listenerOpen={listenerOpen}
            onToggleOpen={() => {
              setListenerOpen((v) => !v);
            }}
            accountOptions={listenerOptions}
            onPickListener={(id) => {
              pickListener(id);
              setListenerOpen(false);
            }}
          />
          {showWarmingBlock ? (
            <p className="mt-2 text-[11.5px] font-medium text-danger">
              {t('neurocomment.listener.warmingBlocked')}
            </p>
          ) : null}

          <CaptchaSolverCard
            solverEnabled={solverEnabled}
            campaignId={campaignId}
            onToggleSolver={() => {
              if (campaignId !== null) {
                setSolver.mutate(
                  { path: { campaign_id: campaignId }, body: { enabled: !solverEnabled } },
                  { onSettled: invalidateNeuro },
                );
              }
            }}
            captchaQueue={captchaQueue}
            accountLabel={accountLabel}
          />

          <CampaignsCard
            campaignList={campaignList}
            campaignId={campaignId}
            activeCampaign={activeCampaign}
            boardChannels={boardChannels}
            openCampaignActions={openCampaignActions}
            onToggleActions={(id) => {
              setOpenCampaignActions((current) => (current === id ? null : id));
            }}
            onSelect={setSelected}
            onToggleStatus={toggleCampaignStatus}
            onEditPrompt={(campaign) => {
              // Select the campaign too, so the board query (and thus the prompt
              // modal's account list) reflects THIS campaign (finding #5).
              setSelected(campaign.campaign_id);
              setPromptFor(campaign);
            }}
            onDelete={setDeleteFor}
            onCreate={() => {
              setShowCreate(true);
            }}
            channelFeedback={channelFeedback.feedback}
            addingChannel={addingChannel}
            onStartAdd={() => {
              setAddingChannel(true);
            }}
            onCancelAdd={() => {
              setAddingChannel(false);
              setChannelInput('');
            }}
            channelInput={channelInput}
            onChannelInput={setChannelInput}
            onAddChannel={addChannel}
            onRemoveChannel={setChannelToRemove}
            onCheckChannels={checkChannels}
            checkingChannels={checkBans.isPending}
            channelCheckStatus={channelCheckStatus}
            discoverySlot={
              <ChannelDiscoveryButton
                campaignId={campaignId}
                campaignName={activeCampaign?.name ?? ''}
              />
            }
          />

          <HowItWorksCard />
        </div>
      </div>

      {showAccounts ? (
        <NeuroAccountsModal
          accounts={neuroAccountRows}
          channels={boardChannelNames}
          feedback={accountFeedback.feedback}
          onClose={() => {
            setShowAccounts(false);
          }}
          onPick={(accountId) => {
            if (campaignId !== null) {
              afterSettle(
                assignAccount.mutateAsync({
                  path: { campaign_id: campaignId },
                  body: { account_id: accountId },
                }),
                (ok) => {
                  accountFeedback.mark(accountId, ok);
                },
              );
            }
          }}
          onRemove={(accountId) => {
            if (campaignId !== null) {
              afterSettle(
                removeAccount.mutateAsync({
                  path: { campaign_id: campaignId },
                  body: { account_id: accountId },
                }),
                (ok) => {
                  accountFeedback.mark(accountId, ok);
                },
              );
            }
          }}
          onChannelChange={(accountId, channels) => {
            if (campaignId !== null) {
              afterSettle(
                setAccountChannel.mutateAsync({
                  path: { campaign_id: campaignId, account_id: accountId },
                  body: { channels },
                }),
                (ok) => {
                  accountFeedback.mark(accountId, ok);
                },
              );
            }
          }}
        />
      ) : null}

      {showHistory && campaignId !== null ? (
        <CommentHistoryModal
          campaignId={campaignId}
          accounts={boardAccounts}
          onClose={() => {
            setShowHistory(false);
          }}
        />
      ) : null}

      {channelToRemove ? (
        <ConfirmModal
          title={t('neurocomment.channels.removeTitle', { channel: channelToRemove })}
          body={t('neurocomment.channels.removeBody')}
          confirmLabel={t('neurocomment.channels.removeConfirm')}
          cancelLabel={t('neurocomment.modal.cancel')}
          onClose={() => {
            setChannelToRemove(null);
          }}
          onConfirm={confirmRemoveChannel}
        />
      ) : null}

      {confirmClearLogs ? (
        <ConfirmModal
          title={t('neurocomment.modal.clearLogs.title')}
          body={t('neurocomment.modal.clearLogs.body', { count: logCount.data?.matching ?? 0 })}
          confirmLabel={t('neurocomment.modal.clearLogs.confirm')}
          cancelLabel={t('neurocomment.modal.cancel')}
          onClose={() => {
            setConfirmClearLogs(false);
          }}
          onConfirm={() =>
            clearLogs.mutateAsync(
              { query: { event_prefix: 'neurocomment' } },
              { onSettled: invalidateNeuro },
            )
          }
        />
      ) : null}

      {showCreate ? (
        <CreateCampaignModal
          onClose={() => {
            setShowCreate(false);
          }}
          onCreate={({ name, prompt, channels }) => {
            void createCampaign
              .mutateAsync({ body: { name, prompt } })
              .then(async (created) => {
                setSelected(created.campaign_id);
                // The old forEach fired N linkChannel.mutate() calls into the one
                // observer, so N-1 outcomes were unobservable, and the single
                // invalidate hung off createCampaign — it refetched the board
                // BEFORE any channel was linked, so a new campaign came up with
                // none of them. Await every link, marking each, then refresh once.
                await Promise.all(
                  channels.map((channel) =>
                    linkChannel
                      .mutateAsync({
                        path: { campaign_id: created.campaign_id },
                        body: { channel },
                      })
                      .then(
                        () => {
                          channelFeedback.mark(channel, true);
                        },
                        () => {
                          channelFeedback.mark(channel, false);
                        },
                      ),
                  ),
                );
              })
              .catch(() => undefined)
              .finally(invalidateNeuro);
          }}
        />
      ) : null}

      {showListenerEdit ? (
        <ListenerEditModal
          options={listenerOptions.map((a) => ({
            id: a.account_id,
            name: accountDisplayName(a),
          }))}
          selected={listenerId || null}
          onClose={() => {
            setShowListenerEdit(false);
          }}
          onSave={pickListener}
        />
      ) : null}

      {promptFor ? (
        <CampaignPromptModal
          campaignName={promptFor.name}
          initialPrompt={promptFor.prompt}
          // Only surface board accounts once the board query reflects promptFor's
          // own campaign; otherwise show none rather than another campaign's
          // accounts (finding #5). Opening the prompt selects the campaign, so
          // this settles after the board refetch.
          accounts={
            promptFor.campaign_id === campaignId
              ? boardAccounts.map((a) => {
                  // Rendered as the row's primary bold label, so it must be the
                  // Telegram name — ``label`` is the operator field and falls back to
                  // the session stem, which showed "5_telethon" where "Alisa" belongs.
                  const name = displayNameById(a.account_id, a.label);
                  return {
                    account_id: a.account_id,
                    phone: name,
                    // An account with a channel subset shows it; an empty subset
                    // serves the whole campaign, so show the campaign — not an
                    // arbitrary first-readiness channel (that misrepresented its scope).
                    channel: (a.pinned_channels ?? []).join(', ') || promptFor.name,
                    initials: initials(name),
                  };
                })
              : []
          }
          onClose={() => {
            setPromptFor(null);
          }}
          onSave={(prompt) => {
            // Per campaign row, and the modal closes on this tick: saving a second
            // campaign's prompt before this one lands took the hook's ONE callback
            // slot and dropped this campaign's refresh.
            afterSettle(
              updatePrompt.mutateAsync({
                path: { campaign_id: promptFor.campaign_id },
                body: { prompt },
              }),
            );
            setPromptFor(null);
          }}
          onRemoveAccount={(accountId) => {
            afterSettle(
              removeAccount.mutateAsync({
                path: { campaign_id: promptFor.campaign_id },
                body: { account_id: accountId },
              }),
            );
          }}
        />
      ) : null}

      {deleteFor ? (
        <CampaignDeleteModal
          name={deleteFor.name}
          onClose={() => {
            setDeleteFor(null);
          }}
          onConfirm={() => {
            // Per campaign row with no isPending guard, and the modal closes on
            // this tick: deleting a second campaign before this DELETE lands took
            // the hook's ONE callback slot, so this campaign's list refresh was
            // dropped and the deleted row stayed on screen.
            afterSettle(
              deleteCampaign.mutateAsync({ path: { campaign_id: deleteFor.campaign_id } }),
            );
            setDeleteFor(null);
            if (selected === deleteFor.campaign_id) setSelected(null);
          }}
        />
      ) : null}
    </div>
  );
}
