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
import { useLogEventStream } from '@/shared/lib';
import { NeurocommentBoard } from '@/widgets/neurocomment-board';

// SSE drives live runtime/board updates; this poll is just the fallback net.
const FALLBACK_POLL_MS = 30000;
const STAGES = ['listen', 'detect', 'filter', 'generate', 'solve', 'comment'] as const;

const INPUT =
  'tb-time w-full rounded-[10px] border border-line bg-white px-3 py-[9px] text-[13px] outline-none';
const CARD = 'rounded-2xl border border-line bg-white p-[14px]';

export function NeurocommentPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const invalidate = () => {
    void queryClient.invalidateQueries();
  };
  useLogEventStream(invalidate);

  const [selected, setSelected] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [prompt, setPrompt] = useState('');
  const [channel, setChannel] = useState('');
  const [listener, setListener] = useState('');
  const [assignee, setAssignee] = useState('');

  const campaigns = useQuery(campaignsQueryOptions());
  const accounts = useQuery(accountsQueryOptions());
  const runtime = useQuery({
    ...neurocommentRuntimeQueryOptions(),
    refetchInterval: FALLBACK_POLL_MS,
  });

  const campaignList = campaigns.data?.campaigns ?? [];
  const campaignId = selected ?? campaignList[0]?.campaign_id ?? null;

  const board = useQuery({
    ...neurocommentBoardQueryOptions({ path: { campaign_id: campaignId ?? '' } }),
    refetchInterval: FALLBACK_POLL_MS,
    enabled: campaignId !== null,
  });

  const createCampaign = useMutation(createCampaignMutation());
  const linkChannel = useMutation(linkCampaignChannelMutation());
  const assignAccount = useMutation(assignCampaignAccountMutation());
  const start = useMutation(startNeurocommentMutation());
  const stop = useMutation(stopNeurocommentMutation());

  const accountOptions = accounts.data?.items ?? [];
  const running = runtime.data?.running ?? false;
  // Decorative pipeline position: a mid-flight look while running, idle when off.
  const activeCell = running ? 2 : -1;

  const stats: { label: string; value: number; color: string }[] = [
    { label: t('neurocomment.stat.campaigns'), value: campaignList.length, color: '#0b0b0c' },
    {
      label: t('neurocomment.stat.channels'),
      value: runtime.data?.active_channels ?? 0,
      color: '#0066ff',
    },
    { label: t('neurocomment.stat.accounts'), value: accountOptions.length, color: '#0b0b0c' },
    { label: t('neurocomment.stat.comments'), value: 0, color: '#12a150' },
  ];

  return (
    <div className="tb-fadeup">
      <h1 className="m-0 mb-[18px] text-[22px] font-bold tracking-[-0.02em]">
        {t('neurocomment.title')}
      </h1>

      <div className="grid grid-cols-[340px_1fr] items-start gap-4">
        {/* LEFT: setup */}
        <div className="flex flex-col gap-4">
          <div className={CARD}>
            <div className="mb-3 text-[13px] font-semibold">
              {t('neurocomment.campaigns.title')}
            </div>
            <select
              value={campaignId ?? ''}
              onChange={(event) => {
                setSelected(event.target.value);
              }}
              aria-label={t('neurocomment.campaigns.select')}
              className={`${INPUT} mb-3`}
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
            <input
              value={name}
              onChange={(event) => {
                setName(event.target.value);
              }}
              placeholder={t('neurocomment.campaigns.name')}
              aria-label={t('neurocomment.campaigns.name')}
              className={`${INPUT} mb-2`}
            />
            <textarea
              value={prompt}
              onChange={(event) => {
                setPrompt(event.target.value);
              }}
              placeholder={t('neurocomment.campaigns.prompt')}
              aria-label={t('neurocomment.campaigns.prompt')}
              rows={2}
              className={`${INPUT} mb-2 resize-none`}
            />
            <button
              type="button"
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
              className="w-full rounded-full bg-primary px-4 py-[9px] text-[13px] font-medium text-white disabled:opacity-50"
            >
              {t('neurocomment.campaigns.create')}
            </button>
          </div>

          <div className={CARD}>
            <div className="mb-3 text-[13px] font-semibold">
              {t('neurocomment.runtime.listener')}
            </div>
            <select
              value={listener}
              onChange={(event) => {
                setListener(event.target.value);
              }}
              aria-label={t('neurocomment.runtime.listener')}
              className={INPUT}
            >
              <option value="">—</option>
              {accountOptions.map((account) => (
                <option key={account.account_id} value={account.account_id}>
                  {account.account_id}
                </option>
              ))}
            </select>
          </div>

          {campaignId !== null ? (
            <div className={CARD}>
              <div className="mb-3 text-[13px] font-semibold">
                {t('neurocomment.channels.title')}
              </div>
              <div className="mb-3 flex gap-2">
                <input
                  value={channel}
                  onChange={(event) => {
                    setChannel(event.target.value);
                  }}
                  placeholder={t('neurocomment.setup.channel')}
                  aria-label={t('neurocomment.setup.channel')}
                  className={`${INPUT} flex-1`}
                />
                <button
                  type="button"
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
                  className="shrink-0 rounded-full border border-line bg-white px-3 py-[9px] text-[12px] font-medium disabled:opacity-50"
                >
                  {t('neurocomment.setup.linkChannel')}
                </button>
              </div>
              <div className="flex gap-2">
                <select
                  value={assignee}
                  onChange={(event) => {
                    setAssignee(event.target.value);
                  }}
                  aria-label={t('neurocomment.setup.assign')}
                  className={`${INPUT} flex-1`}
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
                  className="shrink-0 rounded-full border border-line bg-white px-3 py-[9px] text-[12px] font-medium disabled:opacity-50"
                >
                  {t('neurocomment.setup.assignAccount')}
                </button>
              </div>
            </div>
          ) : null}
        </div>

        {/* RIGHT: pipeline engine + board */}
        <div className="flex flex-col gap-4">
          <div className="rounded-2xl border border-[#e4ecfa] bg-[#f7faff] p-[16px_18px]">
            <div className="mb-[14px] flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-[10px]">
                <span className="text-[14px] font-semibold">
                  {t('neurocomment.pipeline.title')}
                </span>
                <span
                  className={`rounded-full px-[10px] py-[3px] text-[11px] font-semibold ${running ? 'tb-pulse bg-success-tint text-success' : 'bg-[#eeedea] text-ink-muted'}`}
                >
                  {running
                    ? t('neurocomment.pipeline.running')
                    : t('neurocomment.pipeline.stopped')}
                </span>
              </div>
              <button
                type="button"
                disabled={!running && !listener}
                onClick={() => {
                  if (running) {
                    stop.mutate({}, { onSettled: invalidate });
                  } else {
                    start.mutate(
                      { body: { listener_account_id: listener } },
                      { onSettled: invalidate },
                    );
                  }
                }}
                className={`flex items-center gap-[7px] rounded-full px-4 py-2 text-[13px] font-semibold text-white disabled:opacity-50 ${running ? 'bg-ink' : 'bg-primary'}`}
              >
                {running ? (
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor">
                    <rect x="6" y="5" width="4" height="14" rx="1.5" />
                    <rect x="14" y="5" width="4" height="14" rx="1.5" />
                  </svg>
                ) : (
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor">
                    <path d="M7 5.5v13a1 1 0 0 0 1.5.87l11-6.5a1 1 0 0 0 0-1.74l-11-6.5A1 1 0 0 0 7 5.5z" />
                  </svg>
                )}
                {running ? t('neurocomment.runtime.stop') : t('neurocomment.runtime.start')}
              </button>
            </div>

            {/* 6-cell pipeline */}
            <div className="relative mx-2 mb-3 flex items-center justify-between">
              <div className="absolute inset-x-[8px] top-1/2 h-[2px] -translate-y-1/2 rounded bg-[#dce2ec]" />
              {STAGES.map((stage, index) => (
                <div key={stage} className="relative z-10 flex h-4 w-4 items-center justify-center">
                  {index < activeCell ? (
                    <span className="flex h-4 w-4 items-center justify-center rounded-full bg-success">
                      <svg
                        width="10"
                        height="10"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="#fff"
                        strokeWidth="3.4"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      >
                        <path d="M20 6 9 17l-5-5" />
                      </svg>
                    </span>
                  ) : index === activeCell ? (
                    <span className="tb-livedot h-[11px] w-[11px] rounded-full bg-primary" />
                  ) : (
                    <span className="h-[9px] w-[9px] rounded-full border-[1.5px] border-[#c9d2e0] bg-white" />
                  )}
                </div>
              ))}
            </div>
            <div className="mb-3 flex justify-between">
              {STAGES.map((stage, index) => (
                <span
                  key={stage}
                  className={`w-[88px] text-center text-[11px] ${index === activeCell ? 'font-semibold text-primary' : 'text-ink-subtle'}`}
                >
                  {t(`neurocomment.stage.${stage}`)}
                </span>
              ))}
            </div>

            <div className="mb-[14px] flex items-center gap-[9px] rounded-[10px] border border-[#dce7fb] bg-[#eef4ff] px-[13px] py-[10px]">
              <span className="pl-pulse h-2 w-2 shrink-0 rounded-full bg-primary" />
              <span className="tb-pulse text-[12.5px] font-medium text-primary">
                {running
                  ? t('neurocomment.pipeline.descRunning')
                  : t('neurocomment.pipeline.descStopped')}
              </span>
            </div>

            <div className="grid grid-cols-4 gap-px overflow-hidden rounded-xl border border-[#e4ecfa] bg-[#e4ecfa]">
              {stats.map((stat) => (
                <div key={stat.label} className="bg-white px-4 py-[14px]">
                  <div className="text-[20px] font-bold tabular-nums" style={{ color: stat.color }}>
                    {stat.value}
                  </div>
                  <div className="mt-[2px] text-[11px] text-ink-subtle">{stat.label}</div>
                </div>
              ))}
            </div>
          </div>

          {board.data ? <NeurocommentBoard board={board.data} /> : null}
        </div>
      </div>
    </div>
  );
}
