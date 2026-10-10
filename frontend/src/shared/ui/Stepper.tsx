import { cn } from '@/shared/lib/cn';

import { Icon } from './Icon';

// Линия этапов: точки на рельсе и подписи под ними. Стояла четырьмя копиями — конвейер
// нейрокомментинга, конвейер нейрошиллинга, цикл прогрева и шаги мастера добавления
// аккаунта, — и копии разошлись во всём: у одной пройденный шаг был зелёной галочкой, у
// другой синей точкой, у третьей синей цифрой; рельса у двух шла одной линией с отступом
// в полколонки, у третьей — двумя половинками в каждой колонке, у четвёртой — отрезками
// между кружками. Образец — конвейер нейрокомментинга.
//
// Состояние у каждого шага своё, а не «индекс текущего»: конвейер нейрошиллинга —
// неупорядоченный список условий, и пройденный шаг может стоять после непройденного.
//
// Рельса — две половинки в каждой ячейке, и они сходятся под центром точки. Так линия
// начинается под первой точкой и кончается под последней, ничего не зная о ширине
// колонки: дробей у шкалы ширин нет, а инсет в полколонки пришлось бы считать в процентах
// и держать в согласии с числом шагов. Отрезок, ведущий к шагу, красится состоянием этого
// шага: зелёным к пройденному, синим к текущему, к предстоящему — никак.
export type StepState = 'done' | 'current' | 'upcoming';

export interface StepperStep {
  id: string;
  label?: string;
  // Вторая строка под подписью: «2/3 ролей», «60–180 с».
  caption?: string;
  state: StepState;
}

// Что делать с подписями, когда экран узок:
// `wrap` — оставить под точками (их мало, и они короткие);
// `current` — спрятать до `md` и назвать одной строкой под рельсой текущий шаг;
// `list` — до `sm` поставить шаги столбцом, точка слева, подпись справа.
export type StepperNarrow = 'wrap' | 'current' | 'list';

const LABEL: Record<StepState, string> = {
  done: 'font-medium text-success-deep',
  current: 'font-medium text-info-strong',
  upcoming: 'text-content-subtle',
};

const FILL: Record<StepState, string> = {
  done: 'w-full bg-success',
  current: 'w-full bg-action-primary',
  upcoming: 'w-0',
};

// Кружок с цифрой; пройденный шаг цифры не носит — у него галочка.
const NUMBER: Record<Exclude<StepState, 'done'>, string> = {
  current: 'bg-action-primary text-on-fill',
  upcoming: 'border border-info-line bg-surface-card text-content-muted',
};

function Half({ hidden, into }: { hidden: boolean; into: StepState }) {
  return (
    <span className={cn('h-rail flex-1 overflow-hidden bg-info-line', hidden && 'invisible')}>
      <span className={cn('block h-full transition-[width] duration-roll ease-out', FILL[into])} />
    </span>
  );
}

function Dot({
  state,
  number,
  pulse,
}: {
  state: StepState;
  number: number | null;
  pulse: boolean;
}) {
  if (number !== null) {
    if (state === 'done') {
      return (
        <span className="tb-pop flex size-icon items-center justify-center rounded-full bg-success">
          <Icon name="check" size={14} className="stroke-on-fill" />
        </span>
      );
    }
    return (
      <span
        className={cn(
          'flex size-icon items-center justify-center rounded-full text-body font-medium',
          NUMBER[state],
        )}
      >
        {number}
      </span>
    );
  }
  if (state === 'done') {
    return (
      <span className="tb-pop flex size-spinner items-center justify-center rounded-full bg-success">
        <Icon name="check" size={10} className="stroke-on-fill" />
      </span>
    );
  }
  if (state === 'current') {
    return (
      <span className={cn('size-node rounded-full bg-action-primary', pulse && 'tb-livedot')} />
    );
  }
  return (
    <span className="size-node rounded-full border-[1.5px] border-info-line bg-surface-card" />
  );
}

export function Stepper({
  steps,
  numbered = false,
  pulse = false,
  narrow = 'wrap',
  label,
  className,
}: {
  steps: readonly StepperStep[];
  // Цифра в кружке вместо точки — шаги мастера, у которых нет подписей.
  numbered?: boolean;
  // Текущая точка дышит: идёт работа, а не ожидание.
  pulse?: boolean;
  narrow?: StepperNarrow;
  label?: string;
  className?: string;
}) {
  const list = narrow === 'list';
  const current = steps.find((step) => step.state === 'current');
  const last = steps.length - 1;
  return (
    <div className={className}>
      <ol aria-label={label} className={cn('flex', list && 'flex-col gap-3 sm:flex-row sm:gap-0')}>
        {steps.map((step, index) => (
          <li
            key={step.id}
            aria-current={step.state === 'current' ? 'step' : undefined}
            className={cn(
              // Без `min-w-0` при `current`: ячейка не сжимается под самую широкую
              // подпись, и шесть подписей в строку не налезают друг на друга.
              'flex flex-1 flex-col items-center',
              narrow !== 'current' && 'min-w-0',
              list && 'flex-row gap-3 sm:flex-col sm:gap-0',
            )}
          >
            <div className={cn('relative flex w-full justify-center', list && 'w-auto sm:w-full')}>
              <div
                aria-hidden="true"
                className={cn(
                  'absolute inset-x-0 top-1/2 flex -translate-y-1/2',
                  list && 'hidden sm:flex',
                )}
              >
                <Half hidden={index === 0} into={step.state} />
                <Half hidden={index === last} into={steps[index + 1]?.state ?? 'upcoming'} />
              </div>
              <span
                className={cn(
                  'relative flex items-center justify-center',
                  numbered ? 'size-icon' : 'size-glyph',
                )}
              >
                <Dot state={step.state} number={numbered ? index + 1 : null} pulse={pulse} />
              </span>
            </div>
            {step.label === undefined ? null : (
              <div
                className={cn(
                  'flex min-w-0 flex-col text-center',
                  narrow === 'current' && 'hidden md:flex',
                  list && 'flex-1 text-left sm:flex-none sm:text-center',
                )}
              >
                <span
                  className={cn(
                    'text-small',
                    narrow === 'current' && 'whitespace-nowrap',
                    LABEL[step.state],
                  )}
                >
                  {step.label}
                </span>
                {step.caption === undefined ? null : (
                  <span className="type-small">{step.caption}</span>
                )}
              </div>
            )}
          </li>
        ))}
      </ol>
      {narrow === 'current' && current?.label !== undefined ? (
        <div className="mt-3 text-center type-small-medium text-info-strong md:hidden">
          {current.label}
        </div>
      ) : null}
    </div>
  );
}
