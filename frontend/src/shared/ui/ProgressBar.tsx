import { cn } from '@/shared/lib/cn';

// Полоса, которую заполняет значение: прогресс прогона, занятость прокси, доверие
// аккаунта, лимит действий, дни прогрева. Стояла десятком копий — рецептом
// `BAR_TRACK`/`BAR_FILL`, который каждое место дособирало само (заливку, переход, роль),
// и ещё тремя своими: на `h-rail` с серой дорожкой, на `rounded-[3px]` и на 42 столбиках
// дней прогрева.
//
// Роль `progressbar` полоса получает, только когда ей дали подпись: прогресс прогона —
// это прогресс, а занятость прокси и доверие — величины, у которых своя подпись рядом, и
// объявлять их «ходом выполнения» значило бы врать читалке экрана.
//
// `segments` — та же полоса, разбитая на равные столбики (дни прогрева): пройденные
// зелёные, текущий синий, предстоящие серые. Щель между столбиками — прозрачная граница,
// до которой заливка не доходит (`bg-clip-padding`), а не зазор в 1px: у ритма нет ступени
// в 1px.
export type ProgressTone = 'primary' | 'success' | 'warning' | 'danger' | 'current';

const FILL: Record<ProgressTone, string> = {
  primary: 'bg-action-primary',
  success: 'bg-success',
  warning: 'bg-warning',
  danger: 'bg-danger',
  // Краска текста места вызова — доверие красится той же ступенью, что и число рядом.
  current: 'bg-current',
};

function ratio(value: number, max: number): number {
  if (max <= 0) return 0;
  return Math.min(1, Math.max(0, value / max));
}

export function ProgressBar({
  value,
  max = 100,
  tone = 'primary',
  label,
  indeterminate = false,
  segments,
  className,
}: {
  value: number;
  max?: number;
  tone?: ProgressTone;
  label?: string;
  // Сколько всего — неизвестно: дорожка дышит, заливки нет.
  indeterminate?: boolean;
  segments?: number;
  className?: string;
}) {
  const share = ratio(value, max);
  const aria =
    label === undefined
      ? {}
      : {
          role: 'progressbar',
          'aria-label': label,
          'aria-valuemin': 0,
          'aria-valuemax': max,
          'aria-valuenow': indeterminate ? undefined : Math.min(Math.max(value, 0), max),
        };

  if (segments !== undefined) {
    const filled = Math.round(segments * share);
    return (
      <div className={cn('flex items-end', className)} {...aria}>
        {Array.from({ length: segments }, (_, index) => (
          <span
            key={index}
            className={cn(
              'h-bar flex-1 rounded-[1.5px] border-r border-transparent bg-clip-padding transition-[background] duration-reveal last:border-r-0',
              index < filled ? 'bg-success' : index === filled ? 'bg-action-primary' : 'bg-line',
            )}
          />
        ))}
      </div>
    );
  }

  return (
    <div
      className={cn(
        'h-meter overflow-hidden rounded-full bg-canvas',
        indeterminate && 'tb-pulse',
        className,
      )}
      {...aria}
    >
      {indeterminate ? null : (
        <div
          className={cn('h-full rounded-full transition-[width] duration-reveal', FILL[tone])}
          style={{ width: `${String(share * 100)}%` }}
        />
      )}
    </div>
  );
}
