import { pageTitleSpacing, boardLayout } from '@/shared/design-system';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  approveNeuroshillingScenarioMutation,
  createNeuroshillingCampaignMutation,
  deleteNeuroshillingCampaignMutation,
  generateNeuroshillingScenarioMutation,
  neuroshillingBoardQueryOptions,
  neuroshillingCampaignsQueryOptions,
  neuroshillingSettingsQueryOptions,
  saveNeuroshillingSettingsMutation,
  startNeuroshillingCampaignMutation,
  stopNeuroshillingCampaignMutation,
  updateNeuroshillingCampaignMutation,
} from '@/entities/neuroshilling';
import { clearLogsMutation, logCountQueryOptions, logsQueryOptions } from '@/entities/log';
import type {
  NeuroshillingAccountAssignment,
  NeuroshillingCampaign,
  NeuroshillingCampaignUpdate,
} from '@/shared/api';
import { cn, useLogEventStream } from '@/shared/lib';
import { PageFrame, SectionStack, ConfirmModal } from '@/shared/ui';
import { LogTerminal } from '@/widgets/log-terminal';

import { ApproveModal } from './ApproveModal';
import { CampaignDetailsModal } from './CampaignDetailsModal';
import { CampaignSettingsModal } from './CampaignSettingsModal';
import { CampaignSetupSection } from './CampaignSetupSection';
import { CampaignsCard } from './CampaignsCard';
import { ChecksBanner } from './ChecksBanner';
import { HowItWorksCard } from './HowItWorksCard';
import { launchBlockers } from './launchChecks';
import { PipelineCard } from './PipelineCard';
import { ScenarioSection } from './ScenarioSection';
import { WorkBoardCard } from './WorkBoardCard';
import type { ScenarioDraft } from './scenarioDraft';
import {
  campaignFieldsOf,
  draftOf,
  MAX_GENERATED_STEPS,
  MAX_ROLES,
  scenarioBody,
} from './scenarioDraft';
import type { SetupDraft } from './setupDraft';
import { setupDraftOf, setupFieldsOf } from './setupDraft';

// The query-key `_id`s this page owns. The SSE stream fires on every log row in
// the whole app, so a bare invalidateQueries() would refetch accounts, warming,
// settings and every open profile snapshot on each one.
//
// `getNeuroshillingSettings` is deliberately NOT here. The stream flushes on a
// 400 ms trailing debounce, and the scenario query backs an explicit-save form:
// refetching it under the operator's typing is exactly what the separate
// endpoint exists to avoid. It refreshes from its own mutations, below.
const NEUROSHILLING_QUERY_IDS = new Set([
  'listNeuroshillingCampaigns',
  'getNeuroshillingBoard',
  // The launch card renders the feed, so the stream that fires on every log row
  // has to refresh the page holding it.
  'listLogs',
]);

// Заготовка на ПУСТОЙ сценарий: с чего начинать, когда ролей и шагов ещё нет.
// Заполненный сценарий диктует размер сам — см. `generationAsk`.
const DEFAULT_PERSONAS = 3;
const DEFAULT_STEPS = 8;

// One page of the activity feed. The same depth the neurocomment terminal reads,
// and well under the `le=1000` ceiling on `LogFilter.limit` (schemas/logs.py).
const LOG_LIMIT = 80;
const LOG_PREFIX = 'neuroshilling';

function isCampaignChanged(error: unknown): boolean {
  if (typeof error !== 'object' || error === null) return false;
  const detail = (error as { error?: { code?: string; message?: string } }).error;
  return detail?.code === 'conflict' && detail.message === 'campaign_changed';
}

// `updateNeuroshillingCampaign` replaces the WHOLE form: a field left out of the
// body is written back as its schema default. Every caller edits one slice of it,
// so each starts from the atomic settings snapshot rather
// than silently resetting the rest.
function campaignBody(
  campaign: NeuroshillingCampaign,
  accounts: NeuroshillingAccountAssignment[],
  expectedUpdatedAt: string,
): NeuroshillingCampaignUpdate {
  return {
    name: campaign.name,
    expected_updated_at: expectedUpdatedAt,
    mode: campaign.mode,
    topic: campaign.topic,
    targets_raw: campaign.targets_raw,
    unique_messages: campaign.unique_messages,
    use_chat_context: campaign.use_chat_context,
    media_message_link: campaign.media_message_link,
    media_step_position: campaign.media_step_position,
    run_mode: campaign.run_mode,
    pause_min_seconds: campaign.pause_min_seconds,
    pause_max_seconds: campaign.pause_max_seconds,
    messages_per_hour: campaign.messages_per_hour,
    messages_per_chat_per_day: campaign.messages_per_chat_per_day,
    total_per_account: campaign.total_per_account,
    reserve_enabled: campaign.reserve_enabled,
    autoresponder: campaign.autoresponder,
    reply_to_humans: campaign.reply_to_humans,
    reply_activity: campaign.reply_activity,
    listen_minutes: campaign.listen_minutes,
    accounts,
  };
}

export function NeuroshillingPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  // The page's ONE refresh scope, narrowed to the queries above. Used by the SSE
  // stream and by every mutation here, because they all need the same thing.
  const refreshBoard = () =>
    queryClient.invalidateQueries({
      predicate: (query) => {
        const id = (query.queryKey[0] as { _id?: string } | undefined)?._id;
        return id !== undefined && NEUROSHILLING_QUERY_IDS.has(id);
      },
    });
  const invalidateNeuroshilling = () => {
    void refreshBoard();
  };
  useLogEventStream(invalidateNeuroshilling);

  const [selected, setSelected] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [createName, setCreateName] = useState('');
  // Настройки и утверждение — два диалога редизайна. Оба относятся к ВЫБРАННОЙ кампании,
  // поэтому это флаги, а не идентификаторы: карандаш строки сначала выбирает её.
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);
  // Строка сайдбара, чей слой действий раскрыт шестерёнкой. Одна на весь список.
  const [openActions, setOpenActions] = useState<string | null>(null);
  const [approveOpen, setApproveOpen] = useState(false);
  const [deleteFor, setDeleteFor] = useState<NeuroshillingCampaign | null>(null);
  const [confirmGenerate, setConfirmGenerate] = useState(false);
  const [confirmClearLogs, setConfirmClearLogs] = useState(false);
  const [draft, setDraft] = useState<ScenarioDraft | null>(null);
  // The draft as it was last adopted, serialised. Comparing against THIS rather
  // than against the live query keeps "dirty" true for the moment between a save
  // landing and the board refetch that reflects it.
  const [baseline, setBaseline] = useState('');
  // The setup card's own draft and baseline, kept apart from the scenario's: the
  // two cards save independently, so a save of one must not adopt the other's
  // unsaved edits as its new baseline.
  const [setup, setSetup] = useState<SetupDraft | null>(null);
  const [setupBaseline, setSetupBaseline] = useState('');
  const [editToken, setEditToken] = useState<{
    campaignId: string;
    updatedAt: string;
    campaign: NeuroshillingCampaign;
    accounts: NeuroshillingAccountAssignment[];
  } | null>(null);
  const [saveConflict, setSaveConflict] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const campaigns = useQuery(neuroshillingCampaignsQueryOptions());
  const campaignList = campaigns.data?.campaigns ?? [];
  // Every scoped read hangs off this: no campaign, no board query at all. The
  // selection is looked UP in the list rather than trusted, because nothing else
  // notices it going stale: a campaign deleted in another tab would keep every
  // scoped read pointed at it, 404 on each refetch, and a failed query toasts
  // nowhere (`shared/lib/query-client` only reports failed MUTATIONS) — so the
  // page would go on showing the last board it managed to read.
  const campaignId =
    campaignList.find((item) => item.campaign_id === selected)?.campaign_id ??
    campaignList[0]?.campaign_id ??
    null;
  const campaignIdRef = useRef(campaignId);
  campaignIdRef.current = campaignId;

  const board = useQuery({
    ...neuroshillingBoardQueryOptions({ path: { campaign_id: campaignId ?? '' } }),
    enabled: campaignId !== null,
  });
  const settingsSnapshot = useQuery({
    ...neuroshillingSettingsQueryOptions({ path: { campaign_id: campaignId ?? '' } }),
    enabled: campaignId !== null,
  });
  // The activity feed the launch card renders. Unscoped by campaign on purpose:
  // `log_event` rows carry no campaign column, and the prefix filter is what
  // keeps a clear from touching neurocomment's history.
  const logs = useQuery(
    logsQueryOptions({ query: { event_prefix: LOG_PREFIX, limit: LOG_LIMIT } }),
  );
  // How many rows a clear would actually delete. Asked only while the confirmation
  // is open: the panel shows one page, so its length is no guide to the size of a
  // purge spanning the whole retention window, and an operator who cleared on that
  // impression once lost a month of history without noticing.
  const logCount = useQuery({
    ...logCountQueryOptions({ query: { event_prefix: LOG_PREFIX } }),
    enabled: confirmClearLogs,
  });

  const campaign = board.data?.campaign;
  const stored = settingsSnapshot.data?.scenario;
  const pool = board.data?.available ?? [];
  const roster = pool.filter((account) => account.assigned);
  const run = board.data?.run ?? {};
  const targets = board.data?.targets ?? [];
  // Имя аккаунта по идентификатору: терминал журнала и остановленные аккаунты называют
  // одни и те же строки, и оба должны называть их одинаково.
  const titleOf = (accountId: string) =>
    pool.find((account) => account.account_id === accountId)?.title ?? accountId;
  // Причины отказа для сводки замечаний в сайдбаре. Конвейер зовёт `launchBlockers` сам,
  // и это не расхождение: функция чистая, а аргументы у обоих одни и те же, поэтому
  // разойтись два вызова не могут — карточка просто остаётся самодостаточной и её можно
  // отрисовать одну. Общим здесь обязан быть ИСТОЧНИК списка, а не его вычисление.
  //
  // Пока сценарий не прочитан, список пуст, а не «всё плохо»: неизвестность — не
  // замечание, и сводка «3 замечания» на ещё не приехавших данных — ложь.
  const blockers =
    campaign === undefined || stored === undefined || stored.campaign_id !== campaignId
      ? []
      : launchBlockers(t, campaign, roster, targets, stored.roles ?? [], stored.steps ?? []);

  // Диалог, переживший смену кампании, правил бы черновики новой под шапкой старой —
  // а «Сохранить» записал бы это.
  useEffect(() => {
    setSettingsOpen(false);
    setApproveOpen(false);
    setDetailsOpen(false);
    setSaveConflict(false);
  }, [campaignId]);

  // Seed both forms and their edit token from the same campaign snapshot. SSE
  // refreshes the board under typing, but must never advance the token for an
  // unchanged draft: that would let an old form overwrite another editor's Save.
  useEffect(() => {
    const snapshot = settingsSnapshot.data;
    if (snapshot === undefined) return;
    const { campaign: current, scenario: currentScenario } = snapshot;
    if (draft?.campaignId === current.campaign_id && setup?.campaignId === current.campaign_id)
      return;
    const nextDraft = draftOf(current, currentScenario);
    const nextSetup = setupDraftOf(current);
    setDraft(nextDraft);
    setBaseline(JSON.stringify(nextDraft));
    setSetup(nextSetup);
    setSetupBaseline(JSON.stringify(nextSetup));
    setEditToken({
      campaignId: current.campaign_id,
      updatedAt: current.updated_at,
      campaign: current,
      accounts: snapshot.accounts,
    });
  }, [settingsSnapshot.data, draft, setup]);

  // «Отмена» действительно отменяет: оба черновика пересеваются из того, что лежит на
  // сервере, вместе со своими эталонами, поэтому диалог закрывается ЧИСТЫМ.
  //
  // Без этого закрытие лишь прятало правки: они переживали его и записывались следующим
  // «Сохранить настройки» — то есть уезжало на сервер то, что оператор считал брошенным.
  // Escape и клик по завесе делают то же, что кнопка: это один жест «уйти отсюда», и
  // разное поведение у трёх его видов было бы хуже, чем у одного.
  const discardDrafts = () => {
    const snapshot = settingsSnapshot.data;
    if (snapshot === undefined) return;
    const nextScenario = draftOf(snapshot.campaign, snapshot.scenario);
    const nextSetup = setupDraftOf(snapshot.campaign);
    setDraft(nextScenario);
    setBaseline(JSON.stringify(nextScenario));
    setSetup(nextSetup);
    setSetupBaseline(JSON.stringify(nextSetup));
    setEditToken({
      campaignId: snapshot.campaign.campaign_id,
      updatedAt: snapshot.campaign.updated_at,
      campaign: snapshot.campaign,
      accounts: snapshot.accounts,
    });
  };

  const fetchFreshSettings = (id: string) =>
    queryClient.fetchQuery({
      ...neuroshillingSettingsQueryOptions({ path: { campaign_id: id } }),
      staleTime: 0,
    });

  const discardAfterConflict = async () => {
    if (campaignId === null) return;
    try {
      const fresh = await fetchFreshSettings(campaignId);
      if (campaignIdRef.current !== campaignId) return;
      const nextDraft = draftOf(fresh.campaign, fresh.scenario);
      const nextSetup = setupDraftOf(fresh.campaign);
      setDraft(nextDraft);
      setBaseline(JSON.stringify(nextDraft));
      setSetup(nextSetup);
      setSetupBaseline(JSON.stringify(nextSetup));
      setEditToken({
        campaignId,
        updatedAt: fresh.campaign.updated_at,
        campaign: fresh.campaign,
        accounts: fresh.accounts,
      });
      setSaveConflict(false);
    } catch {
      // Keep the conflict and its reload guidance if the fresh read fails.
    }
  };

  const dirty = draft !== null && JSON.stringify(draft) !== baseline;
  const setupDirty = setup !== null && JSON.stringify(setup) !== setupBaseline;

  const createCampaign = useMutation(createNeuroshillingCampaignMutation());
  const deleteCampaign = useMutation(deleteNeuroshillingCampaignMutation());
  const updateCampaign = useMutation(updateNeuroshillingCampaignMutation());
  const saveSettings = useMutation(saveNeuroshillingSettingsMutation());
  const generateScenario = useMutation(generateNeuroshillingScenarioMutation());
  const approveScenario = useMutation(approveNeuroshillingScenarioMutation());
  const startCampaign = useMutation(startNeuroshillingCampaignMutation());
  const stopCampaign = useMutation(stopNeuroshillingCampaignMutation());
  const clearLogs = useMutation(clearLogsMutation());
  const busy =
    refreshing ||
    updateCampaign.isPending ||
    saveSettings.isPending ||
    generateScenario.isPending ||
    approveScenario.isPending ||
    startCampaign.isPending ||
    stopCampaign.isPending;
  const saving = updateCampaign.isPending || saveSettings.isPending;
  const recordCampaignConflict = (error: unknown) => {
    if (isCampaignChanged(error)) {
      setSaveConflict(true);
      setSettingsOpen(true);
    }
  };

  const adopt = (next: ScenarioDraft) => {
    setDraft(next);
    setBaseline(JSON.stringify(next));
  };

  // The atomic settings snapshot is out of the SSE scope on purpose; explicit
  // mutations refresh it without reseeding a draft under the operator's typing.
  const refresh = async () => {
    setRefreshing(true);
    try {
      const reads = [refreshBoard()];
      if (campaignId !== null) {
        reads.push(
          queryClient.invalidateQueries({
            queryKey: neuroshillingSettingsQueryOptions({ path: { campaign_id: campaignId } })
              .queryKey,
          }),
        );
      }
      await Promise.allSettled(reads);
    } finally {
      setRefreshing(false);
    }
  };

  const create = () => {
    const name = createName.trim();
    if (!name) return;
    setCreating(false);
    setCreateName('');
    void createCampaign
      .mutateAsync({ body: { name } })
      .then((created) => {
        // Select it, so the cards below land on the campaign just created.
        setSelected(created.campaign_id);
      })
      .catch(() => undefined)
      .finally(invalidateNeuroshilling);
  };

  // mutateAsync, never .mutate(): one useMutation is ONE callback slot, so a
  // second save before the first settles would take it over and drop the first
  // one's refresh.

  // The campaign half of the scenario card: the topic and everything else that
  // decides WHAT gets said, over an echo of the fields other cards own.
  const briefBody = (value: ScenarioDraft, snapshot: NonNullable<typeof editToken>) => ({
    ...campaignBody(snapshot.campaign, snapshot.accounts, snapshot.updatedAt),
    ...campaignFieldsOf(value),
  });

  const save = () => {
    if (campaign === undefined || draft === null || setup === null) return;
    if (editToken?.campaignId !== campaign.campaign_id) return;
    if ((!dirty && !setupDirty) || saveConflict || saving) return;
    const path = { campaign_id: campaign.campaign_id };
    const body = {
      ...campaignBody(editToken.campaign, editToken.accounts, editToken.updatedAt),
      ...(dirty ? campaignFieldsOf(draft) : {}),
      ...(setupDirty ? setupFieldsOf(setup) : {}),
    };
    void (async () => {
      const updated = dirty
        ? await saveSettings.mutateAsync({
            path,
            body: { campaign: body, scenario: scenarioBody(draft) },
          })
        : {
            campaign: await updateCampaign.mutateAsync({ path, body }),
            scenario: null,
            accounts: editToken.accounts,
          };
      if (campaignIdRef.current !== campaign.campaign_id) return;
      setEditToken({
        campaignId: campaign.campaign_id,
        updatedAt: updated.campaign.updated_at,
        campaign: updated.campaign,
        accounts: updated.accounts,
      });
      if (updated.scenario !== null) adopt(draftOf(updated.campaign, updated.scenario));
      if (setupDirty) {
        const next = setupDraftOf(updated.campaign);
        setSetup(next);
        setSetupBaseline(JSON.stringify(next));
      }
    })()
      .catch(recordCampaignConflict)
      .finally(refresh);
  };

  const generate = () => {
    if (
      campaign === undefined ||
      draft === null ||
      editToken?.campaignId !== campaign.campaign_id ||
      saveConflict
    )
      return Promise.resolve();
    const path = { campaign_id: campaign.campaign_id };
    // The model is briefed from the STORED topic, so what the operator typed has
    // to land before the ask goes out — otherwise a first generation is refused
    // for an empty topic that is plainly on screen.
    return updateCampaign
      .mutateAsync({ path, body: briefBody(draft, editToken) })
      .then(async () => {
        await generateScenario.mutateAsync({ path, body: generationAsk() });
        // Generation also changes the campaign version and clears its media slot.
        // Read the campaign and dialogue together before accepting a new edit token.
        const fresh = await fetchFreshSettings(campaign.campaign_id);
        if (campaignIdRef.current !== campaign.campaign_id) return;
        adopt(draftOf(fresh.campaign, fresh.scenario));
        const freshSetup = setupDraftOf(fresh.campaign);
        if (setupDirty) {
          if (JSON.stringify(freshSetup) !== setupBaseline) setSaveConflict(true);
        } else {
          setSetup(freshSetup);
          setSetupBaseline(JSON.stringify(freshSetup));
        }
        setEditToken({
          campaignId: campaign.campaign_id,
          updatedAt: fresh.campaign.updated_at,
          campaign: fresh.campaign,
          accounts: fresh.accounts,
        });
      })
      .catch((error: unknown) => {
        recordCampaignConflict(error);
        throw error;
      })
      .finally(refresh);
  };

  const requestGenerate = () => {
    // Generation replaces the stored dialogue outright, so an existing one is
    // confirmed first: one stray click would otherwise destroy every manual edit
    // with nothing to undo it.
    if ((stored?.steps ?? []).length > 0 || (draft?.steps ?? []).length > 0) {
      setConfirmGenerate(true);
      return;
    }
    void generate().catch(() => undefined);
  };

  const approve = () => {
    if (campaignId === null || editToken?.campaignId !== campaignId || saveConflict || dirty)
      return;
    void approveScenario
      .mutateAsync({
        path: { campaign_id: campaignId },
        body: { expected_updated_at: editToken.updatedAt },
      })
      .then(async () => {
        const fresh = await fetchFreshSettings(campaignId);
        if (campaignIdRef.current !== campaignId) return;
        adopt(draftOf(fresh.campaign, fresh.scenario));
        const freshSetup = setupDraftOf(fresh.campaign);
        if (setupDirty) {
          if (JSON.stringify(freshSetup) !== setupBaseline) setSaveConflict(true);
        } else {
          setSetup(freshSetup);
          setSetupBaseline(JSON.stringify(freshSetup));
        }
        setEditToken({
          campaignId,
          updatedAt: fresh.campaign.updated_at,
          campaign: fresh.campaign,
          accounts: fresh.accounts,
        });
      })
      .catch(recordCampaignConflict)
      .finally(refresh);
  };

  // Сколько персон и шагов просить у модели. Считается по тому, что уже собрано на
  // экране, а не двумя счётчиками рядом с кнопкой: счётчики называли те же два числа,
  // которыми и так распоряжаются «+ Реплика», «+ Реакция» и «+ Добавить роль», — и
  // расходились с ними на глазах, показывая «3», когда ролей в списке было пять.
  //
  // Пустому сценарию брать неоткуда, поэтому у обоих есть заготовка, и оба зажаты теми
  // же границами, что стояли на счётчиках: модели нужны хотя бы двое, чтобы вышел диалог.
  const generationAsk = () => ({
    persona_count: Math.min(MAX_ROLES, Math.max(2, draft?.roles.length || DEFAULT_PERSONAS)),
    step_count: Math.min(MAX_GENERATED_STEPS, Math.max(2, draft?.steps.length || DEFAULT_STEPS)),
  });

  const assignRole = (roleId: string, accountId: string | null) => {
    if (
      campaign === undefined ||
      editToken?.campaignId !== campaign.campaign_id ||
      busy ||
      saveConflict
    )
      return;
    // Роль играет РОВНО ОДИН аккаунт, поэтому назначение сначала снимается с того, кто
    // её держал, и только потом ставится выбранному: иначе «переназначить» оставляло бы
    // двух исполнителей одной роли, а укомплектованность считается множеством `role_id`
    // и разницы бы не заметила.
    //
    // Снятый аккаунт УХОДИТ из ростера, а не остаётся в нём без роли: с тех пор как
    // отдельной секции «Аккаунты» нет, «в кампании» и «играет роль» — одно и то же, а
    // аккаунт, числящийся за кампанией и ничего не играющий, лишь занят для остальных.
    const kept = editToken.accounts.filter(
      (account) => account.role_id !== roleId && account.account_id !== accountId,
    );
    const chosen = accountId === null ? [] : [{ account_id: accountId, role_id: roleId }];
    const accounts = [
      ...kept.map((account) => ({
        account_id: account.account_id,
        role_id: account.role_id ?? null,
        is_reserve: account.is_reserve ?? false,
      })),
      ...chosen.map((account) => ({ ...account, is_reserve: false })),
    ];
    void updateCampaign
      .mutateAsync({
        path: { campaign_id: campaign.campaign_id },
        body: campaignBody(editToken.campaign, accounts, editToken.updatedAt),
      })
      .then((updated) => {
        if (campaignIdRef.current === campaign.campaign_id) {
          setEditToken({
            campaignId: campaign.campaign_id,
            updatedAt: updated.updated_at,
            campaign: updated,
            accounts,
          });
        }
      })
      .catch(recordCampaignConflict)
      .finally(refresh);
  };

  // Fire-on-click, unlike the two forms above: there is nothing to save, and the
  // refusal an operator can still hit here is a race the board is about to show
  // them anyway.
  const runAction = (call: Promise<unknown>) => {
    void call.catch(recordCampaignConflict).finally(invalidateNeuroshilling);
  };

  return (
    // `max-w-shell`, а не `max-w-page`: страница стала двухколоночной, и на ширине
    // страницы (1000px) сайдбар в 328px оставил бы главной колонке меньше, чем ей нужно
    // под шесть узлов конвейера и таблицу.
    <PageFrame variant="full" className="tb-fadeup">
      <h1 className={cn('m-0 type-page-title', pageTitleSpacing())}>{t('neuroshilling.title')}</h1>

      {/* Колонки разъезжаются на `lg`, а ниже складываются в стопку. Порядок в стопке —
          порядок в разметке: сводка замечаний и выбор кампании стоят ВЫШЕ конвейера,
          потому что на узком экране сначала выбирают, а потом смотрят. */}
      <div className={boardLayout('launch')}>
        <SectionStack gap="compact" className="min-w-0">
          <ChecksBanner blockers={blockers} />

          <CampaignsCard
            campaignList={campaignList}
            campaignId={campaignId}
            onSelect={setSelected}
            onSettings={(id) => {
              setSelected(id);
              setSettingsOpen(true);
            }}
            onDelete={setDeleteFor}
            onToggleStatus={(target) => {
              // Кнопка одна, и что она сделает, решает статус: остановка — для того, что
              // уже бежит, запуск — для всего остального. Отказ ловит `runAction`, он же
              // обновляет доску: гонку, в которую оператор мог попасть, доска покажет.
              const running = target.status === 'running' || target.status === 'stopping';
              if (running) {
                runAction(stopCampaign.mutateAsync({ path: { campaign_id: target.campaign_id } }));
              } else if (target.campaign_id !== campaignId) {
                setSelected(target.campaign_id);
              } else if (editToken?.campaignId === target.campaign_id && !saveConflict) {
                runAction(
                  startCampaign.mutateAsync({
                    path: { campaign_id: target.campaign_id },
                    body: { expected_updated_at: editToken.updatedAt },
                  }),
                );
              }
            }}
            openActions={openActions}
            onToggleActions={(id) => {
              setOpenActions((current) => (current === id ? null : id));
            }}
            creating={creating}
            createName={createName}
            onStartCreate={() => {
              setCreating(true);
            }}
            onCancelCreate={() => {
              setCreating(false);
              setCreateName('');
            }}
            onCreateName={setCreateName}
            onCreate={create}
          />

          <HowItWorksCard />
        </SectionStack>

        <SectionStack className="min-w-0 flex-1">
          {campaign === undefined ||
          stored === undefined ||
          stored.campaign_id !== campaignId ? null : (
            <PipelineCard
              campaign={campaign}
              run={run}
              pool={pool}
              targets={targets}
              roles={stored.roles ?? []}
              steps={stored.steps ?? []}
              onStart={() => {
                if (editToken?.campaignId !== campaign.campaign_id || saveConflict) return;
                runAction(
                  startCampaign.mutateAsync({
                    path: { campaign_id: campaign.campaign_id },
                    body: { expected_updated_at: editToken.updatedAt },
                  }),
                );
              }}
              onStop={() => {
                runAction(
                  stopCampaign.mutateAsync({ path: { campaign_id: campaign.campaign_id } }),
                );
              }}
              busy={busy || editToken?.campaignId !== campaign.campaign_id || saveConflict}
            />
          )}

          {campaignList.length === 0 ? null : (
            <WorkBoardCard
              campaignList={campaignList}
              campaignId={campaignId}
              run={run}
              targets={targets}
              onSelect={(id) => {
                setSelected(id);
                setDetailsOpen(true);
              }}
            />
          )}

          {/* Терминал журнала — САМ по себе карточка со своим заголовком, счётчиком и
              очисткой, поэтому отдельной обёртки под «Лог кампаний» нет. Стоит последним
              в колонке и вне выбора кампании: `log_event` не несёт колонки кампании, лента
              отфильтрована префиксом и показывает нейрошиллинг целиком. */}
          <LogTerminal
            title={t('neuroshilling.launch.log')}
            logLines={logs.data?.items ?? []}
            onClear={() => {
              setConfirmClearLogs(true);
            }}
            accountName={titleOf}
          />
        </SectionStack>
      </div>

      {/* Подробности кампании: кто, в каком чате и что скажет. Ждёт ТЕ ЖЕ данные, что и
          настройки: клик по чужой строке сперва переключает выбор, и до прихода её доски
          показывать было бы нечего. */}
      {detailsOpen &&
      campaign !== undefined &&
      stored !== undefined &&
      stored.campaign_id === campaignId ? (
        <CampaignDetailsModal
          campaign={campaign}
          pool={pool}
          targets={targets}
          roles={stored.roles ?? []}
          steps={stored.steps ?? []}
          run={run}
          onOpenSettings={() => {
            setDetailsOpen(false);
            setSettingsOpen(true);
          }}
          onClose={() => {
            setDetailsOpen(false);
          }}
        />
      ) : null}

      {/* Настройки кампании: всё, что раньше стояло четырьмя карточками в колонке —
          ростер, цели с режимом прогона, роли и шаги. Диалог открывается карандашом в
          строке кампании и сохраняет ОБА черновика одной кнопкой. */}
      {settingsOpen &&
      campaign !== undefined &&
      stored !== undefined &&
      draft !== null &&
      setup !== null &&
      draft.campaignId === campaignId &&
      setup.campaignId === campaignId &&
      stored.campaign_id === campaignId ? (
        <CampaignSettingsModal
          name={campaign.name}
          dirty={dirty || setupDirty}
          busy={busy}
          saving={saving}
          conflict={saveConflict}
          onSave={save}
          onClose={() => {
            if (
              saveConflict ||
              (campaign !== undefined && editToken?.updatedAt !== campaign.updated_at)
            ) {
              void discardAfterConflict();
            } else {
              discardDrafts();
            }
            setSettingsOpen(false);
          }}
        >
          <CampaignSetupSection
            draft={setup}
            onDraft={setSetup}
            scenario={draft}
            onScenario={setDraft}
            reserveCount={
              roster.filter(
                (account) =>
                  account.is_reserve === true && (account.state ?? 'active') === 'active',
              ).length
            }
            live={campaign.status === 'running' || campaign.status === 'stopping'}
          />

          <ScenarioSection
            draft={draft}
            onDraft={setDraft}
            status={stored.scenario_status ?? 'draft'}
            dirty={dirty}
            onGenerate={requestGenerate}
            pool={pool}
            onAssignRole={assignRole}
            // «Утвердить» здесь ОТКРЫВАЕТ утверждение, а не утверждает: утверждают
            // прочитанное, и читать нечего, пока диалог с текстом не показан.
            onApprove={() => {
              setApproveOpen(true);
            }}
            busy={busy || saveConflict}
          />
        </CampaignSettingsModal>
      ) : null}

      {approveOpen && stored !== undefined && stored.campaign_id === campaignId ? (
        <ApproveModal
          roles={stored.roles ?? []}
          steps={stored.steps ?? []}
          status={stored.scenario_status ?? 'draft'}
          dirty={dirty}
          onRegenerate={requestGenerate}
          // Паузы правятся в черновике, а сопоставляются с показанным сценарием ПО
          // ИНДЕКСУ, поэтому при разной длине списков ручка не предлагается вовсе:
          // подписать чужой шаг хуже, чем не дать его тронуть.
          delays={
            draft !== null && draft.steps.length === (stored.steps ?? []).length
              ? draft.steps.map((step) => ({
                  min: step.delayMinSeconds,
                  max: step.delayMaxSeconds,
                }))
              : null
          }
          onDelay={(index, min, max) => {
            setDraft((current) =>
              current === null
                ? current
                : {
                    ...current,
                    steps: current.steps.map((step, at) =>
                      at === index ? { ...step, delayMinSeconds: min, delayMaxSeconds: max } : step,
                    ),
                  },
            );
          }}
          onApprove={() => {
            approve();
            setApproveOpen(false);
          }}
          onClose={() => {
            setApproveOpen(false);
          }}
          busy={busy || saveConflict}
        />
      ) : null}

      {confirmGenerate ? (
        <ConfirmModal
          title={t('neuroshilling.modal.regenerate.title')}
          body={t('neuroshilling.modal.regenerate.body')}
          confirmLabel={t('neuroshilling.modal.regenerate.confirm')}
          cancelLabel={t('neuroshilling.modal.regenerate.cancel')}
          onClose={() => {
            setConfirmGenerate(false);
          }}
          // Returning the promise keeps the dialog up (and pending) until the
          // model answers, and leaves it open on a refusal — a busy generation or
          // an exhausted daily budget is something the operator has to see.
          onConfirm={generate}
        />
      ) : null}

      {confirmClearLogs ? (
        // The count comes FIRST and the operator confirms against it: the panel
        // shows one page, and clearing on that impression once cost a month of
        // history. The prefix keeps the purge off neurocomment's rows.
        <ConfirmModal
          title={t('neuroshilling.modal.clearLogs.title')}
          body={t('neuroshilling.modal.clearLogs.body', { count: logCount.data?.matching ?? 0 })}
          confirmLabel={t('neuroshilling.modal.clearLogs.confirm')}
          cancelLabel={t('neuroshilling.modal.clearLogs.cancel')}
          onClose={() => {
            setConfirmClearLogs(false);
          }}
          onConfirm={() =>
            clearLogs
              .mutateAsync({ query: { event_prefix: LOG_PREFIX } })
              .finally(invalidateNeuroshilling)
          }
        />
      ) : null}

      {deleteFor ? (
        <ConfirmModal
          title={t('neuroshilling.modal.delete.title', { name: deleteFor.name })}
          body={t('neuroshilling.modal.delete.body')}
          confirmLabel={t('neuroshilling.modal.delete.confirm')}
          cancelLabel={t('neuroshilling.modal.delete.cancel')}
          onClose={() => {
            setDeleteFor(null);
          }}
          // Returning the promise keeps the dialog up (and pending) until the
          // DELETE lands, and leaves it open on a refusal — a running campaign
          // answers 409 and the operator has to see that.
          onConfirm={() => {
            const target = deleteFor.campaign_id;
            return deleteCampaign
              .mutateAsync({ path: { campaign_id: target } })
              .then(() => {
                if (selected === target) setSelected(null);
              })
              .finally(invalidateNeuroshilling);
          }}
        />
      ) : null}
    </PageFrame>
  );
}
