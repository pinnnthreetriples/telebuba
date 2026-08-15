import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  createNeuroshillingCampaignMutation,
  deleteNeuroshillingCampaignMutation,
  NeuroshillingAccountsModal,
  neuroshillingBoardQueryOptions,
  neuroshillingCampaignsQueryOptions,
  updateNeuroshillingCampaignMutation,
} from '@/entities/neuroshilling';
import type {
  NeuroshillingBoardAccount,
  NeuroshillingCampaign,
  NeuroshillingCampaignUpdate,
} from '@/shared/api';
import { useLogEventStream } from '@/shared/lib';
import { ConfirmModal } from '@/shared/ui';

import { AccountsCard } from './AccountsCard';
import { CampaignsCard } from './CampaignsCard';
import { HowItWorksCard } from './HowItWorksCard';

// The query-key `_id`s this page owns. The SSE stream fires on every log row in
// the whole app, so a bare invalidateQueries() would refetch accounts, warming,
// settings and every open profile snapshot on each one.
const NEUROSHILLING_QUERY_IDS = new Set(['listNeuroshillingCampaigns', 'getNeuroshillingBoard']);

// `updateNeuroshillingCampaign` replaces the WHOLE form: a field left out of the
// body is written back as its schema default. Stage one edits only the roster, so
// every other field is echoed from the campaign the board just returned rather
// than silently reset.
function rosterUpdate(
  campaign: NeuroshillingCampaign,
  pool: NeuroshillingBoardAccount[],
  accountIds: string[],
): NeuroshillingCampaignUpdate {
  const byId = new Map(pool.map((account) => [account.account_id, account]));
  return {
    name: campaign.name,
    mode: campaign.mode,
    topic: campaign.topic,
    targets_raw: campaign.targets_raw,
    unique_messages: campaign.unique_messages,
    use_chat_context: campaign.use_chat_context,
    media_message_link: campaign.media_message_link,
    media_step_position: campaign.media_step_position,
    run_mode: campaign.run_mode,
    pause_min_seconds: campaign.pause_min_seconds,
    pause_max_seconds: campaign.pause_max_seconds,
    messages_per_hour: campaign.messages_per_hour,
    messages_per_chat_per_day: campaign.messages_per_chat_per_day,
    total_per_account: campaign.total_per_account,
    reserve_enabled: campaign.reserve_enabled,
    autoresponder: campaign.autoresponder,
    reply_to_humans: campaign.reply_to_humans,
    reply_activity: campaign.reply_activity,
    listen_minutes: campaign.listen_minutes,
    // Role and reserve belong to later stages, but they are already stored per
    // account: carry whatever the board holds so a roster edit never drops them.
    accounts: accountIds.map((accountId) => ({
      account_id: accountId,
      role_id: byId.get(accountId)?.role_id ?? null,
      is_reserve: byId.get(accountId)?.is_reserve ?? false,
    })),
  };
}

export function NeuroshillingPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  // The page's ONE refresh scope, narrowed to the queries above. Used by the SSE
  // stream and by every mutation here, because they all need the same thing.
  const invalidateNeuroshilling = () => {
    void queryClient.invalidateQueries({
      predicate: (query) => {
        const id = (query.queryKey[0] as { _id?: string } | undefined)?._id;
        return id !== undefined && NEUROSHILLING_QUERY_IDS.has(id);
      },
    });
  };
  useLogEventStream(invalidateNeuroshilling);

  const [selected, setSelected] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [createName, setCreateName] = useState('');
  const [showAccounts, setShowAccounts] = useState(false);
  const [deleteFor, setDeleteFor] = useState<NeuroshillingCampaign | null>(null);

  const campaigns = useQuery(neuroshillingCampaignsQueryOptions());
  const campaignList = campaigns.data?.campaigns ?? [];
  // Every scoped read hangs off this: no campaign, no board query at all.
  const campaignId = selected ?? campaignList[0]?.campaign_id ?? null;

  const board = useQuery({
    ...neuroshillingBoardQueryOptions({ path: { campaign_id: campaignId ?? '' } }),
    enabled: campaignId !== null,
  });
  const campaign = board.data?.campaign;
  const pool = board.data?.available ?? [];
  const roster = pool.filter((account) => account.assigned);

  // A picker left open across a campaign switch would show the new campaign's pool
  // over the old campaign's draft, and closing it would write that draft to the
  // wrong campaign.
  useEffect(() => {
    setShowAccounts(false);
  }, [campaignId]);

  const createCampaign = useMutation(createNeuroshillingCampaignMutation());
  const deleteCampaign = useMutation(deleteNeuroshillingCampaignMutation());
  const updateCampaign = useMutation(updateNeuroshillingCampaignMutation());

  const create = () => {
    const name = createName.trim();
    if (!name) return;
    setCreating(false);
    setCreateName('');
    void createCampaign
      .mutateAsync({ body: { name } })
      .then((created) => {
        // Select it, so the cards below land on the campaign just created.
        setSelected(created.campaign_id);
      })
      .catch(() => undefined)
      .finally(invalidateNeuroshilling);
  };

  // mutateAsync, never .mutate(): one useMutation is ONE callback slot, so a
  // second save before the first settles would take it over and drop the first
  // one's refresh.
  const saveRoster = (accountIds: string[]) => {
    if (campaign === undefined) return;
    void updateCampaign
      .mutateAsync({
        path: { campaign_id: campaign.campaign_id },
        body: rosterUpdate(campaign, pool, accountIds),
      })
      .catch(() => undefined)
      .finally(invalidateNeuroshilling);
  };

  return (
    <div className="tb-fadeup mx-auto max-w-[1000px]">
      <h1 className="m-0 mb-[18px] text-[22px] font-bold tracking-[-0.02em]">
        {t('neuroshilling.title')}
      </h1>

      <div className="flex flex-col gap-4">
        <CampaignsCard
          campaignList={campaignList}
          campaignId={campaignId}
          onSelect={setSelected}
          onDelete={setDeleteFor}
          creating={creating}
          createName={createName}
          onStartCreate={() => {
            setCreating(true);
          }}
          onCancelCreate={() => {
            setCreating(false);
            setCreateName('');
          }}
          onCreateName={setCreateName}
          onCreate={create}
        />

        {campaignId === null ? null : (
          <AccountsCard
            accounts={roster}
            onPick={() => {
              setShowAccounts(true);
            }}
          />
        )}

        <HowItWorksCard />
      </div>

      {showAccounts ? (
        <NeuroshillingAccountsModal
          accounts={pool}
          onClose={() => {
            setShowAccounts(false);
          }}
          onSave={saveRoster}
        />
      ) : null}

      {deleteFor ? (
        <ConfirmModal
          title={t('neuroshilling.modal.delete.title', { name: deleteFor.name })}
          body={t('neuroshilling.modal.delete.body')}
          confirmLabel={t('neuroshilling.modal.delete.confirm')}
          cancelLabel={t('neuroshilling.modal.delete.cancel')}
          onClose={() => {
            setDeleteFor(null);
          }}
          // Returning the promise keeps the dialog up (and pending) until the
          // DELETE lands, and leaves it open on a refusal — a running campaign
          // answers 409 and the operator has to see that.
          onConfirm={() => {
            const target = deleteFor.campaign_id;
            return deleteCampaign
              .mutateAsync({ path: { campaign_id: target } })
              .then(() => {
                if (selected === target) setSelected(null);
              })
              .finally(invalidateNeuroshilling);
          }}
        />
      ) : null}
    </div>
  );
}
