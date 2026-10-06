// Поверхности, на которых блоки стоят в продукте. Шапка и подвал диалога живут внутри
// диалога, строка настройки — внутри карточки, и показывать их на голом фоне значило бы
// показывать не то.
import type { ReactNode } from 'react';

import { surface } from '@/shared/design-system';

export function InDialog({ children }: { children: ReactNode }) {
  return <div className={surface('dialog', 'overflow-hidden')}>{children}</div>;
}

export function InCard({ children }: { children: ReactNode }) {
  return <div className={surface('card', 'px-6 py-6')}>{children}</div>;
}
