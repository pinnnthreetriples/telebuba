import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { allAccountsQueryOptions } from '@/entities/account';
import { clearLogsMutation, logCountQueryOptions, logsQueryOptions } from '@/entities/log';
import {
  actOnMasstaggingTargetMutation,
  createMasstaggingCampaignMutation,
  deleteMasstaggingCampaignMutation,
  masstaggingBoardQueryOptions,
  masstaggingCampaignsQueryOptions,
  masstaggingSettingsQueryOptions,
  startMasstaggingCampaignMutation,
  stopMasstaggingCampaignMutation,
} from '@/entities/masstagging';
import type { AccountRead, MasstaggingCampaign } from '@/shared/api';
import { formatLocalTime, useLogEventStream } from '@/shared/lib';
import { Card, ConfirmModal } from '@/shared/ui';
import { LogTerminal } from '@/widgets/log-terminal';

import { draftOf } from '../model/draft';
import { pipelineView } from '../model/pipeline';

import { BoardCard } from './BoardCard';
import { CampaignList, MasstaggingHowItWorks } from './CampaignList';
import { MasstaggingPipeline } from './MasstaggingPipeline';
import { PaceDialog } from './PaceDialog';
import { SettingsDialog } from './settings/SettingsDialog';

// The query ids this page owns: the SSE stream fires on every log row of the whole app,
// and a bare invalidate would refetch every page's data on each one. The settings query
// is left out on purpose — it backs an explicit-save dialog.
const PAGE_QUERY_IDS = new Set(['listMasstaggingCampaigns', 'getMasstaggingBoard', 'listLogs']);
const LOG_LIMIT = 80;
const LOG_PREFIX = 'masstagging';
// "Next story in 2 min" counts down without a server event; a light tick keeps it.
const CLOCK_TICK_MS = 30_000;
const GOING: MasstaggingCampaign['status'][] = ['running', 'resting'];

export function MasstaggingPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const refresh = () =>
    queryClient.invalidateQueries({
      predicate: (query) => {
        const id = (query.queryKey[0] as { _id?: string } | undefined)?._id;
        return id !== undefined && PAGE_QUERY_IDS.has(id);
      },
    });
  const refreshSettings = () =>
    queryClient.invalidateQueries({
      predicate: (query) =>
        (query.queryKey[0] as { _id?: string } | undefined)?._id === 'getMasstaggingSettings',
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
  const [deleteFor, setDeleteFor] = useState<MasstaggingCampaign | null>(null);
  const [confirmClearLogs, setConfirmClearLogs] = useState(false);

  const campaigns = useQuery(masstaggingCampaignsQueryOptions());
  const list = campaigns.data?.items ?? [];
  const campaignId =
    selected !== null && list.some((item) => item.campaign_id === selected)
      ? selected
      : (list[0]?.campaign_id ?? null);
  const path = { path: { campaign_id: campaignId ?? '' } };
  const board = useQuery({ ...masstaggingBoardQueryOptions(path), enabled: campaignId !== null });
  const settings = useQuery({
    ...masstaggingSettingsQueryOptions(path),
    enabled: campaignId !== null,
  });
  const fleetQuery = useQuery(allAccountsQueryOptions());
  const fleetList: AccountRead[] = fleetQuery.data?.items ?? [];
  const fleet = new Map(fleetList.map((account) => [account.account_id, account]));
  const logs = useQuery(
    logsQueryOptions({ query: { event_prefix: LOG_PREFIX, limit: LOG_LIMIT } }),
  );
  const logCount = useQuery({
    ...logCountQueryOptions({ query: { event_prefix: LOG_PREFIX } }),
    enabled: confirmClearLogs,
  });

  const create = useMutation(createMasstaggingCampaignMutation());
  const remove = useMutation(deleteMasstaggingCampaignMutation());
  const start = useMutation(startMasstaggingCampaignMutation());
  const stop = useMutation(stopMasstaggingCampaignMutation());
  const act = useMutation(actOnMasstaggingTargetMutation());
  const clearLogs = useMutation(clearLogsMutation());

  const run = (call: Promise<unknown>) => {
    void call
      .catch(() => undefined)
      .finally(() => {
        void refresh();
        void refreshSettings();
      });
  };
  // Start needs the version the operator saw. Until the campaign's settings are loaded
  // (another campaign was selected, or the read is in flight), the press waits here and
  // fires once they arrive, instead of being dropped.
  const [pendingStart, setPendingStart] = useState<string | null>(null);
  const loadedStamp =
    settings.data?.campaign_id === pendingStart ? settings.data?.updated_at : null;
  const settingsFailed = settings.isError;
  useEffect(() => {
    if (pendingStart !== null && settingsFailed) {
      // The read failed (its toast says why): the press is dropped, not kept waiting.
      setPendingStart(null);
      return;
    }
    if (pendingStart === null || loadedStamp === null || loadedStamp === undefined) return;
    setPendingStart(null);
    void start
      .mutateAsync({
        path: { campaign_id: pendingStart },
        body: { expected_updated_at: loadedStamp },
      })
      .catch(() => undefined)
      .finally(() => {
        void refresh();
        void refreshSettings();
      });
    // `start`, `refresh` and `refreshSettings` are stable for the press being served.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingStart, loadedStamp, settingsFailed]);
  const busy = start.isPending || stop.isPending || act.isPending || pendingStart !== null;
  const startCampaign = (id: string) => {
    setSelected(id);
    setPendingStart(id);
  };
  const toggleRun = (campaign: MasstaggingCampaign) => {
    if (GOING.includes(campaign.status)) {
      run(stop.mutateAsync({ path: { campaign_id: campaign.campaign_id } }));
    } else {
      startCampaign(campaign.campaign_id);
    }
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
  const draft = ready ? draftOf(settings.data) : null;
  const view =
    ready && draft !== null
      ? pipelineView(t, { board: board.data, settings: draft.settings, now, time })
      : null;

  return (
    <div className="tb-fadeup mx-auto max-w-shell pb-16">
      <h1 className="m-0 mb-6 type-h1">{t('masstagging.title')}</h1>
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
            onToggleRun={toggleRun}
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
          <MasstaggingHowItWorks />
        </div>

        <div className="flex min-w-0 flex-1 flex-col gap-4">
          {list.length === 0 && campaigns.data !== undefined ? (
            <Card>
              <div className="py-8 text-center">
                <div className="type-h3">{t('masstagging.empty.title')}</div>
                <div className="mt-2 type-body text-content-subtle">
                  {t('masstagging.empty.text')}
                </div>
              </div>
            </Card>
          ) : null}
          {view !== null && draft !== null && current !== undefined && board.data !== undefined ? (
            <>
              <MasstaggingPipeline
                name={current.name}
                mode={draft.settings.mode}
                freeAccounts={board.data.counters.free_accounts}
                view={view}
                busy={busy}
                onAction={() => {
                  if (view.action.kind === 'pause') {
                    run(stop.mutateAsync({ path: { campaign_id: current.campaign_id } }));
                  } else {
                    startCampaign(current.campaign_id);
                  }
                }}
                onSettings={() => {
                  setSettingsFor(current.campaign_id);
                }}
              />
              <BoardCard
                key={`${current.campaign_id}-${board.data.campaign.status}`}
                board={board.data}
                fleet={fleet}
                time={time}
                stories={draft.settings.mode === 'stories'}
                busy={busy}
                onPace={() => {
                  setPaceFor(current.campaign_id);
                }}
                onAddTargets={() => {
                  setSettingsFor(current.campaign_id);
                }}
                onAction={(row, action) => {
                  run(
                    act.mutateAsync({
                      path: { campaign_id: current.campaign_id },
                      body: { target_key: row.target_key, action },
                    }),
                  );
                }}
              />
            </>
          ) : null}
          <LogTerminal
            title={t('masstagging.log')}
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
          title={t('masstagging.clearLogs.title')}
          body={t('masstagging.clearLogs.body', { count: logCount.data?.matching ?? 0 })}
          confirmLabel={t('masstagging.clearLogs.confirm')}
          cancelLabel={t('masstagging.clearLogs.cancel')}
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
          title={t('masstagging.campaign.deleteTitle', { name: deleteFor.name })}
          body={t('masstagging.campaign.deleteBody')}
          confirmLabel={t('masstagging.campaign.deleteConfirm')}
          cancelLabel={t('masstagging.campaign.deleteCancel')}
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
