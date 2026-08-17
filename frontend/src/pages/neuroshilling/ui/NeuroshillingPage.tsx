import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  approveNeuroshillingScenarioMutation,
  createNeuroshillingCampaignMutation,
  deleteNeuroshillingCampaignMutation,
  generateNeuroshillingScenarioMutation,
  NeuroshillingAccountsModal,
  neuroshillingBoardQueryOptions,
  neuroshillingCampaignsQueryOptions,
  neuroshillingScenarioQueryOptions,
  setNeuroshillingScenarioMutation,
  updateNeuroshillingCampaignMutation,
} from '@/entities/neuroshilling';
import type {
  NeuroshillingAccountAssignment,
  NeuroshillingBoardAccount,
  NeuroshillingCampaign,
  NeuroshillingCampaignUpdate,
} from '@/shared/api';
import { useLogEventStream } from '@/shared/lib';
import { ConfirmModal } from '@/shared/ui';

import { AccountsCard } from './AccountsCard';
import { CampaignsCard } from './CampaignsCard';
import { HowItWorksCard } from './HowItWorksCard';
import { PreviewCard } from './PreviewCard';
import { ScenarioCard } from './ScenarioCard';
import type { ScenarioDraft } from './scenarioDraft';
import { campaignFieldsOf, draftOf, scenarioBody } from './scenarioDraft';

// The query-key `_id`s this page owns. The SSE stream fires on every log row in
// the whole app, so a bare invalidateQueries() would refetch accounts, warming,
// settings and every open profile snapshot on each one.
//
// `getNeuroshillingScenario` is deliberately NOT here. The stream flushes on a
// 400 ms trailing debounce, and the scenario query backs an explicit-save form:
// refetching it under the operator's typing is exactly what the separate
// endpoint exists to avoid. It refreshes from its own mutations, below.
const NEUROSHILLING_QUERY_IDS = new Set(['listNeuroshillingCampaigns', 'getNeuroshillingBoard']);

// The generation ask, which is not stored anywhere: it describes ONE call. It
// lives on the page rather than inside the scenario card because the preview
// card's "regenerate" fires the same request.
const DEFAULT_PERSONAS = 3;
const DEFAULT_STEPS = 8;

function assignmentsOf(
  pool: NeuroshillingBoardAccount[],
  accountIds: string[],
): NeuroshillingAccountAssignment[] {
  const byId = new Map(pool.map((account) => [account.account_id, account]));
  // Role and reserve belong to later stages, but they are already stored per
  // account: carry whatever the board holds so an edit never drops them.
  return accountIds.map((accountId) => ({
    account_id: accountId,
    role_id: byId.get(accountId)?.role_id ?? null,
    is_reserve: byId.get(accountId)?.is_reserve ?? false,
  }));
}

// `updateNeuroshillingCampaign` replaces the WHOLE form: a field left out of the
// body is written back as its schema default. Every caller edits one slice of it,
// so each starts from this echo of the campaign the board just returned rather
// than silently resetting the rest.
function campaignBody(
  campaign: NeuroshillingCampaign,
  accounts: NeuroshillingAccountAssignment[],
): NeuroshillingCampaignUpdate {
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
    accounts,
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
  const [confirmGenerate, setConfirmGenerate] = useState(false);
  const [personaCount, setPersonaCount] = useState(DEFAULT_PERSONAS);
  const [stepCount, setStepCount] = useState(DEFAULT_STEPS);
  const [draft, setDraft] = useState<ScenarioDraft | null>(null);
  // The draft as it was last adopted, serialised. Comparing against THIS rather
  // than against the live query keeps "dirty" true for the moment between a save
  // landing and the board refetch that reflects it.
  const [baseline, setBaseline] = useState('');

  const campaigns = useQuery(neuroshillingCampaignsQueryOptions());
  const campaignList = campaigns.data?.campaigns ?? [];
  // Every scoped read hangs off this: no campaign, no board query at all.
  const campaignId = selected ?? campaignList[0]?.campaign_id ?? null;

  const board = useQuery({
    ...neuroshillingBoardQueryOptions({ path: { campaign_id: campaignId ?? '' } }),
    enabled: campaignId !== null,
  });
  const scenario = useQuery({
    ...neuroshillingScenarioQueryOptions({ path: { campaign_id: campaignId ?? '' } }),
    enabled: campaignId !== null,
  });
  const campaign = board.data?.campaign;
  const stored = scenario.data;
  const pool = board.data?.available ?? [];
  const roster = pool.filter((account) => account.assigned);

  // A picker left open across a campaign switch would show the new campaign's pool
  // over the old campaign's draft, and closing it would write that draft to the
  // wrong campaign.
  useEffect(() => {
    setShowAccounts(false);
  }, [campaignId]);

  // The scenario form is seeded from the server EXACTLY ONCE per campaign — here,
  // when that campaign's two reads first agree — and afterwards only from the
  // response of a mutation the operator fired themselves. A resync on every
  // server value (the `useEffect(() => form.reset(server), [server])` shape the
  // settings page uses) would empty the form under the operator's hands: the
  // board query IS in the invalidation set above, and the log stream refetches it
  // 400 ms after every log row.
  useEffect(() => {
    if (campaign === undefined || stored === undefined) return;
    if (stored.campaign_id !== campaign.campaign_id) return;
    if (draft?.campaignId === campaign.campaign_id) return;
    const next = draftOf(campaign, stored);
    setDraft(next);
    setBaseline(JSON.stringify(next));
  }, [campaign, stored, draft]);

  const dirty = draft !== null && JSON.stringify(draft) !== baseline;

  const createCampaign = useMutation(createNeuroshillingCampaignMutation());
  const deleteCampaign = useMutation(deleteNeuroshillingCampaignMutation());
  const updateCampaign = useMutation(updateNeuroshillingCampaignMutation());
  const saveScenario = useMutation(setNeuroshillingScenarioMutation());
  const generateScenario = useMutation(generateNeuroshillingScenarioMutation());
  const approveScenario = useMutation(approveNeuroshillingScenarioMutation());
  const busy =
    updateCampaign.isPending ||
    saveScenario.isPending ||
    generateScenario.isPending ||
    approveScenario.isPending;

  const adopt = (next: ScenarioDraft) => {
    setDraft(next);
    setBaseline(JSON.stringify(next));
  };

  // The scenario query is out of the SSE scope on purpose, so its own mutations
  // are the only thing that refreshes it.
  const refresh = () => {
    invalidateNeuroshilling();
    if (campaignId === null) return;
    void queryClient.invalidateQueries({
      queryKey: neuroshillingScenarioQueryOptions({ path: { campaign_id: campaignId } }).queryKey,
    });
  };

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
        body: campaignBody(campaign, assignmentsOf(pool, accountIds)),
      })
      .catch(() => undefined)
      .finally(invalidateNeuroshilling);
  };

  // The campaign half of the scenario card: the topic and everything else that
  // decides WHAT gets said, over an echo of the fields other cards own.
  const briefBody = (current: NeuroshillingCampaign, value: ScenarioDraft) => ({
    ...campaignBody(
      current,
      assignmentsOf(
        pool,
        roster.map((account) => account.account_id),
      ),
    ),
    ...campaignFieldsOf(value),
  });

  const save = () => {
    if (campaign === undefined || draft === null) return;
    const path = { campaign_id: campaign.campaign_id };
    void updateCampaign
      .mutateAsync({ path, body: briefBody(campaign, draft) })
      .then((updated) =>
        // Two writes, in this order and never in parallel: the PUT below always
        // returns the campaign to `draft`, so it must be the LAST thing to touch
        // the approval.
        saveScenario.mutateAsync({ path, body: scenarioBody(draft) }).then((saved) => {
          // Adopt the answer: the server has just minted real ids for roles the
          // form invented, and keeping the invented keys would mint a second set
          // on the next save.
          adopt(draftOf(updated, saved));
        }),
      )
      .catch(() => undefined)
      .finally(refresh);
  };

  const generate = () => {
    if (campaign === undefined || draft === null) return Promise.resolve();
    const path = { campaign_id: campaign.campaign_id };
    // The model is briefed from the STORED topic, so what the operator typed has
    // to land before the ask goes out — otherwise a first generation is refused
    // for an empty topic that is plainly on screen.
    return updateCampaign
      .mutateAsync({ path, body: briefBody(campaign, draft) })
      .then((updated) =>
        generateScenario
          .mutateAsync({ path, body: { persona_count: personaCount, step_count: stepCount } })
          .then((generated) => {
            // Overwriting the form IS what the button means.
            adopt(draftOf(updated, generated));
          }),
      )
      .finally(refresh);
  };

  const requestGenerate = () => {
    // Generation replaces the stored dialogue outright, so an existing one is
    // confirmed first: one stray click would otherwise destroy every manual edit
    // with nothing to undo it.
    if ((stored?.steps ?? []).length > 0) {
      setConfirmGenerate(true);
      return;
    }
    void generate().catch(() => undefined);
  };

  const approve = () => {
    if (campaignId === null) return;
    void approveScenario
      .mutateAsync({ path: { campaign_id: campaignId } })
      .catch(() => undefined)
      .finally(refresh);
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

        {/* Both reads must be about the CURRENT campaign: a cached scenario from an
            earlier visit would otherwise pair with the previous campaign's draft
            for a frame after a switch. */}
        {draft === null ||
        stored === undefined ||
        draft.campaignId !== campaignId ||
        stored.campaign_id !== campaignId ? null : (
          <>
            <ScenarioCard
              draft={draft}
              onDraft={setDraft}
              status={stored.scenario_status ?? 'draft'}
              dirty={dirty}
              personaCount={personaCount}
              stepCount={stepCount}
              onPersonaCount={setPersonaCount}
              onStepCount={setStepCount}
              onGenerate={requestGenerate}
              onSave={save}
              onApprove={approve}
              busy={busy}
            />
            <PreviewCard
              roles={stored.roles ?? []}
              steps={stored.steps ?? []}
              status={stored.scenario_status ?? 'draft'}
              dirty={dirty}
              onRegenerate={requestGenerate}
              busy={busy}
            />
          </>
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

      {confirmGenerate ? (
        <ConfirmModal
          title={t('neuroshilling.modal.regenerate.title')}
          body={t('neuroshilling.modal.regenerate.body')}
          confirmLabel={t('neuroshilling.modal.regenerate.confirm')}
          cancelLabel={t('neuroshilling.modal.regenerate.cancel')}
          onClose={() => {
            setConfirmGenerate(false);
          }}
          // Returning the promise keeps the dialog up (and pending) until the
          // model answers, and leaves it open on a refusal — a busy generation or
          // an exhausted daily budget is something the operator has to see.
          onConfirm={generate}
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
