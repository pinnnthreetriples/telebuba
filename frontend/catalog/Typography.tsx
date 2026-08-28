// Роли типографики — то, чем текст является читателю, а не то, какого он размера.
//
// Ступени размера сюда не выведены намеренно: их печатает `docs/design-system.html`
// из конфига, и второй список тех же чисел — это ровно та расходящаяся пара, от которой
// вся эта работа. Здесь показано то, чего таблица чисел показать не может: как роль
// выглядит на настоящем экране и что происходит, когда её перекрашивают.
import { Row, Section } from './Frame';

const ROLES = [
  ['type-page-title', 'Прогрев'],
  ['type-dialog-title', 'Удалить аккаунт?'],
  ['type-dialog-body', 'Аккаунт и его сессия будут удалены безвозвратно.'],
  ['type-card-title', 'Ограничения аккаунта'],
  ['type-item-title', 'Иван Петров'],
  ['type-eyebrow', 'Прокси и сеть'],
  ['type-label', 'Действий в сутки'],
  ['type-value', '+7 900 111-22-33'],
  ['type-prose', 'Аккаунт выйдет из прогрева через пять дней и встанет в рабочую очередь.'],
  ['type-caption', 'Обновлено две минуты назад · 12.08.2026, 19:04'],
  ['type-stat', '1 248'],
] as const;

export function Typography() {
  return (
    <Section
      id="typography"
      title="Типографика"
      note="Одиннадцать ролей. Роль несёт размер, вес и краску сразу — страница называет роль, а не пересказывает три решения. Роль плюс перекраска (`type-caption text-danger`) — предусмотренный способ сказать то же другим цветом."
    >
      {ROLES.map(([role, sample]) => (
        <Row key={role} label={role}>
          <span className={role}>{sample}</span>
        </Row>
      ))}

      <Row label="роль + перекраска" hint="утилита поверх роли выигрывает">
        <span className="type-caption text-danger">Прокси не отвечает</span>
        <span className="type-caption text-success-deep">Прокси отвечает</span>
        <span className="type-caption font-bold">Жирная подпись</span>
      </Row>
    </Section>
  );
}
