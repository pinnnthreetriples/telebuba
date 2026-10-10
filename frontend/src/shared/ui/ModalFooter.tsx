import type { ReactNode } from 'react';

import { cn } from '@/shared/lib/cn';

// Подвал диалога: рамка сверху и кнопки справа. Строка его классов стояла в десяти
// диалогах слово в слово; то, что слева от кнопок (итог, счётчик), ставит себе `mr-auto`.
export function ModalFooter({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <div
      className={cn(
        'flex flex-wrap items-center justify-end gap-2 border-t border-canvas px-6 py-4',
        className,
      )}
    >
      {children}
    </div>
  );
}
