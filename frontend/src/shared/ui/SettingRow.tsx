import type { ReactNode } from 'react';

import { cn } from '@/shared/lib/cn';

// Строка настройки: подпись (и подсказка под ней) слева, контрол справа, волосяной
// разделитель сверху. Жила двумя копиями — в настройках кампании нейрошиллинга и в поиске
// каналов, — и копии успели разойтись подписью: одна набирала её `text-body`, другая
// `type-body-medium` вторым серым. Осталась первая: её носит экран, а не диалог.
export function SettingRow({
  label,
  hint,
  first = false,
  htmlFor,
  children,
}: {
  label: string;
  hint?: string;
  // Первая строка блока не рисует разделитель: он отделял бы её от заголовка.
  first?: boolean;
  // Текстовые поля получают настоящий <label>; группам радио хватает своего aria-label.
  htmlFor?: string;
  children: ReactNode;
}) {
  return (
    <div
      className={cn(
        'flex min-h-touch flex-wrap items-center gap-3 py-2',
        !first && 'border-t border-canvas',
      )}
    >
      <div className="min-w-0 flex-1">
        {htmlFor === undefined ? (
          <div className="text-body">{label}</div>
        ) : (
          <label htmlFor={htmlFor} className="block text-body">
            {label}
          </label>
        )}
        {hint === undefined ? null : <div className="mt-1 type-small">{hint}</div>}
      </div>
      {children}
    </div>
  );
}

// Подпись над группой строк: название группы и, через пробел, короткое уточнение.
export function SectionLabel({ title, caption }: { title: string; caption?: string }) {
  return (
    <div className="flex flex-wrap items-baseline gap-2 pb-2">
      <span className="type-small-medium">{title}</span>
      {caption === undefined ? null : <span className="type-small">{caption}</span>}
    </div>
  );
}
