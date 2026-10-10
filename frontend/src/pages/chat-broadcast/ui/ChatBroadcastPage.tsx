import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { accountDisplayName, allAccountsQueryOptions } from '@/entities/account';
import {
  actOnChatBroadcastTargetMutation,
  chatBroadcastBoardQueryOptions,
  chatBroadcastCampaignsQueryOptions,
  chatBroadcastSettingsQueryOptions,
  createChatBroadcastCampaignMutation,
  deleteChatBroadcastCampaignMutation,
  startChatBroadcastCampaignMutation,
  stopChatBroadcastCampaignMutation,
} from '@/entities/chat-broadcast';
import { clearLogsMutation, logCountQueryOptions, logsQueryOptions } from '@/entities/log';
import type { AccountRead, ChatBroadcastCampaign } from '@/shared/api';
import { formatLocalTime, useLogEventStream } from '@/shared/lib';
import { Card, ConfirmModal } from '@/shared/ui';
import { LogTerminal } from '@/widgets/log-terminal';

import { draftOf, isFilled } from '../model/draft';
import { pipelineView } from '../model/pipeline';

import { BoardCard } from './BoardCard';
import { BroadcastHowItWorks, CampaignList } from './CampaignList';
import { BroadcastPipeline } from './BroadcastPipeline';
import { PaceDialog } from './PaceDialog';
import { SettingsDialog } from './settings/SettingsDialog';

// The query ids this page owns: the SSE stream fires on every log row of the whole app,
// and a bare invalidate would refetch every page's data on each one. The settings query
// is left out on purpose — it backs an explicit-save dialog.
const PAGE_QUERY_IDS = new Set(['listChatBroadcastCampaigns', 'getChatBroadcastBoard', 'listLogs']);
const LOG_LIMIT = 80;
const LOG_PREFIX = 'chat_broadcast';
// "Waiting until 15:40" turns into "queued" without a server event; a light tick keeps it.
const CLOCK_TICK_MS = 30_000;

export function ChatBroadcastPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const refresh = () =>
    queryClient.invalidateQueries({
      predicate: (query) => {
        const id = (query.queryKey[0] as { _id?: string } | undefined)?._id;
        return id !== undefined && PAGE_QUERY_IDS.has(id);
      },
    });
  useLogEventStream(() => {
    void refresh();
  });

  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => {
      setNow(Date.now());
    }, CLOCK_TICK_MS);
    return () => {
      window.clearInterval(timer);
    };
  }, []);

  const [selected, setSelected] = useState<string | null>(null);
  const [openActions, setOpenActions] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [createName, setCreateName] = useState('');
  const [settingsFor, setSettingsFor] = useState<string | null>(null);
  const [paceFor, setPaceFor] = useState<string | null>(null);
  const [deleteFor, setDeleteFor] = useState<ChatBroadcastCampaign | null>(null);
  const [confirmClearLogs, setConfirmClearLogs] = useState(false);

  const campaigns = useQuery(chatBroadcastCampaignsQueryOptions());
  const list = campaigns.data?.items ?? [];
  const campaignId =
    selected !== null && list.some((item) => item.campaign_id === selected)
      ? selected
      : (list[0]?.campaign_id ?? null);
  const path = { path: { campaign_id: campaignId ?? '' } };
  const board = useQuery({ ...chatBroadcastBoardQueryOptions(path), enabled: campaignId !== null });
  const settings = useQuery({
    ...chatBroadcastSettingsQueryOptions(path),
    enabled: campaignId !== null,
  });
  const fleetQuery = useQuery(allAccountsQueryOptions());
  const fleetList: AccountRead[] = fleetQuery.data?.items ?? [];
  const fleet = new Map(fleetList.map((account) => [account.account_id, account]));
  const nameOf = (id: string) => {
    const account = fleet.get(id);
    return account === undefined ? id : accountDisplayName(account);
  };
  const logs = useQuery(
    logsQueryOptions({ query: { event_prefix: LOG_PREFIX, limit: LOG_LIMIT } }),
  );
  const logCount = useQuery({
    ...logCountQueryOptions({ query: { event_prefix: LOG_PREFIX } }),
    enabled: confirmClearLogs,
  });

  const create = useMutation(createChatBroadcastCampaignMutation());
  const remove = useMutation(deleteChatBroadcastCampaignMutation());
  const start = useMutation(startChatBroadcastCampaignMutation());
  const stop = useMutation(stopChatBroadcastCampaignMutation());
  const act = useMutation(actOnChatBroadcastTargetMutation());
  const clearLogs = useMutation(clearLogsMutation());
  const busy = start.isPending || stop.isPending || act.isPending;

  const refreshSettings = () =>
    queryClient.invalidateQueries({
      predicate: (query) =>
        (query.queryKey[0] as { _id?: string } | undefined)?._id === 'getChatBroadcastSettings',
    });
  const run = (call: Promise<unknown>) => {
    void call
      .catch(() => undefined)
      .finally(() => {
        void refresh();
        void refreshSettings();
      });
  };
  const startCampaign = (id: string) => {
    const stamp = settings.data?.campaign_id === id ? settings.data.updated_at : undefined;
    if (stamp === undefined) return;
    run(start.mutateAsync({ path: { campaign_id: id }, body: { expected_updated_at: stamp } }));
  };
  const time = (iso: string) => formatLocalTime(iso);

  const createCampaign = () => {
    const name = createName.trim();
    if (!name) return;
    void create
      .mutateAsync({ body: { name } })
      .then((created) => {
        setSelected(created.campaign_id);
        setCreating(false);
        setCreateName('');
        setSettingsFor(created.campaign_id);
      })
      .catch(() => undefined)
      .finally(() => {
        void refresh();
      });
  };

  const current = list.find((item) => item.campaign_id === campaignId);
  const ready =
    board.data !== undefined &&
    settings.data !== undefined &&
    board.data.campaign.campaign_id === campaignId &&
    settings.data.campaign_id === campaignId;
  let pipeline = null;
  if (ready) {
    const draft = draftOf(settings.data);
    const missing: string[] = [];
    if (draft.accountIds.length === 0)
      missing.push(t('chatBroadcast.pipeline.notice.missingAccounts'));
    if (draft.settings.target_mode === 'list' && draft.settings.targets.length === 0) {
      missing.push(t('chatBroadcast.pipeline.notice.missingChats'));
    }
    if (!draft.messages.some((message, index) => isFilled(message, draft, index))) {
      missing.push(t('chatBroadcast.pipeline.notice.missingMessages'));
    }
    pipeline = pipelineView(t, {
      board: board.data,
      settings: draft.settings,
      missing,
      time,
      nameOf,
    });
  }

  return (
    <div className="tb-fadeup mx-auto max-w-shell">
      <h1 className="m-0 mb-6 type-h1">{t('chatBroadcast.title')}</h1>
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start">
        <div className="flex flex-col gap-3 lg:w-sidebar lg:shrink-0">
          <CampaignList
            campaigns={list}
            selectedId={campaignId}
            openActions={openActions}
            creating={creating}
            createName={createName}
            onSelect={setSelected}
            onToggleActions={(id) => {
              setOpenActions((value) => (value === id ? null : id));
            }}
            onToggleRun={(campaign) => {
              if (campaign.status === 'running' || campaign.status === 'stopping') {
                run(stop.mutateAsync({ path: { campaign_id: campaign.campaign_id } }));
              } else if (campaign.campaign_id !== campaignId) {
                setSelected(campaign.campaign_id);
              } else {
                startCampaign(campaign.campaign_id);
              }
            }}
            onSettings={(id) => {
              setSelected(id);
              setSettingsFor(id);
            }}
            onDelete={setDeleteFor}
            onStartCreate={() => {
              setCreating(true);
            }}
            onCancelCreate={() => {
              setCreating(false);
              setCreateName('');
            }}
            onCreateName={setCreateName}
            onCreate={createCampaign}
          />
          <BroadcastHowItWorks />
        </div>

        <div className="flex min-w-0 flex-1 flex-col gap-4">
          {list.length === 0 && campaigns.data !== undefined ? (
            <Card>
              <div className="py-8 text-center">
                <div className="type-h3">{t('chatBroadcast.empty.title')}</div>
                <div className="mt-2 type-body text-content-subtle">
                  {t('chatBroadcast.empty.text')}
                </div>
              </div>
            </Card>
          ) : null}
          {pipeline !== null && current !== undefined && board.data !== undefined ? (
            <>
              <BroadcastPipeline
                name={current.name}
                view={pipeline}
                busy={busy}
                onAction={() => {
                  if (pipeline.action.kind === 'stop') {
                    run(stop.mutateAsync({ path: { campaign_id: current.campaign_id } }));
                  } else {
                    startCampaign(current.campaign_id);
                  }
                }}
              />
              <BoardCard
                key={current.campaign_id}
                board={board.data}
                fleet={fleet}
                time={time}
                now={now}
                approvalHours={settings.data?.settings.approval_wait_hours ?? 24}
                busy={busy}
                onPace={() => {
                  setPaceFor(current.campaign_id);
                }}
                onAction={(row, action) => {
                  run(
                    act.mutateAsync({
                      path: { campaign_id: current.campaign_id },
                      body: {
                        chat_key: row.chat_key,
                        action: action.kind,
                        account_id: action.kind === 'hand' ? action.accountId : null,
                      },
                    }),
                  );
                }}
              />
            </>
          ) : null}
          <LogTerminal
            title={t('chatBroadcast.log')}
            logLines={logs.data?.items ?? []}
            onClear={() => {
              setConfirmClearLogs(true);
            }}
            accountOf={(id) => fleet.get(id)}
          />
        </div>
      </div>

      {settingsFor !== null && settings.data?.campaign_id === settingsFor ? (
        <SettingsDialog
          read={settings.data}
          fleet={fleetList}
          onClose={() => {
            setSettingsFor(null);
          }}
          onSaved={() => {
            void refresh();
            void refreshSettings();
          }}
        />
      ) : null}

      {paceFor !== null && settings.data?.campaign_id === paceFor ? (
        <PaceDialog
          read={settings.data}
          onClose={() => {
            setPaceFor(null);
          }}
          onSaved={() => {
            void refresh();
            void refreshSettings();
          }}
        />
      ) : null}

      {confirmClearLogs ? (
        <ConfirmModal
          title={t('chatBroadcast.clearLogs.title')}
          body={t('chatBroadcast.clearLogs.body', { count: logCount.data?.matching ?? 0 })}
          confirmLabel={t('chatBroadcast.clearLogs.confirm')}
          cancelLabel={t('chatBroadcast.clearLogs.cancel')}
          onClose={() => {
            setConfirmClearLogs(false);
          }}
          onConfirm={() =>
            clearLogs
              .mutateAsync({ query: { event_prefix: LOG_PREFIX } })
              .then(() => {
                setConfirmClearLogs(false);
              })
              .finally(() => {
                void refresh();
              })
          }
        />
      ) : null}

      {deleteFor !== null ? (
        <ConfirmModal
          title={t('chatBroadcast.campaign.deleteTitle', { name: deleteFor.name })}
          body={t('chatBroadcast.campaign.deleteBody')}
          confirmLabel={t('chatBroadcast.campaign.deleteConfirm')}
          cancelLabel={t('chatBroadcast.campaign.deleteCancel')}
          onClose={() => {
            setDeleteFor(null);
          }}
          onConfirm={() =>
            remove
              .mutateAsync({ path: { campaign_id: deleteFor.campaign_id } })
              .then(() => {
                setDeleteFor(null);
              })
              .finally(() => {
                void refresh();
              })
          }
        />
      ) : null}
    </div>
  );
}
