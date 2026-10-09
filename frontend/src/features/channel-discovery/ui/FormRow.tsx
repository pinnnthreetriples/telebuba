import type { ReactNode } from 'react';

import { cn } from '@/shared/lib/cn';
import { HelpHint } from '@/shared/ui';

// Второй носитель `Row`/`Eyebrow` — первый живёт в pages/neuroshilling/ui/CampaignSetupSection.
// Скопировано, а не импортировано: фича не тянет страницу. Третий носитель поднимает обе
// в shared/ui. / Second wearer; a third promotes both to shared/ui.

// Строка настройки: подпись слева, контрол справа, волосяной разделитель сверху.
export function Row({
  label,
  hint,
  help,
  first = false,
  htmlFor,
  children,
}: {
  label: string;
  hint?: string;
  // Значок «?» рядом с подписью: одна фраза и пример, видны при наведении.
  help?: { text: string; example?: string };
  // Первая строка блока не рисует разделитель: он отделял бы её от заголовка.
  first?: boolean;
  // Текстовые поля получают настоящий <label>; группам радио хватает своего aria-label.
  htmlFor?: string;
  children: ReactNode;
}) {
  return (
    <div
      className={cn(
        'flex min-h-touch flex-wrap items-center gap-md py-sm',
        !first && 'border-t border-line-row',
      )}
    >
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-xs">
          {htmlFor === undefined ? (
            <span className="type-label">{label}</span>
          ) : (
            <label htmlFor={htmlFor} className="type-label">
              {label}
            </label>
          )}
          {/* Вне <label>: внутри него текст подсказки вошёл бы в имя поля. */}
          {help === undefined ? null : <HelpHint text={help.text} example={help.example} />}
        </div>
        {hint === undefined ? null : <div className="mt-hair type-caption">{hint}</div>}
      </div>
      {children}
    </div>
  );
}

export function Eyebrow({ title, caption }: { title: string; caption?: string }) {
  return (
    <div className="flex flex-wrap items-baseline gap-sm pb-sm">
      <span className="type-eyebrow">{title}</span>
      {caption === undefined ? null : <span className="type-caption">{caption}</span>}
    </div>
  );
}
