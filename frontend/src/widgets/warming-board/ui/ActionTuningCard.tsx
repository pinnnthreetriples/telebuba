import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  updateWarmingSettingsMutation,
  warmingBoardQueryOptions,
  warmingSettingsQueryOptions,
} from '@/entities/warming';
import type { WarmingSettings } from '@/shared/api';
import { mutationErrorText } from '@/shared/lib';
import { Badge, Button, CollapsibleCard, FeedbackMark, Icon, Switch } from '@/shared/ui';
import type { IconName } from '@/shared/ui';

// Каждое действие прогрева одной строкой, сгруппированное так, как оператор их
// ищет. Состояние — не украшение, а то, что тумблер РЕАЛЬНО может: `live` пишется
// в настройки, `always` работает и выключить его нельзя (ядро цикла или ключ в
// конфиге сервера), `soon` ещё не написан в `core/telegram_client`. Тумблер, который
// двигается и ничего не меняет, хуже тумблера, который честно отказывает — поэтому
// у последних двух он `disabled`, а плашка рядом говорит, почему.
type ActionState = 'live' | 'always' | 'soon';

// Три настройки, которые бэкенд действительно хранит (`WarmingSettingsUpdate`).
// Гейт готовности сюда не входит: он не действие, а допуск в прогрев, и стоит
// отдельной строкой под сеткой.
type LiveKey = 'reactions_enabled' | 'join_enabled' | 'inter_account_chat';

interface Action {
  // Ключ строки в `warming.tune.action.*`, он же ключ React.
  key: string;
  state: ActionState;
  // Есть только у `live`: поле настроек, которым эта строка управляет.
  field?: LiveKey;
}

interface Group {
  key: string;
  icon: IconName;
  // Группа, которая тратит трафик прокси заметно больше остальных.
  heavy?: boolean;
  actions: Action[];
}

const GROUPS: Group[] = [
  {
    key: 'reading',
    icon: 'eye',
    actions: [
      { key: 'scroll', state: 'always' },
      { key: 'markRead', state: 'always' },
      { key: 'dialogs', state: 'soon' },
      { key: 'searchMessages', state: 'soon' },
    ],
  },
  {
    key: 'activity',
    icon: 'chart',
    actions: [
      { key: 'online', state: 'always' },
      { key: 'reactions', state: 'live', field: 'reactions_enabled' },
      { key: 'polls', state: 'soon' },
      { key: 'video', state: 'soon' },
      { key: 'voice', state: 'soon' },
    ],
  },
  {
    key: 'fun',
    icon: 'sparkles',
    heavy: true,
    actions: [
      { key: 'stories', state: 'always' },
      { key: 'gif', state: 'soon' },
      { key: 'stickers', state: 'soon' },
      { key: 'inlineBots', state: 'soon' },
      { key: 'linkPreview', state: 'soon' },
    ],
  },
  {
    key: 'social',
    icon: 'arrow-swap',
    actions: [
      { key: 'interAccountChat', state: 'live', field: 'inter_account_chat' },
      { key: 'typing', state: 'always' },
      { key: 'forward', state: 'soon' },
      { key: 'saved', state: 'soon' },
      { key: 'contacts', state: 'soon' },
      { key: 'scheduled', state: 'soon' },
    ],
  },
  {
    key: 'chats',
    icon: 'users',
    actions: [
      { key: 'join', state: 'live', field: 'join_enabled' },
      { key: 'leave', state: 'soon' },
      { key: 'archive', state: 'soon' },
      { key: 'mute', state: 'soon' },
      { key: 'notifications', state: 'soon' },
    ],
  },
  {
    key: 'profile',
    icon: 'user-round',
    actions: [
      { key: 'viewProfiles', state: 'soon' },
      { key: 'checkSettings', state: 'soon' },
      { key: 'updateProfile', state: 'soon' },
      { key: 'emojiStatus', state: 'soon' },
      { key: 'drafts', state: 'soon' },
    ],
  },
];

const ACTIONS = GROUPS.flatMap((group) => group.actions);
// Счётчики в легенде считаются по таблице, а не вписаны числом: подключение
// одного действия не должно требовать правки надписи рядом.
const WORKING_COUNT = ACTIONS.filter((a) => a.state !== 'soon').length;
const SOON_COUNT = ACTIONS.filter((a) => a.state === 'soon').length;
const LIVE_FIELDS = ACTIONS.map((a) => a.field).filter((f): f is LiveKey => f != null);

interface Toggles {
  reactions_enabled: boolean;
  join_enabled: boolean;
  inter_account_chat: boolean;
  enforce_readiness: boolean;
}

function initialToggles(settings?: WarmingSettings): Toggles {
  return {
    reactions_enabled: settings?.reactions_enabled ?? true,
    join_enabled: settings?.join_enabled ?? true,
    inter_account_chat: settings?.inter_account_chat ?? false,
    enforce_readiness: settings?.enforce_readiness ?? true,
  };
}

function ActionRow({
  title,
  state,
  on,
  onToggle,
}: {
  title: string;
  state: ActionState;
  on: boolean;
  onToggle: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex items-center gap-md">
      <Switch checked={on} disabled={state !== 'live'} label={title} onChange={onToggle} />
      <span
        className={`min-w-0 flex-1 type-label ${state === 'soon' ? 'text-content-subtle' : ''}`}
      >
        {title}
      </span>
      {state === 'always' ? <Badge tone="info">{t('warming.tune.state.always')}</Badge> : null}
      {state === 'soon' ? (
        // С рамкой: заливка `neutral` — это `canvas`, а панель под ней `surface`, и
        // три единицы между ними плашкой не читаются. Тот же приём, что у пилюль каналов.
        <Badge className="border border-line">{t('warming.tune.state.soon')}</Badge>
      ) : null}
    </div>
  );
}

// Настройки прогрева ОБЩИЕ (`WarmingSettingsUpdate` не знает про account_id), поэтому
// карточка одна на страницу и стоит под сеткой аккаунтов, а не в модалке у каждого:
// шестерёнка на карточке аккаунта обещала настройку «этого аккаунта» и писала всем.
export function ActionTuningCard() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const settings = useQuery(warmingSettingsQueryOptions()).data;
  const save = useMutation(updateWarmingSettingsMutation());
  const [toggles, setToggles] = useState<Toggles>(() => initialToggles(settings));

  // Засеять ОДИН раз, когда придёт настоящая строка: на холодном кэше начальное
  // состояние — это заглушки выше, и «Сохранить» из него записало бы их поверх
  // хранимого. И именно один раз, а не на каждую смену `settings`: перезапрос
  // (тот, что делает и неудачное сохранение) приносит строку с новым `updated_at`,
  // структурное переиспользование React Query на этом ломается — и правки
  // оператора откатывались, пока ошибка ещё была на экране.
  const seeded = useRef(false);
  useEffect(() => {
    if (!settings || seeded.current) return;
    seeded.current = true;
    setToggles(initialToggles(settings));
  }, [settings]);

  const flip = (key: keyof Toggles) => {
    setToggles((prev) => ({ ...prev, [key]: !prev[key] }));
  };
  const setAllActions = (on: boolean) => {
    setToggles((prev) => ({
      ...prev,
      ...Object.fromEntries(LIVE_FIELDS.map((field) => [field, on])),
    }));
  };

  const onSave = () => {
    save.mutate(
      {
        body: {
          reactions_enabled: toggles.reactions_enabled,
          join_enabled: toggles.join_enabled,
          inter_account_chat: toggles.inter_account_chat,
          enforce_readiness: toggles.enforce_readiness,
          // Модель Gemini и два её ограничителя ОТСУТСТВУЮТ намеренно, а не
          // повторены: путь записи сохраняет каждое опущенное поле, а эхо читало
          // кэш, который эта карточка не перезапрашивает по фокусу — и сохранение
          // из давно открытой вкладки писало устаревшую строку поверх настроек.
          gemini_api_key: null,
          clear_gemini_key: false,
        },
      },
      {
        onSettled: () => {
          // Ровно то, что задевает эта запись: строка настроек и доска прогрева,
          // чья читающая модель вкладывает те же настройки. НЕ весь кэш.
          void queryClient.invalidateQueries({
            queryKey: warmingSettingsQueryOptions().queryKey,
          });
          void queryClient.invalidateQueries({ queryKey: warmingBoardQueryOptions().queryKey });
        },
      },
    );
  };

  return (
    <CollapsibleCard
      wrapperClassName="rounded-card border border-line bg-surface-card"
      headerClassName="px-lg py-lg"
      bodyClassName="px-lg pb-lg"
      label={t('warming.tune.title')}
      header={
        <>
          <span className="flex size-tile shrink-0 items-center justify-center rounded-lg bg-info-tint text-info-strong">
            <Icon name="gear" size={18} />
          </span>
          <div className="min-w-0">
            <div className="type-card-title">{t('warming.tune.title')}</div>
            <div className="mt-hair type-caption">{t('warming.tune.subtitle')}</div>
          </div>
        </>
      }
    >
      <div className="mb-lg flex flex-wrap items-center gap-sm">
        <Button
          size="xs"
          onClick={() => {
            setAllActions(true);
          }}
        >
          {t('warming.tune.enableAll')}
        </Button>
        <Button
          size="xs"
          onClick={() => {
            setAllActions(false);
          }}
        >
          {t('warming.tune.disableAll')}
        </Button>
        <span className="ml-auto flex items-center gap-tight type-caption">
          <span className="size-dot shrink-0 rounded-full bg-action-primary" />
          {t('warming.tune.legend.working', { n: WORKING_COUNT })}
        </span>
        <span className="flex items-center gap-tight type-caption">
          <span className="size-dot shrink-0 rounded-full bg-line-strong" />
          {t('warming.tune.legend.soon', { n: SOON_COUNT })}
        </span>
      </div>

      <div className="grid items-start gap-md sm:grid-cols-2 lg:grid-cols-3">
        {GROUPS.map((group) => (
          <div key={group.key} className="rounded-lg border border-line bg-surface p-md">
            <div className="mb-md flex items-center gap-sm">
              <Icon name={group.icon} size={14} className="shrink-0 text-content-subtle" />
              <span className="type-eyebrow">{t(`warming.tune.group.${group.key}`)}</span>
              {group.heavy ? <Badge tone="warning">{t('warming.tune.trafficHeavy')}</Badge> : null}
            </div>
            <div className="flex flex-col gap-md">
              {group.actions.map((action) => (
                <ActionRow
                  key={action.key}
                  title={t(`warming.tune.action.${action.key}`)}
                  state={action.state}
                  on={action.field ? toggles[action.field] : action.state === 'always'}
                  onToggle={() => {
                    if (action.field) flip(action.field);
                  }}
                />
              ))}
            </div>
          </div>
        ))}
      </div>

      <div className="mt-lg border-t border-line-row pt-lg">
        <div className="flex items-start justify-between gap-lg">
          <div className="min-w-0 flex-1">
            <div className="type-item-title">{t('warming.tune.gate.title')}</div>
            <div className="mt-hair type-caption">{t('warming.tune.gate.desc')}</div>
          </div>
          <Switch
            checked={toggles.enforce_readiness}
            label={t('warming.tune.gate.title')}
            onChange={() => {
              flip('enforce_readiness');
            }}
          />
        </div>

        {save.isError ? (
          // Тот же конкретный текст, что и в общем тосте мутаций: этот сигнал —
          // отчёт по месту, и он не должен быть менее внятным из двух.
          <div role="alert" className="mt-md type-caption text-danger">
            {mutationErrorText(save.error)}
          </div>
        ) : null}

        <div className="mt-lg flex items-center gap-md">
          <Button
            variant="primary"
            size="sm"
            disabled={save.isPending || !settings}
            onClick={onSave}
          >
            {t('warming.tune.save')}
          </Button>
          {/* Отдельной отметки об ОШИБКЕ тут нет: её причину строкой выше пишет
              `role="alert"`, а поверх страницы — общий тост мутаций. */}
          <FeedbackMark result={save.isSuccess ? 'ok' : undefined} />
        </div>
      </div>
    </CollapsibleCard>
  );
}
