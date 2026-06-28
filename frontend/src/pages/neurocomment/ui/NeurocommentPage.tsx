import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { accountsQueryOptions } from '@/entities/account';
import {
  assignCampaignAccountMutation,
  campaignsQueryOptions,
  createCampaignMutation,
  linkCampaignChannelMutation,
  neurocommentBoardQueryOptions,
  neurocommentRuntimeQueryOptions,
  startNeurocommentMutation,
  stopNeurocommentMutation,
} from '@/entities/campaign';
import { NeurocommentBoard } from '@/widgets/neurocomment-board';

const POLL_MS = 4000;

export function NeurocommentPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const invalidate = () => {
    void queryClient.invalidateQueries();
  };

  const [selected, setSelected] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [prompt, setPrompt] = useState('');
  const [channel, setChannel] = useState('');
  const [listener, setListener] = useState('');
  const [assignee, setAssignee] = useState('');

  const campaigns = useQuery(campaignsQueryOptions());
  const accounts = useQuery(accountsQueryOptions());
  const runtime = useQuery({ ...neurocommentRuntimeQueryOptions(), refetchInterval: POLL_MS });

  const campaignList = campaigns.data?.campaigns ?? [];
  const campaignId = selected ?? campaignList[0]?.campaign_id ?? null;

  const board = useQuery({
    ...neurocommentBoardQueryOptions({ path: { campaign_id: campaignId ?? '' } }),
    refetchInterval: POLL_MS,
    enabled: campaignId !== null,
  });

  const createCampaign = useMutation(createCampaignMutation());
  const linkChannel = useMutation(linkCampaignChannelMutation());
  const assignAccount = useMutation(assignCampaignAccountMutation());
  const start = useMutation(startNeurocommentMutation());
  const stop = useMutation(stopNeurocommentMutation());

  const accountOptions = accounts.data?.items ?? [];

  return (
    <main className="mx-auto max-w-5xl space-y-6 p-8">
      <header className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">{t('neurocomment.title')}</h1>
        <span className="text-sm text-ink-muted">
          {runtime.data?.running
            ? t('neurocomment.runtime.running', { count: runtime.data.active_channels })
            : t('neurocomment.runtime.stopped')}
        </span>
      </header>

      <section className="flex flex-wrap items-center gap-2 rounded-md border border-line bg-surface p-4">
        <select
          value={listener}
          onChange={(event) => {
            setListener(event.target.value);
          }}
          aria-label={t('neurocomment.runtime.listener')}
          className="rounded-md border border-line px-3 py-2 text-sm"
        >
          <option value="">{t('neurocomment.runtime.listener')}</option>
          {accountOptions.map((account) => (
            <option key={account.account_id} value={account.account_id}>
              {account.account_id}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="rounded-md bg-primary px-3 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
          disabled={!listener}
          onClick={() => {
            start.mutate({ body: { listener_account_id: listener } }, { onSettled: invalidate });
          }}
        >
          {t('neurocomment.runtime.start')}
        </button>
        <button
          type="button"
          className="rounded-md border border-line px-3 py-2 text-sm hover:bg-canvas"
          onClick={() => {
            stop.mutate({}, { onSettled: invalidate });
          }}
        >
          {t('neurocomment.runtime.stop')}
        </button>
      </section>

      <section className="rounded-md border border-line bg-surface p-4">
        <h2 className="mb-3 text-sm font-medium text-ink-muted">
          {t('neurocomment.campaigns.title')}
        </h2>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <select
            value={campaignId ?? ''}
            onChange={(event) => {
              setSelected(event.target.value);
            }}
            aria-label={t('neurocomment.campaigns.select')}
            className="rounded-md border border-line px-3 py-2 text-sm"
          >
            {campaignList.length === 0 ? (
              <option value="">{t('neurocomment.campaigns.none')}</option>
            ) : null}
            {campaignList.map((campaign) => (
              <option key={campaign.campaign_id} value={campaign.campaign_id}>
                {campaign.name}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-wrap gap-2">
          <input
            value={name}
            onChange={(event) => {
              setName(event.target.value);
            }}
            placeholder={t('neurocomment.campaigns.name')}
            aria-label={t('neurocomment.campaigns.name')}
            className="rounded-md border border-line px-3 py-2 text-sm"
          />
          <input
            value={prompt}
            onChange={(event) => {
              setPrompt(event.target.value);
            }}
            placeholder={t('neurocomment.campaigns.prompt')}
            aria-label={t('neurocomment.campaigns.prompt')}
            className="flex-1 rounded-md border border-line px-3 py-2 text-sm"
          />
          <button
            type="button"
            className="rounded-md bg-primary px-3 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
            disabled={!name.trim() || !prompt.trim()}
            onClick={() => {
              createCampaign.mutate(
                { body: { name, prompt } },
                {
                  onSettled: () => {
                    setName('');
                    setPrompt('');
                    invalidate();
                  },
                },
              );
            }}
          >
            {t('neurocomment.campaigns.create')}
          </button>
        </div>
      </section>

      {campaignId !== null ? (
        <section className="space-y-4 rounded-md border border-line bg-surface p-4">
          <div className="flex flex-wrap gap-2">
            <input
              value={channel}
              onChange={(event) => {
                setChannel(event.target.value);
              }}
              placeholder={t('neurocomment.setup.channel')}
              aria-label={t('neurocomment.setup.channel')}
              className="flex-1 rounded-md border border-line px-3 py-2 text-sm"
            />
            <button
              type="button"
              className="rounded-md border border-line px-3 py-2 text-sm hover:bg-canvas disabled:opacity-50"
              disabled={!channel.trim()}
              onClick={() => {
                linkChannel.mutate(
                  { path: { campaign_id: campaignId }, body: { channel } },
                  {
                    onSettled: () => {
                      setChannel('');
                      invalidate();
                    },
                  },
                );
              }}
            >
              {t('neurocomment.setup.linkChannel')}
            </button>
            <select
              value={assignee}
              onChange={(event) => {
                setAssignee(event.target.value);
              }}
              aria-label={t('neurocomment.setup.assign')}
              className="rounded-md border border-line px-3 py-2 text-sm"
            >
              <option value="">{t('neurocomment.setup.assign')}</option>
              {accountOptions.map((account) => (
                <option key={account.account_id} value={account.account_id}>
                  {account.account_id}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="rounded-md border border-line px-3 py-2 text-sm hover:bg-canvas disabled:opacity-50"
              disabled={!assignee}
              onClick={() => {
                assignAccount.mutate(
                  { path: { campaign_id: campaignId }, body: { account_id: assignee } },
                  {
                    onSettled: () => {
                      setAssignee('');
                      invalidate();
                    },
                  },
                );
              }}
            >
              {t('neurocomment.setup.assignAccount')}
            </button>
          </div>
          {board.data ? <NeurocommentBoard board={board.data} /> : null}
        </section>
      ) : null}
    </main>
  );
}
