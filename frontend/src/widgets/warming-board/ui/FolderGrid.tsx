import { useTranslation } from 'react-i18next';

import { AccountAvatar } from '@/entities/account';
import type { WarmedAccount, WarmingAccountState } from '@/shared/api';
import { FOCUS_RING } from '@/shared/design-system';
import { cn } from '@/shared/lib';
import { Badge, Button, Icon, IconButton, ProgressBar } from '@/shared/ui';

import { PERSONAS, type WarmingFolder } from '../model/folders';
import { useAccountDrop } from '../model/useAccountDrop';

// Сколько аватарок на карточке папки: дальше — «+N», карточка не растёт с пачкой.
const AVATARS = 10;
// Состояния, при которых аккаунт просто идёт по плану; остальные — «требуют внимания».
const CALM_STATES: ReadonlySet<WarmingAccountState['state']> = new Set(['active', 'sleeping']);

function FolderCard({
  title,
  meta,
  accounts,
  warmed = [],
  days,
  onOpen,
  dragging = false,
  onDropAccount,
}: {
  // Идёт перетаскивание готового аккаунта: папка показывает, что примет его.
  dragging?: boolean;
  onDropAccount?: (accountId: string) => void;
  title: string;
  meta: string;
  // Греются сейчас.
  accounts: WarmingAccountState[];
  // Уже в «Прогреты»: завершённая часть пачки, а не выбывшие из неё.
  warmed?: WarmedAccount[];
  // Длительность папки; null — «Без папки», у каждого своя.
  days: number | null;
  onOpen: () => void;
}) {
  const { t } = useTranslation();
  const drop = useAccountDrop(onDropAccount);
  const members: (WarmingAccountState | WarmedAccount)[] = [...accounts, ...warmed];
  const total = members.length;
  const shown = members.slice(0, AVATARS);
  const more = total - shown.length;
  const worked = members.map((a) => a.warmed_days ?? 0);
  const minDay = worked.length ? Math.min(...worked) : 0;
  const maxDay = worked.length ? Math.max(...worked) : 0;
  const attention = accounts.filter((a) => !CALM_STATES.has(a.state)).length;
  // Догреты — уже в «Прогреты», или ещё на доске, но отработали свою длительность
  // (свою у аккаунта, иначе папки).
  const done =
    warmed.length +
    accounts.filter(
      (a) => (a.warmed_days ?? 0) >= (a.target_days ?? days ?? Number.POSITIVE_INFINITY),
    ).length;
  const allDone = total > 0 && done === total;
  const avg = worked.length ? worked.reduce((sum, d) => sum + d, 0) / worked.length : 0;
  const dayText =
    days === null
      ? null
      : t(minDay === maxDay ? 'warming.folders.card.day' : 'warming.folders.card.dayRange', {
          from: minDay,
          to: maxDay,
          count: days,
        });

  return (
    <button
      type="button"
      onClick={onOpen}
      {...drop.handlers}
      className={cn(
        'flex flex-col gap-3 rounded-lg border bg-surface-card p-4 text-left transition-colors hover:border-action-primary',
        FOCUS_RING,
        drop.over
          ? 'border-action-primary bg-info-tint'
          : dragging && onDropAccount
            ? 'border-dashed border-info-line'
            : allDone
              ? 'border-success-line'
              : attention > 0
                ? 'border-warning-line'
                : 'border-line',
      )}
    >
      <div className="flex w-full items-center gap-2">
        <Icon name="folder" size={16} />
        <span className="min-w-0 flex-1 truncate type-body-medium">{title}</span>
        <Badge size="xs">{total}</Badge>
        <Icon name="chevron-right" size={16} />
      </div>
      <div className="type-small text-content-muted">{meta}</div>
      {total === 0 ? (
        <div className="type-small text-content-subtle">{t('warming.folders.card.empty')}</div>
      ) : (
        <div className="flex flex-wrap items-center gap-1">
          {shown.map((account) => (
            <AccountAvatar
              key={account.account_id}
              account={account}
              className="size-icon shrink-0 rounded-full"
              fallbackClassName="text-small font-medium bg-info-tint text-info-strong"
            />
          ))}
          {more > 0 ? (
            <span className="inline-flex size-icon items-center justify-center rounded-full bg-canvas type-small text-content-muted">
              +{more}
            </span>
          ) : null}
        </div>
      )}
      {days !== null && total > 0 ? (
        <ProgressBar
          value={avg}
          max={days}
          tone="success"
          label={dayText ?? undefined}
          className="w-full"
        />
      ) : null}
      {total > 0 ? (
        <div className="flex w-full flex-wrap gap-x-3 gap-y-1 type-small text-content-muted">
          {dayText ? <span>{dayText}</span> : null}
          {allDone ? (
            <span className="inline-flex items-center gap-1 text-success-deep">
              <Icon name="check" size={12} />
              {t('warming.folders.card.allDone')}
            </span>
          ) : (
            <span>{t('warming.folders.card.running', { count: accounts.length - attention })}</span>
          )}
          {done > 0 && !allDone ? (
            <span className="text-success-deep">
              {t('warming.folders.card.done', { count: done })}
            </span>
          ) : null}
          {attention > 0 ? (
            <span className="text-warning-deep">
              {t('warming.folders.card.attention', { count: attention })}
            </span>
          ) : null}
        </div>
      ) : null}
    </button>
  );
}

// Первый экран «В прогреве»: папки карточками — название, кратко настройки, аватарки
// аккаунтов и как далеко пачка ушла. Клик — провал в папку, к подробным карточкам.
export function FolderGrid({
  folders,
  membersOf,
  warmedOf,
  unfiled,
  onOpen,
  onCreate,
  dragging = false,
  onDropAccount,
}: {
  dragging?: boolean;
  onDropAccount?: (folderId: string, accountId: string) => void;
  folders: WarmingFolder[];
  membersOf: (folderId: string) => WarmingAccountState[];
  // Догретые аккаунты папки (в «Прогреты»).
  warmedOf?: (folderId: string) => WarmedAccount[];
  // Греющиеся без папки — те, что запущены по одному.
  unfiled: WarmingAccountState[];
  onOpen: (view: string) => void;
  onCreate: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="grid grid-cols-1 items-start gap-3 sm:grid-cols-[repeat(auto-fill,minmax(280px,1fr))]">
      {folders.map((folder) => (
        <FolderCard
          key={folder.id}
          title={folder.name}
          meta={[
            t('warming.folders.daysValue', { count: folder.days }),
            t(`warming.folders.device.${folder.device}`),
          ].join(' · ')}
          accounts={membersOf(folder.id)}
          warmed={warmedOf?.(folder.id)}
          days={folder.days}
          onOpen={() => {
            onOpen(folder.id);
          }}
          dragging={dragging}
          onDropAccount={
            onDropAccount
              ? (accountId) => {
                  onDropAccount(folder.id, accountId);
                }
              : undefined
          }
        />
      ))}
      {unfiled.length > 0 ? (
        <FolderCard
          title={t('warming.folders.unfiled')}
          meta={t('warming.folders.card.unfiledMeta')}
          accounts={unfiled}
          days={null}
          onOpen={() => {
            onOpen('unfiled');
          }}
        />
      ) : null}
      <Button variant="dashed" className="gap-2 self-stretch py-6" onClick={onCreate}>
        <Icon name="folder-plus" size={16} />
        {t('warming.folders.create')}
      </Button>
    </div>
  );
}

// Шапка внутри папки: назад ко всем папкам.
export function FolderBack({ onBack }: { onBack: () => void }) {
  const { t } = useTranslation();
  return (
    <Button variant="ghost" size="sm" className="mb-2 gap-1" onClick={onBack}>
      <Icon name="chevron-left" size={14} />
      {t('warming.folders.back')}
    </Button>
  );
}

// Сводка папки над её карточками. Темп и «когда догрет» — прототип, сервер их не
// читает: чип так и подписан.
export function FolderSummary({
  folder,
  caption,
  onEdit,
}: {
  folder: WarmingFolder;
  caption: string;
  onEdit?: () => void;
}) {
  const { t } = useTranslation();
  const mix = PERSONAS.filter((p) => folder.mix[p] > 0);
  const persona =
    mix.length === 1 && mix[0]
      ? t(`warming.folders.mixOnly.${mix[0]}`)
      : t('warming.folders.mixValue', {
          calm: folder.mix.calm,
          normal: folder.mix.normal,
          active: folder.mix.active,
        });
  const chips: { key: string; value: string; proto?: boolean }[] = [
    { key: 'days', value: t('warming.folders.daysValue', { count: folder.days }) },
    { key: 'persona', value: persona },
    { key: 'device', value: t(`warming.folders.device.${folder.device}`) },
    {
      key: 'delay',
      value: t(folder.spread ? 'warming.folders.delaySpread' : 'warming.folders.delayValue', {
        count: folder.delayHours,
      }),
    },
    { key: 'pace', value: t(`warming.folders.pace.${folder.pace}`), proto: true },
    { key: 'finish', value: t(`warming.folders.finish.${folder.finish}`), proto: true },
  ];
  return (
    <div className="mb-3 rounded-lg border border-line bg-surface p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <Icon name="folder" size={16} />
        <span className="type-body-medium">{folder.name}</span>
        <span className="type-small text-content-muted">{caption}</span>
        <span className="flex-1" />
        {onEdit ? (
          <IconButton
            shape="circle"
            aria-label={t('warming.folders.settings')}
            title={t('warming.folders.settings')}
            onClick={onEdit}
          >
            <Icon name="gear" size={16} />
          </IconButton>
        ) : null}
      </div>
      <div className="flex flex-wrap gap-2">
        {chips.map((chip) => (
          <Badge key={chip.key} size="xs">
            {t(`warming.folders.chip.${chip.key}`)}: {chip.value}
            {chip.proto ? ` · ${t('warming.folders.chip.proto')}` : null}
          </Badge>
        ))}
      </div>
    </div>
  );
}
