// PROTOTYPE — throwaway. Variant B: mode first, in the neuroshilling page shape.
// A 328px sidebar holds the mode choice, the target-side preview and launch; the
// main column is ONE card whose sections appear only when the mode uses them —
// priming drops the creative block instead of greying it.
import { Card, Notice, SegmentedControl } from '@/shared/ui';

import type { Draft, Mode, Patch } from './model';
import {
  AccountChecklist,
  CreativeFields,
  Eyebrow,
  LaunchPanel,
  MODE_BODY,
  MODE_LABEL,
  PacingRows,
  StoryOptions,
  TargetPreview,
  TargetsEditor,
  VolumeFields,
} from './parts';

export const NAME = 'Режим первым, сайдбар';

export function VariantB({
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
  const priming = draft.mode === 'priming';
  return (
    <div className="pb-empty">
      <h1 className="m-0 mb-xl type-page-title">Масстегинг</h1>
      <div className="flex flex-col gap-lg lg:flex-row lg:items-start">
        <div className="flex flex-col gap-md lg:w-sidebar lg:shrink-0">
          <Card className="px-lg py-lg">
            <Eyebrow title="Режим" />
            <SegmentedControl
              variant="outline"
              className="grid gap-sm"
              value={draft.mode}
              ariaLabel="Режим"
              options={(['stories', 'priming'] as const satisfies readonly Mode[]).map((mode) => ({
                value: mode,
                label: (
                  <span className="block text-left">
                    <span className="block type-item-title">{MODE_LABEL[mode]}</span>
                    <span className="mt-xs block type-caption">{MODE_BODY[mode]}</span>
                  </span>
                ),
              }))}
              onChange={(mode) => {
                patch({ mode });
              }}
            />
          </Card>
          <Card className="px-lg py-lg">
            <TargetPreview draft={draft} />
          </Card>
          <Card className="px-lg py-lg">
            <Eyebrow title="Запуск" />
            <LaunchPanel draft={draft} running={running} onToggle={onToggleRun} />
          </Card>
        </div>

        <Card className="flex min-w-0 flex-1 flex-col gap-xl px-xl py-xl">
          <section>
            <Eyebrow title="Аккаунты" caption="кто отмечает" />
            <AccountChecklist draft={draft} patch={patch} />
          </section>
          <section className="border-t border-line pt-lg">
            <Eyebrow title="Цели" caption={`${String(draft.targets.length)} в списке`} />
            <TargetsEditor draft={draft} patch={patch} />
          </section>
          {priming ? (
            <section className="border-t border-line pt-lg">
              <Eyebrow title="Креатив" caption="в профиле аккаунта" />
              <Notice tone="info">
                В прайминге сторис не публикуются. Цель увидит то, что уже лежит в профиле аккаунта:
                закреплённый канал-переходник или сторис. Проверьте оформление в карточке аккаунта.
              </Notice>
            </section>
          ) : (
            <section className="border-t border-line pt-lg">
              <Eyebrow title="Креатив" caption="пул медиа, подпись, ссылка" />
              <CreativeFields draft={draft} patch={patch} />
            </section>
          )}
          <section className="grid gap-xl border-t border-line pt-lg md:grid-cols-2 md:gap-2xl md:divide-x md:divide-line">
            <div className="min-w-0">
              <Eyebrow title="Задержки и лимиты" />
              <PacingRows draft={draft} patch={patch} inactive="hide" />
            </div>
            <div className="flex min-w-0 flex-col gap-lg md:pl-2xl">
              <div>
                <Eyebrow title="Объём" caption="на каждый аккаунт" />
                <VolumeFields draft={draft} patch={patch} />
              </div>
              <div>
                <Eyebrow title={priming ? 'Повторы' : 'Сторис'} />
                <StoryOptions draft={draft} patch={patch} withPrimingSwitch={false} />
              </div>
            </div>
          </section>
        </Card>
      </div>
    </div>
  );
}
