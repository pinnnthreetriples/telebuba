import { expect, test } from 'vitest';

import { parseProxyLine } from './proxyFormValue';

test.each([
  ['1.2.3.4:8080', { host: '1.2.3.4', port: '8080' }],
  ['  1.2.3.4:8080\n', { host: '1.2.3.4', port: '8080' }],
  ['1.2.3.4:8080:user:p:ss', { host: '1.2.3.4', port: '8080', username: 'user', password: 'p:ss' }],
  ['user:pass@1.2.3.4:1080', { host: '1.2.3.4', port: '1080', username: 'user', password: 'pass' }],
  ['socks5://1.2.3.4:1080', { host: '1.2.3.4', port: '1080', proxy_type: 'socks5' }],
  [
    'socks5h://u:p@1.2.3.4:1080',
    {
      host: '1.2.3.4',
      port: '1080',
      username: 'u',
      password: 'p',
      proxy_type: 'socks5',
    },
  ],
  ['http://1.2.3.4:3128', { host: '1.2.3.4', port: '3128', proxy_type: 'https' }],
  ['HTTPS://1.2.3.4:3128', { host: '1.2.3.4', port: '3128', proxy_type: 'https' }],
])('parses %j', (line, expected) => {
  expect(parseProxyLine(line)).toEqual(expected);
});

test.each([
  '1.2.3.4',
  'abc123.45.67.89x',
  'example.com:80',
  '1.2.3.4:',
  '1.2.3.4:abc',
  'ftp://1.2.3.4:21',
])('leaves %j to the plain field', (line) => {
  expect(parseProxyLine(line)).toBeNull();
});
