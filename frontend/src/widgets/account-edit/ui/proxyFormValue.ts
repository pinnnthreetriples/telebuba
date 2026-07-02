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

// Client-side validation for the proxy form. Messages are i18n keys resolved by
// the field renderer via t(); host + port are the only required fields.
export const proxyFormSchema = z.object({
  proxy_type: z.enum(['socks5', 'https']),
  host: z.string().trim().min(1, 'accounts.proxyForm.errHost'),
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
