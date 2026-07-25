import { type ColumnDef } from '@tanstack/react-table';
import { useTranslation } from 'react-i18next';

import type { DiscoveryBoard, DiscoveryCandidate } from '@/shared/api';
import { DataTable, StatusIcon, type DataTableColumnMeta } from '@/shared/ui';

import { formatSubscribers, isSelectable, selectableChannels } from '../model/discovery';

const CHECKBOX = 'h-[14px] w-[14px] shrink-0 accent-primary disabled:opacity-40';

function CommentsCell({ candidate, settled }: { candidate: DiscoveryCandidate; settled: boolean }) {
  const { t } = useTranslation();
  // Polling stops at a terminal phase, so a row still 'pending' by then will never
  // resolve — it reads as unchecked, not as work in progress.
  const state = candidate.qualification ?? 'pending';
  const qualification = settled && state === 'pending' ? 'unknown' : state;
  const label = t(`neurocomment.modal.discovery.comments.${qualification}`);
  if (qualification === 'comments_on') {
    return (
      <span className="inline-flex text-success" role="img" title={label} aria-label={label}>
        <StatusIcon kind="ok" />
      </span>
    );
  }
  if (qualification === 'comments_off') {
    return (
      <span className="inline-flex text-ink-muted" role="img" title={label} aria-label={label}>
        <StatusIcon kind="err" />
      </span>
    );
  }
  // pending: a pulsing dot reads as "still working", which is what the qualification
  // pass is doing while the operator watches. 'unknown' is final, so it stays still.
  return (
    <span className="inline-flex items-center gap-[5px] text-[11.5px] text-ink-subtle">
      <span
        className={`h-[6px] w-[6px] rounded-full bg-line-strong ${
          qualification === 'pending' ? 'animate-pulse' : ''
        }`}
      />
      {label}
    </span>
  );
}

type Props = {
  board: DiscoveryBoard | undefined;
  loading: boolean;
  errored: boolean;
  selected: ReadonlySet<string>;
  onToggle: (channel: string) => void;
  onToggleAll: (channels: string[], next: boolean) => void;
};

export function DiscoveryResults({
  board,
  loading,
  errored,
  selected,
  onToggle,
  onToggleAll,
}: Props) {
  const { t, i18n } = useTranslation();
  const candidates = board?.candidates ?? [];
  const eligible = selectableChannels(candidates);
  const checkedCount = eligible.filter((channel) => selected.has(channel)).length;
  const allChecked = eligible.length > 0 && checkedCount === eligible.length;
  const someChecked = checkedCount > 0 && !allChecked;
  const phase = board?.progress.phase ?? 'idle';
  const failed = phase === 'failed';
  const settled = phase === 'done' || failed;

  const columns: ColumnDef<DiscoveryCandidate>[] = [
    {
      id: 'select',
      header: () => (
        <input
          type="checkbox"
          checked={allChecked}
          disabled={eligible.length === 0}
          ref={(element) => {
            if (element) element.indeterminate = someChecked;
          }}
          onChange={() => {
            onToggleAll(eligible, !allChecked);
          }}
          aria-label={t('neurocomment.modal.discovery.results.selectAll')}
          className={CHECKBOX}
        />
      ),
      cell: ({ row }) => (
        <input
          type="checkbox"
          checked={selected.has(row.original.channel)}
          disabled={!isSelectable(row.original)}
          onChange={() => {
            onToggle(row.original.channel);
          }}
          aria-label={t('neurocomment.modal.discovery.results.select', {
            channel: row.original.channel,
          })}
          className={CHECKBOX}
        />
      ),
      meta: { className: 'w-[38px]', cellClassName: 'w-[38px]' } satisfies DataTableColumnMeta,
    },
    {
      id: 'channel',
      header: () => t('neurocomment.modal.discovery.results.colChannel'),
      cell: ({ row }) => <span className="font-medium">@{row.original.channel}</span>,
    },
    {
      id: 'title',
      header: () => t('neurocomment.modal.discovery.results.colTitle'),
      cell: ({ row }) => (
        <span className="block max-w-[240px] truncate text-ink-muted">
          {row.original.title ?? ''}
        </span>
      ),
    },
    {
      id: 'subscribers',
      header: () => t('neurocomment.modal.discovery.results.colSubscribers'),
      cell: ({ row }) => (
        <span className="tb-time">
          {formatSubscribers(row.original.subscribers, i18n.language || 'ru')}
        </span>
      ),
      meta: { className: 'text-right', cellClassName: 'text-right' } satisfies DataTableColumnMeta,
    },
    {
      id: 'source',
      header: () => t('neurocomment.modal.discovery.results.colSource'),
      cell: ({ row }) => (
        <span className="text-[11.5px] text-ink-subtle">
          {t(`neurocomment.modal.discovery.source.${row.original.source}`)}
        </span>
      ),
    },
    {
      id: 'comments',
      header: () => t('neurocomment.modal.discovery.results.colComments'),
      cell: ({ row }) => <CommentsCell candidate={row.original} settled={settled} />,
    },
    {
      id: 'state',
      header: () => t('neurocomment.modal.discovery.results.colState'),
      cell: ({ row }) => {
        if (row.original.in_campaign === true) {
          return (
            <span className="rounded-full bg-track px-[8px] py-[2px] text-[11px] text-ink-muted">
              {t('neurocomment.modal.discovery.results.inCampaign')}
            </span>
          );
        }
        if (row.original.taken_by_other_campaign === true) {
          return (
            <span className="rounded-full bg-track px-[8px] py-[2px] text-[11px] text-ink-muted">
              {t('neurocomment.modal.discovery.results.takenElsewhere')}
            </span>
          );
        }
        return null;
      },
    },
  ];

  // Candidates are replaced only after the whole search stage, so any rows still on
  // screen while it runs belong to the PREVIOUS run — never show them as results.
  if (loading) {
    return (
      <p className="py-[26px] text-center text-[12.5px] text-ink-subtle">
        {t('neurocomment.modal.discovery.results.searching')}
      </p>
    );
  }

  if (errored) {
    return (
      <p className="py-[26px] text-center text-[12.5px] text-danger">
        {t('neurocomment.modal.discovery.results.error')}
      </p>
    );
  }

  if (failed && candidates.length === 0) {
    return (
      <p className="py-[26px] text-center text-[12.5px] text-danger">
        {t('neurocomment.modal.discovery.results.failed', {
          reason: board?.progress.last_error ?? '',
        })}
      </p>
    );
  }

  if (candidates.length === 0) {
    return (
      <p className="py-[26px] text-center text-[12.5px] text-ink-subtle">
        {t('neurocomment.modal.discovery.results.empty')}
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-[9px]">
      <div className="flex items-center justify-between gap-2 text-[11.5px] text-ink-subtle">
        <span>{t('neurocomment.modal.discovery.results.count', { count: candidates.length })}</span>
        {phase === 'qualifying' ? (
          <span className="tb-pulse">
            {t('neurocomment.modal.discovery.results.qualifying', {
              done: board?.progress.qualified ?? 0,
              total: board?.progress.total ?? 0,
            })}
          </span>
        ) : null}
        {board?.progress.last_error != null && phase !== 'qualifying' ? (
          <span className="text-danger">
            {/* An aborted run keeps whatever it collected, so the reason has to ride
                along with the rows instead of replacing them. */}
            {t(`neurocomment.modal.discovery.results.${failed ? 'failed' : 'degraded'}`, {
              reason: board.progress.last_error,
            })}
          </span>
        ) : null}
      </div>
      <div className="tb-scroll overflow-x-auto">
        <DataTable
          data={candidates}
          columns={columns}
          getRowProps={(row) => ({
            className: isSelectable(row.original) ? undefined : 'opacity-60',
          })}
        />
      </div>
    </div>
  );
}
