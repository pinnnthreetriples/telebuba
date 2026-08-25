import { type ColumnDef } from '@tanstack/react-table';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { accountDisplayName, allAccountsQueryOptions } from '@/entities/account';
import { LogStatusBadge, logsQueryOptions } from '@/entities/log';
import type { LogEntry, PageLogEntry } from '@/shared/api';
import { Card, DataTable, Select, type DataTableColumnMeta } from '@/shared/ui';
import { eventLabel, eventReason, formatLocalTime, useLogEventStream } from '@/shared/lib';

const PAGE_SIZE = 50;
const STATUS_FILTERS = ['all', 'success', 'warning', 'error'] as const;
type StatusFilter = (typeof STATUS_FILTERS)[number];

// The channel an event is about, when it carries one. Four rules unlink a channel by
// themselves (re-join exhausted, address impossible, join request never approved, pause
// rounds spent) and the handle they name lives only here, in `extra.channel`. Reading it
// on THIS page is the whole point: the neurocomment activity card shows the same field but
// only for the last ~50 streamed lines, so an unattended overnight drop became a level and
// a label with nothing to act on. `extra` is a free-form object, hence the type check.
function extraChannel(extra: LogEntry['extra']): string | undefined {
  const value = extra?.channel;
  return typeof value === 'string' ? value : undefined;
}

export function LogsPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<StatusFilter>('all');
  const [account, setAccount] = useState('');
  const [cursorStack, setCursorStack] = useState<(string | null)[]>([null]);

  const cursor = cursorStack[cursorStack.length - 1] ?? undefined;
  const { data, isPending, isError } = useQuery(
    logsQueryOptions({
      query: { status, account_id: account, cursor, limit: PAGE_SIZE },
    }),
  );

  // Account filter + column labels come from GET /accounts (a fixed id→label
  // list), NOT the loaded log page — so every account is selectable even when it
  // has no rows on the current page, and the column shows the Telegram name, not
  // the internal session-stem id. Same resolver as the accounts table and the
  // neurocomment feed, so one account reads the same everywhere.
  const accountsData = useQuery(allAccountsQueryOptions());
  const accountLabels = useMemo(() => {
    const map = new Map<string, string>();
    for (const acc of accountsData.data?.items ?? []) {
      // `phone` is nullable, and accountDisplayName's chain is name → phone → id, with
      // no slot for the operator `label` this column used to fall back to. Feed the
      // label in as the phone stand-in so a nameless, phoneless account still reads as
      // something an operator recognises instead of a session-stem id.
      map.set(acc.account_id, accountDisplayName({ ...acc, phone: acc.phone ?? acc.label }));
    }
    return map;
  }, [accountsData.data]);
  const resolveAccount = (id: string): string => accountLabels.get(id) ?? id;

  const items = data?.items ?? [];
  const hasPrev = cursorStack.length > 1;
  const hasNext = Boolean(data?.next_cursor);

  // Live tail: prepend each incoming row to the newest page's cache, in place,
  // when it matches the active filter (no refetch). Key-scoped — only the newest
  // logs page's cache entry is touched, never a blanket invalidate.
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

  // '' is a real choice here — "every account" — so it is the first option rather
  // than a placeholder.
  const accountOptions = [
    { value: '', label: t('logs.filter.allAccounts') },
    ...[...accountLabels.keys()].map((id) => ({ value: id, label: resolveAccount(id) })),
  ];

  const columns = useMemo<ColumnDef<LogEntry>[]>(
    () => [
      {
        id: 'time',
        header: () => t('logs.col.time'),
        cell: ({ row }) => formatLocalTime(row.original.created_at, { seconds: true }),
        meta: {
          className: 'w-stamp',
          cellClassName: 'font-mono text-body text-ink-subtle',
          cardSlot: 'title',
        } satisfies DataTableColumnMeta,
      },
      {
        id: 'level',
        header: () => t('logs.col.level'),
        cell: ({ row }) => <LogStatusBadge status={row.original.status} />,
        meta: { className: 'w-stamp', cardSlot: 'control' } satisfies DataTableColumnMeta,
      },
      {
        id: 'account',
        header: () => t('logs.col.account'),
        cell: ({ row }) =>
          row.original.account_id ? resolveAccount(row.original.account_id) : '—',
        meta: {
          className: 'w-col',
          cellClassName: 'text-body text-ink-body',
        } satisfies DataTableColumnMeta,
      },
      {
        id: 'channel',
        header: () => t('logs.col.channel'),
        cell: ({ row }) => extraChannel(row.original.extra) ?? '—',
        meta: {
          className: 'w-col',
          cellClassName: 'truncate text-body text-ink-body',
        } satisfies DataTableColumnMeta,
      },
      {
        id: 'event',
        header: () => t('logs.col.event'),
        // Same hover hint as the neurocomment activity card: the label alone says a
        // channel left the campaign, the hint says why and what to do about it. Empty
        // string → no `title`, so an event without a hint gets no blank tooltip.
        cell: ({ row }) => (
          <span title={t(`logEventHint.${row.original.event}`, { defaultValue: '' }) || undefined}>
            {eventLabel(t, row.original.event)}
          </span>
        ),
        meta: { cellClassName: 'text-body text-ink-body' } satisfies DataTableColumnMeta,
      },
      {
        id: 'reason',
        header: () => t('logs.col.reason'),
        // WHY the row turned out that way. Without it a failure here read "Вступление в
        // чат канала — ошибка" and stopped: the label names the attempt, never the
        // refusal, even though `extra` carried it all along. No `truncate` and no fixed
        // width, unlike `channel`: a handle is still recognisable clipped, a refusal is
        // prose and clipping it hides the answer — the column is last, so it takes the
        // remaining width and the table already scrolls horizontally.
        cell: ({ row }) => eventReason(t, row.original) || '—',
        meta: {
          cellClassName: 'text-body text-ink-body',
        } satisfies DataTableColumnMeta,
      },
    ],
    // resolveAccount closes over accountLabels; re-derive columns when labels change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [t, accountLabels],
  );

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

  const pickAccount = (value: string) => {
    setAccount(value);
    resetPaging();
  };

  return (
    <div className="tb-fadeup">
      <h1 className="m-0 mb-xl text-display font-bold tracking-[-0.02em]">{t('logs.title')}</h1>

      <div className="mb-lg flex flex-wrap items-center gap-sm">
        <div ref={pillsRef} className="relative flex rounded-full bg-white p-xs">
          {/* The one shadow with no name and no second site: a BLUE glow that belongs to
              this sliding capsule alone, tinting the filled pill it sits under rather
              than lifting anything off the page. `pop`/`ring`/`thumb` are all neutral
              greys, and a token for one call site is not an improvement. */}
          <span
            aria-hidden
            className="absolute top-[3px] z-0 rounded-full bg-primary shadow-pill transition-[left,width] duration-enter"
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
              className={`relative z-raised px-lg py-tight text-body font-medium transition-colors ${status === value ? 'text-white' : 'text-ink-muted'}`}
            >
              {t(`logs.filter.${value}`)}
            </button>
          ))}
        </div>
        <div className="flex-1" />
        <div className="w-full shrink-0 sm:w-menu">
          <Select
            value={account}
            onChange={pickAccount}
            options={accountOptions}
            ariaLabel={t('logs.filter.account')}
          />
        </div>
      </div>

      {isPending ? (
        <p className="text-ink-muted">{t('logs.loading')}</p>
      ) : isError ? (
        <p role="alert" className="text-danger">
          {t('logs.error')}
        </p>
      ) : items.length === 0 ? (
        <Card className="px-lg py-empty text-center text-lead text-ink-subtle">
          {t('logs.empty')}
        </Card>
      ) : (
        <>
          <Card className="overflow-hidden">
            <div className="tb-scroll overflow-x-auto">
              <DataTable data={items} columns={columns} />
            </div>
          </Card>
          <div className="mt-lg flex items-center justify-end gap-sm">
            <button
              type="button"
              disabled={!hasPrev}
              onClick={() => {
                setCursorStack((stack) => stack.slice(0, -1));
              }}
              className="rounded-full border border-line bg-white px-lg py-sm text-lead disabled:opacity-50"
            >
              {t('logs.pagination.prev')}
            </button>
            <button
              type="button"
              disabled={!hasNext}
              onClick={() => {
                setCursorStack((stack) => [...stack, data.next_cursor ?? null]);
              }}
              className="rounded-full border border-line bg-white px-lg py-sm text-lead disabled:opacity-50"
            >
              {t('logs.pagination.next')}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
