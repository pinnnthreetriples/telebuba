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
import type { WarmingAccountState } from '@/shared/api';
import { useLogEventStream } from '@/shared/lib';
import { CollapsibleCard } from '@/shared/ui';
import { WarmDaysModal, WarmingBoard } from '@/widgets/warming-board';

// SSE drives live board updates; this poll is just the fallback safety net.
const FALLBACK_POLL_MS = 30000;

// ponytail: mock graduated accounts until the board exposes a "warmed" list.
const WARMED_MOCK = [
  { id: 'wm-1', phone: '+79261112233', cc: 'ru', proxy: 'SOCKS5', days: '14 / 14 дней', trust: 88 },
  {
    id: 'wm-2',
    phone: '+447700900123',
    cc: 'gb',
    proxy: 'SOCKS5',
    days: '14 / 14 дней',
    trust: 91,
  },
] as const;

function mono(id: string): string {
  return id.replace(/\D/g, '').slice(-2) || id.slice(0, 2).toUpperCase();
}

// Trust 3-tier colour (design): healthy / watch / risk.
function trustColor(trust: number): string {
  if (trust >= 70) return '#12a150';
  if (trust >= 45) return '#e08700';
  return '#e5372a';
}

// Design-first derivations for fields the board read model doesn't expose yet
// on idle accounts: a stable pseudo value keyed off the account id so the meta
// row (trust / flag / proxy-type) renders per the spec.
const FLAGS = ['ru', 'gb', 'de', 'us', 'fr', 'nl'] as const;
const PROXY_TYPES = ['SOCKS5', 'HTTP', 'MTProto'] as const;

function hash(id: string): number {
  let h = 0;
  for (const ch of id) h = (h * 31 + ch.charCodeAt(0)) | 0;
  return Math.abs(h);
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
  const [addingChannel, setAddingChannel] = useState(false);
  const [warmDaysFor, setWarmDaysFor] = useState<WarmingAccountState | null>(null);

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

  const cancelAddChannel = () => {
    setAddingChannel(false);
    setChannelInput('');
  };
  const addChannel = () => {
    if (!channelInput.trim()) return;
    addChannels.mutate(
      { body: { raw: channelInput } },
      {
        onSettled: () => {
          cancelAddChannel();
          invalidate();
        },
      },
    );
  };

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
                idle.map((account) => {
                  const seed = hash(account.account_id);
                  const trust = account.trust_score ?? 45 + (seed % 50);
                  const tColor = trustColor(trust);
                  const cc = (
                    account.phone_country ??
                    FLAGS[seed % FLAGS.length] ??
                    'ru'
                  ).toLowerCase();
                  const ptype = PROXY_TYPES[seed % PROXY_TYPES.length];
                  const available = account.health !== 'fail' && account.state !== 'error';
                  return (
                    <div
                      key={account.account_id}
                      className="flex items-center gap-[10px] rounded-xl border border-line bg-white px-3 py-[11px]"
                    >
                      <div className="flex h-[30px] w-[30px] shrink-0 items-center justify-center rounded-full bg-primary-tint text-[12px] font-semibold text-primary">
                        {mono(account.account_id)}
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-[13px] font-semibold">
                          {account.label ?? account.account_id}
                        </div>
                        <div className="mt-[2px] flex items-center gap-[6px]">
                          <svg
                            width="13"
                            height="13"
                            viewBox="0 0 24 24"
                            fill="none"
                            stroke={tColor}
                            strokeWidth="2"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            className="shrink-0"
                          >
                            <path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z" />
                            <path d="m9 12 2 2 4-4" />
                          </svg>
                          <span className="text-[11px] font-semibold" style={{ color: tColor }}>
                            {trust}
                          </span>
                          <span className="text-[11px] text-line-strong">·</span>
                          <span
                            className={`fi fi-${cc} h-[11px] w-[15px] rounded-[2px] shadow-[0_0_0_1px_rgba(0,0,0,0.07)]`}
                          />
                          <span className="text-[11px] text-[#9a9893]">{ptype}</span>
                        </div>
                      </div>
                      <button
                        type="button"
                        disabled={!available || busyId === account.account_id}
                        onClick={() => {
                          setWarmDaysFor(account);
                        }}
                        className={`rounded-full px-[14px] py-[6px] text-[12px] font-medium disabled:opacity-50 ${available ? 'bg-primary text-white' : 'cursor-not-allowed bg-track text-ink-subtle'}`}
                      >
                        {available ? t('warming.ready.start') : t('warming.ready.unavailable')}
                      </button>
                    </div>
                  );
                })
              )}
            </div>
          </div>

          <CollapsibleCard
            wrapperClassName="rounded-[13px] border border-line bg-white"
            header={
              <span className="text-[13px] font-semibold">{t('warming.channels.title')}</span>
            }
            label={t('warming.channels.title')}
          >
            <div className="mb-[11px] text-[11px] leading-[1.4] text-[#9a9893]">
              {t('warming.channels.hint')}
            </div>
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
                    className="text-[14px] leading-none text-[#b5b3ae]"
                  >
                    ×
                  </button>
                </span>
              ))}
              {addingChannel ? (
                <span className="inline-flex items-center gap-1 rounded-full border border-primary bg-white py-[3px] pl-[11px] pr-1">
                  <input
                    autoFocus
                    value={channelInput}
                    onChange={(event) => {
                      setChannelInput(event.target.value);
                    }}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') addChannel();
                      if (event.key === 'Escape') cancelAddChannel();
                    }}
                    placeholder={t('warming.channels.placeholderSingle')}
                    aria-label={t('warming.channels.placeholderSingle')}
                    className="w-[150px] border-none bg-transparent text-[12px] outline-none"
                  />
                  <button
                    type="button"
                    title={t('warming.channels.add')}
                    aria-label={t('warming.channels.add')}
                    disabled={!channelInput.trim()}
                    onClick={addChannel}
                    className="flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-full bg-primary text-white disabled:opacity-50"
                  >
                    <svg
                      width="12"
                      height="12"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="#fff"
                      strokeWidth="3"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    >
                      <path d="M20 6 9 17l-5-5" />
                    </svg>
                  </button>
                  <button
                    type="button"
                    title={t('warming.channels.cancel')}
                    aria-label={t('warming.channels.cancel')}
                    onClick={cancelAddChannel}
                    className="flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-full bg-[#f0eeeb] text-[14px] leading-none text-ink-muted"
                  >
                    ×
                  </button>
                </span>
              ) : (
                <button
                  type="button"
                  onClick={() => {
                    setAddingChannel(true);
                  }}
                  className="inline-flex items-center gap-1 rounded-full border border-dashed border-line-strong bg-white px-[11px] py-[5px] text-[12px] text-ink-muted hover:border-primary hover:text-primary"
                >
                  {t('warming.channels.addPill')}
                </button>
              )}
            </div>
          </CollapsibleCard>

          <CollapsibleCard
            label={t('warming.warmed.title')}
            header={
              <>
                <span className="flex h-[26px] w-[26px] items-center justify-center rounded-lg bg-success-tint">
                  <svg
                    width="15"
                    height="15"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="#12a150"
                    strokeWidth="2.6"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <path d="M20 6 9 17l-5-5" />
                  </svg>
                </span>
                <span className="text-[13.5px] font-bold">{t('warming.warmed.title')}</span>
                <span className="rounded-full bg-success-tint px-2 py-[2px] text-[10.5px] font-bold text-success">
                  {WARMED_MOCK.length}
                </span>
              </>
            }
          >
            <div className="flex flex-col gap-3">
              {WARMED_MOCK.map((acc) => (
                <div key={acc.id} className="rounded-[14px] border border-line p-[14px]">
                  <div className="flex items-start gap-[11px]">
                    <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary-tint text-[11.5px] font-bold text-primary ring-2 ring-success">
                      {acc.phone.slice(-2)}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="text-[14px] font-bold leading-tight">{acc.phone}</div>
                      <div className="mt-[5px] flex items-center gap-[6px]">
                        <span className={`fi fi-${acc.cc} h-[10px] w-[14px] rounded-[2px]`} />
                        <span className="text-[11.5px] text-ink-subtle">{acc.proxy}</span>
                      </div>
                    </div>
                    <span className="inline-flex items-center gap-1 rounded-full bg-success-tint px-[9px] py-[3px] text-[9.5px] font-bold tracking-[0.03em] text-success">
                      <svg
                        width="9"
                        height="9"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="#12a150"
                        strokeWidth="3.4"
                      >
                        <path d="M20 6 9 17l-5-5" />
                      </svg>
                      {t('warming.warmed.badge')}
                    </span>
                  </div>
                  <div className="mt-[13px] flex items-center rounded-[10px] bg-[#f7f6f4] px-[14px] py-[10px]">
                    <div className="flex-1">
                      <div className="text-[10.5px] text-ink-subtle">
                        {t('warming.warmed.days')}
                      </div>
                      <div className="text-[13px] font-bold">{acc.days}</div>
                    </div>
                    <span className="h-[26px] w-px bg-[#e4e2de]" />
                    <div className="flex-1 pl-[14px]">
                      <div className="text-[10.5px] text-ink-subtle">
                        {t('warming.warmed.trust')}
                      </div>
                      <div className="text-[13px] font-bold text-success">{acc.trust}</div>
                    </div>
                  </div>
                  <div className="mt-[13px] flex items-center gap-[9px]">
                    <button
                      type="button"
                      className="flex flex-1 items-center justify-center gap-[6px] rounded-full bg-ink px-[14px] py-[10px] text-[12.5px] font-semibold text-white"
                    >
                      {t('warming.warmed.toNeuro')}
                      <svg
                        width="13"
                        height="13"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2.2"
                      >
                        <path d="M5 12h14M13 6l6 6-6 6" />
                      </svg>
                    </button>
                    <button
                      type="button"
                      title={t('warming.warmed.backToWarm')}
                      aria-label={t('warming.warmed.backToWarm')}
                      className="flex h-[38px] w-[38px] shrink-0 items-center justify-center rounded-full border border-line-input bg-white text-ink-muted"
                    >
                      <svg
                        width="15"
                        height="15"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2"
                      >
                        <path d="M3 12a9 9 0 1 0 3-6.7L3 8" />
                        <path d="M3 3v5h5" />
                      </svg>
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </CollapsibleCard>

          <CollapsibleCard
            label={t('warming.howto.title')}
            wrapperClassName="rounded-2xl border border-line bg-[#f6f5f2]"
            header={<span className="text-[13px] font-semibold">{t('warming.howto.title')}</span>}
          >
            <div className="mb-[13px] text-[11px] leading-[1.4] text-[#9a9893]">
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
          </CollapsibleCard>
        </div>

        <WarmingBoard
          warming={warming}
          onStop={(id) => {
            runOnAccount(stop, id);
          }}
          busyId={busyId}
        />
      </div>

      {warmDaysFor ? (
        <WarmDaysModal
          phone={warmDaysFor.label ?? warmDaysFor.account_id}
          onClose={() => {
            setWarmDaysFor(null);
          }}
          onConfirm={() => {
            runOnAccount(start, warmDaysFor.account_id);
          }}
        />
      ) : null}
    </div>
  );
}
