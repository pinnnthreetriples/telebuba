// Основной каталог, который показывает КОМПОНЕНТЫ и проходит визуальный гейт.
//
// Второй документ — `docs/design-system.html` — показывает ТОКЕНЫ и порождается из
// конфига скриптом; разделение намеренное и это ответ на вопрос «почему их два». Числа
// печатает генератор, потому что число, набранное руками, расходится с конфигом на
// первой правке и `ds:doc:check` это ловит. Компоненты показывает эта страница, потому
// что компонент, перерисованный в HTML, расходится с кодом так же — а поймать это может
// только рендер настоящего компонента.
import type { ReactNode } from 'react';

import { Controls } from './Controls';
import { Feedback } from './Feedback';
import { Surfaces } from './Surfaces';
import { Typography } from './Typography';

const NAV = [
  ['controls', 'Контролы'],
  ['feedback', 'Обратная связь'],
  ['surfaces', 'Поверхности'],
] as const;

export function Catalog({
  patterns,
  buttonGuide,
}: {
  patterns?: ReactNode;
  buttonGuide?: ReactNode;
}) {
  return (
    <div className="min-h-screen bg-canvas">
      <header className="sticky top-0 z-sticky border-b border-line bg-surface-card/85 backdrop-blur">
        <div className="mx-auto flex h-header max-w-shell items-center gap-lg px-lg">
          <h1 className="type-h3">Дизайн-система Telebuba</h1>
          <nav className="flex flex-wrap gap-md">
            {NAV.map(([id, label]) => (
              <a key={id} href={`#${id}`} className="type-small hover:text-info-strong">
                {label}
              </a>
            ))}
            {patterns && (
              <a href="#patterns" className="hidden type-small hover:text-info-strong md:inline">
                Блоки продукта
              </a>
            )}
            <a href="#typography" className="type-small hover:text-info-strong">
              Типографика
            </a>
          </nav>
        </div>
      </header>
      <main className="mx-auto flex max-w-shell flex-col gap-page px-lg py-page">
        <Controls intro={buttonGuide} />
        <Feedback />
        <Surfaces />
        {patterns}
        <Typography />
      </main>
    </div>
  );
}
