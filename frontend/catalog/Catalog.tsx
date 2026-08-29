// Единственный каталог, который показывает КОМПОНЕНТЫ.
//
// Второй документ — `docs/design-system.html` — показывает ТОКЕНЫ и порождается из
// конфига скриптом; разделение намеренное и это ответ на вопрос «почему их два». Числа
// печатает генератор, потому что число, набранное руками, расходится с конфигом на
// первой правке и `ds:doc:check` это ловит. Компоненты показывает эта страница, потому
// что компонент, перерисованный в HTML, расходится с кодом так же — а поймать это может
// только рендер настоящего компонента.
import { Controls } from './Controls';
import { Feedback } from './Feedback';
import { Surfaces } from './Surfaces';
import { Typography } from './Typography';

const NAV = [
  ['controls', 'Контролы'],
  ['feedback', 'Обратная связь'],
  ['surfaces', 'Поверхности'],
  ['typography', 'Типографика'],
] as const;

export function Catalog() {
  return (
    <div className="min-h-screen bg-canvas">
      <header className="sticky top-0 z-sticky border-b border-line bg-white/85 backdrop-blur">
        <div className="mx-auto flex h-header max-w-shell items-center gap-lg px-lg">
          <span className="type-card-title">Дизайн-система Telebuba</span>
          <nav className="flex flex-wrap gap-md">
            {NAV.map(([id, label]) => (
              <a key={id} href={`#${id}`} className="type-caption hover:text-info-strong">
                {label}
              </a>
            ))}
          </nav>
        </div>
      </header>
      <main className="mx-auto flex max-w-shell flex-col gap-page px-lg py-page">
        <Controls />
        <Feedback />
        <Surfaces />
        <Typography />
      </main>
    </div>
  );
}
