import { expect, test } from 'vitest';

import { proxyTypeLabel } from './proxy';

test('upper-cases the proxy type for display', () => {
  expect(proxyTypeLabel('socks5')).toBe('SOCKS5');
  expect(proxyTypeLabel('https')).toBe('HTTPS');
});
