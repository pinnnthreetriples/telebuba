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
    <div className="pb-16">
      <h1 className="m-0 mb-4 type-h1">Масстегинг</h1>
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start">
        <div className="flex flex-col gap-3 lg:w-sidebar lg:shrink-0">
          <Card className="px-4 py-4">
            <Eyebrow title="Режим" />
            <SegmentedControl
              variant="outline"
              className="grid gap-2"
              value={draft.mode}
              ariaLabel="Режим"
              options={(['stories', 'priming'] as const satisfies readonly Mode[]).map((mode) => ({
                value: mode,
                label: (
                  <span className="block text-left">
                    <span className="block type-body-medium">{MODE_LABEL[mode]}</span>
                    <span className="mt-1 block type-small">{MODE_BODY[mode]}</span>
                  </span>
                ),
              }))}
              onChange={(mode) => {
                patch({ mode });
              }}
            />
          </Card>
          <Card className="px-4 py-4">
            <TargetPreview draft={draft} />
          </Card>
          <Card className="px-4 py-4">
            <Eyebrow title="Запуск" />
            <LaunchPanel draft={draft} running={running} onToggle={onToggleRun} />
          </Card>
        </div>

        <Card className="flex min-w-0 flex-1 flex-col gap-4 px-6 py-6">
          <section>
            <Eyebrow title="Аккаунты" caption="кто отмечает" />
            <AccountChecklist draft={draft} patch={patch} />
          </section>
          <section className="border-t border-line pt-4">
            <Eyebrow title="Цели" caption={`${String(draft.targets.length)} в списке`} />
            <TargetsEditor draft={draft} patch={patch} />
          </section>
          {priming ? (
            <section className="border-t border-line pt-4">
              <Eyebrow title="Креатив" caption="в профиле аккаунта" />
              <Notice tone="info">
                В прайминге сторис не публикуются. Цель увидит то, что уже лежит в профиле аккаунта:
                закреплённый канал-переходник или сторис. Проверьте оформление в карточке аккаунта.
              </Notice>
            </section>
          ) : (
            <section className="border-t border-line pt-4">
              <Eyebrow title="Креатив" caption="пул медиа, подпись, ссылка" />
              <CreativeFields draft={draft} patch={patch} />
            </section>
          )}
          <section className="grid gap-4 border-t border-line pt-4 md:grid-cols-2 md:gap-6 md:divide-x md:divide-line">
            <div className="min-w-0">
              <Eyebrow title="Задержки и лимиты" />
              <PacingRows draft={draft} patch={patch} inactive="hide" />
            </div>
            <div className="flex min-w-0 flex-col gap-4 md:pl-6">
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
