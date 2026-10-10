import { cn } from '@/shared/lib/cn';

import { Odometer } from './Odometer';

// Плитка числа: крупное значение и подпись под ним. Стояла четырьмя копиями — в конвейерах
// нейрокомментинга и нейрошиллинга, над таблицей аккаунтов и в шапке прогрева, — и копии
// разошлись в мелочах, которые и делают плитки одной семьёй: отступ подписи, катится число
// или стоит, табличные ли цифры, рамка у каждой плитки или общая. Образец — конвейер
// нейрокомментинга.
//
// Число катится (`Odometer`); строка — «4:30», «—» — стоит в той же строке той же высоты.
export type StatTone = 'default' | 'primary' | 'success' | 'warning' | 'danger';

export interface Stat {
  label: string;
  value: number | string;
  tone?: StatTone;
}

const INK: Record<StatTone, string> = {
  default: 'text-content-primary',
  primary: 'text-action-primary',
  success: 'text-success-deep',
  warning: 'text-warning-deep',
  danger: 'text-danger',
};

export function StatTile({ label, value, tone = 'default' }: Stat) {
  return (
    <div>
      {typeof value === 'number' ? (
        <Odometer value={value} className={INK[tone]} />
      ) : (
        <span
          className={cn('inline-flex h-[1.1em] type-h1 leading-[1.1em] tabular-nums', INK[tone])}
        >
          {value}
        </span>
      )}
      <div className="mt-1 type-small">{label}</div>
    </div>
  );
}

// Сетка плиток в одной рамке. Разделители — собственные границы плиток, а не зазор в 1px
// над подложкой: у ритма нет ступени в 1px.
//
// До трёх плиток стоят одной строкой на любой ширине, и каждая после первой рисует левую
// границу. Больше трёх — до `md` по две: левая в паре рисует вертикаль, каждая строка после
// первой — горизонталь, а НЕЧЁТНАЯ последняя растягивается на обе колонки (`odd:last:`);
// с `md` все в одну строку.
const WIDE: Record<number, string> = {
  4: 'grid-cols-2 md:grid-cols-4',
  5: 'grid-cols-2 md:grid-cols-5',
  6: 'grid-cols-2 md:grid-cols-6',
};
const NARROW: Record<number, string> = {
  1: 'grid-cols-1',
  2: 'grid-cols-2',
  3: 'grid-cols-3',
};

export function StatGrid({ stats, className }: { stats: readonly Stat[]; className?: string }) {
  const wide = stats.length > 3;
  return (
    <div
      className={cn(
        'grid overflow-hidden rounded-md border border-info-hairline',
        wide ? WIDE[Math.min(stats.length, 6)] : NARROW[Math.max(stats.length, 1)],
        className,
      )}
    >
      {stats.map((stat) => (
        <div
          key={stat.label}
          className={cn(
            'border-info-hairline bg-surface-card px-4 py-4',
            wide
              ? 'max-md:odd:border-r max-md:odd:last:col-span-2 max-md:odd:last:border-r-0 max-md:[&:nth-child(n+3)]:border-t md:border-l md:first:border-l-0'
              : 'border-l first:border-l-0',
          )}
        >
          <StatTile {...stat} />
        </div>
      ))}
    </div>
  );
}
