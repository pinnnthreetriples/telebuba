import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  addWarmingChannelsMutation,
  removeWarmingChannelMutation,
  startWarmingMutation,
  stopWarmingMutation,
  warmingBoardQueryOptions,
} from '@/entities/warming';
import { useLogEventStream } from '@/shared/lib';
import { WarmingBoard } from '@/widgets/warming-board';

// SSE drives live board updates; this poll is just the fallback safety net.
const FALLBACK_POLL_MS = 30000;

function mono(id: string): string {
  return id.replace(/\D/g, '').slice(-2) || id.slice(0, 2).toUpperCase();
}

function Counter({ value, label, cls }: { value: number; label: string; cls: string }) {
  return (
    <div className="text-right">
      <div className={`text-[19px] font-bold ${cls}`}>{value}</div>
      <div className="text-[11px] text-ink-muted">{label}</div>
    </div>
  );
}

export function WarmingPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [busyId, setBusyId] = useState<string | null>(null);
  const [channelInput, setChannelInput] = useState('');

  const { data, isPending, isError } = useQuery({
    ...warmingBoardQueryOptions(),
    refetchInterval: FALLBACK_POLL_MS,
  });

  const invalidate = () => {
    void queryClient.invalidateQueries();
  };
  // Live status: any runtime event refreshes the board (event-driven, not timed).
  useLogEventStream(invalidate);
  const start = useMutation(startWarmingMutation());
  const stop = useMutation(stopWarmingMutation());
  const addChannels = useMutation(addWarmingChannelsMutation());
  const removeChannel = useMutation(removeWarmingChannelMutation());

  const runOnAccount = (mutation: typeof start | typeof stop, accountId: string) => {
    setBusyId(accountId);
    mutation.mutate(
      { body: { account_id: accountId } },
      {
        onSettled: () => {
          setBusyId(null);
          invalidate();
        },
      },
    );
  };

  if (isPending) return <p className="text-ink-muted">{t('warming.loading')}</p>;
  if (isError) {
    return (
      <p role="alert" className="text-danger">
        {t('warming.error')}
      </p>
    );
  }

  const idle = data.idle ?? [];
  const warming = data.warming ?? [];
  const channels = data.channels.channels ?? [];
  const errors = [...idle, ...warming].filter((a) => a.state === 'error').length;
  const poolOn = warming.length > 0;

  return (
    <div className="tb-fadeup">
      <div className="mb-[18px] flex flex-wrap items-center justify-between gap-4">
        <h1 className="m-0 text-[22px] font-bold tracking-[-0.02em]">{t('warming.titleFull')}</h1>
        <div className="flex items-center gap-[18px]">
          <div className="flex gap-4">
            <Counter
              value={warming.length}
              label={t('warming.counter.warming')}
              cls="text-primary"
            />
            <Counter value={idle.length} label={t('warming.counter.ready')} cls="text-ink" />
            <Counter value={errors} label={t('warming.counter.errors')} cls="text-danger" />
          </div>
          <button
            type="button"
            onClick={() => {
              (poolOn ? warming : idle).forEach((a) => {
                runOnAccount(poolOn ? stop : start, a.account_id);
              });
            }}
            className={`flex items-center gap-[7px] rounded-full px-4 py-2 text-[13px] font-semibold text-white ${poolOn ? 'bg-ink' : 'bg-primary'}`}
          >
            {poolOn ? (
              <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor">
                <rect x="6" y="5" width="4" height="14" rx="1.5" />
                <rect x="14" y="5" width="4" height="14" rx="1.5" />
              </svg>
            ) : (
              <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor">
                <path d="M7 5.5v13a1 1 0 0 0 1.5.87l11-6.5a1 1 0 0 0 0-1.74l-11-6.5A1 1 0 0 0 7 5.5z" />
              </svg>
            )}
            {poolOn ? t('warming.pool.stop') : t('warming.pool.start')}
          </button>
        </div>
      </div>

      <div className="grid grid-cols-[340px_1fr] items-start gap-4">
        <div className="flex flex-col gap-4">
          <div className="rounded-2xl border border-line bg-white p-[14px]">
            <div className="mb-3 flex items-center justify-between">
              <span className="text-[13px] font-semibold">{t('warming.ready.title')}</span>
              <span className="rounded-full border border-line bg-white px-2 py-[2px] text-[11px] text-ink-subtle">
                {idle.length}
              </span>
            </div>
            <div className="flex flex-col gap-2">
              {idle.length === 0 ? (
                <div className="py-[26px] text-center text-[12px] text-ink-subtle">
                  {t('warming.ready.empty')}
                </div>
              ) : (
                idle.map((account) => (
                  <div
                    key={account.account_id}
                    className="flex items-center gap-[10px] rounded-xl border border-line bg-white px-3 py-[11px]"
                  >
                    <div className="flex h-[30px] w-[30px] shrink-0 items-center justify-center rounded-full bg-primary-tint text-[12px] font-semibold text-primary">
                      {mono(account.account_id)}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-[13px] font-semibold">{account.account_id}</div>
                      <div className="mt-[2px] flex items-center gap-[6px] text-ink-subtle">
                        <svg
                          width="13"
                          height="13"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                        >
                          <path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z" />
                          <path d="m9 12 2 2 4-4" />
                        </svg>
                        <span className="text-[11px] font-semibold">
                          {account.trust_score ?? '—'}
                        </span>
                      </div>
                    </div>
                    <button
                      type="button"
                      disabled={busyId === account.account_id}
                      onClick={() => {
                        runOnAccount(start, account.account_id);
                      }}
                      className="rounded-full bg-primary px-[14px] py-[6px] text-[12px] font-medium text-white disabled:opacity-50"
                    >
                      {t('warming.ready.start')}
                    </button>
                  </div>
                ))
              )}
            </div>
          </div>

          <div className="rounded-[13px] border border-line bg-white p-4">
            <div className="mb-[11px] text-[13px] font-semibold">{t('warming.channels.title')}</div>
            <div className="flex flex-wrap gap-[7px]">
              {channels.map((channel) => (
                <span
                  key={channel.channel}
                  className="inline-flex items-center gap-[6px] rounded-full border border-line bg-[#f4f3f0] px-[11px] py-[5px] text-[12px] text-[#3a3a3a]"
                >
                  {channel.channel}
                  <button
                    type="button"
                    aria-label={t('warming.channels.remove')}
                    onClick={() => {
                      removeChannel.mutate(
                        { body: { channel: channel.channel } },
                        { onSettled: invalidate },
                      );
                    }}
                    className="text-[14px] leading-none text-ink-subtle"
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
            <div className="mt-3 flex gap-2">
              <input
                value={channelInput}
                onChange={(event) => {
                  setChannelInput(event.target.value);
                }}
                placeholder={t('warming.channels.placeholder')}
                aria-label={t('warming.channels.placeholder')}
                className="tb-time flex-1 rounded-full border border-line px-3 py-[7px] text-[12px] outline-none"
              />
              <button
                type="button"
                disabled={!channelInput.trim()}
                onClick={() => {
                  addChannels.mutate(
                    { body: { raw: channelInput } },
                    {
                      onSettled: () => {
                        setChannelInput('');
                        invalidate();
                      },
                    },
                  );
                }}
                className="rounded-full bg-primary px-4 py-[7px] text-[12px] font-medium text-white disabled:opacity-50"
              >
                {t('warming.channels.add')}
              </button>
            </div>
          </div>

          <div className="rounded-2xl border border-line bg-[#f6f5f2] p-4">
            <div className="text-[13px] font-semibold">{t('warming.howto.title')}</div>
            <div className="mb-3 mt-2 text-[11px] leading-[1.5] text-ink-subtle">
              {t('warming.howto.hint')}
            </div>
            <div className="grid grid-cols-2 gap-x-[22px] gap-y-[11px]">
              {[0, 1, 2, 3, 4, 5].map((index) => (
                <div key={index} className="flex items-start gap-[9px]">
                  <span className="mt-px flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-full bg-primary text-[10px] font-semibold text-white">
                    {index + 1}
                  </span>
                  <span className="text-[12px] leading-[1.45] text-[#5c5c5c]">
                    {t(`warming.howto.steps.${String(index)}`)}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>

        <WarmingBoard
          warming={warming}
          onStop={(id) => {
            runOnAccount(stop, id);
          }}
          busyId={busyId}
        />
      </div>
    </div>
  );
}
