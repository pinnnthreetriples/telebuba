// PROTOTYPE — throwaway. Building blocks the three layout variants share. Only the
// field-level pieces live here; each variant owns its own layout.
import { useState, type ReactNode } from 'react';

import { HEADING_ICON_TILE, surface } from '@/shared/design-system';
import {
  Badge,
  Button,
  HelpHint,
  Icon,
  IconButton,
  type IconName,
  Input,
  Notice,
  SegmentedControl,
  Select,
  Switch,
  TerminalPane,
  Textarea,
} from '@/shared/ui';

import {
  ACCOUNTS,
  blockers,
  type Draft,
  estimate,
  FREE_CAPTION_LIMIT,
  type MockAccount,
  type Mode,
  parseTargets,
  type Patch,
  USERNAME_RESOLVES_PER_DAY,
} from './model';

export const MODE_LABEL: Record<Mode, string> = {
  stories: 'Отметки в историях',
  priming: 'Прайминг',
};

export const MODE_BODY: Record<Mode, string> = {
  stories:
    'Аккаунт публикует сторис из пула медиа и упоминает в ней цели. Цель получает пуш «вас упомянули в истории».',
  priming:
    'Без сторис. Аккаунт включает и выключает автоудаление в личке с целью — у неё появляется служебная строка, и она идёт смотреть профиль.',
};

export function SectionHead({
  icon,
  title,
  badge,
}: {
  icon: IconName;
  title: string;
  badge?: ReactNode;
}) {
  return (
    <div className="flex min-w-0 items-center gap-3">
      <span className={HEADING_ICON_TILE}>
        <Icon name={icon} size={16} />
      </span>
      <span className="type-h3">{title}</span>
      {badge}
    </div>
  );
}

export function Eyebrow({
  title,
  caption,
  hint,
  example,
  aside,
}: {
  title: string;
  caption?: string;
  hint?: string;
  example?: string;
  aside?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 pb-2">
      <span className="type-small-medium">{title}</span>
      {caption === undefined ? null : <span className="type-small">{caption}</span>}
      {aside}
      {hint === undefined ? null : <HelpHint text={hint} example={example} />}
    </div>
  );
}

export function Row({
  label,
  hint,
  example,
  caption,
  dim = false,
  first = false,
  children,
}: {
  label: string;
  hint?: string;
  example?: string;
  caption?: string;
  dim?: boolean;
  first?: boolean;
  children: ReactNode;
}) {
  return (
    <div
      className={`flex min-h-touch flex-wrap items-center gap-3 py-2 ${first ? '' : 'border-t border-canvas'} ${dim ? 'opacity-60' : ''}`}
    >
      <div className="flex min-w-0 flex-1 items-center gap-1">
        <span className="min-w-0 text-body">{label}</span>
        {hint === undefined && caption === undefined ? null : (
          <HelpHint text={[hint, caption].filter(Boolean).join(' ')} example={example} />
        )}
      </div>
      {children}
    </div>
  );
}

export function Num({
  value,
  onChange,
  disabled,
  label,
  unit,
}: {
  value: number;
  onChange: (next: number) => void;
  disabled?: boolean;
  label: string;
  unit?: string;
}) {
  return (
    <span className="inline-flex items-center gap-1">
      <Input
        size="sm"
        type="number"
        className="w-number tabular-nums"
        min={0}
        value={value}
        disabled={disabled}
        aria-label={label}
        onChange={(event) => {
          onChange(Math.max(0, Number(event.target.value) || 0));
        }}
      />
      {unit === undefined ? null : <span className="type-small">{unit}</span>}
    </span>
  );
}

export function NumRange({
  min,
  max,
  onChange,
  disabled,
  label,
  unit,
}: {
  min: number;
  max: number;
  onChange: (min: number, max: number) => void;
  disabled?: boolean;
  label: string;
  unit: string;
}) {
  return (
    <span className="inline-flex items-center gap-2">
      <Num
        value={min}
        disabled={disabled}
        label={`${label}: от`}
        onChange={(next) => {
          onChange(next, Math.max(next, max));
        }}
      />
      <span className="type-small">—</span>
      <Num
        value={max}
        disabled={disabled}
        label={`${label}: до`}
        unit={unit}
        onChange={(next) => {
          onChange(Math.min(min, next), next);
        }}
      />
    </span>
  );
}

// ── Accounts ────────────────────────────────────────────────────────────────

export function Initials({ name }: { name: string }) {
  return (
    <span className="flex size-tile shrink-0 items-center justify-center rounded-full bg-info-tint text-body font-medium text-info-strong">
      {name.slice(0, 1)}
    </span>
  );
}

export function AccountBadges({ account, mode }: { account: MockAccount; mode: Mode }) {
  return (
    <span className="flex flex-wrap items-center gap-1">
      {account.premium ? (
        <Badge tone="info">Premium</Badge>
      ) : mode === 'stories' ? (
        <Badge tone="warning">без Premium · 1 сторис/сут</Badge>
      ) : null}
      {account.proxyOk ? (
        <Badge tone="success" dot>
          прокси
        </Badge>
      ) : (
        <Badge tone="danger" dot>
          прокси
        </Badge>
      )}
      <Badge>{account.ageDays} дн</Badge>
      {account.spamblock ? <Badge tone="danger">спамблок</Badge> : null}
      {account.busy ? <Badge tone="warning">в работе</Badge> : null}
    </span>
  );
}

export function AccountLine({
  account,
  mode,
  trailing,
}: {
  account: MockAccount;
  mode: Mode;
  trailing?: ReactNode;
}) {
  return (
    <div className="flex min-w-0 flex-1 items-center gap-3">
      <Initials name={account.name} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="type-body-medium">{account.name}</span>
          <span className="type-small">{account.country}</span>
        </div>
        <div className="truncate type-small">
          {account.phone} · <span className="text-info-strong">@{account.username}</span>
        </div>
      </div>
      <AccountBadges account={account} mode={mode} />
      {trailing}
    </div>
  );
}

export type AccountFilters = { search: string; premiumOnly: boolean; hideBusy: boolean; hideSpam: boolean };

export function useAccountFilters() {
  const [filters, setFilters] = useState<AccountFilters>({
    search: '',
    premiumOnly: false,
    hideBusy: true,
    hideSpam: true,
  });
  const visible = ACCOUNTS.filter(
    (account) =>
      (filters.search === '' ||
        `${account.name} ${account.phone} ${account.username}`
          .toLowerCase()
          .includes(filters.search.toLowerCase())) &&
      (!filters.premiumOnly || account.premium) &&
      (!filters.hideBusy || !account.busy) &&
      (!filters.hideSpam || !account.spamblock),
  );
  return { filters, setFilters, visible };
}

export function AccountFilterBar({
  filters,
  setFilters,
}: {
  filters: AccountFilters;
  setFilters: (next: AccountFilters) => void;
}) {
  const chip = (key: 'premiumOnly' | 'hideBusy' | 'hideSpam', label: string) => (
    <label className="inline-flex cursor-pointer items-center gap-1 rounded-full border border-line px-3 py-1 text-body">
      <input
        type="checkbox"
        className="accent-action-primary"
        checked={filters[key]}
        onChange={(event) => {
          setFilters({ ...filters, [key]: event.target.checked });
        }}
      />
      {label}
    </label>
  );
  return (
    <div className="flex flex-col gap-2">
      <Input
        size="sm"
        placeholder="Поиск по имени, телефону, username…"
        value={filters.search}
        onChange={(event) => {
          setFilters({ ...filters, search: event.target.value });
        }}
      />
      <div className="flex flex-wrap gap-2">
        {chip('premiumOnly', 'Только Premium')}
        {chip('hideBusy', 'Скрыть в работе')}
        {chip('hideSpam', 'Скрыть со спамблоком')}
      </div>
    </div>
  );
}

// A checkbox list — the compact picker variants B and C use.
export function AccountChecklist({ draft, patch }: { draft: Draft; patch: Patch }) {
  const { filters, setFilters, visible } = useAccountFilters();
  const toggle = (id: string) => {
    patch({
      accountIds: draft.accountIds.includes(id)
        ? draft.accountIds.filter((x) => x !== id)
        : [...draft.accountIds, id],
    });
  };
  const allOn = visible.length > 0 && visible.every((a) => draft.accountIds.includes(a.id));
  return (
    <div className="flex flex-col gap-3">
      <AccountFilterBar filters={filters} setFilters={setFilters} />
      <div className="flex items-center justify-between">
        <span className="type-small">
          Выбрано {draft.accountIds.length} из {ACCOUNTS.length}
        </span>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => {
            patch({
              accountIds: allOn
                ? draft.accountIds.filter((id) => !visible.some((a) => a.id === id))
                : [...new Set([...draft.accountIds, ...visible.map((a) => a.id)])],
            });
          }}
        >
          {allOn ? 'Снять все' : 'Выбрать все'}
        </Button>
      </div>
      <div className="flex flex-col">
        {visible.map((account) => (
          <label
            key={account.id}
            className="flex cursor-pointer items-center gap-3 border-t border-canvas py-2 first:border-t-0"
          >
            <input
              type="checkbox"
              className="accent-action-primary"
              checked={draft.accountIds.includes(account.id)}
              onChange={() => {
                toggle(account.id);
              }}
            />
            <AccountLine account={account} mode={draft.mode} />
          </label>
        ))}
      </div>
      <PremiumNotice draft={draft} />
    </div>
  );
}

export function PremiumNotice({ draft }: { draft: Draft }) {
  const { free } = estimate(draft);
  if (draft.mode !== 'stories' || free === 0) return null;
  return (
    <Notice tone="warning" bordered>
      Без Premium: {free}. Telegram даёт им 1 сторис в сутки (до 5 отметок) и всего несколько сторис
      за жизнь аккаунта, без ссылок-стикеров. Premium-аккаунты: до 100 сторис в сутки. Для прайминга
      Premium не нужен.
    </Notice>
  );
}

// ── Targets ─────────────────────────────────────────────────────────────────

export function TargetsEditor({ draft, patch }: { draft: Draft; patch: Patch }) {
  const [entry, setEntry] = useState('');
  const add = () => {
    const parsed = parseTargets(entry).filter((t) => !draft.targets.includes(t));
    if (parsed.length > 0) patch({ targets: [...draft.targets, ...parsed] });
    setEntry('');
  };
  return (
    <div className="flex flex-col gap-3">
      <SegmentedControl
        variant="tray"
        value={draft.targetSource}
        ariaLabel="Откуда цели"
        options={[
          { value: 'manual', label: 'Список вручную' },
          { value: 'contacts', label: 'Контакты аккаунтов' },
        ]}
        onChange={(targetSource) => {
          patch({ targetSource });
        }}
      />
      {draft.targetSource === 'manual' ? (
        <>
          <Textarea
            value={entry}
            placeholder={'@username\n+79001234567\n@durov_chat (чат → комментаторы/активные)\nили вставьте CSV…'}
            onChange={(event) => {
              setEntry(event.target.value);
            }}
          />
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="primary" disabled={entry.trim() === ''} onClick={add}>
              <Icon name="plus" size={14} />
              Добавить ({parseTargets(entry).length})
            </Button>
            <Button size="sm">
              <Icon name="upload-cloud" size={14} />
              Загрузить файл
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={draft.targets.length === 0}
              onClick={() => {
                patch({ targets: [] });
              }}
            >
              <Icon name="trash" size={14} />
              Очистить
            </Button>
          </div>
        </>
      ) : (
        <span className="type-body text-content-subtle">Цели — контакты выбранных аккаунтов.</span>
      )}
      {draft.targets.length === 0 ? null : (
        <div className="flex flex-wrap gap-2">
          {draft.targets.map((target) => (
            <span
              key={target}
              className="inline-flex items-center gap-2 rounded-full border border-line bg-canvas px-3 py-1 text-body text-content-secondary"
            >
              {target}
              <IconButton
                size="sm"
                shape="circle"
                aria-label={`Убрать ${target}`}
                onClick={() => {
                  patch({ targets: draft.targets.filter((t) => t !== target) });
                }}
              >
                <Icon name="close" size={14} />
              </IconButton>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Creative (stories only) ─────────────────────────────────────────────────

export function CreativeFields({
  draft,
  patch,
  disabled = false,
}: {
  draft: Draft;
  patch: Patch;
  disabled?: boolean;
}) {
  return (
    <fieldset disabled={disabled} className={`flex flex-col gap-3 ${disabled ? 'opacity-60' : ''}`}>
      <div
        className={surface(
          'inset',
          'flex flex-col items-center gap-1 border border-dashed border-line-strong px-4 py-6 text-center',
        )}
      >
        <Icon name="upload-cloud" size={20} />
        <span className="flex items-center gap-1 text-body">
          Перетащите фото или видео сюда
          <HelpHint text="Сторис берут медиа из пула по кругу. Видео — до 60 секунд." />
        </span>
      </div>
      <div className="flex flex-wrap gap-2">
        {draft.media.map((file) => (
          <Badge key={file} size="sm">
            <Icon name="video" size={12} /> {file}
          </Badge>
        ))}
      </div>
      <label className="flex flex-col gap-1">
        <span className="flex items-center justify-between">
          <span className="flex items-center gap-1">
            <span className="type-body-medium">Подпись к сторис</span>
            <HelpHint text="Упоминания целей дописываются к подписи автоматически. Без Premium — до 200 символов." />
          </span>
          <span className="type-small tabular-nums">
            {draft.caption.length}/{FREE_CAPTION_LIMIT}
          </span>
        </span>
        <Textarea
          value={draft.caption}
          onChange={(event) => {
            patch({ caption: event.target.value });
          }}
        />
      </label>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="flex flex-col gap-1">
          <span className="flex items-center gap-1">
            <span className="type-body-medium">Ссылка-стикер</span>
            <Badge tone="info">Premium</Badge>
          </span>
          <Input
            size="sm"
            value={draft.link}
            onChange={(event) => {
              patch({ link: event.target.value });
            }}
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="type-body-medium">Вид ссылки</span>
          <Select
            value={draft.linkStyle}
            disabled={disabled}
            ariaLabel="Вид ссылки"
            options={[
              { value: 'visible', label: 'Видимая плашка' },
              { value: 'hidden', label: 'Невидимая область' },
            ]}
            onChange={(value) => {
              patch({ linkStyle: value as Draft['linkStyle'] });
            }}
          />
        </label>
      </div>
      <label className="flex flex-col gap-1">
        <span className="type-body-medium">Текст на плашке</span>
        <Input
          size="sm"
          value={draft.plateText}
          onChange={(event) => {
            patch({ plateText: event.target.value });
          }}
        />
      </label>
    </fieldset>
  );
}

// ── Pacing ──────────────────────────────────────────────────────────────────

// `inactive` decides what happens to story-only rows in priming: variant A greys
// them out like the reference, B and C drop them.
export function PacingRows({
  draft,
  patch,
  inactive,
}: {
  draft: Draft;
  patch: Patch;
  inactive: 'dim' | 'hide';
}) {
  const priming = draft.mode === 'priming';
  const showStory = !(priming && inactive === 'hide');
  return (
    <div>
      {showStory ? (
        <>
          <Row first label="Отметок в одной сторис" hint="Без Premium — не больше 5." dim={priming}>
            <Num
              value={draft.mentionsPerStory}
              disabled={priming}
              label="Отметок в одной сторис"
              unit="чел."
              onChange={(mentionsPerStory) => {
                patch({ mentionsPerStory });
              }}
            />
          </Row>
          <Row label="Сторис на аккаунт в сутки" hint="Premium: до 100. Без Premium — 1." dim={priming}>
            <Num
              value={draft.storiesPerDay}
              disabled={priming}
              label="Сторис на аккаунт в сутки"
              onChange={(storiesPerDay) => {
                patch({ storiesPerDay });
              }}
            />
          </Row>
          <Row label="Между сторис" dim={priming}>
            <NumRange
              min={draft.storyGapMin}
              max={draft.storyGapMax}
              disabled={priming}
              label="Между сторис"
              unit="с"
              onChange={(storyGapMin, storyGapMax) => {
                patch({ storyGapMin, storyGapMax });
              }}
            />
          </Row>
        </>
      ) : null}
      {priming ? (
        <Row
          first={!showStory}
          label="Диалогов на аккаунт в сутки"
          hint="Первые 3 дня — 5–6, после спокойных дней — до 35–40."
        >
          <Num
            value={draft.dialogsPerDay}
            label="Диалогов на аккаунт в сутки"
            onChange={(dialogsPerDay) => {
              patch({ dialogsPerDay });
            }}
          />
        </Row>
      ) : null}
      <Row label="Перед диалогом с целью" hint="Пауза перед каждой отметкой. Первые дни — 200–600 с.">
        <NumRange
          min={draft.dialogDelayMin}
          max={draft.dialogDelayMax}
          label="Перед диалогом с целью"
          unit="с"
          onChange={(dialogDelayMin, dialogDelayMax) => {
            patch({ dialogDelayMin, dialogDelayMax });
          }}
        />
      </Row>
      <Row label="Пауза при флуд-лимите">
        <Num
          value={draft.floodPause}
          label="Пауза при флуд-лимите"
          unit="с"
          onChange={(floodPause) => {
            patch({ floodPause });
          }}
        />
      </Row>
      <Row label="Флуд-лимитов до карантина" hint="После стольких флуд-лимитов подряд аккаунт останавливается.">
        <Num
          value={draft.floodsToQuarantine}
          label="Флуд-лимитов до карантина"
          onChange={(floodsToQuarantine) => {
            patch({ floodsToQuarantine });
          }}
        />
      </Row>
    </div>
  );
}

export function VolumeFields({ draft, patch }: { draft: Draft; patch: Patch }) {
  return (
    <div className="flex flex-col gap-3">
      <SegmentedControl
        variant="tray"
        value={draft.volume}
        ariaLabel="Темп и объём"
        options={[
          { value: 'count', label: 'По количеству' },
          { value: 'time', label: 'По времени' },
        ]}
        onChange={(volume) => {
          patch({ volume });
        }}
      />
      {draft.volume === 'count' ? (
        <Row first label="Максимум отметок за запуск" caption="На каждый аккаунт · 0 = без ограничения">
          <Num
            value={draft.maxTags}
            label="Максимум отметок"
            onChange={(maxTags) => {
              patch({ maxTags });
            }}
          />
        </Row>
      ) : (
        <Row first label="Длительность" caption="0 = без ограничения">
          <Num
            value={draft.maxMinutes}
            label="Длительность"
            unit="мин"
            onChange={(maxMinutes) => {
              patch({ maxMinutes });
            }}
          />
        </Row>
      )}
    </div>
  );
}

export function StoryOptions({
  draft,
  patch,
  withPrimingSwitch,
}: {
  draft: Draft;
  patch: Patch;
  withPrimingSwitch: boolean;
}) {
  const priming = draft.mode === 'priming';
  return (
    <div>
      {withPrimingSwitch ? (
        <Row first label="Только прайминг" hint="Сторис не публикуются: аккаунт отмечает цель через автоудаление в личке.">
          <Switch
            checked={priming}
            label="Только прайминг"
            onChange={(on) => {
              patch({ mode: on ? 'priming' : 'stories' });
            }}
          />
        </Row>
      ) : null}
      {priming ? null : (
        <Row
          first={!withPrimingSwitch}
          label="Сначала создать диалог"
          hint="Перед отметкой аккаунт включает и выключает автоудаление в личке с целью. Без диалога отметка может не прийти пушем."
        >
          <Switch
            checked={draft.primeFirst}
            label="Сначала создать диалог"
            onChange={(primeFirst) => {
              patch({ primeFirst });
            }}
          />
        </Row>
      )}
      <Row first={!withPrimingSwitch && priming} label="Пропускать уже отмеченных" hint="Цель, которую любой аккаунт уже отметил, второй раз не трогаем.">
        <Switch
          checked={draft.skipTagged}
          label="Пропускать уже отмеченных"
          onChange={(skipTagged) => {
            patch({ skipTagged });
          }}
        />
      </Row>
      {priming ? null : (
        <Row
          label="Кто видит сторис"
          dim={priming}
          hint="Если сторис видят не все, отмеченная цель может её не увидеть."
        >
          <Select
            value={draft.visibility}
            disabled={priming}
            ariaLabel="Кто видит сторис"
            options={[
              { value: 'all', label: 'Все' },
              { value: 'contacts', label: 'Только контакты' },
            ]}
            onChange={(value) => {
              patch({ visibility: value as Draft['visibility'] });
            }}
          />
        </Row>
      )}
      {priming ? null : (
        <Row
          label="Время жизни сторис"
          dim={priming}
          hint="48 часов — дольше висит и больше переходов. 6, 12 и 48 ч — только Premium; без него сторис живёт 24 ч."
        >
          <Select
            value={draft.lifetime}
            disabled={priming}
            ariaLabel="Время жизни сторис"
            options={[
              { value: '6', label: '6 часов (Premium)' },
              { value: '12', label: '12 часов (Premium)' },
              { value: '24', label: '24 часа' },
              { value: '48', label: '48 часов (Premium)' },
            ]}
            onChange={(value) => {
              patch({ lifetime: value as Draft['lifetime'] });
            }}
          />
        </Row>
      )}
    </div>
  );
}

// ── What the target sees ────────────────────────────────────────────────────

export function TargetPreview({ draft }: { draft: Draft }) {
  const sender = 'Артём';
  return (
    <div className={surface('inset', 'flex flex-col gap-2 p-3')}>
      <span className="type-small-medium">Что увидит цель</span>
      {draft.mode === 'stories' ? (
        <>
          <div className="flex items-center gap-2 rounded-lg bg-surface-card p-2 shadow-ring">
            <Initials name={sender} />
            <div className="min-w-0">
              <div className="type-body-medium">{sender}</div>
              <div className="type-small">упомянул(а) вас в истории</div>
            </div>
          </div>
          <div className="flex flex-col items-center gap-2 rounded-lg bg-term p-4 text-center text-term-text">
            <Icon name="play" size={20} />
            <span className="text-body font-medium">{draft.caption || 'Подпись'}</span>
            <span className="text-small">@bivenmo @eduardafc_b7z</span>
            {draft.linkStyle === 'visible' ? (
              <span className="rounded-full bg-surface-card px-3 py-1 text-small font-medium text-content-primary">
                {draft.plateText || 'Ссылка'}
              </span>
            ) : null}
          </div>
        </>
      ) : (
        <>
          <div className="flex flex-col gap-1 rounded-lg bg-surface-card p-2 shadow-ring">
            <div className="flex items-center gap-2 pb-1">
              <Initials name={sender} />
              <span className="type-body-medium">{sender}</span>
            </div>
            <span className="self-center rounded-full bg-canvas px-3 py-1 text-small text-content-muted">
              {sender} включил(а) автоудаление: 1 день
            </span>
            <span className="self-center rounded-full bg-canvas px-3 py-1 text-small text-content-muted">
              {sender} отключил(а) автоудаление
            </span>
            <div className="flex gap-2 pt-1">
              <span className="flex-1 text-center text-small font-medium text-info-strong">Добавить контакт</span>
              <span className="flex-1 text-center text-small font-medium text-danger">Заблокировать</span>
            </div>
          </div>
          <span className="type-small">
            Дальше цель открывает профиль: там должен быть креатив — закреплённый канал-переходник или
            сторис в профиле. Оформляется в карточке аккаунта.
          </span>
        </>
      )}
    </div>
  );
}

// ── Launch ──────────────────────────────────────────────────────────────────

const LOG_LINES = [
  '12:04:11  Артём → @bivenmo  · сторис #3, отмечено 2',
  '12:04:13  Carolina → @eduardafc_b7z  · сторис #1, отмечено 2',
  '12:05:02  Иван  · FloodWait 180 с → пауза 300 с (1/3)',
  '12:05:40  Артём → +79001234567  · пропущен: уже отмечен',
];

export function Stat({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="flex flex-col gap-1">
      <span className={`type-h1 tabular-nums ${tone ?? ''}`}>{value}</span>
      <span className="type-small">{label}</span>
    </div>
  );
}

export function LaunchPanel({
  draft,
  running,
  onToggle,
}: {
  draft: Draft;
  running: boolean;
  onToggle: () => void;
}) {
  const { accounts, perDay, days } = estimate(draft);
  const blocked = blockers(draft);
  const verb = draft.mode === 'priming' ? 'прайминг' : 'отметки';
  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-2 gap-3">
        <Stat label="аккаунтов" value={String(accounts.length)} />
        <Stat label="целей" value={String(draft.targets.length)} />
        <Stat label="отметок в сутки, потолок" value={String(perDay)} />
        <Stat label="дней на весь список" value={days === null ? '—' : String(days)} />
      </div>
      {blocked.length === 0 ? null : (
        <Notice tone="danger" bordered>
          <ul className="m-0 flex list-none flex-col gap-1 p-0">
            {blocked.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </Notice>
      )}
      <Button
        variant={running ? 'danger' : 'primary'}
        size="lg"
        fullWidth
        disabled={!running && blocked.length > 0}
        onClick={onToggle}
      >
        <Icon name={running ? 'pause' : 'play'} size={16} />
        {running ? 'Остановить' : `Начать ${verb}`}
      </Button>
      {running ? (
        <>
          <div className="grid grid-cols-3 gap-3 border-t border-canvas pt-3">
            <Stat label="отмечено" value="14" tone="text-success-deep" />
            <Stat label="ошибок" value="1" tone="text-danger-deep" />
            <Stat label="осталось" value={String(Math.max(draft.targets.length - 15, 0))} />
          </div>
          <TerminalPane size="inline">
            {LOG_LINES.map((line) => (
              <div key={line}>{line}</div>
            ))}
          </TerminalPane>
        </>
      ) : null}
    </div>
  );
}

export const TARGETS_HINT = `@username, телефон или чат — из чата берутся его участники. Telegram находит не больше ${String(USERNAME_RESOLVES_PER_DAY)} username в сутки на аккаунт: это потолок отметок по @username.`;

export const MODE_HINT =
  'Отметки в историях: аккаунт публикует сторис из пула медиа и упоминает в ней цели — им приходит пуш «вас упомянули в истории». Нужен Premium: без него Telegram даёт 1 сторис в сутки. Прайминг: без сторис и сообщений — аккаунт включает и выключает автоудаление в личке с целью, у неё остаётся служебная строка, и она идёт смотреть профиль. Креатив держите в профиле.';

export const PREMIUM_HINT =
  'Telegram даёт аккаунту без Premium 1 сторис в сутки (до 5 отметок) и всего несколько сторис за жизнь аккаунта, без ссылок-стикеров. С Premium — до 100 сторис в сутки. Для прайминга Premium не нужен.';
