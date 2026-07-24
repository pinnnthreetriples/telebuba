import { fireEvent, render, screen } from '@testing-library/react';
import { expect, test } from 'vitest';

import { AccountAvatar } from './AccountAvatar';

const CLASS = 'h-8 w-8 shrink-0 rounded-full';
const FALLBACK = 'text-[12px] font-semibold';

test('renders the cached photo when an avatar etag is set', () => {
  const { container } = render(
    <AccountAvatar
      account={{ account_id: 'acc-1', avatar_etag: 'abc123' }}
      className={CLASS}
      fallbackClassName={FALLBACK}
    />,
  );
  const img = container.querySelector('img');
  expect(img?.getAttribute('src')).toBe('/api/v1/accounts/acc-1/avatar?v=abc123');
  expect(img?.getAttribute('loading')).toBe('lazy');
});

test('renders the initials fallback when there is no etag', () => {
  const { container } = render(
    <AccountAvatar
      account={{ account_id: 'acc-2', first_name: 'Ann', last_name: 'Lee' }}
      className={CLASS}
      fallbackClassName={FALLBACK}
    />,
  );
  expect(container.querySelector('img')).toBeNull();
  expect(screen.getByText('AL')).toBeInTheDocument();
});

test('falls back to initials when the image fails to load', () => {
  const { container } = render(
    <AccountAvatar
      account={{ account_id: 'acc-3', avatar_etag: 'zzz', first_name: 'Bo' }}
      className={CLASS}
      fallbackClassName={FALLBACK}
    />,
  );
  fireEvent.error(container.querySelector('img')!);
  expect(container.querySelector('img')).toBeNull();
  expect(screen.getByText('B')).toBeInTheDocument();
});
