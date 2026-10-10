import type { ReactNode } from 'react';

import { HEADING_ICON_TILE } from '@/shared/design-system';
import { cn } from '@/shared/lib/cn';

// Шапка диалога: заголовок `type-h2`, подзаголовок под ним, необязательная плитка с иконкой
// слева и то, что стоит справа (плашка, счётчик, кнопка закрытия), — `children`. Одиннадцать
// диалогов набирали её сами одной и той же строкой классов; теперь поля, рамка и роли
// текста решены здесь.
//
// Подзаголовок у шапки с плиткой — `type-body` (плитка выше строки `type-small`, и мелкая
// подпись рядом с ней проваливалась), без плитки — `type-small`.
export function ModalHeader({
  title,
  subtitle,
  icon,
  divided = true,
  className,
  children,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  icon?: ReactNode;
  // Рамка снизу отделяет шапку от тела. Её нет, когда сразу под шапкой стоят вкладки,
  // которые рисуют свою.
  divided?: boolean;
  className?: string;
  children?: ReactNode;
}) {
  return (
    <div
      className={cn(
        'flex items-center gap-3 px-6 pb-4 pt-6',
        divided && 'border-b border-canvas',
        className,
      )}
    >
      {icon === undefined ? null : <span className={HEADING_ICON_TILE}>{icon}</span>}
      <div className="min-w-0 break-words">
        <h2 className="type-h2">{title}</h2>
        {subtitle === undefined || subtitle === null ? null : (
          <div
            className={cn(
              'mt-1',
              icon === undefined ? 'type-small' : 'type-body text-content-subtle',
            )}
          >
            {subtitle}
          </div>
        )}
      </div>
      {children}
    </div>
  );
}
