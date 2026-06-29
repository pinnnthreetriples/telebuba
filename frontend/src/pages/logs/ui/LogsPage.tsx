import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { LogStatusBadge, logsQueryOptions } from '@/entities/log';
import type { PageLogEntry } from '@/shared/api';
import { useLogEventStream } from '@/shared/lib';

const PAGE_SIZE = 50;
const STATUS_FILTERS = ['all', 'success', 'warning', 'error'] as const;
type StatusFilter = (typeof STATUS_FILTERS)[number];

const TH =
  'px-4 py-[11px] text-left text-[11px] font-medium uppercase tracking-[0.04em] text-ink-subtle';

export function LogsPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<StatusFilter>('all');
  const [account, setAccount] = useState('');
  const [accountOpen, setAccountOpen] = useState(false);
  const [cursorStack, setCursorStack] = useState<(string | null)[]>([null]);

  const cursor = cursorStack[cursorStack.length - 1] ?? undefined;
  const { data, isPending, isError } = useQuery(
    logsQueryOptions({
      query: { status, account_id: account, cursor, limit: PAGE_SIZE },
    }),
  );

  const items = data?.items ?? [];
  const hasPrev = cursorStack.length > 1;
  const hasNext = Boolean(data?.next_cursor);

  // Live tail: prepend each incoming row to the newest page's cache, in place,
  // when it matches the active filter (no refetch).
  useLogEventStream((entry) => {
    if (hasPrev) return;
    if (status !== 'all' && entry.status !== status) return;
    if (account && entry.account_id !== account) return;
    const { queryKey } = logsQueryOptions({
      query: { status, account_id: account, cursor: undefined, limit: PAGE_SIZE },
    });
    queryClient.setQueryData<PageLogEntry>(queryKey, (old) => {
      if (!old) return old;
      if (old.items.some((row) => row.id === entry.id)) return old;
      return { ...old, items: [entry, ...old.items].slice(0, PAGE_SIZE) };
    });
  });

  const resetPaging = () => {
    setCursorStack([null]);
  };

  // Account options: derived from the loaded rows' account ids, plus an "all"
  // option. Graceful when empty (just "all").
  const accountIds = [
    ...new Set(items.map((row) => row.account_id).filter((id): id is string => Boolean(id))),
  ];
  const accountLabel = account || t('logs.filter.account');

  // Level-filter sliding indicator: measure the active pill and CSS-transition a
  // single capsule behind it (the GSAP #log-ind slide, like the nav indicator).
  const pillsRef = useRef<HTMLDivElement>(null);
  const [indicator, setIndicator] = useState({ left: 0, width: 0, height: 0 });
  const activeIdx = STATUS_FILTERS.indexOf(status);
  useLayoutEffect(() => {
    const group = pillsRef.current;
    if (!group) return;
    const move = () => {
      const active = group.querySelectorAll('button')[activeIdx];
      if (active instanceof HTMLElement) {
        setIndicator({
          left: active.offsetLeft,
          width: active.offsetWidth,
          height: active.offsetHeight,
        });
      }
    };
    move();
    window.addEventListener('resize', move);
    void document.fonts?.ready.then(move);
    return () => {
      window.removeEventListener('resize', move);
    };
  }, [activeIdx]);

  // Close the account dropdown on outside click.
  const accountRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!accountOpen) return;
    const onDown = (event: MouseEvent) => {
      if (accountRef.current && !accountRef.current.contains(event.target as Node)) {
        setAccountOpen(false);
      }
    };
    document.addEventListener('mousedown', onDown);
    return () => {
      document.removeEventListener('mousedown', onDown);
    };
  }, [accountOpen]);

  const pickAccount = (value: string) => {
    setAccount(value);
    setAccountOpen(false);
    resetPaging();
  };

  return (
    <div className="tb-fadeup">
      <h1 className="m-0 mb-[18px] text-[22px] font-bold tracking-[-0.02em]">{t('logs.title')}</h1>

      <div className="mb-[14px] flex flex-wrap items-center gap-2">
        <div ref={pillsRef} className="relative flex gap-0 rounded-full bg-white p-[3px]">
          <span
            aria-hidden
            className="absolute top-[3px] z-0 rounded-full bg-primary shadow-[0_1px_2px_rgba(0,102,255,0.3)] transition-[left,width] duration-300"
            style={{ left: indicator.left, width: indicator.width, height: indicator.height }}
          />
          {STATUS_FILTERS.map((value) => (
            <button
              key={value}
              type="button"
              onClick={() => {
                setStatus(value);
                resetPaging();
              }}
              className={`relative z-[1] px-[14px] py-[6px] text-[12px] font-medium transition-colors ${status === value ? 'text-white' : 'text-ink-muted'}`}
            >
              {t(`logs.filter.${value}`)}
            </button>
          ))}
        </div>
        <div className="flex-1" />
        <div ref={accountRef} className="relative w-[200px] shrink-0">
          <button
            type="button"
            aria-label={t('logs.filter.account')}
            onClick={() => {
              setAccountOpen((open) => !open);
            }}
            className="tb-time flex w-full items-center justify-between gap-2 rounded-full border border-line bg-white px-4 py-[7px] text-[13px] outline-none"
          >
            <span className="overflow-hidden text-ellipsis whitespace-nowrap">{accountLabel}</span>
            <span
              className={`tb-ddchev flex shrink-0 text-ink-subtle${accountOpen ? ' open' : ''}`}
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
            className={`tb-dd absolute inset-x-0 top-[calc(100%+5px)] z-[5] rounded-[11px] border border-line bg-white shadow-[0_10px_28px_rgba(0,0,0,0.13)]${accountOpen ? ' open' : ''}`}
          >
            <div className="p-1">
              {['', ...accountIds].map((value) => {
                const selected = value === account;
                return (
                  <button
                    key={value || 'all'}
                    type="button"
                    onClick={() => {
                      pickAccount(value);
                    }}
                    className="flex w-full items-center justify-between rounded-[7px] px-[10px] py-[8px] text-[13px] hover:bg-[#faf9f7]"
                  >
                    {value || t('logs.filter.allAccounts')}
                    {selected && (
                      <svg
                        width="14"
                        height="14"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="#0066ff"
                        strokeWidth="2.5"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        className="shrink-0"
                      >
                        <path d="M20 6 9 17l-5-5" />
                      </svg>
                    )}
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      </div>

      {isPending ? (
        <p className="text-ink-muted">{t('logs.loading')}</p>
      ) : isError ? (
        <p role="alert" className="text-danger">
          {t('logs.error')}
        </p>
      ) : items.length === 0 ? (
        <div className="rounded-2xl border border-line bg-white px-4 py-16 text-center text-[13px] text-ink-subtle">
          {t('logs.empty')}
        </div>
      ) : (
        <>
          <div className="overflow-hidden rounded-2xl border border-line bg-white">
            <div className="tb-scroll overflow-x-auto">
              <table className="w-full min-w-[760px] border-collapse">
                <thead>
                  <tr className="bg-surface">
                    <th className={`${TH} w-[120px]`}>{t('logs.col.time')}</th>
                    <th className={`${TH} w-[110px]`}>{t('logs.col.level')}</th>
                    <th className={`${TH} w-[150px]`}>{t('logs.col.account')}</th>
                    <th className={TH}>{t('logs.col.event')}</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((row) => (
                    <tr key={row.id} className="border-t border-[#f0eeeb]">
                      <td className="px-4 py-[10px] font-mono text-[12px] text-ink-subtle">
                        {row.created_at}
                      </td>
                      <td className="px-4 py-[10px]">
                        <LogStatusBadge status={row.status} />
                      </td>
                      <td className="px-4 py-[10px] text-[12.5px] text-[#3a3a3a]">
                        {row.account_id ?? '—'}
                      </td>
                      <td className="px-4 py-[10px] text-[12.5px] text-[#3a3a3a]">{row.event}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          <div className="mt-4 flex items-center justify-end gap-2">
            <button
              type="button"
              disabled={!hasPrev}
              onClick={() => {
                setCursorStack((stack) => stack.slice(0, -1));
              }}
              className="rounded-full border border-line bg-white px-4 py-[7px] text-[13px] disabled:opacity-50"
            >
              {t('logs.pagination.prev')}
            </button>
            <button
              type="button"
              disabled={!hasNext}
              onClick={() => {
                setCursorStack((stack) => [...stack, data.next_cursor ?? null]);
              }}
              className="rounded-full border border-line bg-white px-4 py-[7px] text-[13px] disabled:opacity-50"
            >
              {t('logs.pagination.next')}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
