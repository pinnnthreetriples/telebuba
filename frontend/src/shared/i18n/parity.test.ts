import { expect, test } from 'vitest';

import en from './en.json';
import ru from './ru.json';

// Every key one locale has, the other has too — compared modulo i18next's plural
// suffixes, because Russian needs `_few`/`_many` forms English has no use for.
const PLURAL = /_(zero|one|two|few|many|other)$/;

function keys(table: object, prefix = ''): Set<string> {
  const found = new Set<string>();
  for (const [key, value] of Object.entries(table)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (value !== null && typeof value === 'object') {
      for (const nested of keys(value as object, path)) found.add(nested);
    } else {
      found.add(path.replace(PLURAL, ''));
    }
  }
  return found;
}

test('ru.json and en.json carry the same keys', () => {
  const ruKeys = keys(ru);
  const enKeys = keys(en);

  expect([...ruKeys].filter((key) => !enKeys.has(key))).toEqual([]);
  expect([...enKeys].filter((key) => !ruKeys.has(key))).toEqual([]);
});
