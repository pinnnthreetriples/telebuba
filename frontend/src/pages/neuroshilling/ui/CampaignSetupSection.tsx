import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Button, HelpHint, Icon, Input, SegmentedControl, Switch } from '@/shared/ui';

import { AdvancedLimitsModal } from './AdvancedLimitsModal';
import type { ScenarioDraft } from './scenarioDraft';
import type { SetupDraft } from './setupDraft';
import { clampInt, MAX_LISTEN_MINUTES, MAX_PAUSE_SECONDS, splitTargets } from './setupDraft';
import { useNumberField } from './useNumberField';

// Строка настройки: подпись слева, контрол справа, разделитель сверху. Весь правый
// столбец и половина левого набраны ею — в макете это одна и та же строка, и раньше
// каждая такая пара набиралась своим `flex` со своим отступом.
function Row({
  label,
  hint,
  children,
  first = false,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
  // Первая строка блока не рисует разделитель: он отделял бы её от заголовка.
  first?: boolean;
}) {
  return (
    <div
      className={`flex min-h-touch flex-wrap items-center gap-md py-sm ${first ? '' : 'border-t border-line-row'}`}
    >
      <div className="min-w-0 flex-1">
        <div className="text-body">{label}</div>
        {hint === undefined ? null : <div className="mt-hair type-caption">{hint}</div>}
      </div>
      {children}
    </div>
  );
}

// Числовое поле дизайн-системы с тем же поведением пустого значения, что у пауз шага.
function NumberInput({
  value,
  min,
  max,
  disabled,
  ariaLabel,
  onCommit,
}: {
  value: number;
  min: number;
  max: number;
  disabled?: boolean;
  ariaLabel: string;
  onCommit: (next: number) => void;
}) {
  const field = useNumberField(value, (raw) => clampInt(raw, min, max), onCommit);
  return (
    <Input
      size="xs"
      className="w-number tabular-nums"
      type="number"
      min={min}
      max={max}
      disabled={disabled}
      value={field.value}
      aria-label={ariaLabel}
      onChange={(event) => {
        field.onChange(event.target.value);
      }}
      onBlur={field.onBlur}
    />
  );
}

function Eyebrow({ title, caption }: { title: string; caption: string }) {
  return (
    <div className="flex flex-wrap items-baseline gap-sm pb-sm">
      <span className="type-eyebrow">{title}</span>
      <span className="type-caption">{caption}</span>
    </div>
  );
}

// Цели, запуск, прослушка и лимиты — верхний блок диалога настроек.
//
// Читает ДВА черновика, и это не небрежность: макет ставит «Кампания / Оживление» и
// «Разные голоса у ролей» в одну колонку с обходом целей и паузой, а первые два живут в
// черновике сценария, вторые — в черновике настроек. Группировка на экране следует
// смыслу («как проходит прогон»), а не тому, каким PUT поле уедет на сервер; резать её по
// границе двух эндпоинтов значило бы показать оператору устройство нашего API.
export function CampaignSetupSection({
  draft,
  onDraft,
  scenario,
  onScenario,
  reserveCount,
  live,
}: {
  draft: SetupDraft;
  onDraft: (draft: SetupDraft) => void;
  scenario: ScenarioDraft;
  onScenario: (draft: ScenarioDraft) => void;
  // Рострованные аккаунты, ещё ждущие в пуле. Число, а не ростер: секция не берёт
  // серверных данных сама.
  reserveCount: number;
  // Прогон в полёте: сервер отказывает всему PUT с `campaign_running`, поэтому секция
  // говорит это замком на каждом поле, а не даёт собрать 409.
  live: boolean;
}) {
  const { t } = useTranslation();
  const [limitsOpen, setLimitsOpen] = useState(false);
  const [adding, setAdding] = useState(false);
  const [entry, setEntry] = useState('');
  const targets = splitTargets(draft.targetsRaw);
  const neuro = draft.autoresponder === 'neurodialog';

  const setTargets = (next: string[]) => {
    onDraft({ ...draft, targetsRaw: next.join('\n') });
  };
  // Вставка целым списком остаётся возможной, хотя поля-простыни больше нет: строка
  // ввода режется тем же разделителем, что и сохранённое значение, поэтому вставленный
  // из таблицы столбец превращается в столько чипов, сколько в нём чатов.
  const commitEntry = () => {
    const parsed = splitTargets(entry);
    if (parsed.length > 0) setTargets([...targets, ...parsed]);
    setEntry('');
    setAdding(false);
  };

  return (
    <section>
      <Eyebrow
        title={t('neuroshilling.setup.targets.eyebrow')}
        caption={t('neuroshilling.targetsCount', { count: targets.length })}
      />
      {/* Чипы набраны РОВНО как каналы кампании в неврокомментинге: пилюля с
          волосяной рамкой, крестик простой кнопкой внутри, добавление — приглушённая
          пунктирная пилюля. Это один и тот же список коротких имён, который правят
          по одному, и двух его начертаний в приложении быть не должно. */}
      <div className="flex flex-wrap items-center gap-sm pb-lg">
        {targets.map((target, index) => (
          <span
            key={`${target}-${String(index)}`}
            className="inline-flex items-center gap-sm rounded-full border border-line bg-canvas px-md py-tight text-body text-content-secondary"
          >
            {target}
            <button
              type="button"
              disabled={live}
              aria-label={t('neuroshilling.setup.targets.remove', { name: target })}
              onClick={() => {
                setTargets(targets.filter((_, at) => at !== index));
              }}
              className="text-body leading-none text-content-subtle disabled:opacity-50"
            >
              ×
            </button>
          </span>
        ))}
        {adding ? (
          <span className="inline-flex items-center gap-tight rounded-full border border-action-primary bg-surface-card py-xs pl-md pr-xs">
            <input
              autoFocus
              value={entry}
              disabled={live}
              placeholder={t('neuroshilling.setup.targets.addPlaceholder')}
              aria-label={t('neuroshilling.setup.targets.add')}
              onChange={(event) => {
                setEntry(event.target.value);
              }}
              onKeyDown={(event) => {
                if (event.key === 'Enter') commitEntry();
                if (event.key === 'Escape') {
                  setEntry('');
                  setAdding(false);
                }
              }}
              className="w-col border-none bg-transparent text-body outline-none"
            />
            <button
              type="button"
              aria-label={t('neuroshilling.setup.targets.confirm')}
              disabled={!entry.trim()}
              onClick={commitEntry}
              className="flex size-chip shrink-0 items-center justify-center rounded-full bg-action-primary text-on-action disabled:opacity-50"
            >
              <Icon name="check" size={12} />
            </button>
          </span>
        ) : (
          // Не `Button variant="dashed"`, и намеренно — по той же причине, что у
          // соседа: это приглушённый строчный добавитель, стоящий в ряду чипов и
          // нарисованный `line-strong` и `ink-muted`, тогда как блочный добавитель под
          // списком синий. Общая у них только пунктирная рамка.
          <button
            type="button"
            disabled={live}
            onClick={() => {
              setAdding(true);
            }}
            className="inline-flex items-center gap-tight rounded-full border border-dashed border-line-strong bg-surface-card px-md py-tight text-body text-content-muted hover:border-action-primary hover:text-action-primary disabled:opacity-50"
          >
            {t('neuroshilling.setup.targets.add')}
          </button>
        )}
      </div>

      {/* Две колонки, разделённые волосяной линией, как в макете. Ниже `sm` они
          складываются в стопку, и разделитель тогда лежит НАД правой колонкой. */}
      <div className="grid gap-xl border-t border-line pt-lg sm:grid-cols-2 sm:gap-2xl sm:divide-x sm:divide-line">
        <div className="min-w-0 sm:pr-2xl">
          <Eyebrow
            title={t('neuroshilling.setup.launch.eyebrow')}
            caption={t('neuroshilling.setup.launch.caption')}
          />

          {/* Режим кампании: две карточки-переключателя, а не сегментированный контрол —
              у каждой есть строка объяснения, и без неё «Оживление» ничего не значит. */}
          <div
            role="radiogroup"
            aria-label={t('neuroshilling.scenario.mode.label')}
            className="grid gap-sm pb-sm sm:grid-cols-2"
          >
            {(['campaign', 'revive'] as const).map((mode) => {
              const picked = scenario.mode === mode;
              return (
                <button
                  key={mode}
                  type="button"
                  role="radio"
                  aria-checked={picked}
                  disabled={live}
                  onClick={() => {
                    onScenario({ ...scenario, mode });
                  }}
                  className={`rounded-lg border p-md text-left disabled:opacity-60 ${picked ? 'border-action-primary bg-info-tint' : 'border-line bg-surface-card'}`}
                >
                  <span className="block type-item-title">
                    {t(`neuroshilling.scenario.mode.${mode}`)}
                  </span>
                  <span className="mt-xs block type-caption">
                    {t(`neuroshilling.setup.mode.${mode}.body`)}
                  </span>
                </button>
              );
            })}
          </div>

          <Row label={t('neuroshilling.setup.traversal.label')}>
            <SegmentedControl
              variant="pill"
              value={draft.runMode}
              disabled={live}
              ariaLabel={t('neuroshilling.setup.runMode.label')}
              options={[
                {
                  value: 'sequential',
                  label: t('neuroshilling.setup.runMode.sequential.title'),
                },
                {
                  value: 'parallel',
                  label: t('neuroshilling.setup.runMode.parallel.title'),
                  // Не просто спрятано: клиент типизирует поле, сервер отвечает 400
                  // `run_mode_not_supported` на сохранение и 409 на запуск. Выключено с
                  // причиной в подсказке — единственная форма, которая говорит это до
                  // клика.
                  disabled: true,
                  title: t('neuroshilling.setup.runMode.parallel.unavailable'),
                },
              ]}
              onChange={(mode) => {
                onDraft({ ...draft, runMode: mode });
              }}
            />
          </Row>

          <Row label={t('neuroshilling.setup.pause.label')}>
            <div className="flex items-center gap-sm">
              {(['min', 'max'] as const).map((bound) => (
                <NumberInput
                  key={bound}
                  min={0}
                  max={MAX_PAUSE_SECONDS}
                  disabled={live}
                  value={bound === 'min' ? draft.pauseMinSeconds : draft.pauseMaxSeconds}
                  ariaLabel={t(`neuroshilling.setup.pause.${bound}Label`)}
                  onCommit={(next) => {
                    // Зажимаются ПАРОЙ: `pause_min > pause_max` — ошибка валидатора
                    // модели, и до оператора она доходит нечитаемым 422.
                    const value = clampInt(next, 0, MAX_PAUSE_SECONDS);
                    onDraft(
                      bound === 'min'
                        ? {
                            ...draft,
                            pauseMinSeconds: value,
                            pauseMaxSeconds: Math.max(value, draft.pauseMaxSeconds),
                          }
                        : {
                            ...draft,
                            pauseMaxSeconds: value,
                            pauseMinSeconds: Math.min(value, draft.pauseMinSeconds),
                          },
                    );
                  }}
                />
              ))}
              <span className="type-caption">{t('neuroshilling.setup.pause.unit')}</span>
            </div>
          </Row>

          <Row
            label={t('neuroshilling.setup.uniqueMessages.label')}
            hint={t('neuroshilling.setup.uniqueMessages.caption')}
          >
            <Switch
              checked={scenario.uniqueMessages}
              disabled={live}
              label={t('neuroshilling.setup.uniqueMessages.label')}
              onChange={(value) => {
                onScenario({ ...scenario, uniqueMessages: value });
              }}
            />
          </Row>
        </div>

        <div className="min-w-0 sm:pl-2xl">
          <Eyebrow
            title={t('neuroshilling.setup.listening.title')}
            caption={t('neuroshilling.setup.listening.caption')}
          />

          <Row first label={t('neuroshilling.setup.autoresponder.label')}>
            <HelpHint text={t('neuroshilling.setup.listening.hint')} />
            <SegmentedControl
              variant="pill"
              value={draft.autoresponder}
              disabled={live}
              ariaLabel={t('neuroshilling.setup.autoresponder.label')}
              options={(['off', 'neurodialog'] as const).map((option) => ({
                value: option,
                label: t(`neuroshilling.setup.autoresponder.${option}`),
              }))}
              onChange={(option) => {
                onDraft({ ...draft, autoresponder: option });
              }}
            />
          </Row>

          {neuro ? (
            <>
              <Row
                label={t('neuroshilling.setup.replyToHumans.label')}
                hint={t('neuroshilling.setup.replyToHumans.caption')}
              >
                <Switch
                  disabled={live}
                  checked={draft.replyToHumans}
                  label={t('neuroshilling.setup.replyToHumans.label')}
                  onChange={(value) => {
                    onDraft({ ...draft, replyToHumans: value });
                  }}
                />
              </Row>

              {/* Показывается ровно на одном сочетании — том единственном, где
                  опубликованное спровоцировал посторонний человек. */}
              {draft.replyToHumans ? (
                <div className="rounded-lg bg-warning-tint px-md py-sm text-tiny text-warning-deep">
                  {t('neuroshilling.setup.replyToHumans.warning')}
                </div>
              ) : (
                <div className="rounded-lg bg-warning-tint px-md py-sm text-tiny text-warning-deep">
                  {t('neuroshilling.setup.replyToHumans.idle')}
                </div>
              )}

              <Row label={t('neuroshilling.setup.replyActivity.label')}>
                <SegmentedControl
                  variant="pill"
                  value={draft.replyActivity}
                  disabled={live}
                  ariaLabel={t('neuroshilling.setup.replyActivity.label')}
                  options={(['calm', 'medium', 'active'] as const).map((option) => ({
                    value: option,
                    label: t(`neuroshilling.setup.replyActivity.${option}`),
                  }))}
                  onChange={(option) => {
                    onDraft({ ...draft, replyActivity: option });
                  }}
                />
              </Row>

              <Row
                label={t('neuroshilling.setup.readChat.label')}
                hint={t('neuroshilling.setup.readChat.caption')}
              >
                <Switch
                  disabled={live}
                  checked={scenario.useChatContext}
                  label={t('neuroshilling.setup.readChat.label')}
                  onChange={(value) => {
                    onScenario({ ...scenario, useChatContext: value });
                  }}
                />
              </Row>

              <Row label={t('neuroshilling.setup.listen.row')}>
                <div className="flex items-center gap-sm">
                  <NumberInput
                    min={1}
                    max={MAX_LISTEN_MINUTES}
                    disabled={live}
                    value={draft.listenMinutes}
                    ariaLabel={t('neuroshilling.setup.listen.label')}
                    onCommit={(next) => {
                      onDraft({ ...draft, listenMinutes: clampInt(next, 1, MAX_LISTEN_MINUTES) });
                    }}
                  />
                  <span className="type-caption">{t('neuroshilling.setup.listen.unit')}</span>
                </div>
              </Row>
            </>
          ) : (
            <div className="rounded-lg border border-dashed border-line-strong px-md py-md type-caption">
              {t('neuroshilling.setup.autoresponder.hintOff')}
            </div>
          )}
        </div>
      </div>

      <Row
        label={t('neuroshilling.setup.limits.label')}
        hint={t('neuroshilling.setup.limits.caption')}
      >
        <span className="type-caption tabular-nums">
          {t('neuroshilling.setup.limits.summary', {
            hour: draft.messagesPerHour,
            chat: draft.messagesPerChatPerDay,
            reserve: t(`neuroshilling.setup.limits.reserve.${draft.reserveEnabled ? 'on' : 'off'}`),
          })}
        </span>
        <Button
          size="xs"
          onClick={() => {
            setLimitsOpen(true);
          }}
        >
          {t('neuroshilling.setup.limits.configure')}
        </Button>
      </Row>

      {limitsOpen ? (
        <AdvancedLimitsModal
          draft={draft}
          onDraft={onDraft}
          reserveCount={reserveCount}
          live={live}
          onClose={() => {
            setLimitsOpen(false);
          }}
        />
      ) : null}
    </section>
  );
}
