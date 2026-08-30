import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { NeuroshillingBoardAccount, NeuroshillingCampaign } from '@/shared/api';
import { Badge, Button, HelpHint, Icon, IconButton, Input, Select, Textarea } from '@/shared/ui';

import { MediaModal } from './MediaModal';
import { useNumberField } from './useNumberField';
import type { DraftRole, DraftStep, ScenarioDraft } from './scenarioDraft';
import {
  clampDelay,
  MAX_ROLES,
  MAX_STEP_DELAY_SECONDS,
  MAX_STEPS,
  mintKey,
  REACTIONS,
  roleTone,
} from './scenarioDraft';

const GHOST_BUTTON =
  'flex items-center justify-center gap-tight rounded-lg border border-dashed border-info-line bg-surface-card py-md text-body font-medium text-info-strong hover:border-action-primary hover:bg-action-hover disabled:opacity-50';

function StepRow({
  step,
  index,
  earlier,
  roles,
  onChange,
  onRemove,
}: {
  step: DraftStep;
  index: number;
  earlier: { position: number; kind: DraftStep['kind'] }[];
  roles: DraftRole[];
  onChange: (patch: Partial<DraftStep>) => void;
  onRemove: () => void;
}) {
  const { t } = useTranslation();
  const position = index + 1;
  // Зажимаются ПАРОЙ: `delay_min > delay_max` — 422 от схемы, который доходит до
  // оператора нечитаемым блобом валидации.
  const minField = useNumberField(step.delayMinSeconds, clampDelay, (value) => {
    onChange({ delayMinSeconds: value, delayMaxSeconds: Math.max(value, step.delayMaxSeconds) });
  });
  const maxField = useNumberField(step.delayMaxSeconds, clampDelay, (value) => {
    onChange({ delayMaxSeconds: value, delayMinSeconds: Math.min(value, step.delayMinSeconds) });
  });
  const link = step.kind === 'reaction' ? step.targetPosition : step.replyToPosition;
  const linkLabel =
    step.kind === 'reaction'
      ? t('neuroshilling.scenario.steps.target', { position })
      : t('neuroshilling.scenario.steps.replyTo', { position });

  return (
    <div className="rounded-lg border border-line bg-surface-card p-sm">
      {/* Одна строка на всё, что описывает шаг: номер, роль, на что отвечает, пауза
          и удаление. Связь и пауза стояли ОТДЕЛЬНОЙ строкой под текстом, и карточка
          из-за этого была в три яруса — при том что читают её сверху вниз ровно
          один раз. `flex-wrap` оставлен: в узкой колонке строка переносится, а не
          выдавливает роль до нечитаемой ширины. */}
      <div className="mb-sm flex flex-wrap items-center gap-tight">
        <Badge className="font-semibold tabular-nums">
          {t('neuroshilling.scenario.steps.position', { position })}
        </Badge>
        <div className="min-w-0 flex-1">
          <Select
            value={step.roleId ?? ''}
            onChange={(value) => {
              onChange({ roleId: value || null });
            }}
            options={[
              { value: '', label: t('neuroshilling.scenario.steps.rolePick') },
              ...roles.map((role) => ({ value: role.roleId, label: role.name })),
            ]}
            ariaLabel={t('neuroshilling.scenario.steps.role', { position })}
          />
        </div>
        <div className="w-number shrink-0">
          <Select
            value={link === null ? '' : String(link)}
            onChange={(value) => {
              const next = value === '' ? null : Number(value);
              onChange(
                step.kind === 'reaction' ? { targetPosition: next } : { replyToPosition: next },
              );
            }}
            options={[
              {
                value: '',
                label:
                  step.kind === 'reaction'
                    ? t('neuroshilling.scenario.steps.targetNone')
                    : t('neuroshilling.scenario.steps.replyNone'),
              },
              // Реакция не сообщение: в чате её нет, отвечать и реагировать не на что.
              // `_play_message` читает `reply_to_position`, `_play_reaction` целится
              // `target_position`, и оба ждут ОТПРАВЛЕННОЕ сообщение.
              ...earlier.map((item) => ({
                value: String(item.position),
                label: t('neuroshilling.scenario.steps.position', { position: item.position }),
                disabled: item.kind !== 'message',
              })),
            ]}
            ariaLabel={linkLabel}
          />
        </div>
        {/* Пауза — ОДНО поле с двумя безрамочными числами, а не два поля рядом.
            Измерено: пара `w-number` занимала 175px из 409, доступных строке, и одна она
            уводила строку на второй ярус. В общей рамке та же пара стоит вдвое дешевле —
            рамка, отбивка и подпись «с» у двух чисел одни. Высота, радиус и краска взяты
            у поля, чтобы рядом с соседними контролами это читалось как поле, а не как
            изобретённая коробка. */}
        {/* Пауза — ОДНО поле с двумя числами: рамка, отбивка и подпись «с» у пары общие,
            и вдвое дешевле по ширине, чем два поля рядом.

            Строка ПЛОСКАЯ, а не «число» + «тире с числом»: во второй форме зазор слева от
            тире был отступом группы, а справа — отступом внутри неё, и тире стояло ближе к
            максимуму, чем к минимуму. Числа прижаты К ТИРЕ (`text-right` и `text-left`),
            поэтому пара читается диапазоном «60–180», а не двумя значениями, плавающими в
            своих коробках. */}
        {/* Пауза — ОДНО поле с двумя числами: рамка, отбивка и подпись «с» у пары общие,
            и вдвое дешевле по ширине, чем два поля рядом. Числа прижаты К ТИРЕ, поэтому
            пара читается диапазоном «60–180», а не двумя значениями в своих коробках. */}
        <div className="flex h-control shrink-0 items-center gap-xs rounded-lg border border-line bg-surface-card px-sm">
          <input
            type="number"
            min={0}
            max={MAX_STEP_DELAY_SECONDS}
            value={minField.value}
            aria-label={t('neuroshilling.scenario.steps.delayMin', { position })}
            onChange={(event) => {
              minField.onChange(event.target.value);
            }}
            onBlur={minField.onBlur}
            className="tb-plain-number w-action border-none bg-transparent text-right text-body tabular-nums outline-none"
          />
          <span className="type-caption">–</span>
          <input
            type="number"
            min={0}
            max={MAX_STEP_DELAY_SECONDS}
            value={maxField.value}
            aria-label={t('neuroshilling.scenario.steps.delayMax', { position })}
            onChange={(event) => {
              maxField.onChange(event.target.value);
            }}
            onBlur={maxField.onBlur}
            className="tb-plain-number w-action border-none bg-transparent text-left text-body tabular-nums outline-none"
          />
          <span className="type-caption">{t('neuroshilling.scenario.steps.seconds')}</span>
        </div>
        {/* Смена вида: реплика ↔ реакция. Стрелки в разные стороны, а не карандаш
            макета: карандаш значит «править», а шаг правят и без этой кнопки — текстом
            под ней; здесь меняют, ЧЕМ шаг является.

            Связь переезжает вместе с видом, а чужая обнуляется: `replyToPosition` читает
            только сообщение, `targetPosition` — только реакция, и позиция, оставшаяся под
            именем другого вида, сервером отвергается (`scenario_invalid`), а прогоном
            просто не читается. Эмодзи реакции ставится по умолчанию тем же, что и у
            свежесозданной: реакция без него — шаг, который прогон молча пропустит. */}
        <IconButton
          size="sm"
          tone="primary"
          title={t(
            `neuroshilling.scenario.steps.switchTo.${step.kind === 'message' ? 'reaction' : 'message'}`,
          )}
          aria-label={t(
            `neuroshilling.scenario.steps.switchTo.${step.kind === 'message' ? 'reaction' : 'message'}`,
          )}
          onClick={() => {
            onChange(
              step.kind === 'message'
                ? {
                    kind: 'reaction',
                    emoji: step.emoji ?? REACTIONS[0]!,
                    targetPosition: step.replyToPosition,
                    replyToPosition: null,
                  }
                : {
                    kind: 'message',
                    replyToPosition: step.targetPosition,
                    targetPosition: null,
                  },
            );
          }}
        >
          <Icon name="arrow-swap" size={12} />
        </IconButton>
        {/* Тот же `IconButton`, что у роли и у цели. Своя кнопка с глифом «×» рисовала
            третий крестик на одном экране: рамка, размер и цвет наведения у неё были
            собственные, и рядом с двумя одинаковыми она читалась как другое действие. */}
        <IconButton
          size="sm"
          tone="danger"
          title={t('neuroshilling.scenario.steps.remove', { position })}
          aria-label={t('neuroshilling.scenario.steps.remove', { position })}
          onClick={onRemove}
        >
          <Icon name="close" size={12} />
        </IconButton>
      </div>

      {step.kind === 'message' ? (
        <Textarea
          size="sm"
          className="resize-none font-[inherit]"
          rows={2}
          value={step.text}
          maxLength={1000}
          placeholder={t('neuroshilling.scenario.steps.textPlaceholder')}
          aria-label={t('neuroshilling.scenario.steps.text', { position })}
          onChange={(event) => {
            onChange({ text: event.target.value });
          }}
        />
      ) : (
        /* A wrapping grid of square glyph tiles, not a row of labelled options: one
           wearer, so it stays hand-written rather than becoming a fourth
           `SegmentedControl` variant. */
        <div
          role="radiogroup"
          aria-label={t('neuroshilling.scenario.steps.emoji', { position })}
          className="flex flex-wrap gap-tight"
        >
          {REACTIONS.map((emoji) => (
            <button
              key={emoji}
              type="button"
              role="radio"
              aria-checked={step.emoji === emoji}
              aria-label={emoji}
              onClick={() => {
                onChange({ emoji });
              }}
              className={`size-icon rounded-md border text-body ${step.emoji === emoji ? 'border-action-primary bg-info-tint' : 'border-line bg-surface-card'}`}
            >
              {emoji}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// Card 2: everything that decides WHAT gets said — the brief the model is given
// and the dialogue itself.
//
// Zero hooks besides `useTranslation`, like its two sibling cards: the draft and
// every request live on the page, which is also what lets the "regenerate" button
// on the preview card reach the same state this one edits.
export function ScenarioSection({
  draft,
  onDraft,
  status,
  dirty,
  onGenerate,
  onApprove,
  pool,
  onAssignRole,
  busy,
}: {
  draft: ScenarioDraft;
  onDraft: (draft: ScenarioDraft) => void;
  status: NonNullable<NeuroshillingCampaign['scenario_status']>;
  dirty: boolean;
  onGenerate: () => void;
  onApprove: () => void;
  // Весь пул: карточка роли выбирает исполнителя из РОСТЕРА (`assigned`), но список
  // приходит целиком, потому что имя аккаунта нужно и для уже назначенного.
  pool: NeuroshillingBoardAccount[];
  // Назначение уезжает СВОИМ запросом, не черновиком сценария: ростер живёт в кампании
  // (`accounts`), а не в сценарии, и класть его в этот черновик значило бы отправлять
  // роли и шаги ради смены исполнителя.
  onAssignRole: (roleId: string, accountId: string | null) => void;
  busy: boolean;
}) {
  const { t } = useTranslation();
  const [media, setMedia] = useState(false);
  const namelessRole = draft.roles.some((role) => !role.name.trim());

  const patchStep = (index: number, patch: Partial<DraftStep>) => {
    onDraft({
      ...draft,
      steps: draft.steps.map((step, at) => (at === index ? { ...step, ...patch } : step)),
    });
  };

  // Removing a step renumbers everything after it, so every one-based reference
  // into the list moves with it: a link AT the removed step is dropped, a link
  // past it slides down one. Left alone these become the forward/dangling links
  // the server refuses on save.
  const removeStep = (index: number) => {
    const shift = (value: number | null) => {
      if (value === null || value === index + 1) return null;
      return value > index + 1 ? value - 1 : value;
    };
    onDraft({
      ...draft,
      mediaStepPosition: shift(draft.mediaStepPosition),
      steps: draft.steps
        .filter((_, at) => at !== index)
        .map((step) => ({
          ...step,
          replyToPosition: shift(step.replyToPosition),
          targetPosition: shift(step.targetPosition),
        })),
    });
  };

  const addStep = (kind: DraftStep['kind']) => {
    onDraft({
      ...draft,
      steps: [
        ...draft.steps,
        {
          key: mintKey('step'),
          kind,
          roleId: draft.roles[0]?.roleId ?? null,
          text: '',
          replyToPosition: null,
          targetPosition: null,
          emoji: kind === 'reaction' ? REACTIONS[0]! : null,
          delayMinSeconds: 60,
          delayMaxSeconds: 180,
        },
      ],
    });
  };

  return (
    <section>
      <div className="mb-md flex items-center gap-md">
        <span className="type-card-title">{t('neuroshilling.scenario.title')}</span>
        {/* Утверждение умирает в ЭТОЙ секции, поэтому здесь оно и должно быть видно:
            любая правка ниже возвращает кампанию в черновик в момент сохранения, и
            оператор, видевший плашку только на превью, узнал бы об этом из отказа. */}
        <span
          className={`shrink-0 rounded-full px-md py-xs text-tiny font-semibold ${
            dirty && status === 'approved'
              ? 'bg-warning-tint text-warning-deep'
              : status === 'approved'
                ? 'bg-success-tint text-success-deep'
                : 'bg-canvas text-content-muted'
          }`}
        >
          {dirty && status === 'approved'
            ? t('neuroshilling.scenario.status.willReset')
            : t(`neuroshilling.scenario.status.${status}`)}
        </span>
      </div>

      {/* Тема и всё, что ею распоряжается, — одной строкой: бриф, две кнопки «добавить
          шаг», генерация и вложение. Тема была полем в три строки над панелью генерации;
          в диалоге это две трети экрана под текст, который почти всегда — одна фраза. */}
      <div className="mb-md flex flex-wrap items-center gap-sm">
        <span className="type-label">{t('neuroshilling.scenario.topic.label')}</span>
        <Input
          size="sm"
          // Пол ширины, а не голый `flex-1`: в строке с ней стоят пять контролов, и без
          // пола тема схлопывалась до одного слова, оставаясь при этом главным полем.
          className="min-w-col flex-1"
          value={draft.topic}
          maxLength={2000}
          placeholder={t('neuroshilling.scenario.topic.placeholder')}
          aria-label={t('neuroshilling.scenario.topic.label')}
          onChange={(event) => {
            onDraft({ ...draft, topic: event.target.value });
          }}
        />
        {(['message', 'reaction'] as const).map((kind) => (
          <Button
            key={kind}
            variant="dashed"
            size="xs"
            disabled={draft.steps.length >= MAX_STEPS}
            onClick={() => {
              addStep(kind);
            }}
          >
            {t(`neuroshilling.scenario.steps.add.${kind}`)}
          </Button>
        ))}
        {/* Иконкой, как в макете: подпись занимала треть строки, в которой стоят ещё
            пять контролов, а тема — главное поле — от этого ужималась до одного слова.
            Имя кнопки живёт в `aria-label` и в подсказке, поэтому ни для клавиатуры, ни
            для скринридера ничего не потерялось. */}
        <IconButton
          size="md"
          tone="action"
          disabled={busy || !draft.topic.trim()}
          title={t('neuroshilling.scenario.generate.run')}
          aria-label={t('neuroshilling.scenario.generate.run')}
          onClick={onGenerate}
        >
          <Icon name="sparkles" size={14} />
        </IconButton>
        {/* Что именно генерация пишет — названия ролей, их манеру речи и текст каждой
            реплики — из одной иконки не прочесть, а знать это нужно ДО нажатия: она
            заменяет уже набранное. */}
        <HelpHint text={t('neuroshilling.scenario.generate.hint')} />
        <IconButton
          size="md"
          tone="primary"
          title={t('neuroshilling.scenario.media.toggle')}
          aria-label={t('neuroshilling.scenario.media.toggle')}
          onClick={() => {
            setMedia(true);
          }}
        >
          <Icon name="paperclip" size={14} />
        </IconButton>
      </div>

      {media ? (
        <MediaModal
          draft={draft}
          onDraft={onDraft}
          onClose={() => {
            setMedia(false);
          }}
        />
      ) : null}

      {/* Роли — карточками в сетку, а не строками во всю ширину: у роли всего два поля,
          которые читают на бегу, — имя и аккаунт, который её играет. */}
      <div className="mb-sm flex items-center gap-sm type-item-title">
        {t('neuroshilling.scenario.roles.title')}
        <HelpHint text={t('neuroshilling.scenario.roles.hint')} />
      </div>
      <div className="mb-md grid gap-sm sm:grid-cols-2 lg:grid-cols-3">
        {draft.roles.map((role, index) => {
          const playing = pool.find(
            (account) => account.assigned && account.role_id === role.roleId,
          );
          return (
            <div
              key={role.roleId}
              className="flex flex-col gap-tight rounded-lg border border-line p-sm"
            >
              <div className="flex items-center gap-sm">
                <span className={`size-node shrink-0 rounded-full ${roleTone(index).bg}`} />
                <Input
                  size="xs"
                  className="min-w-0 flex-1"
                  value={role.name}
                  maxLength={60}
                  placeholder={t('neuroshilling.scenario.roles.namePlaceholder')}
                  aria-label={t('neuroshilling.scenario.roles.name', { position: index + 1 })}
                  onChange={(event) => {
                    onDraft({
                      ...draft,
                      roles: draft.roles.map((item, at) =>
                        at === index ? { ...item, name: event.target.value } : item,
                      ),
                    });
                  }}
                />
                <IconButton
                  size="sm"
                  tone="danger"
                  title={t('neuroshilling.scenario.roles.remove', { position: index + 1 })}
                  aria-label={t('neuroshilling.scenario.roles.remove', { position: index + 1 })}
                  onClick={() => {
                    // Иначе шаги продолжают указывать на роль, которой больше нет, и
                    // сервер отвечает 400 вместо пустого «выберите роль», которым
                    // удаление на самом деле и является.
                    onDraft({
                      ...draft,
                      roles: draft.roles.filter((_, at) => at !== index),
                      steps: draft.steps.map((step) =>
                        step.roleId === role.roleId ? { ...step, roleId: null } : step,
                      ),
                    });
                  }}
                >
                  <Icon name="close" size={12} />
                </IconButton>
              </div>

              {/* Аккаунт роли — прямо в карточке, и это ЕДИНСТВЕННОЕ место, где аккаунт
                  попадает в кампанию: отдельной секции «Аккаунты» с своим пикером больше
                  нет. Она спрашивала ростер списком, а роли раздавались отдельно, поэтому
                  «выбрал двоих» и «обе роли играют» были разными действиями, и запуск
                  отказывал между ними.

                  Поэтому список — ВЕСЬ пул, а не только уже набранные: иначе первый выбор
                  делать было бы не из чего. Занятый в другом месте аккаунт показан и
                  выключен вместе с тем, кто его держит: спрятать его значило бы оставить
                  оператора гадать, почему аккаунта нет в списке. */}
              <Select
                value={playing?.account_id ?? ''}
                onChange={(value) => {
                  onAssignRole(role.roleId, value || null);
                }}
                options={[
                  { value: '', label: t('neuroshilling.scenario.roles.accountNone') },
                  ...pool.map((account) => ({
                    value: account.account_id,
                    label:
                      account.busy_owner == null
                        ? account.title
                        : t('neuroshilling.scenario.roles.accountBusy', {
                            title: account.title,
                            owner: t(`neuroshilling.modal.accounts.busy.${account.busy_owner}`),
                          }),
                    disabled: account.busy_owner != null,
                  })),
                ]}
                ariaLabel={t('neuroshilling.scenario.roles.account', { position: index + 1 })}
              />

              <Input
                size="xs"
                value={role.description}
                maxLength={1000}
                placeholder={t('neuroshilling.scenario.roles.descriptionPlaceholder')}
                aria-label={t('neuroshilling.scenario.roles.description', { position: index + 1 })}
                onChange={(event) => {
                  onDraft({
                    ...draft,
                    roles: draft.roles.map((item, at) =>
                      at === index ? { ...item, description: event.target.value } : item,
                    ),
                  });
                }}
              />
            </div>
          );
        })}
        <button
          type="button"
          disabled={draft.roles.length >= MAX_ROLES}
          onClick={() => {
            onDraft({
              ...draft,
              roles: [...draft.roles, { roleId: mintKey('role'), name: '', description: '' }],
            });
          }}
          className={`${GHOST_BUTTON} min-h-touch`}
        >
          {t('neuroshilling.scenario.roles.add')}
        </button>
      </div>
      {draft.roles.length === 0 ? (
        <div className="mb-md type-prose">{t('neuroshilling.scenario.roles.none')}</div>
      ) : null}

      <div className="mb-sm flex items-center gap-sm type-item-title">
        {t('neuroshilling.scenario.steps.title')}
        <HelpHint text={t('neuroshilling.scenario.steps.hint')} />
      </div>
      {/* Две колонки: диалог из восьми реплик в один столбец — это экран с половиной
          прокрутки, а карточка шага своей ширины не требует. */}
      <div className="mb-lg grid gap-sm sm:grid-cols-2">
        {draft.steps.map((step, index) => (
          <StepRow
            key={step.key}
            step={step}
            index={index}
            roles={draft.roles}
            // Только сообщения выше него: ответу и реакции нужна цель, уже лежащая в
            // чате, а реакция сообщением не является.
            // ВСЕ шаги выше, а не только сообщения: реакция среди них остаётся видимой
            // и выключенной. Пока их просто выбрасывали, в списке зияла дыра — «#1, #2,
            // #3, #5», — и она читалась как сбой нумерации, а не как правило.
            earlier={draft.steps
              .slice(0, index)
              .map((item, at) => ({ position: at + 1, kind: item.kind }))}
            onChange={(patch) => {
              patchStep(index, patch);
            }}
            onRemove={() => {
              removeStep(index);
            }}
          />
        ))}
        {draft.steps.length === 0 ? (
          <div className="type-prose">{t('neuroshilling.scenario.steps.none')}</div>
        ) : null}
      </div>

      <div className="flex flex-wrap items-center gap-sm">
        {namelessRole ? (
          <span className="mr-auto type-caption text-danger">
            {t('neuroshilling.scenario.roles.nameRequired')}
          </span>
        ) : null}
        <Button
          variant="primary"
          size="block"
          disabled={busy || dirty || namelessRole || draft.steps.length === 0}
          title={dirty ? t('neuroshilling.scenario.approveHint') : undefined}
          onClick={onApprove}
        >
          {/* «Превью сценария», а не «Утвердить»: кнопка ОТКРЫВАЕТ чтение, а утверждают
              уже прочитанное — кнопкой в подвале того диалога. */}
          {t('neuroshilling.scenario.openApprove')}
        </Button>
      </div>
    </section>
  );
}
