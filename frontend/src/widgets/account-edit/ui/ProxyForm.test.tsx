import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { expect, test, vi } from 'vitest';

import '@/shared/i18n';

import { ProxyForm } from './ProxyForm';
import { EMPTY_PROXY_FORM, type ProxyFormValue } from './proxyFormValue';

function Harness() {
  const [value, setValue] = useState<ProxyFormValue>(EMPTY_PROXY_FORM);
  return <ProxyForm value={value} onChange={setValue} />;
}

function renderWithClient() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <Harness />
    </QueryClientProvider>,
  );
}

test('password eye toggles and the type segments switch', async () => {
  renderWithClient();
  const pass = screen.getByPlaceholderText('пароль');
  expect(pass.getAttribute('type')).toBe('password');
  await userEvent.click(screen.getByRole('button', { name: 'Пароль' }));
  expect(pass.getAttribute('type')).toBe('text');

  await userEvent.click(screen.getByText('HTTPS'));
  await userEvent.click(screen.getByText('SOCKS5'));
});

test('host and port strip unsupported characters from pasted values', async () => {
  const user = userEvent.setup();
  renderWithClient();
  const host = screen.getByPlaceholderText('123.45.67.89');
  const port = screen.getByPlaceholderText('1080');

  await user.click(host);
  await user.paste('abc123.45.67.89x');
  await user.click(port);
  await user.paste('1a080');

  expect(host).toHaveValue('123.45.67.89');
  expect(port).toHaveValue('1080');
  const detect = screen.getByRole('button', { name: 'Определить' });
  expect(detect).toBeEnabled();

  await user.clear(host);
  await user.type(host, '256.45.67.89');
  expect(detect).toBeDisabled();
});

test('probe button hits /proxies/probe and shows the detected country', async () => {
  vi.mocked(fetch).mockResolvedValue(
    new Response(JSON.stringify({ status: 'tcp_working', country_code: 'NL' }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }),
  );
  renderWithClient();
  const textboxes = screen.getAllByRole('textbox');
  await userEvent.type(textboxes[0]!, '1.2.3.4'); // host
  await userEvent.type(textboxes[1]!, '1080'); // port
  await userEvent.click(screen.getByRole('button', { name: 'Определить' }));

  await waitFor(() => {
    expect(screen.getByText('NL')).toBeInTheDocument();
  });
  const probed = vi
    .mocked(fetch)
    .mock.calls.some(([input]) => (input as Request).url.includes('/proxies/probe'));
  expect(probed).toBe(true);
});
