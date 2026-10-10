// Каркас каталога: то, во что обёрнуты образцы, и договор со скриншотами.
//
// `data-probe` на образце — это адрес для Playwright. `hover`, `focus`, `press` и `open`
// нельзя нарисовать пропом: они принадлежат браузеру, и страница, которая «показывает
// hover» своими классами, показывает догадку о нём. Поэтому здесь образец только
// помечается, а наводит курсор, зажимает кнопку и раскрывает список сам тест.
//
// `press` и `open` — разные виды, потому что это разные жесты: `:active` живёт, только
// пока кнопка зажата, а списку нужен полный клик.
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
    <section id={id} className="scroll-mt-8 border-t border-line pt-6">
      <h2 className="type-h1">{title}</h2>
      {note !== undefined && (
        <p className="mt-2 max-w-page type-body text-content-subtle">{note}</p>
      )}
      <div className="mt-4 flex flex-col gap-4">{children}</div>
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
    <div className="flex flex-col gap-2 border-b border-canvas pb-4 sm:flex-row sm:gap-4">
      <div className="w-col shrink-0">
        <div className="type-body-medium text-content-secondary">{label}</div>
        {hint !== undefined && <div className="mt-1 type-small">{hint}</div>}
      </div>
      <div className="flex min-w-0 flex-wrap items-center gap-3">{children}</div>
    </div>
  );
}

// Образец с подписью под ним. `probe` называет состояние, которое должен вызвать тест;
// без него образец статический и снимается как есть.
export function Cell({
  caption,
  probe,
  scrollable = false,
  children,
}: {
  caption: string;
  probe?: 'hover' | 'focus' | 'press' | 'open';
  scrollable?: boolean;
  children: ReactNode;
}) {
  return (
    <div className="flex min-w-0 max-w-full flex-col items-start gap-1">
      <div
        className={scrollable ? 'max-w-full overflow-x-auto' : 'max-w-full'}
        tabIndex={scrollable ? 0 : undefined}
        data-probe={probe}
        data-cell={caption}
      >
        {children}
      </div>
      <span className="type-small">{caption}</span>
    </div>
  );
}

// Тёмная подложка для того, что рисуется на `term`: терминальные чернила на белой
// карточке каталога не читаются, и показывать их так — значит показывать не то.
export function Dark({ children }: { children: ReactNode }) {
  return <div className="rounded-md bg-term p-3">{children}</div>;
}
