import { z } from 'zod';

// Value type + empty default + zod schema for the shared proxy form. Kept out of
// ProxyForm.tsx so that file only exports a component
// (react-refresh/only-export-components).
export interface ProxyFormValue {
  proxy_type: 'socks5' | 'https';
  host: string;
  port: string;
  username: string;
  password: string;
}

export const EMPTY_PROXY_FORM: ProxyFormValue = {
  proxy_type: 'socks5',
  host: '',
  port: '',
  username: '',
  password: '',
};

// Whether anything was typed into a form that opened empty — closing would lose it.
export function isProxyFormTouched(value: ProxyFormValue): boolean {
  return (Object.keys(EMPTY_PROXY_FORM) as (keyof ProxyFormValue)[]).some(
    (key) => value[key] !== EMPTY_PROXY_FORM[key],
  );
}

// A proxy line as sellers hand it out, pasted into the host field: `ip:port`,
// `ip:port:login:password`, `login:password@ip:port`, each optionally behind a
// `socks5://` / `http(s)://` scheme. Returns the fields it names, or null when the
// text is not such a line — then the host field keeps it as typed text.
const SCHEME_TYPE: Record<string, ProxyFormValue['proxy_type']> = {
  socks5: 'socks5',
  socks5h: 'socks5',
  http: 'https',
  https: 'https',
};
const ENDPOINT = /^((?:\d{1,3}\.){3}\d{1,3}):(\d{1,5})$/;

export function parseProxyLine(text: string): Partial<ProxyFormValue> | null {
  let rest = text.trim();
  let proxyType: ProxyFormValue['proxy_type'] | undefined;
  const scheme = /^([a-z0-9]+):\/\//i.exec(rest);
  if (scheme) {
    proxyType = SCHEME_TYPE[(scheme[1] ?? '').toLowerCase()];
    if (!proxyType) return null;
    rest = rest.slice(scheme[0].length);
  }
  let credentials: string | undefined;
  const at = rest.lastIndexOf('@');
  if (at !== -1) {
    credentials = rest.slice(0, at);
    rest = rest.slice(at + 1);
  }
  // `ip:port:login:password` — the endpoint is the first two colon-separated parts.
  const parts = rest.split(':');
  if (credentials === undefined && parts.length > 2) {
    credentials = parts.slice(2).join(':');
    rest = parts.slice(0, 2).join(':');
  }
  const [, host, port] = ENDPOINT.exec(rest) ?? [];
  if (!host || !port) return null;
  const parsed: Partial<ProxyFormValue> = { host, port };
  if (credentials) {
    const colon = credentials.indexOf(':');
    parsed.username = colon === -1 ? credentials : credentials.slice(0, colon);
    if (colon !== -1) parsed.password = credentials.slice(colon + 1);
  }
  if (proxyType) parsed.proxy_type = proxyType;
  return parsed;
}

// Client-side validation for the proxy form. Messages are i18n keys resolved by
// the field renderer via t(); host + port are the only required fields.
export const proxyFormSchema = z.object({
  proxy_type: z.enum(['socks5', 'https']),
  host: z
    .string()
    .trim()
    .regex(/^(?:\d{1,3}\.){3}\d{1,3}$/, 'accounts.proxyForm.errHostIpv4')
    .refine(
      (value) => value.split('.').every((part) => Number(part) <= 255),
      'accounts.proxyForm.errHostIpv4',
    ),
  port: z
    .string()
    .regex(/^\d+$/, 'accounts.proxyForm.errPort')
    .refine((value) => {
      const port = Number(value);
      return port >= 1 && port <= 65535;
    }, 'accounts.proxyForm.errPortRange'),
  username: z.string(),
  password: z.string(),
});
