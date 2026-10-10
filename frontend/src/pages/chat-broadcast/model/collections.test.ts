import { expect, test } from 'vitest';

import { linkKey, mergeTargets, sameTargets } from './collections';

test('the same chat spelled three ways is one key', () => {
  expect(linkKey('@Chat')).toBe('chat');
  expect(linkKey('t.me/chat')).toBe('chat');
  expect(linkKey('https://t.me/Chat/')).toBe('chat');
});

test('an invite keeps its case: two hashes differing in case are two chats', () => {
  expect(linkKey('t.me/+AbCd')).not.toBe(linkKey('t.me/+abcd'));
  expect(linkKey('https://t.me/+AbCd')).toBe(linkKey('t.me/+AbCd'));
});

test('a merge keeps the current order and skips what is already there', () => {
  expect(mergeTargets(['@b', '@a'], ['t.me/A', '@c', '@c', ' '])).toEqual(['@b', '@a', '@c']);
  expect(mergeTargets([], ['@x'])).toEqual(['@x']);
});

test('sameTargets compares order too', () => {
  expect(sameTargets(['@a', '@b'], ['@a', '@b'])).toBe(true);
  expect(sameTargets(['@a', '@b'], ['@b', '@a'])).toBe(false);
});
