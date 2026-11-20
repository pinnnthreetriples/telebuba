import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { createPortal } from 'react-dom';
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
import {
  cn,
  errorMessage,
  formatLocalTime,
  mutationErrorText,
  useTransientFeedback,
} from '@/shared/lib';
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
  IconButton,
  InlineChipEditor,
  Notice,
  SegmentedControl,
  StatGrid,
  toastError,
} from '@/shared/ui';
import { HEADING_ICON_TILE } from '@/shared/design-system';
import { DeleteAccountModal, type DeleteReason } from '@/widgets/accounts-table';
import { DialogueFeed } from '@/widgets/dialogue-feed';
import {
  AddToFolderModal,
  type BoardTool,
  FolderChannels,
  ACCOUNT_DRAG_TYPE,
  ChannelLanguageShortages,
  FolderBack,
  FolderGrid,
  FolderSettingsModal,
  FolderSummary,
  newFolder,
  personaFor,
  UNFILED,
  useWarmingFolders,
  type WarmingFolder,
  ChannelPoolFill,
  DmPauseBanner,
  GeoMismatchBanner,
  HandoffModal,
  type HandoffMode,
  needsGeoConsent,
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

// Первая загрузка: каркас страницы той же раскладки, а не строка «Загрузка…» —
// страница не прыгает, когда данные приходят.
const SKELETON = 'animate-pulse rounded-md bg-canvas motion-reduce:animate-none';

function WarmingSkeleton() {
  const { t } = useTranslation();
  return (
    <div aria-busy="true" className="tb-fadeup">
      <span className="sr-only">{t('warming.loading')}</span>
      <div className="mb-6 flex flex-wrap items-center justify-between gap-4">
        <h1 className="m-0 type-h1">{t('warming.titleFull')}</h1>
        <div className={cn(SKELETON, 'h-touch w-sidebar max-w-full')} />
      </div>
      <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-[340px_minmax(0,1fr)]">
        <Card className="flex flex-col gap-2 p-4">
          {[0, 1, 2, 3, 4].map((row) => (
            <div key={row} className={cn(SKELETON, 'h-touch')} />
          ))}
        </Card>
        <Card className="grid grid-cols-1 gap-3 p-4 sm:grid-cols-[repeat(auto-fill,minmax(280px,1fr))]">
          {[0, 1, 2].map((tile) => (
            <div key={tile} className={cn(SKELETON, 'py-16')} />
          ))}
        </Card>
      </div>
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
  // "Запустить пул" opens the start dialog for every idle account instead of firing.
  // Окно запуска пула: кого запускаем (все готовые или выбранные) и сколько не готовых
  // осталось за бортом.
  const [poolStart, setPoolStart] = useState<{ ids: string[]; skipped: number } | null>(null);
  // Выбранные галочками готовые аккаунты: «В папку», «Прогреть» и перетаскивание пачкой.
  const [selectedIds, setSelectedIds] = useState<ReadonlySet<string>>(new Set());
  // «Вернуть в прогрев» opens the same start dialog; the account warms again from zero
  // as its next wave.
  const [rewarmFor, setRewarmFor] = useState<WarmedAccount | null>(null);
  // «Удалить» on a spam-blocked card opens the ordinary account delete dialog.
  const [deletingId, setDeletingId] = useState<string | null>(null);
  // «В нейрокомментинг» asks for the start mode first (item 2.4).
  const [handoffFor, setHandoffFor] = useState<string | null>(null);
  // The survival tile in the header opens its panel under the banners (stage 3).
  const [survivalOpen, setSurvivalOpen] = useState(false);
  const [stopPoolOpen, setStopPoolOpen] = useState(false);
  const [channelToRemove, setChannelToRemove] = useState<string | null>(null);
  const accountFeedback = useTransientFeedback();
  // «Прогрев пачками» (прототип): папки, открытая папка (null — экран всех папок),
  // окна папки и «В папку».
  const folderStore = useWarmingFolders();
  const [folderView, setFolderView] = useState<string | null>(null);
  // Вкладка внутри папки: карточки аккаунтов, переписка папки или её каналы.
  const [folderTab, setFolderTab] = useState<'accounts' | 'dialogues' | 'channels'>('accounts');
  const [folderEdit, setFolderEdit] = useState<WarmingFolder | null>(null);
  // «В папку»: кого добавляем и (после броска на папку) в какую папку.
  const [addTo, setAddTo] = useState<{ accountIds: string[]; folderId?: string } | null>(null);
  const [draggingId, setDraggingId] = useState<string | null>(null);
  const channelFeedback = useTransientFeedback();

  const { data, isPending, isError, dataUpdatedAt, refetch } = useQuery({
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

  if (isPending) return <WarmingSkeleton />;
  // Ошибка без данных — страницы нет: сказать и дать повторить. Ошибка ОПРОСА при уже
  // загруженных данных страницу не убирает (раньше один сбойный опрос стирал всё):
  // данные остаются, сверху — что связь пропала и от какого они времени.
  if (data === undefined) {
    return (
      <div role="alert" className="tb-fadeup">
        <h1 className="mb-6 type-h1">{t('warming.titleFull')}</h1>
        <Notice tone="danger" className="flex flex-wrap items-center gap-3">
          <span className="flex-1">{t('warming.error')}</span>
          <Button
            size="sm"
            onClick={() => {
              void refetch();
            }}
          >
            {t('warming.states.retry')}
          </Button>
        </Notice>
      </div>
    );
  }
  const stale = isError;

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
  // Нечего показывать вовсе: ни готовых, ни греющихся, ни прогретых.
  const nothingYet = idle.length === 0 && warming.length === 0 && warmed.length === 0;
  // With the readiness check switched off the engine starts a not-ready account, so
  // the button does too; the reasons stay on the row as a warning, not a block.
  const enforceReadiness = data.settings.enforce_readiness ?? true;
  // The pool starts what each card's own Start would: a not-ready account is left
  // out while the readiness check is on, and the dialog says how many.
  const poolIdle = idle.filter((a) => (a.readiness?.ready ?? false) || !enforceReadiness);
  // Выбор держится только за тех, кого ещё можно запустить: ушедший в прогрев выпадает.
  const picked = poolIdle.filter((a) => selectedIds.has(a.account_id));
  const pickedIds = picked.map((a) => a.account_id);
  const allPicked = poolIdle.length > 0 && picked.length === poolIdle.length;
  const togglePick = (accountId: string) => {
    setSelectedIds((ids) => {
      const next = new Set(ids);
      if (next.has(accountId)) next.delete(accountId);
      else next.add(accountId);
      return next;
    });
  };
  const clearPicked = () => {
    setSelectedIds(new Set());
  };
  // Every card on the page whose phone/proxy pair still waits for a consent.
  const geoMismatches = [...idle, ...warming].filter(needsGeoConsent).length;
  // Папки: экран всех папок, а внутри папки — её карточки.
  const { folders, assignment } = folderStore;
  const inView = (accountId: string, view: string) =>
    view === UNFILED ? !assignment[accountId] : assignment[accountId] === view;
  const membersOf = (view: string) => warming.filter((a) => inView(a.account_id, view));
  // Пока папок нет, «В прогреве» выглядит как раньше: карточки сразу. С первой папкой
  // первым экраном становятся папки, а карточки — внутри них.
  const byFolders = folders.length > 0;
  const overviewOpen = byFolders && folderView === null;
  const shownWarming = !byFolders ? warming : folderView === null ? [] : membersOf(folderView);
  const openFolder = folders.find((f) => f.id === folderView);
  const folderMembers = (folderId: string) =>
    Object.values(assignment).filter((id) => id === folderId).length;
  const openFolderCaption = openFolder
    ? t('warming.folders.caption', {
        count: folderMembers(openFolder.id),
        warming: membersOf(openFolder.id).length,
      })
    : '';
  // Старт пачки в папку: у каждого аккаунта настройки папки, тип — по её смеси.
  const startIntoFolder = (folderId: string, onlyGeoMatching: boolean, accountIds: string[]) => {
    const folder = folders.find((f) => f.id === folderId);
    if (!folder) return;
    const picked = idle.filter((a) => accountIds.includes(a.account_id));
    const targets = onlyGeoMatching ? picked.filter((a) => !needsGeoConsent(a)) : picked;
    const from = folderMembers(folderId);
    folderStore.assign(
      targets.map((a) => a.account_id),
      folderId,
    );
    runBatch(
      targets.map((a, index) => {
        // Нет нужного устройства — идёт со своего основного, а не не идёт вовсе.
        const own = a.devices ?? [];
        const device =
          folder.device === 'primary' || !own.includes(folder.device) ? own[0] : folder.device;
        return startAccount(a.account_id, {
          target_days: folder.days,
          activity_persona: personaFor(folder.mix, from + index),
          start_delay_hours: folder.delayHours,
          spread_start: folder.spread,
          accept_geo_mismatch: !onlyGeoMatching,
          device_mode: device,
        });
      }),
    );
  };
  // Брошенный на папку готовый аккаунт: то же окно «В папку», папка уже выбрана.
  const dropIntoFolder = (folderId: string, dropped: string) => {
    setDraggingId(null);
    const accountIds = dropped.split(',').filter((id) => idle.some((a) => a.account_id === id));
    if (accountIds.length) setAddTo({ accountIds, folderId });
  };
  // Каналы прогрева — общий пул, теперь окном из шапки «В прогреве».
  const channelsPanel = (
    <>
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
        refillRequest={data.pool_refill_request}
        onPoolChanged={invalidate}
      />
    </>
  );
  // Папка аккаунта для приписки к собеседнику в переписке (только чужая папка).
  const partnerTag = (accountId: string) => {
    const folder = folderStore.folderOf(accountId);
    if (!folder || folder.id === folderView) return undefined;
    return folder.name;
  };
  const boardTools: BoardTool[] = [
    {
      key: 'dialogues',
      icon: 'users',
      title: t('warming.dialogues.title'),
      subtitle: t('warming.folders.dialoguesAll'),
      content: <DialogueFeed bare nameTag={partnerTag} />,
    },
    {
      key: 'channels',
      icon: 'link',
      title: t('warming.channels.title'),
      subtitle: t('warming.channels.hint'),
      content: channelsPanel,
    },
  ];
  // Аккаунты открытой папки: и греющиеся, и уже отданные в неё (для переписки).
  const folderAccountIds = new Set(
    folderView === null
      ? []
      : folderView === UNFILED
        ? membersOf(UNFILED).map((a) => a.account_id)
        : Object.entries(assignment)
            .filter(([, id]) => id === folderView)
            .map(([accountId]) => accountId),
  );
  const folderTabContent =
    folderView === null || folderTab === 'accounts' ? undefined : folderTab === 'dialogues' ? (
      <DialogueFeed bare accountIds={folderAccountIds} nameTag={partnerTag} />
    ) : (
      <FolderChannels accounts={membersOf(folderView)} channelLabels={channelLabels} />
    );
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
              {
                value: errors,
                label: t('warming.counter.errors'),
                // Ноль ошибок — не тревога: красный только когда они есть.
                tone: errors > 0 ? 'danger' : 'default',
              },
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
            // Остановка всего пула — редкое действие: второстепенная кнопка и
            // подтверждение, а не самая заметная заливка на странице.
            variant={poolOn ? 'secondary' : 'primary'}
            size="sm"
            disabled={
              bulkBusy || start.isPending || stop.isPending || (!poolOn && !poolIdle.length)
            }
            onClick={() => {
              if (poolOn) setStopPoolOpen(true);
              else
                setPoolStart({
                  ids: poolIdle.map((a) => a.account_id),
                  skipped: idle.length - poolIdle.length,
                });
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
      {stale ? (
        <div className="mb-4">
          <Notice tone="warning" role="status" className="flex items-center gap-2">
            <Icon name="alert-triangle" size={14} className="shrink-0" />
            {t('warming.states.stale', {
              time: formatLocalTime(new Date(dataUpdatedAt).toISOString()),
            })}
          </Notice>
        </div>
      ) : null}
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
      {nothingYet ? (
        <Card className="flex flex-col items-center gap-3 px-6 py-16 text-center">
          <span className={HEADING_ICON_TILE}>
            <Icon name="users" size={16} />
          </span>
          <div className="type-h3">{t('warming.states.nothingTitle')}</div>
          <p className="m-0 type-body text-content-muted">{t('warming.states.nothingBody')}</p>
          <Button
            variant="primary"
            size="sm"
            onClick={() => {
              window.location.assign('/accounts');
            }}
          >
            {t('warming.states.nothingAction')}
          </Button>
        </Card>
      ) : (
        <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-[340px_minmax(0,1fr)]">
          <div className="flex min-w-0 flex-col gap-4">
            <Card className="p-4">
              <CardHeader
                className="mb-3"
                title={t('warming.ready.title')}
                badge={<Badge>{idle.length}</Badge>}
              >
                <Button
                  size="sm"
                  className="gap-2"
                  disabled={poolIdle.length === 0 || bulkBusy}
                  onClick={() => {
                    // Выбранные — если есть, иначе все готовые.
                    setAddTo({
                      accountIds: picked.length ? pickedIds : poolIdle.map((a) => a.account_id),
                    });
                  }}
                >
                  <Icon name="folder" size={14} />
                  {t('warming.folders.add.button')}
                </Button>
              </CardHeader>
              {poolIdle.length > 1 ? (
                <div className="mb-2 flex flex-wrap items-center gap-2 px-1">
                  <label className="flex items-center gap-2 type-small text-content-muted">
                    <input
                      type="checkbox"
                      className="size-spinner shrink-0 accent-action-primary"
                      checked={allPicked}
                      ref={(element) => {
                        if (element) element.indeterminate = picked.length > 0 && !allPicked;
                      }}
                      onChange={() => {
                        setSelectedIds(
                          allPicked ? new Set() : new Set(poolIdle.map((a) => a.account_id)),
                        );
                      }}
                    />
                    {picked.length
                      ? t('warming.ready.picked', { count: picked.length })
                      : t('warming.ready.pickAll')}
                  </label>
                </div>
              ) : null}
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
                    const reasons = (account.readiness?.reasons ?? []).map((reason) => {
                      const key = readinessReasonKey(reason);
                      return key ? t(key) : reason;
                    });
                    const blockers = reasons.join(', ');
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
                        // Готовый аккаунт тащится в папку: на карточку папки или в
                        // открытую папку. Бросок открывает «Добавить в папку».
                        // Выбранный тащит за собой всех выбранных.
                        draggable={startable && byFolders}
                        onDragStart={(event) => {
                          const ids = selectedIds.has(account.account_id)
                            ? pickedIds
                            : [account.account_id];
                          event.dataTransfer.setData(ACCOUNT_DRAG_TYPE, ids.join(','));
                          event.dataTransfer.effectAllowed = 'move';
                          setDraggingId(account.account_id);
                        }}
                        onDragEnd={() => {
                          setDraggingId(null);
                        }}
                        className={cn(
                          'group rounded-md border px-3 py-2',
                          selectedIds.has(account.account_id)
                            ? 'border-action-primary bg-info-tint'
                            : 'border-line bg-surface-card',
                          startable && byFolders && 'cursor-grab active:cursor-grabbing',
                          (draggingId === account.account_id ||
                            (draggingId !== null &&
                              selectedIds.has(draggingId) &&
                              selectedIds.has(account.account_id))) &&
                            'opacity-50',
                        )}
                      >
                        <div className="flex items-center gap-2">
                          {startable && poolIdle.length > 1 ? (
                            <input
                              type="checkbox"
                              className="size-spinner shrink-0 accent-action-primary"
                              aria-label={t('warming.ready.pickOne', { name })}
                              checked={selectedIds.has(account.account_id)}
                              onChange={() => {
                                togglePick(account.account_id);
                              }}
                            />
                          ) : null}
                          <AccountAvatar
                            account={account}
                            className="size-icon shrink-0 rounded-full"
                            fallbackClassName="text-small font-medium bg-info-tint text-info-strong"
                          />
                          <div className="min-w-0 flex-1">
                            <div className="flex items-center gap-1">
                              <span className="truncate type-body-medium">{name}</span>
                              {showPhone ? null : flag}
                            </div>
                            <div className="flex min-w-0 items-center gap-1 type-small text-content-muted">
                              {showPhone ? <span className="truncate">{account.phone}</span> : null}
                              {showPhone ? flag : null}
                              <Icon
                                name="shield-check"
                                size={12}
                                className={`ml-1 shrink-0 ${tTone}`}
                              />
                              <span className={`font-medium ${tTone}`}>{trust ?? '—'}</span>
                              {ptype ? (
                                <span className="ml-1 inline-flex shrink-0 items-center gap-1">
                                  {pc ? (
                                    <span
                                      className={`fi fi-${pc} h-flag w-flag shrink-0 rounded-[2px] shadow-ring`}
                                    />
                                  ) : null}
                                  {proxyTypeLabel(ptype)}
                                </span>
                              ) : null}
                              {account.geo_accepted ? (
                                <span className="tb-tip ml-1 inline-flex shrink-0">
                                  <button
                                    type="button"
                                    aria-label={t('warming.geo.accepted')}
                                    className="inline-flex text-content-subtle"
                                  >
                                    <Icon name="globe" size={12} />
                                  </button>
                                  <span role="tooltip" className="tb-tip-pop">
                                    {t('warming.geo.accepted')}
                                  </span>
                                </span>
                              ) : null}
                              <SpamBlockBadge blockedAt={account.spam_blocked_at} />
                            </div>
                          </div>
                          {/* Что мешает старту — иконками предупреждающего цвета; что именно,
                            говорит подсказка. Текст целиком остаётся описанием кнопки. */}
                          {reasons.length > 0 ? (
                            <span className="tb-tip inline-flex shrink-0">
                              <button
                                type="button"
                                aria-label={blockers}
                                className="inline-flex rounded-full"
                              >
                                <Badge tone="warning">
                                  <Icon name="alert-triangle" size={12} />
                                  {reasons.length}
                                </Badge>
                              </button>
                              <span role="tooltip" className="tb-tip-pop">
                                {blockers}
                              </span>
                            </span>
                          ) : null}
                          <IconButton
                            shape="circle"
                            tone={startable ? 'action' : 'neutral'}
                            disabled={!startable || busyIds.has(account.account_id)}
                            aria-label={
                              startable ? t('warming.ready.start') : t('warming.ready.unavailable')
                            }
                            // Причина недоступности — в подсказке, как было у кнопки.
                            title={ready || !blockers ? t('warming.ready.start') : blockers}
                            aria-describedby={!ready && blockers ? blockersId : undefined}
                            // Пять ярких ▶ подряд шумят: на широком экране кнопка проявляется
                            // при наведении, фокусе и у выбранной строки. На узком (тач без
                            // наведения) видна всегда.
                            className={cn(
                              'transition-opacity duration-state',
                              !selectedIds.has(account.account_id) &&
                                'lg:opacity-0 lg:group-focus-within:opacity-100 lg:group-hover:opacity-100',
                            )}
                            onClick={() => {
                              setWarmDaysFor(account);
                            }}
                          >
                            <Icon name="play" size={14} />
                          </IconButton>
                        </div>
                        {!ready && blockers ? (
                          <span id={blockersId} className="sr-only">
                            {blockers}
                          </span>
                        ) : null}
                      </div>
                    );
                  })
                )}
              </div>
            </Card>

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
              warming={shownWarming}
              tools={boardTools}
              toolbar={
                <>
                  {draggingId !== null ? (
                    <p className="m-0 mb-3 flex items-center gap-2 type-small text-info-strong">
                      <Icon name="folder" size={14} className="shrink-0" />
                      {t(openFolder ? 'warming.states.dropHere' : 'warming.states.dropOnFolder')}
                    </p>
                  ) : null}
                  {!byFolders ? (
                    <div className="mb-3 flex justify-end">
                      <Button
                        variant="dashed"
                        size="sm"
                        className="gap-2"
                        onClick={() => {
                          setFolderEdit(newFolder(data.default_target_days ?? 14));
                        }}
                      >
                        <Icon name="folder-plus" size={14} />
                        {t('warming.folders.create')}
                      </Button>
                    </div>
                  ) : folderView === null ? null : (
                    <>
                      <FolderBack
                        onBack={() => {
                          setFolderView(null);
                        }}
                      />
                      {openFolder ? (
                        <FolderSummary
                          folder={openFolder}
                          caption={openFolderCaption}
                          onEdit={() => {
                            setFolderEdit(openFolder);
                          }}
                        />
                      ) : (
                        <div className="mb-3 type-body-medium">{t('warming.folders.unfiled')}</div>
                      )}
                      <SegmentedControl
                        className="mb-3"
                        value={folderTab}
                        ariaLabel={t('warming.folders.tabs.label')}
                        options={(['accounts', 'dialogues', 'channels'] as const).map((tab) => ({
                          value: tab,
                          label: t(`warming.folders.tabs.${tab}`),
                        }))}
                        onChange={setFolderTab}
                      />
                    </>
                  )}
                </>
              }
              overview={
                folderTabContent ??
                (overviewOpen ? (
                  <FolderGrid
                    folders={folders}
                    membersOf={membersOf}
                    unfiled={membersOf(UNFILED)}
                    onOpen={(view) => {
                      setFolderView(view);
                      setFolderTab('accounts');
                    }}
                    dragging={draggingId !== null}
                    onDropAccount={dropIntoFolder}
                    onCreate={() => {
                      setFolderEdit(newFolder(data.default_target_days ?? 14));
                    }}
                  />
                ) : undefined)
              }
              emptyText={openFolder ? t('warming.folders.empty') : undefined}
              dragging={draggingId !== null && openFolder !== undefined}
              onDropAccount={
                openFolder
                  ? (accountId) => {
                      dropIntoFolder(openFolder.id, accountId);
                    }
                  : undefined
              }
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
          </div>
        </div>
      )}

      {/* Выбрано несколько готовых: действия над ними — в закреплённой панели снизу,
          чтобы не искать их в начале длинного списка. */}
      {/* Порталом в body: у обёртки страницы анимация с transform, и `fixed` внутри неё
          считался бы от обёртки, а не от окна — панель висела посреди страницы. */}
      {picked.length > 0
        ? createPortal(
            <div className="pointer-events-none fixed inset-x-0 bottom-4 z-sticky flex justify-center px-4">
              <div
                role="toolbar"
                aria-label={t('warming.ready.picked', { count: picked.length })}
                className="pointer-events-auto flex flex-wrap items-center gap-2 rounded-full border border-line bg-surface-card py-2 pl-4 pr-2 shadow-pop"
              >
                <span className="type-body-medium">
                  {t('warming.ready.picked', { count: picked.length })}
                </span>
                <Button
                  size="sm"
                  className="gap-2"
                  disabled={bulkBusy}
                  onClick={() => {
                    setAddTo({ accountIds: pickedIds });
                  }}
                >
                  <Icon name="folder" size={14} />
                  {t('warming.folders.add.button')}
                </Button>
                <Button
                  size="sm"
                  variant="primary"
                  className="gap-2"
                  disabled={bulkBusy}
                  onClick={() => {
                    setPoolStart({ ids: pickedIds, skipped: 0 });
                  }}
                >
                  <Icon name="play" size={14} />
                  {t('warming.ready.startPicked', { count: picked.length })}
                </Button>
                <IconButton
                  shape="circle"
                  aria-label={t('warming.ready.clearPick')}
                  title={t('warming.ready.clearPick')}
                  onClick={clearPicked}
                >
                  <Icon name="close" size={14} />
                </IconButton>
              </div>
            </div>,
            document.body,
          )
        : null}

      {folderEdit ? (
        <FolderSettingsModal
          initial={folderEdit}
          members={folderEdit.id ? folderMembers(folderEdit.id) : 0}
          onClose={() => {
            setFolderEdit(null);
          }}
          onSave={(folder) => {
            const id = folderStore.save(folder);
            setFolderView(id);
          }}
          onDelete={() => {
            folderStore.remove(folderEdit.id);
            setFolderView(null);
          }}
        />
      ) : null}

      {addTo ? (
        <AddToFolderModal
          accountIds={addTo.accountIds}
          initialFolderId={addTo.folderId}
          skipped={addTo.folderId ? 0 : idle.length - poolIdle.length}
          geoMismatch={
            idle.filter((a) => addTo.accountIds.includes(a.account_id) && needsGeoConsent(a)).length
          }
          folders={folders}
          startIndex={folderMembers}
          onCreateFolder={() => {
            setAddTo(null);
            setFolderEdit(newFolder(data.default_target_days ?? 14));
          }}
          onClose={() => {
            setAddTo(null);
          }}
          onConfirm={(folderId, onlyGeoMatching) => {
            startIntoFolder(folderId, onlyGeoMatching, addTo.accountIds);
            clearPicked();
          }}
        />
      ) : null}

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

      {poolStart ? (
        <WarmDaysModal
          poolAccountIds={poolStart.ids}
          poolSkipped={poolStart.skipped}
          poolGeoMismatch={
            poolIdle.filter((a) => poolStart.ids.includes(a.account_id) && needsGeoConsent(a))
              .length
          }
          defaultDays={data.default_target_days}
          cadence={data.cadence_preview}
          onClose={() => {
            setPoolStart(null);
          }}
          onConfirm={(choice) => {
            // Same length, cadence and window for all; the backend draws each account
            // its own moment inside the window. «Только совпадающие» leaves out every
            // account whose geo still waits for a consent.
            const request = { ...startChoice(choice), spread_start: true };
            const chosen = poolIdle.filter((a) => poolStart.ids.includes(a.account_id));
            const targets = choice.onlyGeoMatching
              ? chosen.filter((a) => !needsGeoConsent(a))
              : chosen;
            clearPicked();
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

      {stopPoolOpen ? (
        <ConfirmModal
          title={t('warming.pool.stopConfirm.title')}
          body={t('warming.pool.stopConfirm.body', { count: warming.length })}
          confirmLabel={t('warming.pool.stopConfirm.confirm')}
          cancelLabel={t('warming.channels.cancel')}
          onClose={() => {
            setStopPoolOpen(false);
          }}
          onConfirm={() => {
            runBatch(warming.map((a) => stopAccount(a.account_id)));
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
