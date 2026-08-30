import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  approveNeuroshillingScenarioMutation,
  createNeuroshillingCampaignMutation,
  deleteNeuroshillingCampaignMutation,
  generateNeuroshillingScenarioMutation,
  neuroshillingBoardQueryOptions,
  neuroshillingCampaignsQueryOptions,
  neuroshillingScenarioQueryOptions,
  setNeuroshillingScenarioMutation,
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
import { useLogEventStream } from '@/shared/lib';
import { ConfirmModal } from '@/shared/ui';
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
// `getNeuroshillingScenario` is deliberately NOT here. The stream flushes on a
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

// `updateNeuroshillingCampaign` replaces the WHOLE form: a field left out of the
// body is written back as its schema default. Every caller edits one slice of it,
// so each starts from this echo of the campaign the board just returned rather
// than silently resetting the rest.
function campaignBody(
  campaign: NeuroshillingCampaign,
  accounts: NeuroshillingAccountAssignment[],
): NeuroshillingCampaignUpdate {
  return {
    name: campaign.name,
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
  const invalidateNeuroshilling = () => {
    void queryClient.invalidateQueries({
      predicate: (query) => {
        const id = (query.queryKey[0] as { _id?: string } | undefined)?._id;
        return id !== undefined && NEUROSHILLING_QUERY_IDS.has(id);
      },
    });
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

  const board = useQuery({
    ...neuroshillingBoardQueryOptions({ path: { campaign_id: campaignId ?? '' } }),
    enabled: campaignId !== null,
  });
  const scenario = useQuery({
    ...neuroshillingScenarioQueryOptions({ path: { campaign_id: campaignId ?? '' } }),
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
  const stored = scenario.data;
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
  }, [campaignId]);

  // The scenario form is seeded from the server EXACTLY ONCE per campaign — here,
  // when that campaign's two reads first agree — and afterwards only from the
  // response of a mutation the operator fired themselves. A resync on every
  // server value (the `useEffect(() => form.reset(server), [server])` shape the
  // settings page uses) would empty the form under the operator's hands: the
  // board query IS in the invalidation set above, and the log stream refetches it
  // 400 ms after every log row.
  useEffect(() => {
    if (campaign === undefined || stored === undefined) return;
    if (stored.campaign_id !== campaign.campaign_id) return;
    if (draft?.campaignId === campaign.campaign_id) return;
    const next = draftOf(campaign, stored);
    setDraft(next);
    setBaseline(JSON.stringify(next));
  }, [campaign, stored, draft]);

  // The setup form is seeded from the server exactly once per campaign, for the
  // same reason as the scenario form above: the board query IS in the invalidation
  // set, so resyncing on every server value would empty it under the operator.
  useEffect(() => {
    if (campaign === undefined) return;
    if (setup?.campaignId === campaign.campaign_id) return;
    const next = setupDraftOf(campaign);
    setSetup(next);
    setSetupBaseline(JSON.stringify(next));
  }, [campaign, setup]);

  // «Отмена» действительно отменяет: оба черновика пересеваются из того, что лежит на
  // сервере, вместе со своими эталонами, поэтому диалог закрывается ЧИСТЫМ.
  //
  // Без этого закрытие лишь прятало правки: они переживали его и записывались следующим
  // «Сохранить настройки» — то есть уезжало на сервер то, что оператор считал брошенным.
  // Escape и клик по завесе делают то же, что кнопка: это один жест «уйти отсюда», и
  // разное поведение у трёх его видов было бы хуже, чем у одного.
  const discardDrafts = () => {
    if (campaign === undefined) return;
    if (stored !== undefined && stored.campaign_id === campaign.campaign_id) {
      const nextScenario = draftOf(campaign, stored);
      setDraft(nextScenario);
      setBaseline(JSON.stringify(nextScenario));
    }
    const nextSetup = setupDraftOf(campaign);
    setSetup(nextSetup);
    setSetupBaseline(JSON.stringify(nextSetup));
  };

  const dirty = draft !== null && JSON.stringify(draft) !== baseline;
  const setupDirty = setup !== null && JSON.stringify(setup) !== setupBaseline;

  const createCampaign = useMutation(createNeuroshillingCampaignMutation());
  const deleteCampaign = useMutation(deleteNeuroshillingCampaignMutation());
  const updateCampaign = useMutation(updateNeuroshillingCampaignMutation());
  const saveScenario = useMutation(setNeuroshillingScenarioMutation());
  const generateScenario = useMutation(generateNeuroshillingScenarioMutation());
  const approveScenario = useMutation(approveNeuroshillingScenarioMutation());
  const startCampaign = useMutation(startNeuroshillingCampaignMutation());
  const stopCampaign = useMutation(stopNeuroshillingCampaignMutation());
  const clearLogs = useMutation(clearLogsMutation());
  const busy =
    updateCampaign.isPending ||
    saveScenario.isPending ||
    generateScenario.isPending ||
    approveScenario.isPending ||
    startCampaign.isPending ||
    stopCampaign.isPending;

  const adopt = (next: ScenarioDraft) => {
    setDraft(next);
    setBaseline(JSON.stringify(next));
  };

  // The scenario query is out of the SSE scope on purpose, so its own mutations
  // are the only thing that refreshes it.
  const refresh = () => {
    invalidateNeuroshilling();
    if (campaignId === null) return;
    void queryClient.invalidateQueries({
      queryKey: neuroshillingScenarioQueryOptions({ path: { campaign_id: campaignId } }).queryKey,
    });
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
  const briefBody = (current: NeuroshillingCampaign, value: ScenarioDraft) => ({
    ...campaignBody(current, rosterEcho()),
    ...campaignFieldsOf(value),
  });

  const save = () => {
    if (campaign === undefined || draft === null) return;
    const path = { campaign_id: campaign.campaign_id };
    void updateCampaign
      .mutateAsync({ path, body: briefBody(campaign, draft) })
      .then((updated) =>
        // Two writes, in this order and never in parallel: the PUT below always
        // returns the campaign to `draft`, so it must be the LAST thing to touch
        // the approval.
        saveScenario.mutateAsync({ path, body: scenarioBody(draft) }).then((saved) => {
          // Adopt the answer: the server has just minted real ids for roles the
          // form invented, and keeping the invented keys would mint a second set
          // on the next save.
          adopt(draftOf(updated, saved));
        }),
      )
      .catch(() => undefined)
      .finally(refresh);
  };

  const generate = () => {
    if (campaign === undefined || draft === null) return Promise.resolve();
    const path = { campaign_id: campaign.campaign_id };
    // The model is briefed from the STORED topic, so what the operator typed has
    // to land before the ask goes out — otherwise a first generation is refused
    // for an empty topic that is plainly on screen.
    return updateCampaign
      .mutateAsync({ path, body: briefBody(campaign, draft) })
      .then((updated) =>
        generateScenario.mutateAsync({ path, body: generationAsk() }).then((generated) => {
          // Overwriting the form IS what the button means, and the media slot goes
          // with it: the generation cleared `media_step_position` in the write that
          // stored these steps, while `updated` is the echo of the PUT above, which
          // ran BEFORE that and answered with the position the form had sent it.
          // The draft is seeded from the server once per campaign, so adopting the
          // echo whole would keep the stale position on screen and save it back.
          adopt(draftOf({ ...updated, media_step_position: null }, generated));
        }),
      )
      .finally(refresh);
  };

  const requestGenerate = () => {
    // Generation replaces the stored dialogue outright, so an existing one is
    // confirmed first: one stray click would otherwise destroy every manual edit
    // with nothing to undo it.
    if ((stored?.steps ?? []).length > 0) {
      setConfirmGenerate(true);
      return;
    }
    void generate().catch(() => undefined);
  };

  const approve = () => {
    if (campaignId === null) return;
    void approveScenario
      .mutateAsync({ path: { campaign_id: campaignId } })
      .catch(() => undefined)
      .finally(refresh);
  };

  // Card 4 is explicit-save too, and it owns a DIFFERENT slice of the same PUT:
  // its fields go over an echo of the ones the other cards own.
  const saveSetup = () => {
    if (campaign === undefined || setup === null) return;
    void updateCampaign
      .mutateAsync({
        path: { campaign_id: campaign.campaign_id },
        body: {
          ...campaignBody(campaign, rosterEcho()),
          ...setupFieldsOf(setup),
        },
      })
      .then((updated) => {
        // Adopt the answer, so the fields the server normalised (a clamped pause,
        // a rejected target dropped from the blob) stop reading as unsaved edits.
        const next = setupDraftOf(updated);
        setSetup(next);
        setSetupBaseline(JSON.stringify(next));
      })
      .catch(() => undefined)
      .finally(invalidateNeuroshilling);
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

  // Ростер как он есть, для тех сохранений, которые его не трогают: PUT кампании
  // заменяет весь список, поэтому форма, правящая другую его половину, обязана вернуть
  // назначения нетронутыми. Раньше это делала `assignmentsOf`, собиравшая роль и резерв
  // из пула по идентификатору; у самого ростера они уже есть, так что искать негде.
  const rosterEcho = () =>
    roster.map((account) => ({
      account_id: account.account_id,
      role_id: account.role_id ?? null,
      is_reserve: account.is_reserve ?? false,
    }));

  // Роль → аккаунт. Роль играет РОВНО ОДИН аккаунт, поэтому назначение сначала
  // снимается со всех, кто её держал, и только потом ставится выбранному: без этого
  // «переназначить» оставляло бы двух исполнителей одной роли, а сервер считает
  // укомплектованность множеством `role_id` и не заметил бы разницы.
  const assignRole = (roleId: string, accountId: string | null) => {
    if (campaign === undefined) return;
    // Роль играет РОВНО ОДИН аккаунт, поэтому назначение сначала снимается с того, кто
    // её держал, и только потом ставится выбранному: иначе «переназначить» оставляло бы
    // двух исполнителей одной роли, а укомплектованность считается множеством `role_id`
    // и разницы бы не заметила.
    //
    // Снятый аккаунт УХОДИТ из ростера, а не остаётся в нём без роли: с тех пор как
    // отдельной секции «Аккаунты» нет, «в кампании» и «играет роль» — одно и то же, а
    // аккаунт, числящийся за кампанией и ничего не играющий, лишь занят для остальных.
    const kept = roster.filter(
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
        body: campaignBody(campaign, accounts),
      })
      .catch(() => undefined)
      .finally(invalidateNeuroshilling);
  };

  // Fire-on-click, unlike the two forms above: there is nothing to save, and the
  // refusal an operator can still hit here is a race the board is about to show
  // them anyway.
  const runAction = (call: Promise<unknown>) => {
    void call.catch(() => undefined).finally(invalidateNeuroshilling);
  };

  return (
    // `max-w-shell`, а не `max-w-page`: страница стала двухколоночной, и на ширине
    // страницы (1000px) сайдбар в 328px оставил бы главной колонке меньше, чем ей нужно
    // под шесть узлов конвейера и таблицу.
    <div className="tb-fadeup mx-auto max-w-shell">
      <h1 className="m-0 mb-xl type-page-title">{t('neuroshilling.title')}</h1>

      {/* Колонки разъезжаются на `lg`, а ниже складываются в стопку. Порядок в стопке —
          порядок в разметке: сводка замечаний и выбор кампании стоят ВЫШЕ конвейера,
          потому что на узком экране сначала выбирают, а потом смотрят. */}
      <div className="flex flex-col gap-lg lg:flex-row lg:items-start">
        <div className="flex flex-col gap-md lg:w-sidebar lg:shrink-0">
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
              runAction(
                running
                  ? stopCampaign.mutateAsync({ path: { campaign_id: target.campaign_id } })
                  : startCampaign.mutateAsync({ path: { campaign_id: target.campaign_id } }),
              );
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
        </div>

        <div className="flex min-w-0 flex-1 flex-col gap-lg">
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
                runAction(
                  startCampaign.mutateAsync({ path: { campaign_id: campaign.campaign_id } }),
                );
              }}
              onStop={() => {
                runAction(
                  stopCampaign.mutateAsync({ path: { campaign_id: campaign.campaign_id } }),
                );
              }}
              busy={busy}
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
        </div>
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
          onSave={() => {
            // Диалог один, кнопка одна — но эндпоинта по-прежнему два, и сохраняется
            // только тронутое: пустой PUT сценария сбросил бы утверждение ни за что.
            if (dirty) save();
            if (setupDirty) saveSetup();
          }}
          onClose={() => {
            discardDrafts();
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
            busy={busy}
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
          busy={busy}
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
    </div>
  );
}
