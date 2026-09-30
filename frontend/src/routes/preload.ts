import type { QueryClient } from '@tanstack/react-query';

import {
  accountsQueryOptions,
  accountStatsQueryOptions,
  allAccountsQueryOptions,
} from '@/entities/account';
import {
  campaignChallengesQueryOptions,
  campaignsQueryOptions,
  neurocommentBoardQueryOptions,
  neurocommentRuntimeQueryOptions,
} from '@/entities/campaign';
import { logsQueryOptions } from '@/entities/log';
import {
  neuroshillingBoardQueryOptions,
  neuroshillingCampaignsQueryOptions,
  neuroshillingSettingsQueryOptions,
} from '@/entities/neuroshilling';
import { proxyPoolQueryOptions } from '@/entities/proxy';
import {
  warmedAccountsQueryOptions,
  warmingBoardQueryOptions,
  warmingSettingsQueryOptions,
} from '@/entities/warming';

// Keep the previous page until its replacement has the data that determines its
// first frame. A hung API request must not trap navigation forever: after the
// budget, the destination page takes over with its own loading/error UI.
async function waitForFirstScreen(queries: Promise<unknown>[]): Promise<void> {
  let timeout: ReturnType<typeof setTimeout> | undefined;
  try {
    await Promise.race([
      Promise.allSettled(queries),
      new Promise<void>((resolve) => {
        timeout = setTimeout(resolve, 1_500);
      }),
    ]);
  } finally {
    clearTimeout(timeout);
  }
}

export function preloadAccounts(client: QueryClient): Promise<void> {
  return waitForFirstScreen([
    client.fetchQuery(accountsQueryOptions({ query: { query: '', status: 'all', limit: 20 } })),
    client.fetchQuery(accountStatsQueryOptions()),
    client.fetchQuery(proxyPoolQueryOptions()),
  ]);
}

export function preloadWarming(client: QueryClient): Promise<void> {
  return waitForFirstScreen([
    client.fetchQuery(warmingBoardQueryOptions()),
    client.fetchQuery(warmingSettingsQueryOptions()),
  ]);
}

export async function preloadNeurocomment(client: QueryClient): Promise<void> {
  const campaigns = client.fetchQuery(campaignsQueryOptions());
  const scoped = campaigns.then(async ({ campaigns: list }) => {
    const first = list?.[0];
    if (!first) return;
    await Promise.allSettled([
      client.fetchQuery(
        neurocommentBoardQueryOptions({ path: { campaign_id: first.campaign_id } }),
      ),
      client.fetchQuery(
        campaignChallengesQueryOptions({
          path: { campaign_id: first.campaign_id },
          query: { limit: 20 },
        }),
      ),
    ]);
  });
  await waitForFirstScreen([
    campaigns,
    scoped,
    client.fetchQuery(allAccountsQueryOptions()),
    client.fetchQuery(warmingBoardQueryOptions()),
    client.fetchQuery(warmedAccountsQueryOptions()),
    client.fetchQuery(neurocommentRuntimeQueryOptions()),
    client.fetchQuery(logsQueryOptions({ query: { event_prefix: 'neurocomment', limit: 80 } })),
  ]);
}

export async function preloadNeuroshilling(client: QueryClient): Promise<void> {
  const campaigns = client.fetchQuery(neuroshillingCampaignsQueryOptions());
  const scoped = campaigns.then(async ({ campaigns: list }) => {
    const first = list?.[0];
    if (!first) return;
    await Promise.allSettled([
      client.fetchQuery(
        neuroshillingBoardQueryOptions({ path: { campaign_id: first.campaign_id } }),
      ),
      client.fetchQuery(
        neuroshillingSettingsQueryOptions({ path: { campaign_id: first.campaign_id } }),
      ),
    ]);
  });
  void Promise.allSettled([
    client.fetchQuery(logsQueryOptions({ query: { event_prefix: 'neuroshilling', limit: 80 } })),
  ]);
  await waitForFirstScreen([campaigns, scoped]);
}

export function preloadLogs(client: QueryClient): Promise<void> {
  return waitForFirstScreen([
    client.fetchQuery(logsQueryOptions({ query: { status: 'all', account_id: '', limit: 50 } })),
    client.fetchQuery(allAccountsQueryOptions()),
  ]);
}

export function preloadSettings(client: QueryClient): Promise<void> {
  return waitForFirstScreen([client.fetchQuery(warmingSettingsQueryOptions())]);
}
