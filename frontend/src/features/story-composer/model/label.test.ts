import { expect, test } from 'vitest';

import { labelProblem } from './label';
import { truncateUtf16, utf16Length } from './utf16';

test('a plain label passes and an empty one is asked for', () => {
  expect(labelProblem('Подробнее')).toBeNull();
  expect(labelProblem('   ')).toBe('empty');
});

test('pictographic characters are rejected, ordinary symbols are not', () => {
  expect(labelProblem('Купить 🔥')).toBe('emoji');
  expect(labelProblem('👍')).toBe('emoji');
  expect(labelProblem('Скидка 50% — сегодня')).toBeNull();
});

test('the limit counts characters, not UTF-16 units', () => {
  expect(labelProblem('я'.repeat(40))).toBeNull();
  expect(labelProblem('я'.repeat(41))).toBe('too_long');
  expect(labelProblem('𝒜'.repeat(40))).toBeNull();
});

test('utf16 helpers count code units and never cut a pair in half', () => {
  expect(utf16Length('a😀')).toBe(3);
  expect(truncateUtf16('abc', 5)).toBe('abc');
  expect(truncateUtf16('ab😀', 3)).toBe('ab');
  expect(truncateUtf16('ab😀', 4)).toBe('ab😀');
});
