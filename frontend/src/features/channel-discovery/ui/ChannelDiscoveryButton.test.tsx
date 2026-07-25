import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import '@/shared/i18n';

import { ChannelDiscoveryButton } from './ChannelDiscoveryButton';

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

function tree(campaignId: string | null) {
  return (
    <QueryClientProvider client={queryClient}>
      <ChannelDiscoveryButton campaignId={campaignId} campaignName="Promo" />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  vi.mocked(fetch).mockResolvedValue(
    new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } }),
  );
});

describe('ChannelDiscoveryButton', () => {
  it('cannot open without a campaign', () => {
    render(tree(null));
    expect(screen.getByRole('button', { name: 'Найти каналы' })).toBeDisabled();
  });

  it('remounts the modal when the campaign underneath it changes', async () => {
    const { rerender } = render(tree('a'));
    await userEvent.click(screen.getByRole('button', { name: 'Найти каналы' }));
    await userEvent.type(screen.getByPlaceholderText('крипта, трейдинг, новости'), 'crypto');

    // The page falls back to the first campaign, so deleting the current one swaps this
    // prop under the open modal. Its state (form, ticks, adopt outcome) was chosen for
    // the old campaign and must not carry over to the new one.
    rerender(tree('b'));

    expect(screen.getByPlaceholderText('крипта, трейдинг, новости')).toHaveValue('');
  });
});
