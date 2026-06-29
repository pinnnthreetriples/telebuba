import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { accountsQueryOptions } from '@/entities/account';
import {
  assignCampaignAccountMutation,
  CampaignDeleteModal,
  CampaignPromptModal,
  campaignsQueryOptions,
  createCampaignMutation,
  CreateCampaignModal,
  linkCampaignChannelMutation,
  ListenerEditModal,
  NeuroAccountsModal,
  neurocommentBoardQueryOptions,
  neurocommentRuntimeQueryOptions,
  startNeurocommentMutation,
  stopNeurocommentMutation,
} from '@/entities/campaign';
import type { NeurocommentCampaign } from '@/shared/api';
import { useLogEventStream } from '@/shared/lib';
import { CollapsibleCard } from '@/shared/ui';
import { NeurocommentBoard } from '@/widgets/neurocomment-board';

// SSE drives live runtime/board updates; this poll is just the fallback net.
const FALLBACK_POLL_MS = 30000;
const STAGES = ['listen', 'detect', 'filter', 'generate', 'solve', 'comment'] as const;
const HOW_STEPS = [0, 1, 2, 3] as const;

// ponytail: mock activity feed until the runtime streams real log events here.
const NEURO_LOG = [
  { time: '12:00:09', msg: 'Комментарий отправлен ✓', color: '#7be0a6' },
  { time: '12:00:05', msg: 'Gemini: генерация комментария…', color: '#ffd27f' },
  { time: '12:00:03', msg: 'Новый пост в @crypto_daily', color: '#5ba3ff' },
  { time: '11:59:40', msg: 'Капча решена', color: '#7be0a6' },
  { time: '11:58:12', msg: 'Слушатель подключён', color: '#9aa0aa' },
] as const;

// ponytail: per-account captcha queue isn't on the read model yet — mock until
// the runtime exposes pending bot-checks.
const CAPTCHA_QUEUE = [
  { id: 'cap-1', acc: '+79261112233', channel: '@crypto_daily', time: '2 мин назад' },
  { id: 'cap-2', acc: '+447700900123', channel: '@web3news', time: '5 мин назад' },
] as const;

const STATUS_COLOR = {
  active: '#12a150',
  paused: '#c47d12',
  archived: '#74726e',
} as const;

function initials(value: string): string {
  return value.replace(/\D/g, '').slice(-2) || value.slice(0, 2).toUpperCase();
}

// The design's stat odometer: each digit is a 0–9 column that rolls into place
// (translateY, .9s cubic-bezier(.16,1,.3,1)) shortly after the screen mounts —
// the reference's count-up. Matches Telebuba.dc.html L732-736.
function Odometer({ value, color }: { value: number; color: string }) {
  const [armed, setArmed] = useState(false);
  useEffect(() => {
    const id = window.setTimeout(() => {
      setArmed(true);
    }, 80);
    return () => {
      window.clearTimeout(id);
    };
  }, []);
  return (
    <div
      className="inline-flex h-[1.1em] overflow-hidden text-[20px] font-bold leading-[1.1] tabular-nums"
      style={{ color }}
    >
      {String(value)
        .split('')
        .map((ch, index) => (
          <span key={index} className="inline-block h-[1.1em] overflow-hidden">
            <span
              className="flex flex-col transition-transform duration-[900ms] [transition-timing-function:cubic-bezier(.16,1,.3,1)]"
              style={{ transform: `translateY(${(armed ? -(Number(ch) * 1.1) : 0).toFixed(2)}em)` }}
            >
              {Array.from({ length: 10 }, (_, digit) => (
                <span key={digit} className="h-[1.1em] leading-[1.1em]">
                  {digit}
                </span>
              ))}
            </span>
          </span>
        ))}
    </div>
  );
}

// Slide-in action layer: the surface translates left on hover to reveal the
// pinned action buttons (the design's lsnSnap/campSnap GSAP, done with CSS).
function SurfHover({
  actions,
  surface,
  shift,
  surfaceId,
}: {
  actions: React.ReactNode;
  surface: React.ReactNode;
  shift: number;
  surfaceId?: string;
}) {
  return (
    <div className="group relative overflow-hidden rounded-[11px]">
      <div className="absolute inset-0 flex items-stretch justify-end rounded-[11px] bg-[#f1efed]">
        {actions}
      </div>
      <div
        id={surfaceId}
        className="relative rounded-[11px] transition-transform duration-[440ms] [transition-timing-function:cubic-bezier(.16,1,.3,1)] [will-change:transform] group-hover:-translate-x-[var(--shift)]"
        style={{ ['--shift' as string]: `${String(shift)}px` }}
      >
        {surface}
      </div>
    </div>
  );
}

export function NeurocommentPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const invalidate = () => {
    void queryClient.invalidateQueries();
  };
  useLogEventStream(invalidate);

  const [selected, setSelected] = useState<string | null>(null);
  const [listener, setListener] = useState('');
  const [listenerOpen, setListenerOpen] = useState(false);
  const [captchaSolve, setCaptchaSolve] = useState(true);
  const [channelInput, setChannelInput] = useState('');
  const [addingChannel, setAddingChannel] = useState(false);

  // Modal open state.
  const [showAccounts, setShowAccounts] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [showListenerEdit, setShowListenerEdit] = useState(false);
  const [promptFor, setPromptFor] = useState<NeurocommentCampaign | null>(null);
  const [deleteFor, setDeleteFor] = useState<NeurocommentCampaign | null>(null);

  const campaigns = useQuery(campaignsQueryOptions());
  const accounts = useQuery(accountsQueryOptions());
  const runtime = useQuery({
    ...neurocommentRuntimeQueryOptions(),
    refetchInterval: FALLBACK_POLL_MS,
  });

  const campaignList = campaigns.data?.campaigns ?? [];
  const campaignId = selected ?? campaignList[0]?.campaign_id ?? null;
  const activeCampaign = campaignList.find((c) => c.campaign_id === campaignId) ?? null;

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
  const listenerId = runtime.data?.listener_account_id ?? listener;
  const boardAccounts = board.data?.accounts ?? [];
  const boardChannels = board.data?.channels ?? [];

  // Decorative pipeline position: a mid-flight look while running, idle when off.
  const activeCell = running ? 2 : -1;
  const greenPct = activeCell > 0 ? (activeCell / (STAGES.length - 1)) * 100 : 0;
  const bluePct = activeCell >= 0 ? (activeCell / (STAGES.length - 1)) * 100 : 0;

  // ponytail: no idle/work split on the neurocomment read model yet; treat
  // accounts not yet linked to any board channel as idle (design-first).
  const idleCount = Math.max(accountOptions.length - boardAccounts.length, 0);

  const stats: { label: string; value: number; color: string }[] = [
    { label: t('neurocomment.stat.campaigns'), value: campaignList.length, color: '#0b0b0c' },
    {
      label: t('neurocomment.stat.channels'),
      value: runtime.data?.active_channels ?? boardChannels.length,
      color: '#0066ff',
    },
    { label: t('neurocomment.stat.accounts'), value: boardAccounts.length, color: '#0b0b0c' },
    {
      label: t('neurocomment.stat.comments'),
      value: boardAccounts.reduce((sum, a) => sum + a.comments_today, 0),
      color: '#12a150',
    },
  ];

  const toggleRuntime = () => {
    if (running) {
      stop.mutate({}, { onSettled: invalidate });
    } else if (listenerId) {
      start.mutate({ body: { listener_account_id: listenerId } }, { onSettled: invalidate });
    }
  };

  const addChannel = () => {
    const value = channelInput.trim();
    if (!value || campaignId === null) return;
    linkChannel.mutate(
      { path: { campaign_id: campaignId }, body: { channel: value } },
      {
        onSettled: () => {
          setChannelInput('');
          setAddingChannel(false);
          invalidate();
        },
      },
    );
  };

  return (
    <div className="tb-fadeup">
      <h1 className="m-0 mb-[18px] text-[22px] font-bold tracking-[-0.02em]">
        {t('neurocomment.title')}
      </h1>

      <div className="grid grid-cols-[340px_1fr] items-start gap-4">
        {/* RIGHT column */}
        <div className="col-start-2 row-start-1 flex flex-col gap-4">
          {/* pipeline */}
          <div className="rounded-2xl border border-[#e4ecfa] bg-[#f7faff] px-[18px] py-4 text-ink">
            <div className="mb-[14px] flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-[10px]">
                <span className="text-[14px] font-semibold">
                  {t('neurocomment.pipeline.title')}
                </span>
                <span
                  className={`rounded-full px-[10px] py-[3px] text-[11px] font-semibold ${running ? 'tb-pulse bg-success-tint text-success' : 'bg-track text-ink-muted'}`}
                >
                  {running
                    ? t('neurocomment.pipeline.running')
                    : t('neurocomment.pipeline.stopped')}
                </span>
              </div>
              <button
                type="button"
                disabled={!running && !listenerId}
                onClick={toggleRuntime}
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

            {/* stepper with dual progress fill */}
            <div className="relative mx-2 mb-3 h-6">
              <div className="absolute inset-x-[13px] top-[11px] h-[2px] overflow-hidden rounded-[2px] bg-[#dce2ec]">
                <div
                  className="absolute left-0 top-0 h-full rounded-[2px] bg-success transition-[width] duration-[900ms] [transition-timing-function:cubic-bezier(.16,1,.3,1)]"
                  style={{ width: `${String(greenPct)}%` }}
                />
                <div
                  className="absolute left-0 top-0 h-full rounded-[2px] bg-primary transition-[width] duration-[900ms] [transition-timing-function:cubic-bezier(.16,1,.3,1)]"
                  style={{ width: `${String(bluePct)}%` }}
                />
              </div>
              <div className="relative flex h-6 items-center justify-between">
                {STAGES.map((stage, index) => (
                  <div
                    key={stage}
                    className="relative flex h-4 w-4 shrink-0 items-center justify-center"
                  >
                    {index < activeCell ? (
                      <span className="tb-pop flex h-4 w-4 items-center justify-center rounded-full bg-success">
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
            </div>
            <div className="mb-3 flex justify-between px-px">
              {STAGES.map((stage, index) => (
                <span
                  key={stage}
                  className={`w-[88px] whitespace-nowrap text-center text-[11px] ${
                    index < activeCell
                      ? 'font-medium text-success'
                      : index === activeCell
                        ? 'font-semibold text-primary'
                        : 'text-ink-subtle'
                  }`}
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
                  <Odometer value={stat.value} color={stat.color} />
                  <div className="mt-[2px] text-[11px] text-ink-subtle">{stat.label}</div>
                </div>
              ))}
            </div>
          </div>

          {board.data ? (
            <NeurocommentBoard
              board={board.data}
              accountsCount={boardAccounts.length}
              onOpenAccounts={() => {
                setShowAccounts(true);
              }}
            />
          ) : null}

          {/* terminal */}
          <CollapsibleCard
            defaultOpen
            label={t('neurocomment.log.title')}
            headerClassName="px-4 py-[13px]"
            bodyClassName="px-[14px] pb-[14px]"
            header={
              <>
                <span className="pl-pulse h-[7px] w-[7px] shrink-0 rounded-full bg-primary" />
                <span className="text-[13px] font-semibold">{t('neurocomment.log.title')}</span>
                <span className="rounded-full bg-[#f2f1ee] px-2 py-[2px] text-[11px] font-medium text-ink-muted">
                  {NEURO_LOG.length}
                </span>
              </>
            }
          >
            <div className="term tb-scroll max-h-[220px] overflow-y-auto rounded-[10px] bg-[#16161a] px-[14px] py-3 font-mono text-[11px] leading-[1.85]">
              {NEURO_LOG.map((line) => (
                <div key={line.time} className="flex gap-[10px]">
                  <span className="shrink-0 text-[#5c5c66]">{line.time}</span>
                  <span style={{ color: line.color }}>{line.msg}</span>
                </div>
              ))}
            </div>
          </CollapsibleCard>
        </div>

        {/* LEFT column */}
        <div className="col-start-1 row-start-1 flex flex-col gap-4">
          {idleCount > 0 ? (
            <button
              type="button"
              onClick={() => {
                setShowAccounts(true);
              }}
              className="flex items-center gap-[11px] rounded-[14px] border border-[#efd79a] bg-[#fffbef] px-[14px] py-3 text-left transition-colors hover:bg-[#fdf6e3]"
            >
              <span className="flex h-[30px] w-[30px] shrink-0 items-center justify-center rounded-[9px] bg-[#fbefcb] text-[#9a7b22]">
                <svg
                  width="16"
                  height="16"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <circle cx="12" cy="12" r="9" />
                  <path d="M12 7v5l3 2" />
                </svg>
              </span>
              <div className="min-w-0 flex-1">
                <div className="text-[12.5px] font-bold leading-[1.25] text-[#7a5e12]">
                  {t('neurocomment.idle.label', { count: idleCount })}
                </div>
                <div className="mt-px text-[11px] text-[#a98a2e]">{t('neurocomment.idle.sub')}</div>
              </div>
              <span className="flex shrink-0 text-[#b8922f]">
                <svg
                  width="16"
                  height="16"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                >
                  <path d="m9 18 6-6-6-6" />
                </svg>
              </span>
            </button>
          ) : null}

          {/* listener account */}
          <div className="relative z-[5] rounded-2xl border border-line bg-white px-[14px] py-[13px]">
            <div className="mb-[3px] flex items-center gap-[9px]">
              <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-primary-tint text-primary">
                <svg
                  width="15"
                  height="15"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                >
                  <path d="M2 10v3" />
                  <path d="M6 6v11" />
                  <path d="M10 3v18" />
                  <path d="M14 8v7" />
                  <path d="M18 5v13" />
                  <path d="M22 10v3" />
                </svg>
              </span>
              <div className="min-w-0">
                <div className="text-[12.5px] font-semibold text-ink">
                  {t('neurocomment.listener.title')}
                </div>
              </div>
            </div>

            {listenerId ? (
              <div className="mt-[9px]">
                <SurfHover
                  shift={144}
                  surfaceId="lsn-surf"
                  actions={
                    <>
                      <button
                        type="button"
                        title={
                          running
                            ? t('neurocomment.listener.pause')
                            : t('neurocomment.listener.resume')
                        }
                        onClick={toggleRuntime}
                        className={`flex w-12 items-center justify-center border-none bg-transparent ${running ? 'text-[#c47d12]' : 'text-success'}`}
                      >
                        {running ? (
                          <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                            <rect x="6" y="5" width="4" height="14" rx="1" />
                            <rect x="14" y="5" width="4" height="14" rx="1" />
                          </svg>
                        ) : (
                          <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
                            <path d="M7 5.5v13a1 1 0 0 0 1.5.87l11-6.5a1 1 0 0 0 0-1.74l-11-6.5A1 1 0 0 0 7 5.5z" />
                          </svg>
                        )}
                      </button>
                      <button
                        type="button"
                        title={t('neurocomment.listener.edit')}
                        onClick={() => {
                          setShowListenerEdit(true);
                        }}
                        className="flex w-12 items-center justify-center border-none bg-transparent text-primary"
                      >
                        <svg
                          width="16"
                          height="16"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="1.8"
                        >
                          <path d="M12 20h9" />
                          <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" />
                        </svg>
                      </button>
                      <button
                        type="button"
                        title={t('neurocomment.listener.remove')}
                        onClick={() => {
                          setListener('');
                          stop.mutate({}, { onSettled: invalidate });
                        }}
                        className="flex w-12 items-center justify-center border-none bg-transparent text-danger"
                      >
                        <svg
                          width="16"
                          height="16"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="1.8"
                        >
                          <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
                        </svg>
                      </button>
                    </>
                  }
                  surface={
                    <div
                      className="flex items-center justify-between gap-2 rounded-[10px] border px-[10px] py-2"
                      style={{
                        background: running ? '#ddf7e9' : '#f7f6f4',
                        borderColor: running ? '#b8ecce' : '#e6e5e3',
                      }}
                    >
                      <div className="flex min-w-0 items-center gap-2">
                        <span
                          className={`h-2 w-2 shrink-0 rounded-full ${running ? 'tb-livedot' : ''}`}
                          style={{ background: running ? '#12a150' : '#9a9893' }}
                        />
                        <span
                          className={`text-[12.5px] font-semibold ${running ? 'tb-pulse' : ''}`}
                          style={{ color: running ? '#12a150' : '#74726e' }}
                        >
                          {running
                            ? t('neurocomment.listener.listening')
                            : t('neurocomment.listener.paused')}
                        </span>
                        <span
                          title={t('neurocomment.listener.activeCampaigns')}
                          className="inline-flex h-[18px] min-w-[18px] shrink-0 items-center justify-center rounded-full px-[5px] text-[10.5px] font-bold text-white"
                          style={{ background: running ? '#12a150' : '#74726e' }}
                        >
                          {campaignList.filter((c) => c.status === 'active').length}
                        </span>
                      </div>
                      <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-[7px] border border-line bg-white text-ink-subtle">
                        <svg
                          width="13"
                          height="13"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2"
                        >
                          <circle cx="12" cy="12" r="3" />
                          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
                        </svg>
                      </span>
                    </div>
                  }
                />
              </div>
            ) : (
              <div className="relative mt-[9px]">
                <button
                  type="button"
                  onClick={() => {
                    setListenerOpen((v) => !v);
                  }}
                  className="tb-time flex w-full items-center justify-between rounded-[10px] border border-line-input bg-white px-[13px] py-[10px] text-[13px]"
                >
                  <span className="text-ink-subtle">{t('neurocomment.listener.choose')}</span>
                  <span
                    className={`tb-ddchev flex shrink-0 text-ink-subtle ${listenerOpen ? 'open' : ''}`}
                  >
                    <svg
                      width="15"
                      height="15"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                    >
                      <path d="m6 9 6 6 6-6" />
                    </svg>
                  </span>
                </button>
                <div
                  className={`tb-dd absolute inset-x-0 top-[calc(100%+5px)] z-20 rounded-[10px] border border-line bg-white p-1 shadow-[0_10px_30px_rgba(11,11,12,0.1)] ${listenerOpen ? 'open' : ''}`}
                >
                  {accountOptions.map((account) => (
                    <button
                      key={account.account_id}
                      type="button"
                      onClick={() => {
                        setListener(account.account_id);
                        setListenerOpen(false);
                      }}
                      className="flex w-full items-center justify-between gap-2 rounded-[7px] px-[10px] py-2 text-left text-[12.5px] transition-colors hover:bg-[#f2f6ff]"
                    >
                      <span className="font-medium">{account.label ?? account.account_id}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* captcha solve + bot-check */}
          <div className="overflow-hidden rounded-2xl border border-line bg-white">
            <div className="flex items-center justify-between gap-[10px] px-[14px] py-3">
              <div className="flex min-w-0 items-center gap-[9px]">
                <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-primary-tint text-primary">
                  <svg
                    width="15"
                    height="15"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                  >
                    <path d="M9 12l2 2 4-4" />
                    <path d="M12 3a9 9 0 1 0 9 9 9 9 0 0 0-9-9z" />
                  </svg>
                </span>
                <div className="min-w-0">
                  <div className="text-[12.5px] font-semibold text-ink">
                    {t('neurocomment.captcha.title')}
                  </div>
                  <div className="text-[10.5px] leading-[1.35] text-ink-subtle">
                    {t('neurocomment.captcha.sub')}
                  </div>
                </div>
              </div>
              <button
                type="button"
                role="switch"
                aria-checked={captchaSolve}
                aria-label={t('neurocomment.captcha.title')}
                onClick={() => {
                  setCaptchaSolve((v) => !v);
                }}
                className="tb-sw relative h-[26px] w-[46px] shrink-0 rounded-full transition-colors"
                style={{ background: captchaSolve ? '#0066ff' : '#d8d6d2' }}
              >
                <span
                  className="absolute top-[3px] transition-[left] duration-200"
                  style={{ left: captchaSolve ? '23px' : '3px' }}
                >
                  <span className="tb-sw-thumb block h-5 w-5 rounded-full bg-white shadow-[0_1px_3px_rgba(0,0,0,0.3)]" />
                </span>
              </button>
            </div>
            {captchaSolve && CAPTCHA_QUEUE.length > 0 ? (
              <div className="px-[14px] pb-[14px]">
                <div className="mb-[9px] flex items-center gap-[7px] border-t border-[#f0eeea] pt-[11px]">
                  <svg
                    width="13"
                    height="13"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="#9a7b22"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z" />
                    <path d="M12 8v4" />
                    <path d="M12 16h.01" />
                  </svg>
                  <span className="text-[11px] font-semibold uppercase tracking-[.03em] text-[#9a7b22]">
                    {t('neurocomment.captcha.pending', { count: CAPTCHA_QUEUE.length })}
                  </span>
                </div>
                <div className="flex flex-col gap-[7px]">
                  {CAPTCHA_QUEUE.map((item) => (
                    <div
                      key={item.id}
                      className="flex items-center justify-between gap-[10px] rounded-[11px] border border-[#efe5cc] bg-[#fcfaf4] py-2 pl-[11px] pr-2"
                    >
                      <div className="flex min-w-0 items-center gap-[9px]">
                        <span className="tb-livedot h-[7px] w-[7px] shrink-0 rounded-full bg-[#e0a82e]" />
                        <div className="min-w-0">
                          <div className="truncate text-[12.5px] font-semibold text-ink">
                            {item.acc}
                          </div>
                          <div className="text-[10.5px] text-ink-subtle">
                            {item.channel} · {item.time}
                          </div>
                        </div>
                      </div>
                      <button
                        type="button"
                        className="shrink-0 rounded-full bg-ink px-[13px] py-[6px] text-[11.5px] font-medium text-white"
                      >
                        {t('neurocomment.captcha.solve')}
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
          </div>

          {/* campaigns */}
          <CollapsibleCard
            defaultOpen
            label={t('neurocomment.campaigns.title')}
            headerClassName="px-4 py-[15px]"
            bodyClassName="px-4 pb-[15px]"
            header={
              <span className="text-[13px] font-semibold">{t('neurocomment.campaigns.title')}</span>
            }
          >
            <div className="flex flex-col gap-2">
              {campaignList.map((campaign) => {
                const isSelected = campaign.campaign_id === campaignId;
                const isRunning = running && isSelected;
                const color = STATUS_COLOR[campaign.status];
                return (
                  <SurfHover
                    key={campaign.campaign_id}
                    shift={156}
                    surfaceId={`camp-surf-${campaign.campaign_id}`}
                    actions={
                      <>
                        <button
                          type="button"
                          title={
                            isRunning
                              ? t('neurocomment.campaign.pause')
                              : t('neurocomment.campaign.run')
                          }
                          onClick={toggleRuntime}
                          className={`flex w-[52px] items-center justify-center border-none bg-transparent ${isRunning ? 'text-[#c47d12]' : 'text-success'}`}
                        >
                          {isRunning ? (
                            <svg width="17" height="17" viewBox="0 0 24 24" fill="currentColor">
                              <rect x="6" y="5" width="4" height="14" rx="1" />
                              <rect x="14" y="5" width="4" height="14" rx="1" />
                            </svg>
                          ) : (
                            <svg width="17" height="17" viewBox="0 0 24 24" fill="currentColor">
                              <path d="M7 5.5v13a1 1 0 0 0 1.5.87l11-6.5a1 1 0 0 0 0-1.74l-11-6.5A1 1 0 0 0 7 5.5z" />
                            </svg>
                          )}
                        </button>
                        <button
                          type="button"
                          title={t('neurocomment.campaign.editPrompt')}
                          onClick={() => {
                            setPromptFor(campaign);
                          }}
                          className="flex w-[52px] items-center justify-center border-none bg-transparent text-primary"
                        >
                          <svg
                            width="17"
                            height="17"
                            viewBox="0 0 24 24"
                            fill="none"
                            stroke="currentColor"
                            strokeWidth="1.8"
                          >
                            <path d="M12 20h9" />
                            <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" />
                          </svg>
                        </button>
                        <button
                          type="button"
                          title={t('neurocomment.campaign.delete')}
                          onClick={() => {
                            setDeleteFor(campaign);
                          }}
                          className="flex w-[52px] items-center justify-center border-none bg-transparent text-danger"
                        >
                          <svg
                            width="17"
                            height="17"
                            viewBox="0 0 24 24"
                            fill="none"
                            stroke="currentColor"
                            strokeWidth="1.8"
                          >
                            <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
                          </svg>
                        </button>
                      </>
                    }
                    surface={
                      <div
                        role="button"
                        tabIndex={0}
                        onClick={() => {
                          setSelected(campaign.campaign_id);
                        }}
                        className={`cursor-pointer rounded-[11px] border bg-white p-[13px] ${isSelected ? 'border-primary bg-primary/[0.06]' : 'border-line'}`}
                      >
                        <div className="flex justify-between gap-[10px]">
                          <div className="min-w-0 flex-1">
                            <div className="mb-[5px] text-[13px] font-semibold">
                              {campaign.name}
                            </div>
                            <div className="text-[11px] text-ink-muted">
                              {t('neurocomment.campaign.meta', {
                                channels:
                                  campaign.campaign_id === campaignId ? boardChannels.length : 0,
                                accounts:
                                  campaign.campaign_id === campaignId ? boardAccounts.length : 0,
                              })}
                            </div>
                          </div>
                          <div className="flex shrink-0 flex-col items-end gap-2">
                            <span
                              className="inline-flex items-center gap-[5px] text-[11px] font-medium"
                              style={{ color }}
                            >
                              <span
                                className="h-[6px] w-[6px] rounded-full"
                                style={{ background: color }}
                              />
                              {t(`neurocomment.campaign.status.${campaign.status}`)}
                            </span>
                          </div>
                        </div>
                      </div>
                    }
                  />
                );
              })}
              {campaignList.length === 0 ? (
                <div className="py-[18px] text-center text-[12px] text-ink-subtle">
                  {t('neurocomment.campaigns.none')}
                </div>
              ) : null}
            </div>

            <button
              type="button"
              onClick={() => {
                setShowCreate(true);
              }}
              className="mt-[9px] flex w-full items-center justify-center gap-[5px] rounded-[10px] border border-dashed border-[#c7d6f0] bg-white py-[9px] text-[12.5px] font-medium text-primary hover:border-primary hover:bg-[#f2f6ff]"
            >
              {t('neurocomment.campaigns.create')}
            </button>

            {/* campaign channels */}
            <div className="mt-[13px] border-t border-[#f0eeeb] pt-3">
              <CollapsibleCard
                defaultOpen
                wrapperClassName=""
                headerClassName="px-0 py-0"
                bodyClassName="px-0 pb-0 pt-[11px]"
                label={t('neurocomment.channels.title')}
                header={
                  <span className="text-[12.5px] font-semibold">
                    {t('neurocomment.channels.title')}
                  </span>
                }
              >
                {activeCampaign ? (
                  <div className="mb-[10px] truncate text-[11.5px] font-medium text-primary">
                    {activeCampaign.name}
                  </div>
                ) : null}
                <div className="flex flex-wrap gap-[7px]">
                  {boardChannels.map((channel) => (
                    <span
                      key={channel.channel}
                      className="inline-flex items-center gap-[6px] rounded-full border border-line bg-[#f4f3f0] px-[11px] py-[5px] text-[12px] text-[#3a3a3a]"
                    >
                      {channel.channel}
                      <span className="cursor-default text-[14px] leading-none text-[#b5b3ae]">
                        ×
                      </span>
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
                          if (event.key === 'Escape') {
                            setAddingChannel(false);
                            setChannelInput('');
                          }
                        }}
                        placeholder={t('neurocomment.channels.placeholder')}
                        aria-label={t('neurocomment.channels.placeholder')}
                        className="w-[150px] border-none bg-transparent text-[12px] outline-none"
                      />
                      <button
                        type="button"
                        aria-label={t('neurocomment.modal.add')}
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
                    </span>
                  ) : (
                    <button
                      type="button"
                      disabled={campaignId === null}
                      onClick={() => {
                        setAddingChannel(true);
                      }}
                      className="inline-flex items-center gap-1 rounded-full border border-dashed border-line-strong bg-white px-[11px] py-[5px] text-[12px] text-ink-muted hover:border-primary hover:text-primary disabled:opacity-50"
                    >
                      {t('neurocomment.channels.addPill')}
                    </button>
                  )}
                </div>
              </CollapsibleCard>
            </div>
          </CollapsibleCard>

          {/* how it works */}
          <CollapsibleCard
            label={t('neurocomment.howto.title')}
            wrapperClassName="rounded-2xl border border-line bg-[#f6f5f2]"
            headerClassName="px-4 py-[15px]"
            header={
              <span className="text-[13px] font-semibold">{t('neurocomment.howto.title')}</span>
            }
          >
            <div className="flex flex-col gap-[10px]">
              {HOW_STEPS.map((index) => (
                <div key={index} className="flex items-start gap-[10px]">
                  <span className="mt-px flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-full bg-primary text-[10px] font-semibold text-white">
                    {index + 1}
                  </span>
                  <span className="text-[12px] leading-[1.5] text-[#5c5c5c]">
                    {t(`neurocomment.howto.steps.${String(index)}`)}
                  </span>
                </div>
              ))}
            </div>
          </CollapsibleCard>
        </div>
      </div>

      {showAccounts ? (
        <NeuroAccountsModal
          accounts={boardAccounts.map((a) => ({
            account_id: a.account_id,
            phone: a.label,
            channel: a.readiness?.[0]?.channel ?? null,
          }))}
          channelOptions={boardChannels.map((c) => c.channel)}
          onClose={() => {
            setShowAccounts(false);
          }}
          onPick={(accountId, channel) => {
            if (campaignId !== null) {
              assignAccount.mutate(
                { path: { campaign_id: campaignId }, body: { account_id: accountId } },
                { onSettled: invalidate },
              );
            }
            void channel;
          }}
          onRemove={() => {
            invalidate();
          }}
        />
      ) : null}

      {showCreate ? (
        <CreateCampaignModal
          onClose={() => {
            setShowCreate(false);
          }}
          onCreate={({ name, prompt, channels }) => {
            createCampaign.mutate(
              { body: { name, prompt } },
              {
                onSuccess: (created) => {
                  channels.forEach((channel) => {
                    linkChannel.mutate({
                      path: { campaign_id: created.campaign_id },
                      body: { channel },
                    });
                  });
                  setSelected(created.campaign_id);
                },
                onSettled: invalidate,
              },
            );
          }}
        />
      ) : null}

      {showListenerEdit ? (
        <ListenerEditModal
          options={accountOptions.map((a) => ({
            id: a.account_id,
            phone: a.label ?? a.account_id,
          }))}
          selected={listenerId || null}
          onClose={() => {
            setShowListenerEdit(false);
          }}
          onSave={(id) => {
            setListener(id);
            if (running) {
              start.mutate({ body: { listener_account_id: id } }, { onSettled: invalidate });
            }
          }}
        />
      ) : null}

      {promptFor ? (
        <CampaignPromptModal
          campaignName={promptFor.name}
          initialPrompt={promptFor.prompt}
          accounts={boardAccounts.map((a) => ({
            account_id: a.account_id,
            phone: a.label,
            channel: a.readiness?.[0]?.channel ?? '—',
            initials: initials(a.label),
          }))}
          onClose={() => {
            setPromptFor(null);
          }}
          onSave={() => {
            // ponytail: no update-prompt endpoint on the generated client yet.
            invalidate();
          }}
          onRemoveAccount={() => {
            invalidate();
          }}
        />
      ) : null}

      {deleteFor ? (
        <CampaignDeleteModal
          name={deleteFor.name}
          onClose={() => {
            setDeleteFor(null);
          }}
          onConfirm={() => {
            // ponytail: no delete-campaign endpoint on the generated client yet.
            invalidate();
          }}
        />
      ) : null}
    </div>
  );
}
