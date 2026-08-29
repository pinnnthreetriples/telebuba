// Отдельная точка входа Vite, а не маршрут приложения. Причина одна и она про бандл:
// каталог существует, чтобы показывать примитивы, и в продуктовом бандле ему делать
// нечего. Как страница в `src/pages/` он бы попал в него весь — со всеми состояниями,
// демо-данными и таблицей, — и добавил бы маршрут, которого у оператора нет. Здесь он
// живёт вне `src`, поэтому steiger и FSD его не касаются, а `npm run build` собирает
// приложение без него.
//
// Импортирует РЕАЛЬНЫЕ компоненты из `src/shared/ui` — это и есть весь смысл: страница,
// перерисованная руками в HTML, расходится с кодом на второй правке и врёт молча.
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import '../src/app/styles/index.css';

import { Catalog } from './Catalog';

const host = document.getElementById('catalog');
if (host === null) throw new Error('catalog: нет узла #catalog');

createRoot(host).render(
  <StrictMode>
    <Catalog />
  </StrictMode>,
);
