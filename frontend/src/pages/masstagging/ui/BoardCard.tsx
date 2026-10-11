// The board «Цели», by person: "in work" and "done" tabs beside the title, the gear for
// «Темп» at the right, an order fixed when the board first loads, and a row that opens into
// its actions and its history. An empty list says where people come from; while the
// campaign rests until tomorrow, one sentence replaces the table of identical rows.
import type { ColumnDef } from '@tanstack/react-table';
import type { TFunction } from 'i18next';
import { type ReactNode, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { AccountAvatar, accountDisplayName } from '@/entities/account';
import type { AccountRead, MasstaggingBoard, MasstaggingBoardRow } from '@/shared/api';
import { FOCUS_RING } from '@/shared/design-system';
import {
  Badge,
  Button,
  CollapsibleCard,
  DataTable,
  Icon,
  type IconName,
  IconButton,
  SegmentedControl,
} from '@/shared/ui';

import {
  defaultTab,
  historyText,
  historyTone,
  ROW_TONE,
  rowActions,
  splitRows,
  whenText,
} from '../model/board';

export type RowAction = 'now' | 'skip' | 'requeue';

const HISTORY_TONE = {
  muted: 'text-content-muted',
  info: 'text-info-strong',
  warning: 'text-warning-deep',
  danger: 'text-danger',
} as const;

type Shared = {
  fleet: Map<string, AccountRead>;
  time: (iso: string) => string;
  status: MasstaggingBoard['campaign']['status'];
  stories: boolean;
};

function AccountFace({
  fleet,
  accountId,
}: {
  fleet: Map<string, AccountRead>;
  accountId?: string | null;
}) {
  const account = accountId ? fleet.get(accountId) : undefined;
  if (account === undefined) return <span className="text-content-subtle">—</span>;
  const name = accountDisplayName(account);
  return (
    <span
      title={account.username ? `${name} · @${account.username}` : name}
      className="inline-flex"
    >
      <AccountAvatar
        account={account}
        className="size-chip rounded-full"
        fallbackClassName="bg-info-tint text-small font-medium text-info-strong"
      />
    </span>
  );
}

// History entries carry no id: the same event at the same second is told apart by how
// many came before it.
function historyKeys(entries: MasstaggingBoardRow['history']) {
  const seen = new Map<string, number>();
  return entries.map((entry) => {
    const base = `${entry.at}-${entry.event}-${entry.detail ?? ''}`;
    const count = (seen.get(base) ?? 0) + 1;
    seen.set(base, count);
    return { entry, key: `${base}-${String(count)}` };
  });
}

function TargetDetails({
  row,
  shared,
  actionable,
  busy,
  onAction,
}: {
  row: MasstaggingBoardRow;
  shared: Shared;
  actionable: boolean;
  busy: boolean;
  onAction: (action: RowAction) => void;
}) {
  const { t } = useTranslation();
  const actions = actionable ? rowActions(row) : [];
  return (
    <div className="border-t border-canvas bg-surface px-4 py-3">
      {actions.length > 0 ? (
        <div className="mb-3 flex flex-wrap items-center gap-2">
          {actions.includes('now') ? (
            <Button
              size="sm"
              variant="primary"
              disabled={busy}
              onClick={() => {
                onAction('now');
              }}
            >
              <Icon name="send" size={12} />
              {shared.stories ? t('masstagging.board.tagNow') : t('masstagging.board.primeNow')}
            </Button>
          ) : null}
          {actions.includes('skip') ? (
            <Button
              size="sm"
              variant="ghost"
              disabled={busy}
              onClick={() => {
                onAction('skip');
              }}
            >
              {t('masstagging.board.skip')}
            </Button>
          ) : null}
          {actions.includes('requeue') ? (
            <Button
              size="sm"
              disabled={busy}
              onClick={() => {
                onAction('requeue');
              }}
            >
              {t('masstagging.board.requeue')}
            </Button>
          ) : null}
        </div>
      ) : null}
      {row.history.length === 0 ? (
        <div className="type-small">{t('masstagging.board.untouched')}</div>
      ) : (
        historyKeys([...row.history].reverse()).map(({ entry, key }) => (
          <div
            key={key}
            className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-canvas py-2 text-body last:border-b-0"
          >
            <span className="shrink-0 text-content-subtle tabular-nums">
              {shared.time(entry.at)}
            </span>
            <AccountFace fleet={shared.fleet} accountId={entry.account_id} />
            <span className={HISTORY_TONE[historyTone(entry)]}>
              {historyText(t, entry, shared.stories)}
            </span>
          </div>
        ))
      )}
    </div>
  );
}

function columns(t: TFunction, shared: Shared): ColumnDef<MasstaggingBoardRow>[] {
  return [
    {
      id: 'target',
      header: t('masstagging.board.columns.target'),
      cell: ({ row }) => row.original.target,
      meta: { cellClassName: 'whitespace-nowrap type-body text-action-primary', cardSlot: 'title' },
    },
    {
      id: 'when',
      header: t('masstagging.board.columns.when'),
      cell: ({ row }) => whenText(t, row.original, shared.status, shared.time),
      meta: { cellClassName: 'whitespace-nowrap type-body text-content-subtle tabular-nums' },
    },
    {
      id: 'account',
      header: t('masstagging.board.columns.account'),
      cell: ({ row }) => <AccountFace fleet={shared.fleet} accountId={row.original.account_id} />,
    },
    {
      id: 'status',
      header: t('masstagging.board.columns.status'),
      cell: ({ row }) => (
        <span className="inline-flex items-center gap-2 whitespace-nowrap">
          <Badge tone={ROW_TONE[row.original.state]}>
            {t(`masstagging.board.state.${row.original.state}`)}
          </Badge>
          {row.original.reason ? (
            <span className="type-small">{t(`masstagging.reason.${row.original.reason}`)}</span>
          ) : null}
        </span>
      ),
      meta: { cardSlot: 'control' },
    },
    {
      id: 'expander',
      header: () => <span className="sr-only">{t('masstagging.board.columns.details')}</span>,
      cell: ({ row }) => (
        <button
          type="button"
          aria-label={t('masstagging.board.columns.details')}
          aria-expanded={row.getIsExpanded()}
          onClick={row.getToggleExpandedHandler()}
          className={`-m-3 flex rounded-md p-3 text-content-subtle transition duration-reveal ease-spring hover:text-content-primary ${FOCUS_RING} ${row.getIsExpanded() ? 'rotate-180' : ''}`}
        >
          <Icon name="chevron-down" size={16} />
        </button>
      ),
      meta: { className: 'w-0', cellClassName: 'w-0', cardSlot: 'control' },
    },
  ];
}

function EmptyBody({
  icon,
  title,
  note,
  action,
}: {
  icon: IconName;
  title: string;
  note?: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center gap-2 px-4 py-8 text-center">
      <Icon name={icon} size={20} className="text-content-subtle" />
      <div className="type-body-medium">{title}</div>
      {note === undefined ? null : <div className="type-body text-content-subtle">{note}</div>}
      {action}
    </div>
  );
}

export function BoardCard({
  board,
  fleet,
  time,
  stories,
  busy,
  onAction,
  onPace,
  onAddTargets,
}: {
  board: MasstaggingBoard;
  fleet: Map<string, AccountRead>;
  time: (iso: string) => string;
  stories: boolean;
  busy: boolean;
  onAction: (row: MasstaggingBoardRow, action: RowAction) => void;
  onPace: () => void;
  onAddTargets: () => void;
}) {
  const { t } = useTranslation();
  const status = board.campaign.status;
  // The order is fixed once per load, so a row the operator acts on stays put.
  const [order] = useState(() => {
    const split = splitRows(board.rows);
    return [...split.active, ...split.done].map((row) => row.target_key);
  });
  const rank = new Map(order.map((key, index) => [key, index]));
  const sorted = [...board.rows].sort(
    (a, b) => (rank.get(a.target_key) ?? order.length) - (rank.get(b.target_key) ?? order.length),
  );
  const active = sorted.filter((row) => row.active);
  const done = sorted.filter((row) => !row.active);
  const [tab, setTab] = useState(() => defaultTab(status, active.length, done.length));
  const [showResting, setShowResting] = useState(false);
  const shown = tab === 'active' ? active : done;
  const shared: Shared = { fleet, time, status, stories };
  // A campaign never started shows its saved list as the coming queue: nothing to act on.
  const actionable = status !== 'draft' && status !== 'ready';

  let body: ReactNode;
  if (board.rows.length === 0) {
    body = (
      <EmptyBody
        icon="users"
        title={t('masstagging.board.empty.title')}
        note={t('masstagging.board.empty.note')}
        action={
          <Button size="sm" variant="primary" onClick={onAddTargets}>
            {t('masstagging.board.empty.add')}
          </Button>
        }
      />
    );
  } else if (status === 'resting' && tab === 'active' && !showResting) {
    body = (
      <EmptyBody
        icon="clock"
        title={t('masstagging.board.resting.title', {
          time: board.campaign.rest_until ? time(board.campaign.rest_until) : '09:00',
          count: active.length,
        })}
        note={t('masstagging.board.resting.note')}
        action={
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              setShowResting(true);
            }}
          >
            {t('masstagging.board.resting.show')}
          </Button>
        }
      />
    );
  } else if (shown.length === 0) {
    body = (
      <div className="px-4 py-8 text-center type-body text-content-subtle">
        {tab === 'active' ? t('masstagging.board.queueEmpty') : t('masstagging.board.noneDone')}
      </div>
    );
  } else {
    body = (
      <DataTable
        data={shown}
        columns={columns(t, shared)}
        getRowId={(row) => row.target_key}
        renderSubRow={(row) => (
          <TargetDetails
            row={row.original}
            shared={shared}
            actionable={actionable}
            busy={busy}
            onAction={(action) => {
              onAction(row.original, action);
            }}
          />
        )}
      />
    );
  }

  return (
    <CollapsibleCard
      defaultOpen
      label={t('masstagging.board.title')}
      headerClassName="border-b border-canvas px-4 py-4"
      bodyClassName="tb-scroll overflow-x-auto"
      title={t('masstagging.board.title')}
      aside={
        board.rows.length === 0 ? undefined : (
          <SegmentedControl
            variant="pill"
            value={tab}
            ariaLabel={t('masstagging.board.tabs')}
            options={[
              {
                value: 'active',
                label: t('masstagging.board.tabActive', { count: active.length }),
              },
              { value: 'done', label: t('masstagging.board.tabDone', { count: done.length }) },
            ]}
            onChange={setTab}
          />
        )
      }
      trailing={
        <IconButton
          size="touch"
          tone="primary"
          title={t('masstagging.paceDialog.title')}
          aria-label={t('masstagging.paceDialog.title')}
          onClick={onPace}
          className="rounded-md sm:size-tile lg:size-icon"
        >
          <Icon name="gear" size={16} />
        </IconButton>
      }
    >
      {body}
    </CollapsibleCard>
  );
}
