// The work board, by chat: "in progress" and "finished" tabs, filter buttons by status
// group, an order by importance fixed when the board first loads, and a row that opens
// into its actions and its history. While every chat rests, one sentence replaces the
// table of identical "resting until" rows.
import type { ColumnDef } from '@tanstack/react-table';
import type { TFunction } from 'i18next';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { AccountAvatar, accountDisplayName } from '@/entities/account';
import type { AccountRead, ChatBroadcastBoard, ChatBroadcastBoardRow } from '@/shared/api';
import { BAR_FILL, BAR_TRACK } from '@/shared/design-system';
import {
  Badge,
  Button,
  CollapsibleCard,
  DataTable,
  HelpHint,
  Icon,
  SegmentedControl,
  Select,
} from '@/shared/ui';

import type { GroupId, RowView } from '../model/board';
import {
  ACTIVE_GROUPS,
  allResting,
  applyOrder,
  DONE_GROUPS,
  historyLine,
  inGroup,
  orderOf,
  rowView,
} from '../model/board';

export type RowAction = { kind: 'now' } | { kind: 'skip' } | { kind: 'hand'; accountId: string };

type Shared = {
  board: ChatBroadcastBoard;
  fleet: Map<string, AccountRead>;
  time: (iso: string) => string;
  now: number;
  approvalHours: number;
};

const HISTORY_TONE = {
  sent: 'text-content-muted',
  waiting: 'text-warning-deep',
  handed: 'text-info-strong',
  skipped: 'text-danger',
  deleted: 'text-danger line-through',
} as const;

function nameOf(fleet: Map<string, AccountRead>, id: string): string {
  const account = fleet.get(id);
  return account === undefined ? id : accountDisplayName(account);
}

function AccountDot({
  fleet,
  accountId,
}: {
  fleet: Map<string, AccountRead>;
  accountId?: string | null;
}) {
  const account = accountId ? fleet.get(accountId) : undefined;
  if (account === undefined) return <span className="text-content-subtle">—</span>;
  return (
    <span
      title={account.username ? `@${account.username}` : accountDisplayName(account)}
      className="inline-flex"
    >
      <AccountAvatar
        account={account}
        className="size-chip rounded-full"
        fallbackClassName="bg-info-tint text-tiny font-semibold text-info-strong"
      />
    </span>
  );
}

function ChatHistory({
  row,
  shared,
  busy,
  onAction,
}: {
  row: ChatBroadcastBoardRow;
  shared: Shared;
  busy: boolean;
  onAction: (action: RowAction) => void;
}) {
  const { t } = useTranslation();
  const waiting =
    row.active &&
    row.state === 'waiting' &&
    row.next_action_at !== null &&
    row.next_action_at !== undefined &&
    Date.parse(row.next_action_at) > shared.now;
  const crew = shared.board.accounts.filter(
    (account) => account.state === 'active' && account.account_id !== row.account_id,
  );
  return (
    <div className="border-t border-line-row bg-surface px-lg py-md">
      {row.active ? (
        <div className="mb-md flex flex-wrap items-center gap-md">
          {waiting ? (
            <span className="flex items-center gap-xs">
              <Button
                size="xs"
                variant="primary"
                disabled={busy}
                onClick={() => {
                  onAction({ kind: 'now' });
                }}
              >
                <Icon name="send" size={12} />
                {t('chatBroadcast.board.now')}
              </Button>
              <HelpHint
                text={t('chatBroadcast.board.nowHint')}
                example={t('chatBroadcast.board.nowExample')}
              />
            </span>
          ) : null}
          {crew.length === 0 ? null : (
            <span className="flex items-center gap-xs">
              <span className="w-col">
                <Select
                  value=""
                  placeholder={t('chatBroadcast.board.hand')}
                  ariaLabel={t('chatBroadcast.board.handLabel')}
                  disabled={busy}
                  options={crew.map((account) => ({
                    value: account.account_id,
                    label: nameOf(shared.fleet, account.account_id),
                  }))}
                  onChange={(accountId) => {
                    onAction({ kind: 'hand', accountId });
                  }}
                />
              </span>
              <HelpHint
                text={t('chatBroadcast.board.handHint')}
                example={t('chatBroadcast.board.handExample')}
              />
            </span>
          )}
          <span className="flex items-center gap-xs">
            <Button
              size="xs"
              variant="danger"
              disabled={busy}
              onClick={() => {
                onAction({ kind: 'skip' });
              }}
            >
              {t('chatBroadcast.board.skip')}
            </Button>
            <HelpHint
              text={t('chatBroadcast.board.skipHint')}
              example={t('chatBroadcast.board.skipExample')}
            />
          </span>
        </div>
      ) : null}
      <div className="mb-sm flex items-center gap-sm">
        <span className="pl-pulse size-dot shrink-0 rounded-full bg-action-primary" />
        <span className="type-item-title">{t('chatBroadcast.board.history')}</span>
        <Badge tone="neutral" size="xs">
          {row.history.length}
        </Badge>
      </div>
      <div className="tb-scroll max-h-feed overflow-y-auto">
        {row.history.map((entry, index) => {
          const line = historyLine(t, entry, {
            time: shared.time,
            nameOf: (id) => nameOf(shared.fleet, id),
            approvalHours: shared.approvalHours,
          });
          return (
            <div
              key={`${entry.at}-${String(index)}`}
              className="flex flex-wrap items-center gap-x-md gap-y-hair border-b border-line-row py-sm text-body last:border-b-0"
            >
              <span className="shrink-0 text-content-subtle tabular-nums">
                {shared.time(entry.at)}
              </span>
              <AccountDot fleet={shared.fleet} accountId={entry.account_id} />
              <Badge size="xs">{t('chatBroadcast.board.round', { round: entry.round })}</Badge>
              <span
                className={`w-full min-w-0 sm:w-auto sm:flex-1 sm:truncate ${HISTORY_TONE[line.tone]}`}
              >
                {line.text}
              </span>
              {line.tone === 'deleted' ? (
                <Badge tone="danger">{t('chatBroadcast.board.deleted')}</Badge>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}

type Row = ChatBroadcastBoardRow & { view: RowView };

function columnsOf(t: TFunction, shared: Shared): ColumnDef<Row>[] {
  const rounds = shared.board.counters.rounds ?? null;
  return [
    {
      id: 'chat',
      header: t('chatBroadcast.board.columns.chat'),
      cell: ({ row }) => (
        <span className="inline-flex items-center gap-sm whitespace-nowrap">
          {row.original.title ?? row.original.raw}
          {row.original.message_deleted ? (
            <span title={t('chatBroadcast.board.deletedHint')}>
              <Badge tone="danger">{t('chatBroadcast.board.deleted')}</Badge>
            </span>
          ) : null}
        </span>
      ),
      meta: {
        cellClassName: 'whitespace-nowrap type-prose text-action-primary',
        cardSlot: 'title',
      },
    },
    {
      id: 'when',
      header: t('chatBroadcast.board.columns.when'),
      cell: ({ row }) => row.original.view.when,
      meta: { cellClassName: 'whitespace-nowrap type-prose tabular-nums' },
    },
    {
      id: 'account',
      header: t('chatBroadcast.board.columns.account'),
      cell: ({ row }) => <AccountDot fleet={shared.fleet} accountId={row.original.account_id} />,
    },
    {
      id: 'left',
      header: t('chatBroadcast.board.columns.left'),
      cell: ({ row }) => {
        const item = row.original;
        if (item.state === 'skipped') return <span className="text-content-subtle">—</span>;
        const total = item.planned_total ?? null;
        return (
          <div className="w-col">
            {total === null || total === 0 ? null : (
              <div className={`${BAR_TRACK} w-full`}>
                <div
                  className={`${BAR_FILL} bg-action-primary`}
                  style={{
                    width: `${String(Math.min(100, Math.round((item.sent_total / total) * 100)))}%`,
                  }}
                />
              </div>
            )}
            <div className="mt-xs whitespace-nowrap type-caption tabular-nums">
              {total === null || rounds === null
                ? t('chatBroadcast.board.leftEndless', { sent: item.sent_total, round: item.round })
                : t('chatBroadcast.board.left', {
                    left: Math.max(0, total - item.sent_total),
                    total,
                    round: item.round,
                    rounds,
                  })}
            </div>
          </div>
        );
      },
    },
    {
      id: 'status',
      header: t('chatBroadcast.board.columns.status'),
      cell: ({ row }) => <Badge tone={row.original.view.tone}>{row.original.view.label}</Badge>,
      meta: { cardSlot: 'control' },
    },
    {
      id: 'expander',
      header: () => <span className="sr-only">{t('chatBroadcast.board.columns.details')}</span>,
      cell: ({ row }) => (
        <button
          type="button"
          aria-label={t('chatBroadcast.board.columns.details')}
          aria-expanded={row.getIsExpanded()}
          onClick={row.getToggleExpandedHandler()}
          className={`-m-md flex p-md text-content-subtle transition duration-reveal ease-spring hover:text-content-primary ${row.getIsExpanded() ? 'rotate-180' : ''}`}
        >
          <Icon name="chevron-down" size={16} />
        </button>
      ),
      meta: { className: 'w-px', cellClassName: 'w-px', cardSlot: 'control' },
    },
  ];
}

export function BoardCard({
  board,
  fleet,
  time,
  now,
  approvalHours,
  busy,
  onAction,
}: {
  board: ChatBroadcastBoard;
  fleet: Map<string, AccountRead>;
  time: (iso: string) => string;
  now: number;
  approvalHours: number;
  busy: boolean;
  onAction: (row: ChatBroadcastBoardRow, action: RowAction) => void;
}) {
  const { t } = useTranslation();
  const shared: Shared = { board, fleet, time, now, approvalHours };
  // Fixed once per board: rows the operator acts on keep their place.
  const [order] = useState(() => orderOf(board.rows));
  const rows: Row[] = applyOrder(board.rows, order).map((row) => ({
    ...row,
    view: rowView(t, row, { board, now, time, approvalHours }),
  }));
  const active = rows.filter((row) => row.active);
  const done = rows.filter((row) => !row.active);
  const [tab, setTab] = useState<'active' | 'done'>(
    active.length === 0 && done.length > 0 ? 'done' : 'active',
  );
  const [group, setGroup] = useState<GroupId | 'all'>('all');
  const [showResting, setShowResting] = useState(false);
  const inTab = tab === 'active' ? active : done;
  const groups = (tab === 'active' ? ACTIVE_GROUPS : DONE_GROUPS)
    .map((id) => ({ id, count: inTab.filter((row) => inGroup(row, id)).length }))
    .filter((item) => item.count > 0);
  const picked = groups.find((item) => item.id === group);
  const shown = picked === undefined ? inTab : inTab.filter((row) => inGroup(row, picked.id));
  const resting = tab === 'active' && board.phase === 'resting' && allResting(active);
  const draft = board.phase === 'draft' && rows.length === 0;

  let body;
  if (draft) {
    body = (
      <div className="px-lg py-page text-center type-prose">{t('chatBroadcast.board.empty')}</div>
    );
  } else if (resting && !showResting) {
    body = (
      <div className="flex flex-col items-center gap-sm px-lg py-page text-center">
        <Icon name="clock" size={20} className="text-content-subtle" />
        <div className="type-item-title">
          {t('chatBroadcast.board.resting', {
            time: board.campaign.rest_until ? time(board.campaign.rest_until) : '—',
          })}
        </div>
        <div className="type-prose">{t('chatBroadcast.board.restingNote')}</div>
        <Button
          size="xs"
          variant="ghost"
          onClick={() => {
            setShowResting(true);
          }}
        >
          {t('chatBroadcast.board.showChats')}
        </Button>
      </div>
    );
  } else if (inTab.length === 0) {
    body = (
      <div className="px-lg py-page text-center type-prose">
        {tab === 'active' ? t('chatBroadcast.board.noneActive') : t('chatBroadcast.board.noneDone')}
      </div>
    );
  } else {
    body = (
      <>
        {groups.length > 1 ? (
          <div className="flex flex-wrap items-center gap-sm border-b border-line-row px-lg py-sm">
            {[{ id: 'all' as const, count: inTab.length }, ...groups].map((item) => {
              const on = item.id === group || (item.id === 'all' && picked === undefined);
              return (
                <Button
                  key={item.id}
                  size="xs"
                  variant={on ? 'primary' : 'secondary'}
                  onClick={() => {
                    setGroup(item.id);
                  }}
                >
                  {item.id === 'all'
                    ? t('chatBroadcast.board.all')
                    : t(`chatBroadcast.board.groups.${item.id}`)}{' '}
                  · {item.count}
                </Button>
              );
            })}
          </div>
        ) : null}
        <DataTable
          data={shown}
          columns={columnsOf(t, shared)}
          getRowId={(row) => row.chat_key}
          renderSubRow={(row) => (
            <ChatHistory
              row={row.original}
              shared={shared}
              busy={busy}
              onAction={(action) => {
                onAction(row.original, action);
              }}
            />
          )}
        />
      </>
    );
  }

  return (
    <CollapsibleCard
      defaultOpen
      label={t('chatBroadcast.board.title')}
      headerClassName="border-b border-line-row px-lg py-lg"
      bodyClassName="tb-scroll overflow-x-auto"
      header={<span className="type-card-title">{t('chatBroadcast.board.title')}</span>}
      trailing={
        <SegmentedControl
          variant="pill"
          value={tab}
          ariaLabel={t('chatBroadcast.board.tabs')}
          options={[
            { value: 'active', label: t('chatBroadcast.board.active', { count: active.length }) },
            { value: 'done', label: t('chatBroadcast.board.done', { count: done.length }) },
          ]}
          onChange={(next) => {
            setTab(next);
            setGroup('all');
          }}
        />
      }
    >
      {body}
    </CollapsibleCard>
  );
}
