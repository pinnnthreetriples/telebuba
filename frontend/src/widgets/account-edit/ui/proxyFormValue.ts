// Value type + empty default for the shared proxy form. Kept out of ProxyForm.tsx
// so that file only exports a component (react-refresh/only-export-components).
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
