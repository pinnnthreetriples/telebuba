import type { ReactNode } from 'react';

import { cn } from '@/shared/lib/cn';

// Пустое место списка: строка по центру, серым, с воздухом сверху и снизу. Двадцать
// списков набирали её сами — `text-center type-body text-content-subtle` и свой `py-*`;
// теперь роль текста и ступени воздуха решены здесь.
//
// `size` — сколько воздуха: `sm` в строке карточки, `md` в теле диалога, `lg` в списке
// (по умолчанию), `xl` — когда пусто всё, что на экране. `boxed` — пунктирная рамка
// там, где пустое место стоит среди других полей и иначе не читается как место.
// `tone="danger"` — та же строка, когда список не загрузился.
const SIZE = {
  sm: 'py-4',
  md: 'py-6',
  lg: 'py-8',
  xl: 'py-16',
} as const;

export function EmptyState({
  size = 'lg',
  tone = 'muted',
  boxed = false,
  className,
  children,
  ...rest
}: {
  size?: keyof typeof SIZE;
  tone?: 'muted' | 'danger';
  boxed?: boolean;
  // Только раскладка снаружи: отступ от соседа, поля по горизонтали, колонка сетки.
  className?: string;
  children: ReactNode;
} & Omit<React.HTMLAttributes<HTMLDivElement>, 'className' | 'children'>) {
  return (
    <div
      className={cn(
        'text-center type-body',
        tone === 'danger' ? 'text-danger' : 'text-content-subtle',
        boxed && 'rounded-md border border-dashed border-line bg-surface-card px-4',
        SIZE[size],
        className,
      )}
      {...rest}
    >
      {children}
    </div>
  );
}
