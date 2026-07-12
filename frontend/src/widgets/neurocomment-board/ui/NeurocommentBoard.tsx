import { type ColumnDef, type Row } from '@tanstack/react-table';
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { ChannelStatusBadge } from '@/entities/campaign';
import type {
  CommentRecord,
  NeurocommentBoard as NeurocommentBoardData,
  NeurocommentChannelRow,
} from '@/shared/api';
import { formatLocalTime } from '@/shared/lib';
import { DataTable, type DataTableColumnMeta } from '@/shared/ui';

interface BoardRow {
  account: string;
  // Carried so the expandable sub-row can filter the board's comments down to
  // just this account's published ones.
  accountId: string;
  channel: string;
  text: string;
  // 'no_data' (no readiness rows yet) is now a real backend status; deriveRows
  // also falls back to it when an account's channel is absent from the board map.
  status: NeurocommentChannelRow['status'];
  // Our comments removed from this row's channel within the 24h board window.
  deletedRecent: number;
  // Onboarding progress for this account: ready channels / target. While the
  // runtime reports onboarding in flight and the account is not yet fully armed,
  // the status cell animates this instead of the (misleading) static status.
  armedReady: number;
  armedTarget: number;
}

// One work row per account, joined on the account's OWN channel: its pinned
// channel when set, else its first joined channel from the readiness list (a
// real link, not an arbitrary pairing) with that channel's real aggregate
// status. The comment cell shows the account's real last comment text (falling
// back to a generic "posted" hint, then an em dash when it has never commented).
function deriveRows(
  board: NeurocommentBoardData,
  placeholder: string,
  totalChannels: number,
): BoardRow[] {
  const channelStatus = new Map((board.channels ?? []).map((c) => [c.channel, c.status]));
  const channelDeleted = new Map((board.channels ?? []).map((c) => [c.channel, c.deleted_recent]));
  return (board.accounts ?? []).map((account) => {
    const readiness = account.readiness ?? [];
    const pins = account.pinned_channels ?? [];
    const primary =
      readiness.find((r) => pins.includes(r.channel)) ??
      readiness.find((r) => r.joined) ??
      readiness[0];
    const channel = primary?.channel ?? '—';
    // An account with a channel subset onboards only those; an empty subset covers
    // every campaign channel. Ready count drives the "N/M" progress badge.
    const armedTarget = pins.length || Math.max(1, totalChannels);
    const armedReady = Math.min(readiness.filter((r) => r.ready).length, armedTarget);
    return {
      account: account.label,
      accountId: account.account_id,
      channel,
      text: account.last_comment_text ?? (account.last_comment_at ? placeholder : '—'),
      status: channelStatus.get(channel) ?? 'no_data',
      deletedRecent: channelDeleted.get(channel) ?? 0,
      armedReady,
      armedTarget,
    };
  });
}

// Animated "onboarding in progress" pill for the status cell — shown while the
// runtime is actively arming an account (joining channels), replacing the
// static "Нет данных" that otherwise reads as a stall.
function OnboardingBadge({ ready, total }: { ready: number; total: number }) {
  const { t } = useTranslation();
  return (
    <span className="inline-flex animate-pulse items-center gap-[5px] rounded-full bg-primary-tint px-[9px] py-[3px] text-[11.5px] font-medium text-primary">
      <span className="h-[5px] w-[5px] rounded-full bg-primary" />
      {t('neurocomment.board.onboarding', { ready, total })}
    </span>
  );
}

// The expandable sub-row under an account: that account's published comments
// (newest first, as the board already orders them), inline instead of the old
// separate feed card. The account column is dropped — we're already inside it.
function AccountComments({
  comments,
  onOpenHistory,
}: {
  comments: CommentRecord[];
  onOpenHistory?: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="border-t border-[#f0eeeb] bg-[#faf9f7] px-4 py-3">
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="pl-pulse h-[7px] w-[7px] shrink-0 rounded-full bg-primary" />
          <span className="text-[12px] font-semibold">{t('neurocomment.feed.title')}</span>
          <span className="rounded-full bg-[#f2f1ee] px-2 py-[2px] text-[11px] font-medium text-ink-muted">
            {comments.length}
          </span>
        </div>
        {onOpenHistory ? (
          <button
            type="button"
            onClick={onOpenHistory}
            className="rounded-full border border-line bg-white px-3 py-[4px] text-[11.5px] font-medium text-primary hover:border-primary"
          >
            {t('neurocomment.feed.history')}
          </button>
        ) : null}
      </div>
      {comments.length === 0 ? (
        <div className="py-4 text-center text-[12.5px] text-ink-subtle">
          {t('neurocomment.feed.empty')}
        </div>
      ) : (
        <div className="tb-scroll max-h-[220px] overflow-y-auto">
          {comments.map((c) => {
            const deleted = Boolean(c.deleted_at);
            return (
              <div
                key={`${c.channel}:${String(c.post_id)}`}
                className="flex items-baseline gap-[10px] border-b border-[#f4f2ef] py-[7px] text-[12.5px] last:border-b-0"
              >
                <span className="shrink-0 text-ink-subtle">{formatLocalTime(c.created_at)}</span>
                <span className="shrink-0 text-primary">{c.channel}</span>
                <span
                  className={`min-w-0 flex-1 truncate ${deleted ? 'text-ink-subtle line-through' : 'text-[#5c5c5c]'}`}
                >
                  {c.comment_text ?? '—'}
                </span>
                {deleted ? (
                  <span className="shrink-0 rounded-full bg-danger-tint px-[7px] py-px text-[10px] font-medium text-danger">
                    {t('neurocomment.feed.deleted')}
                  </span>
                ) : null}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// The design's "Доска работ" card: a collapsible header (account count pill,
// freshness, gear→neuro-accounts modal, chevron) over the shared DataTable with
// the design's 4 work columns (account / channel / comment / status).
export function NeurocommentBoard({
  board,
  accountsCount,
  onboarding = false,
  onOpenAccounts,
  onOpenHistory,
}: {
  board: NeurocommentBoardData;
  accountsCount: number;
  // True while the runtime is actively onboarding (joining channels): the board
  // animates a live indicator instead of reading as an idle "no data" state.
  onboarding?: boolean;
  onOpenAccounts: () => void;
  // Opens the full comment-history modal from an expanded account's sub-row.
  onOpenHistory?: () => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(true);
  const rows = deriveRows(
    board,
    t('neurocomment.board.commentPlaceholder'),
    (board.channels ?? []).length,
  );

  const columns = useMemo<ColumnDef<BoardRow>[]>(
    () => [
      {
        accessorKey: 'account',
        header: t('neurocomment.board.col.account'),
        cell: (info) => info.getValue<string>(),
        meta: {
          cellClassName: 'whitespace-nowrap text-[12.5px] font-medium',
        } satisfies DataTableColumnMeta,
      },
      {
        accessorKey: 'channel',
        header: t('neurocomment.board.col.channel'),
        cell: ({ row }) => (
          <span className="inline-flex items-center gap-[6px] whitespace-nowrap">
            {row.original.channel}
            {row.original.deletedRecent > 0 ? (
              <span className="rounded-full bg-danger-tint px-[7px] py-px text-[10px] font-medium text-danger">
                {t('neurocomment.board.deleted', { count: row.original.deletedRecent })}
              </span>
            ) : null}
          </span>
        ),
        meta: {
          cellClassName: 'whitespace-nowrap text-[12.5px] text-primary',
        } satisfies DataTableColumnMeta,
      },
      {
        accessorKey: 'text',
        header: t('neurocomment.board.col.comment'),
        cell: (info) => info.getValue<string>(),
        meta: {
          cellClassName:
            'max-w-[240px] overflow-hidden text-ellipsis whitespace-nowrap text-[12.5px] text-[#5c5c5c]',
        } satisfies DataTableColumnMeta,
      },
      {
        accessorKey: 'status',
        header: t('neurocomment.board.col.status'),
        cell: (info) => {
          const row = info.row.original;
          // Actively arming this account → animate progress, not a static status.
          return onboarding && row.armedReady < row.armedTarget ? (
            <OnboardingBadge ready={row.armedReady} total={row.armedTarget} />
          ) : (
            <ChannelStatusBadge status={row.status} />
          );
        },
      },
      {
        id: 'expander',
        header: () => null,
        cell: ({ row }) => (
          <button
            type="button"
            aria-label={t('neurocomment.feed.title')}
            aria-expanded={row.getIsExpanded()}
            onClick={row.getToggleExpandedHandler()}
            className={`flex text-ink-subtle transition-transform duration-[420ms] [transition-timing-function:cubic-bezier(.34,1.45,.6,1)] ${row.getIsExpanded() ? 'rotate-180' : ''}`}
          >
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <path d="m6 9 6 6 6-6" />
            </svg>
          </button>
        ),
        // Last column, sized to the chevron so it hugs the row's right edge.
        meta: { className: 'w-px', cellClassName: 'w-px' } satisfies DataTableColumnMeta,
      },
    ],
    [t, onboarding],
  );

  return (
    <div className="overflow-hidden rounded-2xl border border-line bg-white">
      <div className="flex items-center justify-between border-b border-[#f0eeeb] px-4 py-[14px]">
        <button
          type="button"
          onClick={() => {
            setOpen((v) => !v);
          }}
          className="flex items-center gap-2 text-left"
        >
          <span className="text-[13px] font-semibold">{t('neurocomment.board.title')}</span>
          <span className="rounded-full bg-primary-tint px-2 py-[2px] text-[11px] font-semibold text-primary">
            {t('neurocomment.board.accounts', { count: accountsCount })}
          </span>
        </button>
        <div className="flex items-center gap-[10px]">
          {onboarding ? (
            <span className="inline-flex animate-pulse items-center gap-[5px] rounded-full bg-primary-tint px-[9px] py-[3px] text-[11px] font-semibold text-primary">
              <span className="h-[5px] w-[5px] rounded-full bg-primary" />
              {t('neurocomment.board.onboardingLive')}
            </span>
          ) : (
            <span className="text-[11px] text-ink-muted">{t('neurocomment.board.updated')}</span>
          )}
          <button
            type="button"
            title={t('neurocomment.modal.neuroAccounts.title')}
            aria-label={t('neurocomment.modal.neuroAccounts.title')}
            onClick={onOpenAccounts}
            className="flex h-7 w-7 items-center justify-center rounded-lg border border-line bg-white text-ink-muted transition-colors hover:border-[#cbd7ec] hover:bg-[#f2f6ff] hover:text-primary"
          >
            <svg
              width="15"
              height="15"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
            </svg>
          </button>
          <button
            type="button"
            aria-label={t('neurocomment.board.title')}
            onClick={() => {
              setOpen((v) => !v);
            }}
            className={`flex text-ink-subtle transition-transform duration-[420ms] [transition-timing-function:cubic-bezier(.34,1.45,.6,1)] ${open ? 'rotate-180' : ''}`}
          >
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <path d="m6 9 6 6 6-6" />
            </svg>
          </button>
        </div>
      </div>
      <div className={`tb-collapse ${open ? 'tb-open' : ''}`}>
        <div className="tb-scroll overflow-x-auto">
          {rows.length > 0 ? (
            <DataTable
              data={rows}
              columns={columns}
              renderSubRow={(row: Row<BoardRow>) => (
                <AccountComments
                  comments={(board.comments ?? []).filter(
                    (c) => c.account_id === row.original.accountId,
                  )}
                  onOpenHistory={onOpenHistory}
                />
              )}
            />
          ) : (
            <div className="px-4 py-8 text-center text-[12.5px] text-ink-subtle">
              {t('neurocomment.board.empty')}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
