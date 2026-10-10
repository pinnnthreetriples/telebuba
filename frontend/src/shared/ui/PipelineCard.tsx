import type { ReactNode } from 'react';

import type { FeedbackTone } from '@/shared/design-system';

import { Button } from './Button';
import { CardHeader } from './CardHeader';
import { Icon } from './Icon';
import { Notice } from './Notice';
import { type Stat, StatGrid } from './StatGrid';
import { Stepper, type StepperNarrow, type StepperStep } from './Stepper';

// Карточка конвейера: шапка (заголовок, статус, пуск/стоп), линия этапов, строка о том,
// что сейчас происходит, и плитки чисел. У нейрокомментинга и нейрошиллинга она была своя
// у каждого — 169 и 269 строк — и они разошлись во всём, что одинаково по смыслу: подложка
// (тонированная против белой), кнопка (с иконкой `sm` против красной `md`), точки этапов,
// строка состояния и сетка плиток. Образец — нейрокомментинг.
//
// Данные страницы приходят пропсами: этапы — уже с состояниями, числа — готовыми.
// Своё, чего у другого конвейера нет (полоса отправки, прослушка, исходы прогона), страница
// кладёт в `children` — оно стоит под плитками.
export function PipelineCard({
  title,
  status,
  running,
  toggleDisabled = false,
  startLabel,
  stopLabel,
  onToggle,
  steps,
  stepsLabel,
  narrow = 'current',
  notice,
  stats,
  children,
}: {
  title: ReactNode;
  // Плашка статуса рядом с заголовком.
  status: ReactNode;
  running: boolean;
  toggleDisabled?: boolean;
  startLabel: string;
  stopLabel: string;
  onToggle: () => void;
  steps: readonly StepperStep[];
  stepsLabel?: string;
  narrow?: StepperNarrow;
  // Строка под этапами: что происходит или что мешает начать. `danger` — прогон упал или
  // встал (у рассылки).
  notice: { tone: FeedbackTone; text: string };
  stats: readonly Stat[];
  children?: ReactNode;
}) {
  return (
    <div className="rounded-lg border border-info-hairline bg-info-tint px-6 py-4 text-content-primary">
      <CardHeader wrap title={title} badge={status} className="mb-4">
        <Button
          variant={running ? 'neutral' : 'primary'}
          size="sm"
          disabled={toggleDisabled}
          onClick={onToggle}
          className="shrink-0 gap-2"
        >
          {running ? <Icon name="pause" size={14} /> : <Icon name="play" size={14} />}
          {running ? stopLabel : startLabel}
        </Button>
      </CardHeader>

      <Stepper steps={steps} pulse narrow={narrow} label={stepsLabel} className="mb-3" />

      {/* Отступ несёт обёртка: расстоянием до соседа распоряжается родитель. */}
      <div className="mb-4">
        <Notice tone={notice.tone} className="flex items-center gap-3 px-4 font-medium">
          <span
            className={`size-dot shrink-0 rounded-full bg-current ${running ? 'pl-pulse' : ''}`}
          />
          <span>{notice.text}</span>
        </Notice>
      </div>

      <StatGrid stats={stats} />

      {children === undefined ? null : <div className="mt-4">{children}</div>}
    </div>
  );
}
