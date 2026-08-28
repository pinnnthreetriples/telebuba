// Каркас каталога: то, во что обёрнуты образцы, и договор со скриншотами.
//
// `data-probe` на образце — это адрес для Playwright. Состояния `hover`, `active` и
// `focus` нельзя нарисовать пропом: они принадлежат браузеру, и страница, которая
// «показывает hover» своими классами, показывает догадку о нём. Поэтому здесь образец
// только помечается, а наводит курсор и жмёт кнопку сам тест — снимок получается с
// настоящего состояния, а не с его копии.
import type { ReactNode } from 'react';

export function Section({
  id,
  title,
  note,
  children,
}: {
  id: string;
  title: string;
  note?: string;
  children: ReactNode;
}) {
  return (
    <section id={id} className="scroll-mt-page border-t border-line pt-xl">
      <h2 className="type-page-title">{title}</h2>
      {note !== undefined && <p className="mt-tight max-w-page type-prose">{note}</p>}
      <div className="mt-lg flex flex-col gap-lg">{children}</div>
    </section>
  );
}

// Одна строка каталога: слева имя того, что показано, справа сами образцы. Имя — роль
// `label`, а не заголовок: это подпись к контролу, ровно та роль, что у подписи поля.
export function Row({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-sm border-b border-line-row pb-lg sm:flex-row sm:gap-lg">
      <div className="w-col shrink-0">
        <div className="type-label">{label}</div>
        {hint !== undefined && <div className="mt-hair type-caption">{hint}</div>}
      </div>
      <div className="flex flex-wrap items-center gap-md">{children}</div>
    </div>
  );
}

// Образец с подписью под ним. `probe` называет состояние, которое должен вызвать тест;
// без него образец статический и снимается как есть.
export function Cell({
  caption,
  probe,
  children,
}: {
  caption: string;
  probe?: 'hover' | 'focus' | 'active';
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col items-start gap-hair">
      <div data-probe={probe} data-cell={caption}>
        {children}
      </div>
      <span className="type-caption">{caption}</span>
    </div>
  );
}

// Тёмная подложка для того, что рисуется на `term`: терминальные чернила на белой
// карточке каталога не читаются, и показывать их так — значит показывать не то.
export function Dark({ children }: { children: ReactNode }) {
  return <div className="rounded-lg bg-term p-md">{children}</div>;
}
