import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect } from 'vitest';

import type { AccountSessionAudit } from '@/shared/api';

// The wizard's session-audit step sits between the proxy and the cloud password. These are
// the three answers it needs: the fleet (every account behind a proxy), the check (alive)
// and the saved audit. `foreign` names accounts whose audit lists that many foreign sessions.

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

export function auditedAudit(accountId: string, foreign = 0): AccountSessionAudit {
  const sessions = [
    {
      hash: '1',
      is_current: true,
      is_ours: true,
      official_app: false,
      device_model: 'Desktop PC',
      platform: 'Windows',
      system_version: '10',
      app_name: 'Telegram Desktop',
      ip: '198.51.100.7',
      country: 'Nowhere',
      unconfirmed: false,
    },
    ...Array.from({ length: foreign }, (_, i) => ({
      hash: String(100 + i),
      is_current: false,
      is_ours: false,
      official_app: false,
      device_model: 'Pixel 8',
      platform: 'Android',
      system_version: 'Android 14',
      app_name: 'Telegram Android',
      ip: '192.0.2.10',
      country: 'Nowhere',
      unconfirmed: false,
    })),
  ];
  return {
    account_id: accountId,
    checked_at: '2026-11-02T10:00:00+00:00',
    total: sessions.length,
    foreign_count: foreign,
    sessions,
  };
}

/** The audit step's routes, or `null` for a request that is not one of them. */
export function auditRoutes(
  request: Request,
  accountIds: string[],
  foreign: Record<string, number> = {},
): Response | null {
  const { pathname } = new URL(request.url);
  if (pathname === '/api/v1/accounts' && request.method === 'GET') {
    return json({
      items: accountIds.map((id) => ({
        account_id: id,
        status: 'new',
        proxy_id: 'pool-1',
        created_at: 'n',
        updated_at: 'n',
      })),
      next_cursor: null,
    });
  }
  if (pathname === '/api/v1/accounts/check') {
    // The body is read by the caller when it matters; every account answers alive.
    return json({ account_id: 'x', status: 'alive', created_at: 'n', updated_at: 'n' });
  }
  const sessions = /^\/api\/v1\/accounts\/([^/]+)\/sessions$/.exec(pathname);
  if (sessions?.[1] && request.method === 'GET') {
    return json(auditedAudit(sessions[1], foreign[sessions[1]] ?? 0));
  }
  return null;
}

/** Wait the audit step out (every account clean) and press Next. */
export async function passAuditStep(): Promise<void> {
  expect(await screen.findByText(/кто ещё держит аккаунты/)).toBeInTheDocument();
  await waitFor(() => {
    expect(screen.getByText('Далее').closest('button')).toBeEnabled();
  });
  await userEvent.click(screen.getByText('Далее'));
}
