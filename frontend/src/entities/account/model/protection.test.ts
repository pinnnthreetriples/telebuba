import { expect, test } from 'vitest';

import { isFullyProtected, protectionTone } from './protection';

const of = (score: number) => ({ score, total: 7, missing: [] });

test('all passed is ok, five or six warn, four and below are danger', () => {
  expect(protectionTone(of(7))).toBe('success');
  expect(protectionTone(of(6))).toBe('warning');
  expect(protectionTone(of(5))).toBe('warning');
  expect(protectionTone(of(4))).toBe('danger');
  expect(protectionTone(of(0))).toBe('danger');
});

test('only a full score is fully protected; no summary is not', () => {
  expect(isFullyProtected(of(7))).toBe(true);
  expect(isFullyProtected(of(6))).toBe(false);
  expect(isFullyProtected(undefined)).toBe(false);
  expect(isFullyProtected(null)).toBe(false);
});
