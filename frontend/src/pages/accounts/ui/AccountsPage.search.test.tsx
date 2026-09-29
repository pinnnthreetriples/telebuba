import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, test } from 'vitest';

import '@/shared/i18n';

import { AccountsPage } from './AccountsPage';
import { account, listGets, renderWithClient, routeApi } from './AccountsPage.test-helpers';

test('typing in the search box keeps the table on screen and fires one request', async () => {
  routeApi({ page1: { items: [account('acc-1')], next_cursor: null } });
  renderWithClient(<AccountsPage />);
  await waitFor(() => {
    expect(screen.getByText('acc-1')).toBeInTheDocument();
  });
  const before = listGets();

  // Open the search field by clicking the search button
  await userEvent.click(screen.getByLabelText('Поиск по аккаунтам…'));
  await userEvent.type(screen.getByPlaceholderText('Поиск по аккаунтам…'), 'abc');

  // The generated key embeds `query`, so each keystroke was a fresh key with no
  // cached data: the table AND the pagination block were replaced by the loading
  // line on every character.
  expect(screen.queryByText('Загрузка…')).not.toBeInTheDocument();
  expect(screen.getByText('acc-1')).toBeInTheDocument();
  // ...and three keystrokes cost one request, not three.
  await waitFor(() => {
    expect(listGets()).toBe(before + 1);
  });
});

test('search field opens on button click and focuses input', async () => {
  routeApi({ page1: { items: [account('acc-1')], next_cursor: null } });
  renderWithClient(<AccountsPage />);
  await waitFor(() => {
    expect(screen.getByText('acc-1')).toBeInTheDocument();
  });

  const searchButton = screen.getByLabelText('Поиск по аккаунтам…');
  const searchInput = screen.getByPlaceholderText('Поиск по аккаунтам…') as HTMLInputElement;
  await userEvent.click(searchButton);
  expect(searchInput).toHaveFocus();
});

test('escape clears the search and returns focus to the search button', async () => {
  routeApi({ page1: { items: [account('acc-1')], next_cursor: null } });
  renderWithClient(<AccountsPage />);
  await waitFor(() => expect(screen.getByText('acc-1')).toBeInTheDocument());

  const searchButton = screen.getByLabelText('Поиск по аккаунтам…');
  await userEvent.click(searchButton);
  const searchInput = screen.getByPlaceholderText('Поиск по аккаунтам…') as HTMLInputElement;
  await userEvent.type(searchInput, 'test');
  await userEvent.keyboard('{Escape}');
  expect(searchInput.value).toBe('');
  expect(searchButton).toHaveFocus();
});

test('a field with text stays open when it loses focus', async () => {
  routeApi({ page1: { items: [account('acc-1')], next_cursor: null } });
  renderWithClient(<AccountsPage />);
  await waitFor(() => expect(screen.getByText('acc-1')).toBeInTheDocument());

  await userEvent.click(screen.getByLabelText('Поиск по аккаунтам…'));
  const searchInput = screen.getByPlaceholderText('Поиск по аккаунтам…') as HTMLInputElement;
  await userEvent.type(searchInput, 'abc');

  // Tab away: the input blurs, but a non-empty query must not collapse the field.
  // The collapse is driven by the wrapper's width class, so an emptied field
  // would carry `w-0`; a field still holding text must not.
  await userEvent.tab();
  expect(searchInput).not.toHaveFocus();
  expect(searchInput.value).toBe('abc');
  expect(searchInput.parentElement).not.toHaveClass('w-0');
});

test('a closed search field is hidden and unfocusable; opening it shows and focuses it', async () => {
  routeApi({ page1: { items: [account('acc-1')], next_cursor: null } });
  renderWithClient(<AccountsPage />);
  await waitFor(() => expect(screen.getByText('acc-1')).toBeInTheDocument());

  const field = screen.getByPlaceholderText('Поиск по аккаунтам…', { exact: true });
  // Closed: hidden from assistive tech, out of the tab order, zero-width and invisible.
  expect(field.parentElement).toHaveAttribute('aria-hidden', 'true');
  expect(field).toHaveAttribute('tabindex', '-1');
  expect(field.parentElement).toHaveClass('w-0', 'invisible', 'opacity-0');

  await userEvent.click(screen.getByLabelText('Поиск по аккаунтам…'));
  expect(field.parentElement).toHaveAttribute('aria-hidden', 'false');
  expect(field).toHaveAttribute('tabindex', '0');
  expect(field.parentElement).not.toHaveClass('invisible');
  expect(field).toHaveFocus();
});
