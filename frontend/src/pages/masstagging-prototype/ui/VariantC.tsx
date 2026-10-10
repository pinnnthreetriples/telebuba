// PROTOTYPE — throwaway. Variant C: a step-by-step wizard with a sticky summary.
// One step on screen at a time; priming skips the creative step outright. The
// right rail keeps the estimate and the target-side preview in view throughout.
import { useState } from 'react';

import { Badge, Button, Card, Icon, SegmentedControl } from '@/shared/ui';

import { type Draft, estimate, type Mode, type Patch } from './model';
import {
  AccountChecklist,
  CreativeFields,
  Eyebrow,
  LaunchPanel,
  MODE_BODY,
  MODE_LABEL,
  PacingRows,
  Stat,
  StoryOptions,
  TargetPreview,
  TargetsEditor,
  VolumeFields,
} from './parts';

export const NAME = 'Пошаговый мастер';

type Step = 'mode' | 'accounts' | 'targets' | 'creative' | 'pacing' | 'launch';

const STEPS: { key: Step; title: string }[] = [
  { key: 'mode', title: 'Режим' },
  { key: 'accounts', title: 'Аккаунты' },
  { key: 'targets', title: 'Цели' },
  { key: 'creative', title: 'Креатив' },
  { key: 'pacing', title: 'Темп и лимиты' },
  { key: 'launch', title: 'Запуск' },
];

export function VariantC({
  draft,
  patch,
  running,
  onToggleRun,
}: {
  draft: Draft;
  patch: Patch;
  running: boolean;
  onToggleRun: () => void;
}) {
  const [step, setStep] = useState<Step>('mode');
  const priming = draft.mode === 'priming';
  const steps = STEPS.filter((s) => !(priming && s.key === 'creative'));
  const at = Math.max(
    steps.findIndex((s) => s.key === step),
    0,
  );
  const current = steps[at] ?? steps[0];
  const { accounts, free, perDay, days } = estimate(draft);

  return (
    <div className="pb-empty">
      <h1 className="m-0 mb-xl type-page-title">Масстегинг</h1>

      <ol className="m-0 mb-lg flex list-none flex-wrap gap-sm p-0">
        {steps.map((s, index) => {
          const done = index < at;
          const active = index === at;
          return (
            <li key={s.key}>
              <button
                type="button"
                onClick={() => {
                  setStep(s.key);
                }}
                className={`inline-flex items-center gap-sm rounded-full border px-md py-tight text-body font-medium ${
                  active
                    ? 'border-action-primary bg-info-tint text-info-strong'
                    : done
                      ? 'border-line bg-surface-card text-content-primary'
                      : 'border-line bg-surface-card text-content-muted'
                }`}
              >
                <span
                  className={`flex size-chip items-center justify-center rounded-full text-tiny ${
                    done ? 'bg-success text-on-success' : active ? 'bg-action-primary text-on-action' : 'bg-canvas'
                  }`}
                >
                  {done ? <Icon name="check" size={12} /> : index + 1}
                </span>
                {s.title}
              </button>
            </li>
          );
        })}
      </ol>

      <div className="flex flex-col gap-lg lg:flex-row lg:items-start">
        <Card className="flex min-w-0 flex-1 flex-col gap-lg px-xl py-xl">
          <div className="type-card-title">
            {at + 1}. {current?.title}
          </div>

          {step === 'mode' ? (
            <SegmentedControl
              variant="outline"
              className="grid gap-md sm:grid-cols-2"
              value={draft.mode}
              ariaLabel="Режим"
              options={(['stories', 'priming'] as const satisfies readonly Mode[]).map((mode) => ({
                value: mode,
                label: (
                  <span className="block py-sm text-left">
                    <span className="block type-item-title">{MODE_LABEL[mode]}</span>
                    <span className="mt-xs block type-caption">{MODE_BODY[mode]}</span>
                    <span className="mt-sm flex flex-wrap gap-xs">
                      {mode === 'stories' ? (
                        <>
                          <Badge tone="info">Premium желателен</Badge>
                          <Badge>нужен пул медиа</Badge>
                        </>
                      ) : (
                        <>
                          <Badge tone="success">Premium не нужен</Badge>
                          <Badge>креатив в профиле</Badge>
                        </>
                      )}
                    </span>
                  </span>
                ),
              }))}
              onChange={(mode) => {
                patch({ mode });
              }}
            />
          ) : null}
          {step === 'accounts' ? <AccountChecklist draft={draft} patch={patch} /> : null}
          {step === 'targets' ? <TargetsEditor draft={draft} patch={patch} /> : null}
          {step === 'creative' ? <CreativeFields draft={draft} patch={patch} /> : null}
          {step === 'pacing' ? (
            <div className="grid gap-xl md:grid-cols-2 md:gap-2xl">
              <div>
                <Eyebrow title="Задержки и лимиты" />
                <PacingRows draft={draft} patch={patch} inactive="hide" />
              </div>
              <div className="flex flex-col gap-lg">
                <div>
                  <Eyebrow title="Объём" caption="на каждый аккаунт" />
                  <VolumeFields draft={draft} patch={patch} />
                </div>
                <StoryOptions draft={draft} patch={patch} withPrimingSwitch={false} />
              </div>
            </div>
          ) : null}
          {step === 'launch' ? (
            <LaunchPanel draft={draft} running={running} onToggle={onToggleRun} />
          ) : null}

          <div className="flex justify-between border-t border-line-row pt-lg">
            <Button
              variant="ghost"
              disabled={at === 0}
              onClick={() => {
                setStep(steps[at - 1]?.key ?? 'mode');
              }}
            >
              <Icon name="chevron-left" size={14} />
              Назад
            </Button>
            {at < steps.length - 1 ? (
              <Button
                variant="primary"
                onClick={() => {
                  setStep(steps[at + 1]?.key ?? 'launch');
                }}
              >
                Далее
                <Icon name="chevron-right" size={14} />
              </Button>
            ) : null}
          </div>
        </Card>

        <div className="flex flex-col gap-md lg:w-sidebar lg:shrink-0">
          <Card className="flex flex-col gap-md px-lg py-lg">
            <Eyebrow title="Сводка" caption={MODE_LABEL[draft.mode]} />
            <div className="grid grid-cols-2 gap-md">
              <Stat label={free > 0 ? `аккаунтов, без Premium ${String(free)}` : 'аккаунтов'} value={String(accounts.length)} />
              <Stat label="целей" value={String(draft.targets.length)} />
              <Stat label="отметок в сутки" value={String(perDay)} />
              <Stat label="дней на список" value={days === null ? '—' : String(days)} />
            </div>
          </Card>
          <Card className="px-lg py-lg">
            <TargetPreview draft={draft} />
          </Card>
        </div>
      </div>
    </div>
  );
}
