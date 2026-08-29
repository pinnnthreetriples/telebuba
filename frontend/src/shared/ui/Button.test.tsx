import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test, vi } from 'vitest';

import { expectNoAxeViolations } from './axe.test-helpers';
import { Button } from './Button';
import { elementBodies, lineAt, tsxSources, wornClasses } from './sourceScan.test-helpers';

function classesOf(name: string): string {
  return screen.getByRole('button', { name }).className;
}

test('the size sets height and padding, the variant the fill', async () => {
  const { container } = render(
    <>
      <Button>Отмена</Button>
      <Button variant="primary" size="sm">
        Запустить
      </Button>
      <Button variant="danger">Удалить</Button>
    </>,
  );

  expect(classesOf('Отмена')).toContain('px-2xl');
  expect(classesOf('Отмена')).toContain('text-body');
  expect(classesOf('Отмена')).toContain('bg-surface-card');
  expect(classesOf('Запустить')).toContain('px-xl');
  // The rung has to survive the variant's colour: both are `text-*`, and an
  // untaught tailwind-merge drops the size in favour of the colour (see cn.ts).
  expect(classesOf('Запустить')).toContain('text-body');
  expect(classesOf('Запустить')).toContain('bg-action-primary');
  expect(classesOf('Удалить')).toContain('bg-danger-tint');
  await expectNoAxeViolations(container);
});

// `block` is the only rung that sets its own width, and the only one that is not
// inline: a `w-full` inline-level button still sits on a line and collects that
// line's leading underneath it, which is the gap six of its wearers used to carry.
test('the block rung spans its form and is not inline', () => {
  render(<Button size="block">Подтвердить</Button>);

  const classes = classesOf('Подтвердить').split(' ');
  expect(classes).toContain('w-full');
  expect(classes).toContain('flex');
  expect(classes).not.toContain('inline-flex');
});

// Форма — не ступень размера, и это утверждение о ВСЕХ ступенях, поэтому они перебираются,
// а не выбираются. Ступень решала форму: `md`/`sm` — пилюля, `xs` — `rounded-md`, `block` —
// `rounded-lg`, — то есть «сделать кнопку меньше» означало «сделать её другой формы».
// Утверждать это одной ступенью нельзя: следующая пришла бы со своим радиусом ровно так же.
test('радиус у всех ступеней один, и ступень его не выбирает', () => {
  render(
    <>
      <Button size="md">Первая</Button>
      <Button size="sm">Вторая</Button>
      <Button size="xs">Третья</Button>
      <Button size="block">Четвёртая</Button>
    </>,
  );

  for (const name of ['Первая', 'Вторая', 'Третья', 'Четвёртая']) {
    const classes = classesOf(name).split(' ');
    expect(classes).toContain('rounded-full');
    // Прежние формы названы поимённо: неверная форма обычно приходит не «какой-то другой»,
    // а одной из тех, что тут стояли.
    expect(classes).not.toContain('rounded-lg');
    expect(classes).not.toContain('rounded-md');
    expect(classes).not.toContain('rounded-sm');
  }
});

// `dashed` is a fill, so it has to compose with the rung rather than replace it —
// the three add-one-more buttons in the app are all `block`, but `block` is worn by
// three different fills and the two must not fuse into one name.
test('dashed is a fill that keeps whatever rung it is given', () => {
  render(
    <>
      <Button variant="dashed" size="block">
        Добавить кампанию
      </Button>
      <Button variant="dashed" size="sm">
        Добавить
      </Button>
    </>,
  );

  for (const name of ['Добавить кампанию', 'Добавить']) {
    expect(classesOf(name)).toContain('border-dashed');
    expect(classesOf(name)).toContain('text-info-strong');
  }
  expect(classesOf('Добавить кампанию')).toContain('w-full');
  expect(classesOf('Добавить')).toContain('px-xl');
  expect(classesOf('Добавить')).not.toContain('w-full');
});

// Every rung carries the same state vocabulary — before the component the app
// spelled `disabled` four ways and `focus-visible` three times in total.
test('every button carries the same disabled and focus treatment', () => {
  render(
    <>
      <Button size="xs">Проверить</Button>
      <Button variant="ghost">Ещё</Button>
      <Button size="block">Готово</Button>
      <Button variant="dashed">Добавить</Button>
    </>,
  );

  for (const name of ['Проверить', 'Ещё', 'Готово', 'Добавить']) {
    expect(classesOf(name)).toContain('disabled:opacity-50');
    expect(classesOf(name)).toContain('focus-visible:outline-focus');
  }
});

test('a caller class wins over the variant it collides with', () => {
  render(
    <Button variant="primary" className="bg-success">
      Сохранено
    </Button>,
  );

  const classes = classesOf('Сохранено').split(' ');
  expect(classes).toContain('bg-success');
  expect(classes).not.toContain('bg-action-primary');
});

// `loading` and `disabled` both stop the click, but a screen reader has to hear
// the difference: one is "wait", the other is "not available".
test('loading reports itself as busy and takes no clicks', async () => {
  const onClick = vi.fn();
  render(
    <Button loading onClick={onClick}>
      Сохраняю…
    </Button>,
  );

  const button = screen.getByRole('button', { name: 'Сохраняю…' });
  expect(button).toHaveAttribute('aria-busy', 'true');
  expect(button).toBeDisabled();
  await userEvent.click(button);
  expect(onClick).not.toHaveBeenCalled();
});

test('a disabled button is not busy', () => {
  render(<Button disabled>Нельзя</Button>);

  expect(screen.getByRole('button', { name: 'Нельзя' })).not.toHaveAttribute('aria-busy');
});

// The default matters: a bare <button> inside a <form> submits it, and half of
// these live in dialogs that wrap their fields in one.
test('the default type is button and a caller can still submit', () => {
  const onSubmit = vi.fn((event: React.FormEvent) => {
    event.preventDefault();
  });
  render(
    <form onSubmit={onSubmit}>
      <Button>Показать</Button>
      <Button type="submit">Сохранить</Button>
    </form>,
  );

  expect(screen.getByRole('button', { name: 'Показать' })).toHaveAttribute('type', 'button');
  expect(screen.getByRole('button', { name: 'Сохранить' })).toHaveAttribute('type', 'submit');
});

// The indicator a keyboard operator navigates by. It was `shadow-focus` — 1.18:1 once
// composited, beside `outline-none` that removed the browser's own — so this asserts the
// two halves that were wrong: that the ring is an outline, and that nothing suppresses it.
test('focus is an outline, and the browser ring is not thrown away', () => {
  render(<Button>Сохранить</Button>);
  const cls = screen.getByRole('button').className;
  expect(cls).toContain('focus-visible:outline-2');
  expect(cls).toContain('focus-visible:outline-focus');
  expect(cls).not.toContain('outline-none');
  expect(cls).not.toContain('shadow-focus');
});

// Ожидание — одно состояние, и его следствия перечислены здесь целиком, потому что раньше
// они были разложены по местам вызова: проп давал `disabled` и `aria-busy`, а кольцо
// рисовал вызывающий — тринадцатью рукописными сборками, из которых шесть ставили кольцо
// РЯДОМ с подписью, а семь ВМЕСТО неё. Второе хуже: доступное имя кнопки — это её
// содержимое, поэтому на время запроса кнопка теряла имя, и `getByRole` уже не нашёл бы её
// по нему. Утверждение про имя стоит первым именно за этим.
test('ожидание рисует кольцо, сохраняя подпись и имя кнопки', () => {
  render(
    <Button variant="primary" loading>
      Публикую…
    </Button>,
  );

  const button = screen.getByRole('button', { name: 'Публикую…' });
  const ring = button.querySelector('.tb-spin');
  expect(ring).not.toBeNull();
  // Кольцо — картинка состояния, о котором уже сказано `aria-busy`; вторым голосом оно
  // читало бы то же самое.
  expect(ring).toHaveAttribute('aria-hidden', 'true');
  expect(button.textContent).toBe('Публикую…');
});

// Тон кольца — следствие заливки, а не второе решение вызывающего: до этой правки три
// сайта на залитой кнопке передавали `onAction` вручную, а один не передавал ничего и
// рисовал синюю дугу на синем.
test('тон кольца следует за заливкой кнопки', () => {
  render(
    <>
      <Button variant="primary" loading>
        Синяя
      </Button>
      <Button variant="danger" loading>
        Красная
      </Button>
      <Button loading>Обычная</Button>
    </>,
  );

  const ringOf = (name: string) =>
    screen.getByRole('button', { name }).querySelector('.tb-spin')?.className ?? '';

  expect(ringOf('Синяя')).toContain('border-t-on-action');
  expect(ringOf('Красная')).toContain('border-t-danger');
  expect(ringOf('Обычная')).toContain('border-t-action-primary');
});

// Гейт на возврат: кольцо внутри кнопки собирает КНОПКА.
//
// Правило ESLint этого не видит — оно читает списки классов, а не вложенность элементов, —
// и правило про рукописное кольцо (`tb-spin` + `border-t-*`) тоже нет: `<Spinner>` внутри
// `<Button>` собран из правильных ролей, он просто собран не там. Дефект от этого не
// меньше: пока кольцо ставит вызывающий, тон выбирает он же, зазор набирает он же, и
// «кольцо вместо подписи» остаётся возможным.
// Гейт на пятое правило формы: обычная кнопка не собирается руками вне дизайн-системы.
//
// Признак выбран узкий и проверяемый — ПИЛЮЛЯ с горизонтальными полями. Пилюля с полями
// это и есть коробка кнопки: круглые торцы и текст, отступивший от дуги. Ничем другим она
// в приложении не бывает — круглые глифы (`size-chip rounded-full`) полей не носят, а
// плитки, зоны сброса и триггеры списков носят `rounded-lg`.
//
// Что ловилось: 14 рукописных пилюль, и каждая заново решала высоту, поля, обводку фокуса,
// прозрачность disabled и переход — те пять вещей, которые `buttonBase` и собрал. Две из
// них были приглушёнными пунктирными добавителями, и их комментарии объясняли, почему они
// НЕ `Button`; объяснение верное про заливку и неверное про коробку, поэтому заливка стала
// `variant="dashedMuted"`, а коробка — общей.
//
// Проверяется `src` без `shared/ui`: внутри библиотеки пилюлю рисует ровно один файл, и это
// `recipes/controls.ts`, за которым стоит `Button`.
const PILL = /(?:^|\s)rounded-full(?![\w-])/;
const PAD_X = /(?:^|\s)px-[\w[]/;

// Проверка самой проверки: её обходили выносом строки в переменную, и это тот случай, где
// «гейт зелёный» и «гейт не смотрит» выглядят одинаково. Фикстура, а не файл приложения —
// утверждение о механизме, и оно должно ломаться, даже когда в приложении всё чисто.
test('гейт видит класс, вынесенный в константу', () => {
  const source = [
    "const PILL = 'rounded-full border px-md';",
    '<button type="button" className={PILL}>x</button>',
  ].join('\n');
  const worn = wornClasses(source, source.indexOf('<button'));

  expect(worn).toContain('rounded-full');
  expect(worn).toContain('px-md');
});

test('обычная кнопка не собирается руками вне дизайн-системы', () => {
  const offenders: string[] = [];
  let handWritten = 0;

  for (const { path, source } of tsxSources()) {
    if (path.startsWith('src/shared/ui/')) continue;
    for (const hit of source.matchAll(/<button\b/g)) {
      handWritten += 1;
      const own = wornClasses(source, hit.index);
      if (PILL.test(own) && PAD_X.test(own)) {
        offenders.push(`${path}:${String(lineAt(source, hit.index))}`);
      }
    }
  }

  expect(offenders).toEqual([]);
  // Рукописные `<button>` в приложении есть и останутся — это вкладки, радиокнопки,
  // строки-мишени, зоны сброса и ссылки-кнопки. Утверждение не про их число, а про то, что
  // ни одна из них не пилюля; если обход перестанет их находить, оно выполнится на пустом
  // списке.
  expect(handWritten).toBeGreaterThan(50);
});

test('ни один вызывающий не собирает кольцо внутри кнопки сам', () => {
  const offenders: string[] = [];
  let buttons = 0;

  for (const { path, source } of tsxSources()) {
    for (const { body } of elementBodies(source, 'Button')) {
      buttons += 1;
      if (body.includes('<Spinner')) offenders.push(path);
    }
  }

  expect(offenders).toEqual([]);
  // Если обход перестанет находить кнопки, список нарушителей тоже окажется пустым.
  expect(buttons).toBeGreaterThan(50);
});
