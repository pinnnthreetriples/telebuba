// Снимки РЕАЛЬНЫХ экранов, а не примитивов по одному.
//
// Каталог проверяет, что кнопка выглядит как кнопка. Он не проверяет, что происходит с
// таблицей на десять колонок, с длинным именем канала в узкой ячейке или с шапкой страницы
// над плотным списком — а именно там видна перевёрстка от смены кегля и ритма, и именно
// там её никто не смотрел. Пять экранов, которые в приложении есть.
//
// Бэкенд не поднимается: `/api/**` перехватывается фикстурами (`fixtures.ts`, формы — из
// `openapi.json`). Живой стенд на :8080 не задействован.
//
// Незамоканный запрос — это ОШИБКА теста, а не тихий пропуск: страница на нём нарисует
// пустое состояние, снимок получится правдоподобным и неверным. Список таких адресов
// печатается, чтобы фикстуру можно было дописать, а не угадывать.
import { expect, test, type Page, type Route } from '@playwright/test';

import * as fx from './fixtures';

const SCREENS = [
  { id: 'accounts', path: '/', ready: 'Аккаунты' },
  { id: 'warming', path: '/warming', ready: 'Прогрев аккаунтов' },
  { id: 'neurocomment', path: '/neurocomment', ready: 'Нейрокомментинг' },
  { id: 'neuroshilling', path: '/neuroshilling', ready: 'НейроШиллинг' },
  { id: 'settings', path: '/settings', ready: 'Настройки' },
] as const;

// Адрес → тело ответа. Порядок важен: первое совпадение выигрывает, поэтому конкретные
// пути стоят выше общих.
const ROUTES: [RegExp, unknown][] = [
  [/\/auth\/me$/, fx.me],
  [/\/health$/, fx.health],
  [/\/accounts\/stats$/, fx.accountStats],
  [/\/accounts(\?|$)/, fx.accounts],
  [/\/proxies(\?|$)/, fx.proxies],
  [/\/warming\/board/, fx.warmingBoard],
  [/\/warming\/settings/, fx.warmingSettings],
  [/\/warming\/(channels|dialogues|warmed)/, {}],
  [/\/neurocomment\/campaigns\/[^/]+\/board/, fx.neurocommentBoard],
  [/\/neurocomment\/campaigns\/[^/]+\/(challenges|comments|discovery)/, fx.challenges],
  [/\/neurocomment\/campaigns$/, fx.neurocommentCampaigns],
  [/\/neurocomment\/runtime/, fx.neurocommentRuntime],
  [/\/neurocomment\/settings/, fx.neurocommentSettings],
  [/\/neuroshilling\/campaigns\/[^/]+\/board/, fx.neuroshillingBoard],
  [/\/neuroshilling\/campaigns\/[^/]+\/scenario/, fx.neuroshillingScenario],
  [/\/neuroshilling\/campaigns$/, fx.neuroshillingCampaigns],
  [/\/logs/, fx.logs],
  [/\/events/, { events: [] }],
];

// Поля, которые обрезают своё значение и признаны долгом, а не регрессией ветки.
//
// Сейчас реестр ПУСТ, и это результат, а не упущение: в нём стояли «Персона роли 1» и
// «Персона роли 2» с пометкой, что починка — отдельная работа, потому что у поля были
// правильные `min-w-0 flex-1`, а ширина содержимого приходила нулевой. Редизайн
// нейрошиллинга снял причину, а не симптом: персона больше не делит строку с именем роли
// и её аккаунтом, а стоит отдельной строкой во всю ширину карточки, где ужиматься ей не
// обо что.
//
// Механизм оставлен: следующему такому долгу нужна строка здесь, а не молчание гейта.
const KNOWN_CLIPPED: Record<string, string[]> = {};

async function stub(page: Page, unmatched: string[]) {
  // `**/api/v1/**`, а НЕ `**/api/**`: второй перехватывал ещё и путь модуля
  // `/src/shared/api/@tanstack/react-query.gen.ts` — в нём тоже есть `/api/`, — отдавал
  // ему JSON вместо JavaScript, и приложение падало на разборе модуля с пустой страницей.
  await page.route('**/api/v1/**', (route: Route) => {
    const url = route.request().url();
    const hit = ROUTES.find(([pattern]) => pattern.test(url));
    if (hit === undefined) {
      unmatched.push(new URL(url).pathname + new URL(url).search);
      // Пустой объект, а не 404: 404 уводит страницу в панель ошибки, и снимок стал бы
      // снимком ошибки. Пустой объект оставляет страницу нарисованной, а тест — упавшим
      // на списке ниже, то есть жалоба приходит один раз и по делу.
      return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(hit[1]),
    });
  });
}

test.describe('экраны приложения', () => {
  for (const screen of SCREENS) {
    test(`экран «${screen.id}» выглядит как эталон`, async ({ page }) => {
      const unmatched: string[] = [];
      await stub(page, unmatched);

      await page.goto(screen.path);
      // Заголовок страницы — признак того, что защита маршрута пропустила и страница
      // отрисовалась, а не осталась на логине.
      //
      // Срок ожидания — общий, из `expect.timeout` в `playwright.config.ts`, и там же
      // записано, почему он не пять секунд по умолчанию.
      await expect(page.getByRole('heading', { name: screen.ready })).toBeVisible();
      await page.evaluate(async () => {
        await document.fonts.ready;
      });
      // Данные приезжают запросом, поэтому «загружаюсь» надо переждать: снимок скелетона
      // стабилен и бесполезен.
      await expect(page.getByText(/Загружаю|Загрузка/).first()).toBeHidden();

      await expect(page).toHaveScreenshot(`page-${screen.id}.png`, { fullPage: true });

      // ПОСЛЕ снимка, и это важно: до него проверка выполнялась раньше, чем React дорисует
      // пришедшие данные, находила пустую страницу и зеленела. `toHaveScreenshot` сам
      // дожидается стабилизации, поэтому за ним страница уже настоящая.
      //
      // Непереведённый ключ на экране — это сломанный снимок, и ловить его надо машиной:
      // два таких (`accounts.status.active`, `warming.warmStatus.warming`) приехали
      // из фикстуры, у которой значения перечисления были придуманы, а не взяты из
      // контракта, — и заметны они были только глазами на картинке. i18next печатает
      // сам ключ, когда перевода нет, поэтому признак — точечная строка из латиницы в
      // русском интерфейсе.
      // Разбор по СЛОВАМ, а не одной регуляркой по всему тексту. Регулярка с `matchAll`
      // над `innerText` возвращала пустой список при том, что искомая строка в тексте
      // была, — то есть проверка зеленела, глядя на непустую страницу. Почему именно так
      // повело себя `matchAll` в контексте страницы, я не выяснил, и это ровно та причина
      // не оставлять его здесь: шаг, поведение которого непонятно, в гейте бесполезен.
      // Разбор по словам проверяем целиком: слово либо похоже на ключ, либо нет.
      const raw = await page.evaluate(() => {
        const words = document.body.innerText.split(/\s+/);
        return words.filter((word) => /^[a-z][a-zA-Z]*(?:\.[a-zA-Z_]+){2,}$/.test(word));
      });
      expect(raw, `непереведённые ключи на «${screen.id}»`).toEqual([]);

      // `undefined` и `NaN` на экране — тот же класс дефекта: фикстура не отдала поле,
      // компонент подставил его как есть, и снимок закрепил бы это как норму. На экране
      // настроек так было в четырёх полях сразу.
      const holes = await page.evaluate(() => {
        const text = document.body.innerText;
        const inputs = [...document.querySelectorAll('input, textarea')].map(
          (node) => (node as HTMLInputElement).value,
        );
        return [text, ...inputs].filter((value) => /(undefined|NaN|null)/.test(value)).length;
      });
      expect(holes, `«undefined»/«NaN» на экране «${screen.id}»`).toBe(0);

      // Обрезанное значение в поле: `scrollWidth` больше `clientWidth` значит, что текст
      // не влезает и часть его не прочитать. Замер, а не глаз: «120» в поле паузы
      // обрезалось на 4px, и на уменьшенном снимке это выглядело как артефакт сжатия.
      //
      // Именно это и нашли снимки реальных экранов: ступень `width.number` (64px) была
      // посчитана под кегль 12.5px и отбивку 10px, оба выросли, и трёхзначное значение
      // перестало влезать. В каталоге числового поля с трёхзначным значением нет.
      //
      // KNOWN_CLIPPED — то, что обрезалось И ДО этой ветки (проверено возвратом старых
      // значений токенов). Список, а не молчание: без него гейт нельзя было бы включить,
      // а с молчанием он не заметил бы, что список вырос.
      const clipped = await page.evaluate(() =>
        [...document.querySelectorAll('input')]
          .filter((node) => node.scrollWidth > node.clientWidth + 1)
          .map((node) => (node.getAttribute('aria-label') ?? node.value).slice(0, 40)),
      );
      expect(clipped.sort(), `обрезанные поля на «${screen.id}»`).toEqual(
        (KNOWN_CLIPPED[screen.id] ?? []).slice().sort(),
      );

      expect(unmatched, `незамоканные адреса на «${screen.id}»`).toEqual([]);
    });
  }
});
