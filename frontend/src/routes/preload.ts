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
  neurocommentSettingsQueryOptions,
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

// Wait for the data that determines the first screen's geometry before replacing the
// previous route. Individual pages still own their loading/error states: a failed
// request should not turn an ordinary page failure into a router error boundary.
async function settle(queries: Promise<unknown>[]): Promise<void> {
  await Promise.allSettled(queries);
}

export function preloadAccounts(client: QueryClient): Promise<void> {
  return settle([
    client.ensureQueryData(
      accountsQueryOptions({ query: { query: '', status: 'all', limit: 20 } }),
    ),
    client.ensureQueryData(accountStatsQueryOptions()),
    client.ensureQueryData(proxyPoolQueryOptions()),
  ]);
}

export function preloadWarming(client: QueryClient): Promise<void> {
  return settle([
    client.ensureQueryData(warmingBoardQueryOptions()),
    client.ensureQueryData(warmingSettingsQueryOptions()),
  ]);
}

export async function preloadNeurocomment(client: QueryClient): Promise<void> {
  const campaigns = client.ensureQueryData(campaignsQueryOptions());
  const common = [
    campaigns,
    client.ensureQueryData(allAccountsQueryOptions()),
    client.ensureQueryData(warmedAccountsQueryOptions()),
    client.ensureQueryData(warmingBoardQueryOptions()),
    client.ensureQueryData(neurocommentRuntimeQueryOptions()),
    client.ensureQueryData(neurocommentSettingsQueryOptions()),
    client.ensureQueryData(
      logsQueryOptions({ query: { event_prefix: 'neurocomment', limit: 80 } }),
    ),
  ];
  await Promise.allSettled(common);
  const first = (await campaigns.catch(() => null))?.campaigns?.[0];
  if (!first) return;
  await settle([
    client.ensureQueryData(
      neurocommentBoardQueryOptions({ path: { campaign_id: first.campaign_id } }),
    ),
    client.ensureQueryData(
      campaignChallengesQueryOptions({
        path: { campaign_id: first.campaign_id },
        query: { limit: 20 },
      }),
    ),
  ]);
}

export async function preloadNeuroshilling(client: QueryClient): Promise<void> {
  const campaigns = client.ensureQueryData(neuroshillingCampaignsQueryOptions());
  await Promise.allSettled([
    campaigns,
    client.ensureQueryData(
      logsQueryOptions({ query: { event_prefix: 'neuroshilling', limit: 80 } }),
    ),
  ]);
  const first = (await campaigns.catch(() => null))?.campaigns?.[0];
  if (!first) return;
  await settle([
    client.ensureQueryData(
      neuroshillingBoardQueryOptions({ path: { campaign_id: first.campaign_id } }),
    ),
    client.ensureQueryData(
      neuroshillingSettingsQueryOptions({ path: { campaign_id: first.campaign_id } }),
    ),
  ]);
}

export function preloadLogs(client: QueryClient): Promise<void> {
  return settle([
    client.ensureQueryData(
      logsQueryOptions({ query: { status: 'all', account_id: '', limit: 50 } }),
    ),
    client.ensureQueryData(allAccountsQueryOptions()),
  ]);
}

export function preloadSettings(client: QueryClient): Promise<void> {
  return settle([
    client.ensureQueryData(warmingSettingsQueryOptions()),
    client.ensureQueryData(neurocommentSettingsQueryOptions()),
  ]);
}
