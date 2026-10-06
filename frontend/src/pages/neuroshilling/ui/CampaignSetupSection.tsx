import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  Button,
  ChipAddButton,
  HelpHint,
  Icon,
  IconButton,
  InlineChipEditor,
  Input,
  SectionLabel,
  SegmentedControl,
  SettingRow,
  Switch,
} from '@/shared/ui';

import { AdvancedLimitsModal } from './AdvancedLimitsModal';
import type { ScenarioDraft } from './scenarioDraft';
import type { SetupDraft } from './setupDraft';
import { clampInt, MAX_LISTEN_MINUTES, MAX_PAUSE_SECONDS, splitTargets } from './setupDraft';
import { useNumberField } from './useNumberField';

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
      <SectionLabel
        title={t('neuroshilling.setup.targets.eyebrow')}
        caption={t('neuroshilling.targetsCount', { count: targets.length })}
      />
      {/* Чипы набраны РОВНО как каналы кампании в неврокомментинге: пилюля с
          волосяной рамкой, крестик простой кнопкой внутри, добавление — приглушённая
          пунктирная пилюля. Это один и тот же список коротких имён, который правят
          по одному, и двух его начертаний в приложении быть не должно. */}
      <div className="flex flex-wrap items-center gap-2 pb-4">
        {targets.map((target, index) => (
          <span
            key={`${target}-${String(index)}`}
            className="inline-flex items-center gap-2 rounded-full border border-line bg-canvas h-control px-3 text-body text-content-secondary"
          >
            {target}
            <IconButton
              size="sm"
              shape="circle"
              disabled={live}
              aria-label={t('neuroshilling.setup.targets.remove', { name: target })}
              onClick={() => {
                setTargets(targets.filter((_, at) => at !== index));
              }}
            >
              <Icon name="close" size={16} />
            </IconButton>
          </span>
        ))}
        {adding ? (
          <InlineChipEditor
            value={entry}
            onChange={setEntry}
            onConfirm={commitEntry}
            onCancel={() => {
              setEntry('');
              setAdding(false);
            }}
            placeholder={t('neuroshilling.setup.targets.addPlaceholder')}
            inputLabel={t('neuroshilling.setup.targets.add')}
            confirmLabel={t('neuroshilling.setup.targets.confirm')}
            disabled={live}
          />
        ) : (
          <ChipAddButton
            disabled={live}
            onClick={() => {
              setAdding(true);
            }}
          >
            {t('neuroshilling.setup.targets.add')}
          </ChipAddButton>
        )}
      </div>

      {/* Две колонки, разделённые волосяной линией, как в макете. Ниже `sm` они
          складываются в стопку, и разделитель тогда лежит НАД правой колонкой. */}
      <div className="grid gap-4 border-t border-line pt-4 sm:grid-cols-2 sm:gap-6 sm:divide-x sm:divide-line">
        <div className="min-w-0 sm:pr-6">
          <SectionLabel
            title={t('neuroshilling.setup.launch.eyebrow')}
            caption={t('neuroshilling.setup.launch.caption')}
          />

          {/* Карточки сохраняют пояснения, а общий контрол даёт им radio keyboard pattern. */}
          <SegmentedControl
            value={scenario.mode}
            disabled={live}
            ariaLabel={t('neuroshilling.scenario.mode.label')}
            variant="outline"
            className="grid gap-2 pb-2 sm:grid-cols-2"
            options={(['campaign', 'revive'] as const).map((mode) => ({
              value: mode,
              label: (
                <span className="block text-left">
                  <span className="block type-body-medium">
                    {t(`neuroshilling.scenario.mode.${mode}`)}
                  </span>
                  <span className="mt-1 block type-small">
                    {t(`neuroshilling.setup.mode.${mode}.body`)}
                  </span>
                </span>
              ),
            }))}
            onChange={(mode) => {
              onScenario({ ...scenario, mode });
            }}
          />

          <SettingRow label={t('neuroshilling.setup.traversal.label')}>
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
          </SettingRow>

          <SettingRow label={t('neuroshilling.setup.pause.label')}>
            <div className="flex items-center gap-2">
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
              <span className="type-small">{t('neuroshilling.setup.pause.unit')}</span>
            </div>
          </SettingRow>

          <SettingRow
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
          </SettingRow>
        </div>

        <div className="min-w-0 sm:pl-6">
          <SectionLabel
            title={t('neuroshilling.setup.listening.title')}
            caption={t('neuroshilling.setup.listening.caption')}
          />

          <SettingRow first label={t('neuroshilling.setup.autoresponder.label')}>
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
          </SettingRow>

          {neuro ? (
            <>
              <SettingRow
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
              </SettingRow>

              {/* Показывается ровно на одном сочетании — том единственном, где
                  опубликованное спровоцировал посторонний человек. */}
              {draft.replyToHumans ? (
                <div className="rounded-md bg-warning-tint px-3 py-2 text-small text-warning-deep">
                  {t('neuroshilling.setup.replyToHumans.warning')}
                </div>
              ) : (
                <div className="rounded-md bg-warning-tint px-3 py-2 text-small text-warning-deep">
                  {t('neuroshilling.setup.replyToHumans.idle')}
                </div>
              )}

              <SettingRow label={t('neuroshilling.setup.replyActivity.label')}>
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
              </SettingRow>

              <SettingRow
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
              </SettingRow>

              <SettingRow label={t('neuroshilling.setup.listen.row')}>
                <div className="flex items-center gap-2">
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
                  <span className="type-small">{t('neuroshilling.setup.listen.unit')}</span>
                </div>
              </SettingRow>
            </>
          ) : (
            <div className="rounded-md border border-dashed border-line-strong px-3 py-3 type-small">
              {t('neuroshilling.setup.autoresponder.hintOff')}
            </div>
          )}
        </div>
      </div>

      <SettingRow
        label={t('neuroshilling.setup.limits.label')}
        hint={t('neuroshilling.setup.limits.caption')}
      >
        <span className="type-small tabular-nums">
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
      </SettingRow>

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
