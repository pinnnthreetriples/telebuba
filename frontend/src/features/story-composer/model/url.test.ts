import { expect, test } from 'vitest';

import { isValidUrl, normalizeUrl } from './url';

test('adds https when the scheme is missing', () => {
  expect(normalizeUrl('example.com')).toBe('https://example.com');
  expect(normalizeUrl('  example.com/path?q=1  ')).toBe('https://example.com/path?q=1');
  expect(normalizeUrl('example.com:8080/x')).toBe('https://example.com:8080/x');
  expect(normalizeUrl('localhost:3000')).toBe('https://localhost:3000');
});

test('keeps http, https and tg addresses as they are', () => {
  expect(normalizeUrl('http://example.com')).toBe('http://example.com');
  expect(normalizeUrl('https://t.me/channel')).toBe('https://t.me/channel');
  expect(normalizeUrl('tg://resolve?domain=durov')).toBe('tg://resolve?domain=durov');
});

test('refuses other schemes, spaces, bare words and empty input', () => {
  expect(normalizeUrl('javascript:alert(1)')).toBeNull();
  expect(normalizeUrl('ftp://example.com')).toBeNull();
  expect(normalizeUrl('data:text/html,hi')).toBeNull();
  expect(normalizeUrl('hello world.com')).toBeNull();
  expect(normalizeUrl('hello')).toBeNull();
  expect(normalizeUrl('')).toBeNull();
  expect(normalizeUrl('   ')).toBeNull();
  expect(normalizeUrl('https://')).toBeNull();
  expect(normalizeUrl('tg:')).toBeNull();
  expect(isValidUrl('nope')).toBe(false);
  expect(isValidUrl('a.io')).toBe(true);
});
