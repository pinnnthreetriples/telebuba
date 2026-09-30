import type { ReactNode } from 'react';

import { cn } from '@/shared/lib/cn';

// Тёмная коробка журнала: одна поверхность и один ритм строк на всё приложение.
// `feed` — журнал, который владеет своим блоком; `inline` — тот же поток внутри карточки,
// ниже и с меньшим радиусом. Строки внутри рисует вызывающий: он знает свои колонки.
const SIZE = {
  feed: 'max-h-feed rounded-lg',
  inline: 'max-h-feedInline rounded-md',
} as const;

export function TerminalPane({
  size = 'feed',
  className,
  children,
}: {
  size?: keyof typeof SIZE;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div
      className={cn(
        'term tb-scroll overflow-y-auto bg-term px-md py-sm font-mono text-tiny leading-log',
        SIZE[size],
        className,
      )}
    >
      {children}
    </div>
  );
}
