import { cn } from '@/shared/lib/cn';

import { CollapsibleCard } from './CollapsibleCard';
import { NumberedStep } from './NumberedStep';

// «Как это работает» внизу экрана: свёрнутая карточка на подложке страницы и
// пронумерованные шаги в ней. Стояла тремя копиями — у нейрокомментинга и нейрошиллинга
// слово в слово, у прогрева с подсказкой и в две колонки.
export function HowItWorksCard({
  title,
  steps,
  hint,
  columns = 1,
  defaultOpen = false,
}: {
  title: string;
  steps: readonly string[];
  hint?: string;
  // Две колонки — с ширины `md`, когда шагов столько, что один столбец тянется на экран.
  columns?: 1 | 2;
  // Экран держит её свёрнутой; раскрытой её показывает страница блоков.
  defaultOpen?: boolean;
}) {
  return (
    <CollapsibleCard
      label={title}
      defaultOpen={defaultOpen}
      wrapperClassName="rounded-lg border border-line bg-canvas"
      header={<span className="type-h3">{title}</span>}
    >
      {hint === undefined ? null : <div className="mb-4 type-small">{hint}</div>}
      <div className={cn('grid grid-cols-1 gap-x-4 gap-y-3', columns === 2 && 'md:grid-cols-2')}>
        {steps.map((step, index) => (
          <NumberedStep key={step} number={index + 1}>
            {step}
          </NumberedStep>
        ))}
      </div>
    </CollapsibleCard>
  );
}
