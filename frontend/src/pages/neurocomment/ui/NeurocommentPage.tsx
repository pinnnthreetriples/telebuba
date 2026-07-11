import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { accountsQueryOptions } from '@/entities/account';
import {
  assignCampaignAccountMutation,
  campaignChallengesQueryOptions,
  CampaignDeleteModal,
  CampaignPromptModal,
  campaignsQueryOptions,
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
  retryChallengeMutation,
  setCampaignAccountChannelMutation,
  setCampaignSolverMutation,
  setCampaignStatusMutation,
  startNeurocommentMutation,
  stopNeurocommentMutation,
  updateCampaignPromptMutation,
} from '@/entities/campaign';
import { logsQueryOptions } from '@/entities/log';
import { warmedAccountsQueryOptions, warmingBoardQueryOptions } from '@/entities/warming';
import type { NeurocommentCampaign } from '@/shared/api';
import { useLogEventStream, useTransientFeedback } from '@/shared/lib';
import { ConfirmModal } from '@/shared/ui';
import { NeurocommentBoard } from '@/widgets/neurocomment-board';

import { ActivityLogCard } from './ActivityLogCard';
import { CommentFeedCard } from './CommentFeedCard';
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
const NEURO_LOG_LIMIT = 40;
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
  const invalidate = () => {
    void queryClient.invalidateQueries();
  };
  // SSE-driven refresh: narrowed to this page's query keys (finding #11).
  const invalidateNeuro = () => {
    void queryClient.invalidateQueries({
      predicate: (query) => {
        const id = (query.queryKey[0] as { _id?: string } | undefined)?._id;
        return id !== undefined && NEURO_QUERY_IDS.has(id);
      },
    });
  };
  useLogEventStream(invalidateNeuro);

  const [selected, setSelected] = useState<string | null>(null);
  const [listener, setListener] = useState('');
  const [listenerOpen, setListenerOpen] = useState(false);
  // Gear-driven action-row reveals (click fallback for hover; finding #6).
  const [listenerActionsOpen, setListenerActionsOpen] = useState(false);
  const [openCampaignActions, setOpenCampaignActions] = useState<string | null>(null);
  const [channelInput, setChannelInput] = useState('');
  const [addingChannel, setAddingChannel] = useState(false);
  const [channelToRemove, setChannelToRemove] = useState<string | null>(null);
  const channelFeedback = useTransientFeedback();
  const accountFeedback = useTransientFeedback();

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
  const accounts = useQuery(accountsQueryOptions());
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
  // Real captcha queue — unsolved bot-challenges across the campaign's channels.
  const challenges = useQuery({
    ...campaignChallengesQueryOptions({
      path: { campaign_id: campaignId ?? '' },
      query: { limit: CAPTCHA_QUEUE_LIMIT },
    }),
    refetchInterval: FALLBACK_POLL_MS,
    enabled: campaignId !== null,
  });
  const logLines = neuroLog.data?.items ?? [];
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
  const retry = useMutation(retryChallengeMutation());
  const deleteCampaign = useMutation(deleteCampaignMutation());
  const removeChannel = useMutation(removeCampaignChannelMutation());
  const removeAccount = useMutation(removeCampaignAccountMutation());
  const setAccountChannel = useMutation(setCampaignAccountChannelMutation());
  const updatePrompt = useMutation(updateCampaignPromptMutation());

  const accountOptions = accounts.data?.items ?? [];
  const warmingIds = new Set((warmingBoard.data?.warming ?? []).map((a) => a.account_id));
  // Listener candidates exclude accounts that are actively warming.
  const listenerOptions = accountOptions.filter((a) => !warmingIds.has(a.account_id));
  // The graduated pool — what neurocomment may actually put to work.
  const warmedAccounts = warmed.data?.accounts ?? [];
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

  // Account label lookup so the captcha queue shows the phone, not the raw id.
  const accountLabel = (accountId: string): string =>
    accountOptions.find((a) => a.account_id === accountId)?.label ?? accountId;

  // Ids already on the selected campaign's board (linked accounts).
  const linkedIds = new Set(boardAccounts.map((a) => a.account_id));

  // Rows for the neuro-accounts modal: the campaign's linked accounts (with a
  // channel-pin dropdown) PLUS every graduated ("Прогреты") account not yet
  // linked (linked: false → shows the "assign" button so an idle warmed account
  // can actually be added).
  const neuroAccountRows = [
    ...boardAccounts.map((a) => ({
      account_id: a.account_id,
      phone: a.label,
      linked: true,
      pinned_channel: a.pinned_channel ?? null,
    })),
    ...warmedAccounts
      .filter((a) => !linkedIds.has(a.account_id))
      .map((a) => ({
        account_id: a.account_id,
        phone: a.label ?? a.account_id,
        linked: false,
        pinned_channel: null,
      })),
  ];

  // Errors stat: error-level rows in today's loaded neuro activity log.
  const errorCount = logLines.filter((line) => line.status === 'error').length;

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
        onSettled: invalidate,
      },
    );
  };

  // GLOBAL listener start/stop (the whole engine). Kept distinct from the
  // per-campaign run/pause below.
  const toggleRuntime = () => {
    if (running) {
      stop.mutate({}, { onSettled: invalidate });
    } else if (listenerId && !warmingIds.has(listenerId)) {
      startListener(listenerId);
    }
    // A warming listenerId is not started; showWarmingBlock already renders the banner.
  };

  // Per-campaign run/pause (finding #2): flips campaign.status via setCampaignStatus.
  // The engine skips paused campaigns; this never touches the global engine.
  const toggleCampaignStatus = (campaign: NeurocommentCampaign) => {
    const next = campaign.status === 'active' ? 'paused' : 'active';
    setStatus.mutate(
      { path: { campaign_id: campaign.campaign_id }, body: { status: next } },
      { onSettled: invalidate },
    );
  };

  // Remove the listener entirely (finding #4) — distinct from pausing (stop).
  const removeListener = () => {
    setListener('');
    clearListener.mutate({}, { onSettled: invalidate });
  };

  const addChannel = () => {
    const value = channelInput.trim();
    if (!value || campaignId === null) return;
    linkChannel.mutate(
      { path: { campaign_id: campaignId }, body: { channel: value } },
      {
        onSettled: (_data, error) => {
          setChannelInput('');
          setAddingChannel(false);
          channelFeedback.mark(value, !error);
          invalidate();
        },
      },
    );
  };

  const confirmRemoveChannel = () => {
    if (!channelToRemove || campaignId === null) return;
    const channel = channelToRemove;
    setChannelToRemove(null);
    removeChannel.mutate(
      { path: { campaign_id: campaignId }, body: { channel } },
      {
        onSettled: (_data, error) => {
          channelFeedback.mark(channel, !error);
          invalidate();
        },
      },
    );
  };

  return (
    <div className="tb-fadeup">
      <h1 className="m-0 mb-[18px] text-[22px] font-bold tracking-[-0.02em]">
        {t('neurocomment.title')}
      </h1>

      <div className="grid grid-cols-[340px_1fr] items-start gap-4">
        {/* RIGHT column */}
        <div className="col-start-2 row-start-1 flex flex-col gap-4">
          <PipelineCard
            running={running}
            canStart={Boolean(listenerId)}
            stats={stats}
            onToggle={toggleRuntime}
          />

          {board.data ? (
            <NeurocommentBoard
              board={board.data}
              accountsCount={boardAccounts.length}
              onboarding={onboarding}
              onOpenAccounts={() => {
                setShowAccounts(true);
              }}
            />
          ) : null}

          {board.data ? (
            <CommentFeedCard
              comments={board.data.comments ?? []}
              accounts={boardAccounts}
              onOpenHistory={() => {
                setShowHistory(true);
              }}
            />
          ) : null}

          <ActivityLogCard logLines={logLines} />
        </div>

        {/* LEFT column */}
        <div className="col-start-1 row-start-1 flex flex-col gap-4">
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
              setStartRejectedWarming(false);
              setListener(id);
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
                  { onSettled: invalidate },
                );
              }
            }}
            captchaQueue={captchaQueue}
            accountLabel={accountLabel}
            onSolve={(item) => {
              retry.mutate(
                { body: { account_id: item.account_id, channel: item.channel } },
                { onSettled: invalidate },
              );
            }}
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
              assignAccount.mutate(
                { path: { campaign_id: campaignId }, body: { account_id: accountId } },
                {
                  onSettled: (_data, error) => {
                    accountFeedback.mark(accountId, !error);
                    invalidate();
                  },
                },
              );
            }
          }}
          onRemove={(accountId) => {
            if (campaignId !== null) {
              removeAccount.mutate(
                { path: { campaign_id: campaignId }, body: { account_id: accountId } },
                {
                  onSettled: (_data, error) => {
                    accountFeedback.mark(accountId, !error);
                    invalidate();
                  },
                },
              );
            }
          }}
          onChannelChange={(accountId, channel) => {
            if (campaignId !== null) {
              setAccountChannel.mutate(
                {
                  path: { campaign_id: campaignId, account_id: accountId },
                  body: { channel },
                },
                {
                  onSettled: (_data, error) => {
                    accountFeedback.mark(accountId, !error);
                    invalidate();
                  },
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

      {showCreate ? (
        <CreateCampaignModal
          onClose={() => {
            setShowCreate(false);
          }}
          onCreate={({ name, prompt, channels }) => {
            createCampaign.mutate(
              { body: { name, prompt } },
              {
                onSuccess: (created) => {
                  channels.forEach((channel) => {
                    linkChannel.mutate({
                      path: { campaign_id: created.campaign_id },
                      body: { channel },
                    });
                  });
                  setSelected(created.campaign_id);
                },
                onSettled: invalidate,
              },
            );
          }}
        />
      ) : null}

      {showListenerEdit ? (
        <ListenerEditModal
          options={listenerOptions.map((a) => ({
            id: a.account_id,
            phone: a.label ?? a.account_id,
          }))}
          selected={listenerId || null}
          onClose={() => {
            setShowListenerEdit(false);
          }}
          onSave={(id) => {
            setStartRejectedWarming(false);
            setListener(id);
            if (running && !warmingIds.has(id)) {
              startListener(id);
            }
          }}
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
              ? boardAccounts.map((a) => ({
                  account_id: a.account_id,
                  phone: a.label,
                  channel: a.readiness?.[0]?.channel ?? '—',
                  initials: initials(a.label),
                }))
              : []
          }
          onClose={() => {
            setPromptFor(null);
          }}
          onSave={(prompt) => {
            updatePrompt.mutate(
              { path: { campaign_id: promptFor.campaign_id }, body: { prompt } },
              { onSettled: invalidate },
            );
            setPromptFor(null);
          }}
          onRemoveAccount={(accountId) => {
            removeAccount.mutate(
              { path: { campaign_id: promptFor.campaign_id }, body: { account_id: accountId } },
              { onSettled: invalidate },
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
            deleteCampaign.mutate(
              { path: { campaign_id: deleteFor.campaign_id } },
              { onSettled: invalidate },
            );
            setDeleteFor(null);
            if (selected === deleteFor.campaign_id) setSelected(null);
          }}
        />
      ) : null}
    </div>
  );
}
