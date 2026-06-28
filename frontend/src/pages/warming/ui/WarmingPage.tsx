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
import { WarmingBoard } from '@/widgets/warming-board';

const POLL_MS = 4000;

export function WarmingPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [busyId, setBusyId] = useState<string | null>(null);
  const [channelInput, setChannelInput] = useState('');

  const { data, isPending, isError } = useQuery({
    ...warmingBoardQueryOptions(),
    refetchInterval: POLL_MS,
  });

  const invalidate = () => {
    void queryClient.invalidateQueries();
  };
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

  if (isPending) return <p className="p-8 text-ink-muted">{t('warming.loading')}</p>;
  if (isError) {
    return (
      <p role="alert" className="p-8 text-danger">
        {t('warming.error')}
      </p>
    );
  }

  return (
    <main className="mx-auto max-w-5xl space-y-6 p-8">
      <header className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">{t('warming.title')}</h1>
        <span className="text-sm text-ink-muted">
          {t('warming.summary', {
            active: data.summary?.active ?? 0,
            total: data.summary?.total ?? 0,
          })}
        </span>
      </header>

      <WarmingBoard
        idle={data.idle ?? []}
        warming={data.warming ?? []}
        onStart={(id) => {
          runOnAccount(start, id);
        }}
        onStop={(id) => {
          runOnAccount(stop, id);
        }}
        busyId={busyId}
      />

      <section className="rounded-md border border-line bg-surface p-4">
        <h2 className="mb-3 text-sm font-medium text-ink-muted">{t('warming.channels.title')}</h2>
        <div className="mb-3 flex gap-2">
          <input
            value={channelInput}
            onChange={(event) => {
              setChannelInput(event.target.value);
            }}
            placeholder={t('warming.channels.placeholder')}
            aria-label={t('warming.channels.placeholder')}
            className="flex-1 rounded-md border border-line px-3 py-2 text-sm"
          />
          <button
            type="button"
            className="rounded-md bg-primary px-3 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
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
          >
            {t('warming.channels.add')}
          </button>
        </div>
        <ul className="space-y-1">
          {(data.channels.channels ?? []).map((channel) => (
            <li
              key={channel.channel}
              className="flex items-center justify-between rounded px-2 py-1 text-sm hover:bg-canvas"
            >
              <span className="font-mono">{channel.channel}</span>
              <button
                type="button"
                className="text-xs text-danger hover:underline"
                onClick={() => {
                  removeChannel.mutate(
                    { body: { channel: channel.channel } },
                    { onSettled: invalidate },
                  );
                }}
              >
                {t('warming.channels.remove')}
              </button>
            </li>
          ))}
        </ul>
      </section>
    </main>
  );
}
