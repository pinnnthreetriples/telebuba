import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { NeuroshillingCampaign, NeuroshillingRole, NeuroshillingStep } from '@/shared/api';
import { Badge, Button, Modal } from '@/shared/ui';

import { useNumberField } from './useNumberField';

import {
  clampDelay,
  clock,
  dialogueSeconds,
  MAX_STEP_DELAY_SECONDS,
  roleTone,
  stepMeanSeconds,
} from './scenarioDraft';

// Утверждение сценария: СОХРАНЁННЫЙ диалог так, как он прочитается в чате.
//
// Рисует сценарий сервера, а не черновик формы — этим и виден явный сохраняющий шаг: тут
// показано то, что прогон действительно отправит. Несохранённые правки названы вслух, а
// не подмешаны в превью молча.
//
// Диалог, а не карточка: утверждение — решение, после которого запуск перестаёт
// отказывать, и редизайн ставит его отдельным шагом, а не абзацем в общей простыне.
// Кнопка «Утвердить» стоит ЗДЕСЬ, под самим текстом, а не в редакторе: утверждают то, что
// прочитали.
// Пауза в разделителе: та же пара, что в редакторе шага, и с тем же поведением пустого
// поля — стёртое значение не превращается в ноль, к которому дописываются цифры.
function PauseBox({
  index,
  min,
  max,
  onDelay,
}: {
  index: number;
  min: number;
  max: number;
  onDelay: (index: number, min: number, max: number) => void;
}) {
  const { t } = useTranslation();
  // Зажимаются ПАРОЙ: `delay_min > delay_max` — 422 от схемы.
  const minField = useNumberField(min, clampDelay, (value) => {
    onDelay(index, value, Math.max(value, max));
  });
  const maxField = useNumberField(max, clampDelay, (value) => {
    onDelay(index, Math.min(value, min), value);
  });
  return (
    <span className="flex h-compact shrink-0 items-center gap-xs rounded-md border border-line bg-surface-card px-sm">
      <input
        type="number"
        min={0}
        max={MAX_STEP_DELAY_SECONDS}
        value={minField.value}
        aria-label={t('neuroshilling.scenario.steps.delayMin', { position: index + 1 })}
        onChange={(event) => {
          minField.onChange(event.target.value);
        }}
        onBlur={minField.onBlur}
        className="tb-plain-number w-action border-none bg-transparent text-right type-caption tabular-nums outline-none"
      />
      <span className="type-caption">–</span>
      <input
        type="number"
        min={0}
        max={MAX_STEP_DELAY_SECONDS}
        value={maxField.value}
        aria-label={t('neuroshilling.scenario.steps.delayMax', { position: index + 1 })}
        onChange={(event) => {
          maxField.onChange(event.target.value);
        }}
        onBlur={maxField.onBlur}
        className="tb-plain-number w-action border-none bg-transparent text-left type-caption tabular-nums outline-none"
      />
      <span className="type-caption">{t('neuroshilling.scenario.steps.seconds')}</span>
    </span>
  );
}

export function ApproveModal({
  roles,
  steps,
  status,
  dirty,
  onRegenerate,
  onApprove,
  onClose,
  delays,
  onDelay,
  busy,
}: {
  roles: NeuroshillingRole[];
  steps: NeuroshillingStep[];
  status: NonNullable<NeuroshillingCampaign['scenario_status']>;
  dirty: boolean;
  onRegenerate: () => void;
  onApprove: () => void;
  onClose: () => void;
  // Паузы ЧЕРНОВИКА, по шагу на каждый, или `null` — когда черновик и сохранённый
  // сценарий разошлись длиной и сопоставить их по индексу нельзя.
  //
  // Почему паузы приходят из черновика, а текст остаётся серверным: утверждают то, что
  // прогон отправит, поэтому реплики читаются с сервера и правке здесь не подлежат. Пауза
  // же — не текст, а ручка: её крутят ровно тогда, когда диалог перед глазами, и гонять
  // за этим в редактор и обратно значит терять то, ради чего его открыли. Правка пишется
  // в черновик и сохраняется общей кнопкой, как и всё остальное.
  delays: { min: number; max: number }[] | null;
  onDelay: (index: number, min: number, max: number) => void;
  busy: boolean;
}) {
  const { t } = useTranslation();
  // Bumped by "play", and part of every bubble's key: remounting the list is what
  // replays the CSS enter animation, so the whole feature is one number and a
  // staggered `animation-delay` rather than a timer per row.
  const [play, setPlay] = useState(0);

  const byPosition = new Map(steps.map((step) => [step.position, step]));
  const roleIndex = new Map(roles.map((role, index) => [role.role_id, index]));
  const total = dialogueSeconds(steps);
  let elapsed = 0;

  return (
    <Modal onClose={onClose} size="table" label={t('neuroshilling.preview.title')}>
      <div className="flex items-center gap-md border-b border-line-row px-2xl pb-lg pt-xl">
        <span className="type-dialog-title">{t('neuroshilling.preview.title')}</span>
        {/* Два счётчика — двумя ключами, а не одним с двумя подстановками: склоняются
            они по РАЗНЫМ числам, и «5 реплик, 1 реакций» — ровно то, что получается,
            когда i18next разрешают склонять только по одному `count`. */}
        <Badge className="tabular-nums">
          {[
            t('neuroshilling.preview.countMessages', {
              count: steps.filter((step) => step.kind === 'message').length,
            }),
            t('neuroshilling.preview.countReactions', {
              count: steps.filter((step) => step.kind === 'reaction').length,
            }),
          ].join(', ')}
        </Badge>
        <div className="flex-1" />
        <span
          className={`shrink-0 rounded-full px-md py-xs text-tiny font-semibold ${status === 'approved' ? 'bg-success-tint text-success-deep' : 'bg-canvas text-content-muted'}`}
        >
          {t(`neuroshilling.preview.status.${status}`)}
        </span>
      </div>

      <div className="px-2xl py-lg">
        {dirty ? (
          <div className="mb-md rounded-lg bg-warning-tint px-md py-sm text-tiny text-warning-deep">
            {t('neuroshilling.preview.unsaved')}
          </div>
        ) : null}

        {steps.length === 0 ? (
          <div className="py-xl text-center type-prose">{t('neuroshilling.preview.none')}</div>
        ) : (
          <div className="flex flex-col">
            {steps.map((step, index) => {
              elapsed += stepMeanSeconds(step);
              const at = roleIndex.get(step.role_id ?? '');
              const role = at === undefined ? undefined : roles[at];
              const tone = roleTone(at ?? 0);
              const quoted =
                step.reply_to_position === null || step.reply_to_position === undefined
                  ? undefined
                  : byPosition.get(step.reply_to_position);
              return (
                <div key={`${String(play)}-${step.step_id}`}>
                  {index > 0 ? (
                    <div className="my-sm flex items-center gap-sm">
                      <span className="h-px flex-1 bg-line" />
                      {delays === null ? (
                        <span className="type-caption tabular-nums">
                          {t('neuroshilling.preview.pause', {
                            min: step.delay_min_seconds ?? 60,
                            max: step.delay_max_seconds ?? 180,
                          })}
                        </span>
                      ) : (
                        // Та же коробка, что у паузы в редакторе шага: одно поле, два
                        // безрамочных числа, подпись «с» на двоих. Одна ручка на две
                        // страницы обязана выглядеть одинаково.
                        <PauseBox
                          index={index}
                          min={delays[index]?.min ?? 0}
                          max={delays[index]?.max ?? 0}
                          onDelay={onDelay}
                        />
                      )}
                      <span className="h-px flex-1 bg-line" />
                    </div>
                  ) : null}
                  <div
                    className="tb-fadeup flex gap-md"
                    style={{ animationDelay: `${String(index * 0.12)}s` }}
                  >
                    <span
                      className={`flex size-icon shrink-0 items-center justify-center rounded-full text-tiny font-bold ${tone.on} ${tone.bg}`}
                    >
                      {(role?.name ?? '?').slice(0, 1).toUpperCase()}
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="mb-xs flex items-center gap-sm">
                        <span className={`type-item-title ${tone.text}`}>
                          {role?.name ?? t('neuroshilling.preview.noRole')}
                        </span>
                        <span className="type-caption tabular-nums">
                          {t('neuroshilling.preview.at', { time: clock(elapsed) })}
                        </span>
                      </div>
                      {step.kind === 'reaction' ? (
                        <span className="inline-flex items-center gap-tight rounded-full border border-line bg-surface-card px-md py-xs text-tiny text-content-muted">
                          <span aria-hidden="true">{step.emoji ?? '·'}</span>
                          {step.target_position === null || step.target_position === undefined
                            ? t('neuroshilling.preview.reactionLoose')
                            : t('neuroshilling.preview.reaction', {
                                position: step.target_position,
                              })}
                        </span>
                      ) : (
                        <div className="rounded-lg rounded-tl-[3px] border border-line bg-surface px-md py-sm text-body">
                          {quoted ? (
                            <span
                              className={`mb-tight block border-l-2 pl-sm text-tiny text-content-subtle ${tone.border}`}
                            >
                              {quoted.text}
                            </span>
                          ) : null}
                          {step.text}
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-sm border-t border-line-row px-2xl py-lg">
        <span className="mr-auto type-caption tabular-nums">
          {t('neuroshilling.preview.total', { time: clock(total) })}
        </span>
        <Button
          size="sm"
          disabled={steps.length === 0}
          onClick={() => {
            setPlay((value) => value + 1);
          }}
        >
          {t('neuroshilling.preview.play')}
        </Button>
        <Button size="sm" className="text-action-primary" disabled={busy} onClick={onRegenerate}>
          {t('neuroshilling.preview.regenerate')}
        </Button>
        {/* Утверждать нечего, пока нечего читать, и незачем — пока сценарий уже утверждён
            и не тронут: сервер ответит тем же отказом, только позже. */}
        <Button
          variant="primary"
          size="sm"
          disabled={busy || steps.length === 0 || (status === 'approved' && !dirty)}
          onClick={onApprove}
        >
          {t('neuroshilling.scenario.approve')}
        </Button>
        <Button size="sm" onClick={onClose}>
          {t('neuroshilling.settings.cancel')}
        </Button>
      </div>
    </Modal>
  );
}
