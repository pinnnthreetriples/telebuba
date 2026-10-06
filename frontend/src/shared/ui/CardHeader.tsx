import type { ReactNode } from 'react';

import { FOCUS_RING, PRESS_FEEDBACK } from '@/shared/design-system';
import { cn } from '@/shared/lib/cn';

// Шапка карточки: плитка с иконкой или точка состояния, заголовок `type-h3`, плашка рядом
// с ним, подзаголовок под ним и то, что стоит справа (действия, статус, переключатель,
// шеврон). Почти двадцать карточек набирали её сами, и копии разошлись: заголовок был то
// `type-h3`, то `type-body-medium`, плитка — 28px или 34px, залитая или тонированная,
// счётчик — справа у края или рядом с заголовком, подзаголовок — строкой ниже или через
// пробел. Образец — нейрокомментинг.
//
// `CollapsibleCard` рисует свою шапку этим же компонентом: заголовок там — кнопка
// раскрытия (`toggle`), а шеврон стоит справа вместе с остальным.
export type CardHeaderTone = 'info' | 'success' | 'warning';

// Точка перед заголовком: `live` — лента свежая, `idle` — тишина, `active` — журнал пишет.
export type CardHeaderDot = 'live' | 'idle' | 'active';

const TILE: Record<CardHeaderTone, string> = {
  info: 'bg-info-tint text-info-strong',
  success: 'bg-success-tint text-success-deep',
  warning: 'bg-warning-tint text-warning-deep',
};

const DOT: Record<CardHeaderDot, string> = {
  live: 'tb-livedot bg-success',
  idle: 'bg-content-subtle',
  active: 'pl-pulse bg-action-primary',
};

export interface CardHeadingProps {
  title: ReactNode;
  icon?: ReactNode;
  tone?: CardHeaderTone;
  dot?: CardHeaderDot;
  // Плашка рядом с заголовком: счётчик, статус, «?» подсказки.
  badge?: ReactNode;
  subtitle?: ReactNode;
}

// Только `span`: внутри `CollapsibleCard` всё это лежит в кнопке.
function Heading({ title, icon, tone = 'info', dot, badge, subtitle }: CardHeadingProps) {
  return (
    <>
      {dot === undefined ? null : (
        <span className={cn('size-dot shrink-0 rounded-full', DOT[dot])} />
      )}
      {icon === undefined ? null : (
        <span
          className={cn(
            'flex size-icon shrink-0 items-center justify-center rounded-md',
            TILE[tone],
          )}
        >
          {icon}
        </span>
      )}
      <span className="min-w-0">
        <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="min-w-0 break-words type-h3">{title}</span>
          {badge}
        </span>
        {subtitle === undefined ? null : <span className="mt-1 block type-small">{subtitle}</span>}
      </span>
    </>
  );
}

export function CardHeader({
  toggle,
  wrap = false,
  className,
  children,
  ...heading
}: CardHeadingProps & {
  // Заголовок — кнопка раскрытия карточки.
  toggle?: { expanded: boolean; controls: string; onToggle: () => void };
  // Когда строке тесно, правая часть уходит на следующую строку целиком, а не сжимает
  // заголовок в столбик (шапка конвейера на телефоне). Шеврону складной карточки
  // переноситься некуда, поэтому по умолчанию строка не переносится.
  wrap?: boolean;
  className?: string;
  // Правая часть строки.
  children?: ReactNode;
}) {
  return (
    <div className={cn('flex items-center gap-3', wrap && 'flex-wrap', className)}>
      {toggle === undefined ? (
        <div className={cn('flex items-center gap-3', wrap ? 'flex-auto' : 'min-w-0 flex-1')}>
          <Heading {...heading} />
        </div>
      ) : (
        <button
          type="button"
          onClick={toggle.onToggle}
          aria-expanded={toggle.expanded}
          aria-controls={toggle.controls}
          className={cn(
            'flex min-w-0 flex-1 items-center gap-3 text-left transition duration-state',
            FOCUS_RING,
            PRESS_FEEDBACK,
          )}
        >
          <Heading {...heading} />
        </button>
      )}
      {children}
    </div>
  );
}
