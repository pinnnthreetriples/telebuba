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
  await userEvent.click(screen.getByRole('button', { name: 'Показать пароль' }));
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
  const detect = screen.getByRole('button', { name: 'Проверить' });
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
  await userEvent.click(screen.getByRole('button', { name: 'Проверить' }));

  await waitFor(() => {
    expect(screen.getByText('NL')).toBeInTheDocument();
  });
  const probed = vi
    .mocked(fetch)
    .mock.calls.some(([input]) => (input as Request).url.includes('/proxies/probe'));
  expect(probed).toBe(true);
});

test('a pasted proxy line fills port and credentials, a typed colon jumps to port', async () => {
  const user = userEvent.setup();
  renderWithClient();
  const host = screen.getByPlaceholderText('123.45.67.89');
  const port = screen.getByPlaceholderText('1080');

  await user.click(host);
  await user.paste('1.2.3.4:8080:alice:s3cret');
  expect(host).toHaveValue('1.2.3.4');
  expect(port).toHaveValue('8080');
  expect(screen.getByPlaceholderText('логин')).toHaveValue('alice');
  expect(screen.getByPlaceholderText('пароль')).toHaveValue('s3cret');

  await user.clear(host);
  await user.clear(port);
  await user.type(host, '5.6.7.8:');
  expect(host).toHaveValue('5.6.7.8');
  expect(port).toHaveFocus();
  await user.keyboard('3128');
  expect(port).toHaveValue('3128');
});

test('tabbing past an empty host shows no error, a bad one does', async () => {
  const user = userEvent.setup();
  renderWithClient();
  const host = screen.getByPlaceholderText('123.45.67.89');

  await user.click(host);
  await user.tab();
  expect(screen.queryByText(/Введите IPv4-адрес/)).not.toBeInTheDocument();

  await user.type(host, '1.2.3');
  await user.tab();
  expect(screen.getByText(/Введите IPv4-адрес/)).toBeInTheDocument();
});

test('clearing a host that showed an error clears the error too', async () => {
  const user = userEvent.setup();
  renderWithClient();
  const host = screen.getByPlaceholderText('123.45.67.89');

  await user.type(host, '1.2.3');
  await user.tab();
  expect(screen.getByText(/Введите IPv4-адрес/)).toBeInTheDocument();

  await user.clear(host);
  expect(screen.queryByText(/Введите IPv4-адрес/)).not.toBeInTheDocument();
  expect(host).not.toHaveAttribute('aria-invalid');
});

test('a probe reply for replaced values is not shown', async () => {
  let reply: (response: Response) => void = () => undefined;
  vi.mocked(fetch).mockReturnValue(
    new Promise<Response>((resolve) => {
      reply = resolve;
    }),
  );
  const user = userEvent.setup();
  renderWithClient();
  const host = screen.getByPlaceholderText('123.45.67.89');

  await user.click(host);
  await user.paste('1.2.3.4:1080');
  await user.click(screen.getByRole('button', { name: 'Проверить' }));
  expect(screen.getByText('Проверяем соединение…')).toBeInTheDocument();

  await user.clear(host);
  await user.paste('5.6.7.8:3128');
  expect(screen.queryByText('Проверяем соединение…')).not.toBeInTheDocument();
  reply(
    new Response(JSON.stringify({ status: 'tcp_working', country_code: 'NL' }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }),
  );
  await new Promise((resolve) => setTimeout(resolve, 50));
  expect(screen.queryByText('NL')).not.toBeInTheDocument();
});
