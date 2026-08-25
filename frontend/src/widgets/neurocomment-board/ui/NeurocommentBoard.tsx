import { type ColumnDef, type Row } from '@tanstack/react-table';
import { useMemo } from 'react';
import { useTranslation } from 'react-i18next';

import { ChannelStatusBadge } from '@/entities/campaign';
import type {
  CommentRecord,
  NeurocommentBoard as NeurocommentBoardData,
  NeurocommentChannelRow,
} from '@/shared/api';
import { formatLocalTime } from '@/shared/lib';
import { Badge, CollapsibleCard, DataTable, type DataTableColumnMeta, Icon } from '@/shared/ui';

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
  // THIS account's comments removed from THIS row's channel in the 24h board window —
  // the pair the row names, which is the only thing a chip beside a channel can mean.
  deletedHere: number;
  textDeleted: boolean;
  textChannel: string | null;
  // Onboarding progress for this account: ready channels / target. While the
  // runtime reports onboarding in flight and the account is not yet fully armed,
  // the status cell animates this instead of the (misleading) static status.
  armedReady: number;
  armedTarget: number;
}

// One work row per account, joined on the account's OWN channel: where it commented
// LAST, so the row names the comment it shows and moves as the account works —
// narrowed to the operator's pins when there are any, because a pin is an explicit
// instruction and re-pinning must be visible before the account next posts. Failing
// both, a stuck pair (banned here, or done re-joining here), then its first joined
// channel from the readiness list — a real link, not an arbitrary pairing — with that
// channel's real aggregate status. The comment cell shows the account's real last
// comment text (falling back to a generic "posted" hint, then an em dash when it has
// never commented).
function deriveRows(
  board: NeurocommentBoardData,
  placeholder: string,
  totalChannels: number,
  displayName: (accountId: string, fallback: string) => string,
): BoardRow[] {
  const channelStatus = new Map((board.channels ?? []).map((c) => [c.channel, c.status]));
  return (board.accounts ?? []).map((account) => {
    const readiness = account.readiness ?? [];
    const pins = account.pinned_channels ?? [];
    // Where this account commented last. Read off the CARD, not looked up in
    // `board.comments`: that feed is a campaign-wide newest-first prefix capped at 50,
    // so a busy account drops out of it within the hour and the row would quietly fall
    // back to a channel it merely joined while still showing the real comment text.
    const lastChannel = account.last_comment_channel;
    // A pin outranks it: pinning is the operator instructing this account where to work,
    // and re-pinning has to show up before the account next posts (which can be a day).
    // Among several pins the last-commented one still wins.
    //
    // A pair carrying its own verdict — banned here (#30), or done re-joining here —
    // used to outrank everything but a pin, because the row shows ONE channel per
    // account and a stuck pair had nowhere else to surface. ponytail: it now sits below
    // the live channel, accepted deliberately — the row's job is to name the comment it
    // shows, and the neuro-accounts modal still lists `banned_channels` per account, so
    // the diagnosis moved rather than disappeared.
    const primary =
      readiness.find((r) => r.channel === lastChannel && pins.includes(r.channel)) ??
      readiness.find((r) => pins.includes(r.channel)) ??
      readiness.find((r) => r.channel === lastChannel) ??
      readiness.find((r) => r.banned || (r.rejoin_gave_up && !r.ready)) ??
      readiness.find((r) => r.joined) ??
      readiness[0];
    const channel = primary?.channel ?? '—';
    // An account with a channel subset onboards only those; an empty subset covers
    // every campaign channel. Ready count drives the "N/M" progress badge.
    const armedTarget = pins.length || Math.max(1, totalChannels);
    const armedReady = Math.min(readiness.filter((r) => r.ready).length, armedTarget);
    return {
      // The board payload carries only ``label`` — the operator-set field, which is
      // empty for an imported session and equal to the id for a phone-named one, so
      // the column read "5_telethon" instead of "Alisa". The Telegram first/last name
      // lives on the full account list, which only the page has, hence the resolver.
      account: displayName(account.account_id, account.label),
      accountId: account.account_id,
      channel,
      text: account.last_comment_text ?? (account.last_comment_at ? placeholder : '—'),
      // Only meaningful next to real text: the placeholder and the em dash stand in for
      // a comment the row does not have, and striking those through says nothing.
      textDeleted: Boolean(account.last_comment_deleted && account.last_comment_text),
      // Which channel that comment went to — NOT always the one the channel column names.
      // A pin outranks the last-comment channel there (see the chain above), so a pinned
      // account can show @news beside a comment it made in @old. The strike is true about
      // the comment either way; without naming the channel it reads as an accusation
      // against the column next to it, which may hold a chip saying zero deletions.
      textChannel: account.last_comment_channel ?? null,
      // The pair's own permanent ban (#30) outranks the channel's aggregate: the
      // aggregate only turns 'banned' once NO account is ready there, so one burnt
      // account among five working ones kept reading the channel's green «Готов» —
      // while the only remedy, adding another account, was being suggested elsewhere.
      // Terminal by design: unlike 'rejoining' there is no attempt left to spend. A
      // spent re-join budget reads the same way and for the same reason — this account
      // left that chat, the others carry on there.
      status: primary?.banned
        ? 'banned'
        : primary?.rejoin_gave_up && !primary.ready
          ? 'rejoin_exhausted'
          : (channelStatus.get(channel) ?? 'no_data'),
      // THIS account in THIS channel, off the very readiness row `primary` already is —
      // the pair the chip sits beside. The channel's own aggregate put the same chip on all
      // five accounts sharing it, and the account's flat total put a deletion from another
      // channel next to whichever channel the row happened to show.
      // The channel aggregate did not just move: it counts a different SET (delivered vs
      // posted) and is what explains a back-off, so it lives on the `CampaignsCard` chips.
      deletedHere: primary?.deleted ?? 0,
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
    <span className="inline-flex animate-pulse items-center gap-tight rounded-full bg-primary-tint px-md py-xs text-tiny font-medium text-primary-deep">
      <span className="size-dot rounded-full bg-primary" />
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
    <div className="border-t border-line-row bg-surface px-lg py-md">
      <div className="mb-sm flex items-center justify-between">
        <div className="flex items-center gap-sm">
          <span className="pl-pulse size-dot shrink-0 rounded-full bg-primary" />
          <span className="type-item-title">{t('neurocomment.feed.title')}</span>
          <span className="rounded-full bg-canvas px-sm py-hair text-tiny font-medium text-ink-muted">
            {comments.length}
          </span>
        </div>
        {onOpenHistory ? (
          <button
            type="button"
            onClick={onOpenHistory}
            className="rounded-full border border-line bg-white px-md py-xs text-tiny font-medium text-primary hover:border-primary"
          >
            {t('neurocomment.feed.history')}
          </button>
        ) : null}
      </div>
      {comments.length === 0 ? (
        <div className="py-lg text-center text-body text-ink-subtle">
          {t('neurocomment.feed.empty')}
        </div>
      ) : (
        <div className="tb-scroll max-h-feed overflow-y-auto">
          {comments.map((c) => {
            const deleted = Boolean(c.deleted_at);
            return (
              <div
                key={`${c.channel}:${String(c.post_id)}`}
                className="flex flex-wrap items-baseline gap-x-md gap-y-hair border-b border-line-row py-sm text-body last:border-b-0"
              >
                <span className="shrink-0 text-ink-subtle">{formatLocalTime(c.created_at)}</span>
                {/* Was shrink-0, which let a long channel (a t.me invite link) push the
                    comment past the card and get clipped by its overflow-hidden. */}
                <span className="min-w-0 truncate text-primary">{c.channel}</span>
                <span
                  // Own line, wrapped, on a phone: sharing one line with the time and
                  // the channel left the comment about a dozen characters of ellipsis,
                  // and the comment is what the operator expanded the row to read.
                  className={`w-full min-w-0 sm:w-auto sm:flex-1 sm:truncate ${deleted ? 'text-ink-subtle line-through' : 'text-ink-muted'}`}
                >
                  {c.comment_text ?? '—'}
                </span>
                {deleted ? <Badge tone="danger">{t('neurocomment.feed.deleted')}</Badge> : null}
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
  displayName,
}: {
  board: NeurocommentBoardData;
  accountsCount: number;
  // Resolves an account's Telegram display name (first + last) from the full
  // account list; falls back to the passed label when the account is unknown.
  displayName: (accountId: string, fallback: string) => string;
  // True while the runtime is actively onboarding (joining channels): the board
  // animates a live indicator instead of reading as an idle "no data" state.
  onboarding?: boolean;
  onOpenAccounts: () => void;
  // Opens the full comment-history modal from an expanded account's sub-row.
  onOpenHistory?: () => void;
}) {
  const { t } = useTranslation();
  const rows = deriveRows(
    board,
    t('neurocomment.board.commentPlaceholder'),
    (board.channels ?? []).length,
    displayName,
  );

  const columns = useMemo<ColumnDef<BoardRow>[]>(
    () => [
      {
        accessorKey: 'account',
        header: t('neurocomment.board.col.account'),
        cell: (info) => info.getValue<string>(),
        meta: {
          cellClassName: 'whitespace-nowrap type-item-title',
          cardSlot: 'title',
        } satisfies DataTableColumnMeta,
      },
      {
        accessorKey: 'channel',
        header: t('neurocomment.board.col.channel'),
        cell: ({ row }) => (
          // Keyed by the channel so a switch remounts the cell and replays `swapin` —
          // the channel now moves with the account's last comment, and a value that
          // changes under the operator's eyes should say so.
          <span
            key={row.original.channel}
            className="tb-swapin inline-flex items-center gap-sm whitespace-nowrap"
          >
            {row.original.channel}
            {/* The hover text carries the scope: the identical «N удалено» string also sits
                on the channel pills in `CampaignsCard`, where it counts every account, and
                a chip that reads 0 here beside a pill that reads 1 is otherwise unexplained
                — the pill also counts a delivered comment the send recorded `failed`. */}
            {row.original.deletedHere > 0 ? (
              <Badge tone="danger" title={t('neurocomment.board.deletedHint')}>
                {t('neurocomment.board.deleted', { count: row.original.deletedHere })}
              </Badge>
            ) : null}
          </span>
        ),
        meta: {
          cellClassName: 'whitespace-nowrap type-prose text-primary',
        } satisfies DataTableColumnMeta,
      },
      {
        accessorKey: 'text',
        header: t('neurocomment.board.col.comment'),
        // Struck through and red when the sweep found this very comment gone — the same
        // vocabulary the expanded feed below the row already uses, minus its «удалён»
        // pill, which a 240px truncating cell has no room for. The text stays: the account
        // did post it, and blanking the cell would read as "never commented".
        cell: ({ row }) =>
          row.original.textDeleted ? (
            <span
              className="text-danger line-through"
              title={
                row.original.textChannel
                  ? t('neurocomment.board.deletedIn', { channel: row.original.textChannel })
                  : t('neurocomment.feed.deleted')
              }
            >
              {row.original.text}
            </span>
          ) : (
            row.original.text
          ),
        meta: {
          cellClassName: 'max-w-name overflow-hidden text-ellipsis whitespace-nowrap type-prose',
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
        meta: { cardSlot: 'control' } satisfies DataTableColumnMeta,
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
            // The row's only control, and a 16px glyph is not a thumb target — the
            // padding/negative-margin pair grows the hit box to 40px without moving the
            // chevron or widening the column it is sized to.
            className={`-m-md flex p-md text-ink-subtle transition-transform duration-reveal ease-spring ${row.getIsExpanded() ? 'rotate-180' : ''}`}
          >
            <Icon name="chevron-down" size={16} />
          </button>
        ),
        // Last column, sized to the chevron so it hugs the row's right edge.
        meta: {
          className: 'w-px',
          cellClassName: 'w-px',
          cardSlot: 'control',
        } satisfies DataTableColumnMeta,
      },
    ],
    [t, onboarding],
  );

  return (
    // CollapsibleCard rather than a hand-rolled `.tb-collapse`: that CSS caps an open
    // body at `var(--mh, 600px)` and clips the rest, which is what swallowed the last
    // account's expanded comments on a phone — six cards are already past 600px before
    // anything expands. The shared card measures its content and then drops the cap.
    <CollapsibleCard
      defaultOpen
      label={t('neurocomment.board.title')}
      headerClassName="border-b border-line-row px-lg py-lg"
      bodyClassName="tb-scroll overflow-x-auto"
      header={
        <>
          <span className="type-card-title">{t('neurocomment.board.title')}</span>
          <span className="rounded-full bg-primary-tint px-sm py-hair text-tiny font-semibold text-primary-deep">
            {t('neurocomment.board.accounts', { count: accountsCount })}
          </span>
        </>
      }
      trailing={
        <div className="flex shrink-0 items-center gap-md">
          {onboarding ? (
            <span className="inline-flex animate-pulse items-center gap-tight rounded-full bg-primary-tint px-md py-xs text-tiny font-semibold text-primary-deep">
              <span className="size-dot rounded-full bg-primary" />
              {t('neurocomment.board.onboardingLive')}
            </span>
          ) : (
            // Hidden on a phone: the header already carries a title, a count pill, the
            // gear and the chevron, and this static label is the one part of it that
            // says nothing actionable — keeping it forced the row to wrap.
            <span className="hidden type-caption sm:inline">{t('neurocomment.board.updated')}</span>
          )}
          <button
            type="button"
            title={t('neurocomment.modal.neuroAccounts.title')}
            aria-label={t('neurocomment.modal.neuroAccounts.title')}
            onClick={onOpenAccounts}
            className="flex size-tile items-center justify-center rounded-lg border border-line bg-white text-ink-muted transition-colors hover:border-primary-line hover:bg-primary-tint hover:text-primary-deep lg:size-icon"
          >
            <Icon name="gear" size={16} />
          </button>
        </div>
      }
    >
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
        <div className="px-lg py-page text-center text-body text-ink-subtle">
          {t('neurocomment.board.empty')}
        </div>
      )}
    </CollapsibleCard>
  );
}
