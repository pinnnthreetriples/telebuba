import { expect, test } from 'vitest';

import { EMPTY_PROXY_FORM, parseProxyLine, proxyFormSchema } from './proxyFormValue';

test.each([
  ['1.2.3.4:8080', { host: '1.2.3.4', port: '8080' }],
  ['  1.2.3.4:8080\n', { host: '1.2.3.4', port: '8080' }],
  ['1.2.3.4:8080:user:p:ss', { host: '1.2.3.4', port: '8080', username: 'user', password: 'p:ss' }],
  [
    '1.2.3.4:8080:alice:p@ss',
    { host: '1.2.3.4', port: '8080', username: 'alice', password: 'p@ss' },
  ],
  [
    'us:er:pa:ss@1.2.3.4:1080',
    { host: '1.2.3.4', port: '1080', username: 'us', password: 'er:pa:ss' },
  ],
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

// The field renderer shows a field's FIRST issue, as an i18n key: these pin both the keys
// and their order, which a zod upgrade could otherwise shift unseen.
const proxyIssues = (host: string, port: string) =>
  proxyFormSchema
    .safeParse({ ...EMPTY_PROXY_FORM, host, port })
    .error?.issues.map((issue) => [issue.path.join('.'), issue.message]) ?? [];

test.each([
  ['1.2.3.4', '1080', []],
  [' 1.2.3.4 ', '65535', []],
  // Not an IPv4 at all: the regex fails without aborting, so the octet refine runs too.
  [
    'example.com',
    '1080',
    [
      ['host', 'accounts.proxyForm.errHostIpv4'],
      ['host', 'accounts.proxyForm.errHostIpv4'],
    ],
  ],
  ['1.2.3.256', '1080', [['host', 'accounts.proxyForm.errHostIpv4']]],
  ['1.2.3.4', '0', [['port', 'accounts.proxyForm.errPortRange']]],
  ['1.2.3.4', '65536', [['port', 'accounts.proxyForm.errPortRange']]],
  [
    '1.2.3.4',
    '',
    [
      ['port', 'accounts.proxyForm.errPort'],
      ['port', 'accounts.proxyForm.errPortRange'],
    ],
  ],
])('proxy %j:%j yields %j', (host, port, issues) => {
  expect(proxyIssues(host, port)).toEqual(issues);
});
