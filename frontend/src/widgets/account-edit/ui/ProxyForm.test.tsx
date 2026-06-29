import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test } from 'vitest';

import '@/shared/i18n';

import { ProxyForm } from './ProxyForm';

test('detect runs idle→loading→ok and password toggles, type segments switch', async () => {
  render(<ProxyForm />);

  // password eye toggles the input type
  const pass = screen.getByPlaceholderText('пароль');
  expect(pass.getAttribute('type')).toBe('password');
  await userEvent.click(screen.getByRole('button', { name: 'Пароль' }));
  expect(pass.getAttribute('type')).toBe('text');

  // type segmented control
  await userEvent.click(screen.getByText('HTTPS'));
  await userEvent.click(screen.getByText('SOCKS5'));

  // detect: idle → loading (checking text) → ok (result after the 900ms mock)
  await userEvent.click(screen.getByText('Определить'));
  expect(screen.getByText('Проверяем соединение…')).toBeInTheDocument();
  await waitFor(() => {
    expect(screen.getByText('Нидерланды · 24 мс')).toBeInTheDocument();
  });
});
