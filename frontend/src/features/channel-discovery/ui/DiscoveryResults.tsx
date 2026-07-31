import { type ColumnDef } from '@tanstack/react-table';
import { useRef } from 'react';
import { useTranslation } from 'react-i18next';

import type { DiscoveryBoard, DiscoveryCandidate, DiscoverySourceReport } from '@/shared/api';
import { DataTable, StatusIcon, useWideContainer, type DataTableColumnMeta } from '@/shared/ui';

import { formatSubscribers, isSelectable, selectableChannels } from '../model/discovery';

const CHECKBOX = 'h-[14px] w-[14px] shrink-0 accent-primary disabled:opacity-40';

const SOURCE_STATE = {
  ran: 'sourceRan',
  failed: 'sourceFailed',
  skipped: 'sourceSkipped',
} as const;

// Reasons are deliberately locale-neutral codes, and printing them raw put strings like
// "telemetr_quota_exhausted" in front of the operator. Unmapped codes fall back to the
// code itself, so a new one degrades to the old behaviour rather than to nothing.
const reasonKey = (reason: string) => `neurocomment.modal.discovery.results.reason.${reason}`;

/** One line per source: what it returned, and what survived into the table.
 *
 * The operator could set a language and a country, watch a run reach "done", and never
 * learn that the only source those two filters reach had contributed nothing — because
 * the board carried a single error string and a skipped source carried none at all. A
 * source crediting `0` here is that missing signal.
 */
function SourceStrip({ sources }: { sources: DiscoverySourceReport[] }) {
  const { t } = useTranslation();
  if (sources.length === 0) return null;
  return (
    <span className="text-ink-subtle">
      {sources
        .map((report) => {
          const name = t(`neurocomment.modal.discovery.source.${report.source}`);
          const kept = report.kept ?? 0;
          const exclusive = report.exclusive ?? 0;
          let line = t(`neurocomment.modal.discovery.results.${SOURCE_STATE[report.state]}`, {
            source: name,
            kept,
            hits: report.hits ?? 0,
          });
          // "50 of 60" hid the case where all 50 were duplicates of another source and
          // every row this one found alone was cut by the cap.
          if (exclusive !== kept) {
            line += ` ${t('neurocomment.modal.discovery.results.sourceExclusive', { exclusive })}`;
          }
          // A capped page otherwise reads as the whole answer.
          if (report.total != null && report.total > (report.hits ?? 0)) {
            line += ` ${t('neurocomment.modal.discovery.results.sourceTruncated', {
              total: report.total,
            })}`;
          }
          // A skipped or failed source is the whole point of this strip, so it says why.
          if (report.reason == null) return line;
          return `${line} — ${t(reasonKey(report.reason), { defaultValue: report.reason })}`;
        })
        .join(' · ')}
    </span>
  );
}

function CommentsCell({ candidate, settled }: { candidate: DiscoveryCandidate; settled: boolean }) {
  const { t } = useTranslation();
  // 'pending' means never probed, which the backend keeps distinct from 'unknown'
  // (probed, unanswerable). Once the run has stopped nothing will probe it, so it has
  // to read as "not checked yet" — a re-run resolves those, unlike 'unknown'.
  const state = candidate.qualification ?? 'pending';
  const qualification = settled && state === 'pending' ? 'notChecked' : state;
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
  // pass is doing while the operator watches. 'unknown' and 'notChecked' are final,
  // so they stay still.
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
  /** Did this run ask for a language or country? Only then is a missing geo a finding. */
  localeFiltered: boolean;
  selected: ReadonlySet<string>;
  onToggle: (channel: string) => void;
  onToggleAll: (channels: string[], next: boolean) => void;
};

export function DiscoveryResults({
  board,
  loading,
  errored,
  localeFiltered,
  selected,
  onToggle,
  onToggleAll,
}: Props) {
  const { t, i18n } = useTranslation();
  // Must be the container query DataTable itself uses, not the viewport one: this table
  // lives in a 920px modal whose padding leaves it 884px, 4px over the 880px table/card
  // floor — so on a narrower viewport the table renders as cards while a viewport query
  // would still say "table", and the select-all below would go missing.
  const results = useRef<HTMLDivElement>(null);
  const wide = useWideContainer(results);
  const candidates = board?.candidates ?? [];
  const eligible = selectableChannels(candidates);
  const checkedCount = eligible.filter((channel) => selected.has(channel)).length;
  const allChecked = eligible.length > 0 && checkedCount === eligible.length;
  const someChecked = checkedCount > 0 && !allChecked;
  const phase = board?.progress.phase ?? 'idle';
  const failed = phase === 'failed';
  // The predicate that stops the poll, not the phase: a frame with running:false is
  // the last one whatever phase it claims. A restarted backend forgets the in-memory
  // phase but still serves the stored rows as 'idle', and those must not pulse on.
  const running = board?.progress.running === true;
  const settled = !running;
  const qualified = board?.progress.qualified ?? 0;
  const total = board?.progress.total ?? 0;

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
      // cardSlot 'control' is load-bearing here, not cosmetic: this column's header
      // is the select-all checkbox, so as a card *label* it would render one
      // select-all per card, each toggling the whole result set.
      meta: {
        className: 'w-[38px]',
        cellClassName: 'w-[38px]',
        cardSlot: 'control',
      } satisfies DataTableColumnMeta,
    },
    {
      id: 'channel',
      header: () => t('neurocomment.modal.discovery.results.colChannel'),
      cell: ({ row }) => <span className="font-medium">@{row.original.channel}</span>,
      meta: { cardSlot: 'title' } satisfies DataTableColumnMeta,
    },
    {
      id: 'title',
      header: () => t('neurocomment.modal.discovery.results.colTitle'),
      cell: ({ row }) => (
        // Capped only where there is room for it: 240px plus the card's own padding
        // overflows the dialog box at a 320px viewport.
        <span className="block truncate text-ink-muted md:max-w-[240px]">
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
      cell: ({ row }) => {
        // The catalogue is the only source that files a channel under a country and a
        // language, so its geo is the per-row proof that the filter reached THIS row.
        // Its absence, when a locale filter was asked for, is the proof that it did not:
        // Telegram's own search has no such filter and its rows are simply unvouched.
        const geo = [row.original.country, row.original.language].filter(Boolean).join(' · ');
        return (
          <span className="text-[11.5px] text-ink-subtle">
            {t(`neurocomment.modal.discovery.source.${row.original.source}`)}
            {geo !== '' ? <span className="ml-[5px] text-ink-muted">{geo}</span> : null}
            {geo === '' && localeFiltered ? (
              <span className="ml-[5px] text-warning">
                {t('neurocomment.modal.discovery.results.unfiltered')}
              </span>
            ) : null}
          </span>
        );
      },
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
      // 'control' rather than a labelled row: the cell is null for most rows, which
      // as a labelled row would leave an empty "state" stub in every card.
      meta: { cardSlot: 'control' } satisfies DataTableColumnMeta,
    },
  ];

  // Candidates are replaced only after the whole search stage, so any rows still on
  // screen while it runs belong to the PREVIOUS run — never show them as results.
  // role=status on every transient state: a search runs 30s+, so a screen-reader
  // operator has to be told when it finishes or fails without polling the table.
  if (loading) {
    return (
      <p role="status" className="py-[26px] text-center text-[12.5px] text-ink-subtle">
        {t('neurocomment.modal.discovery.results.searching')}
      </p>
    );
  }

  // Only with nothing to fall back on: a failed refetch leaves status 'error' with the
  // cached frame intact, and blanking the table would take N rows and every tick the
  // operator has made with it.
  if (errored && candidates.length === 0) {
    return (
      <p role="status" className="py-[26px] text-center text-[12.5px] text-danger">
        {t('neurocomment.modal.discovery.results.error')}
      </p>
    );
  }

  if (failed && candidates.length === 0) {
    // A catalogue that is terminally down (revoked key, lapsed plan, spent quota) blocks
    // EVERY run while a locale filter is set, because storing unfiltered rows over a
    // filtered set is a downgrade. Nothing said so, and nothing named the way out.
    const catalogueDown =
      localeFiltered &&
      (board?.progress.sources ?? []).some(
        (report) => report.source === 'telemetr' && report.state === 'failed',
      );
    return (
      <p role="status" className="py-[26px] text-center text-[12.5px] text-danger">
        {t('neurocomment.modal.discovery.results.failed', {
          reason:
            board?.progress.last_error == null
              ? ''
              : t(reasonKey(board.progress.last_error), {
                  defaultValue: board.progress.last_error,
                }),
        })}
        {catalogueDown ? (
          <span className="mt-[6px] block text-ink-subtle">
            {t('neurocomment.modal.discovery.results.catalogueDown')}
          </span>
        ) : null}
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
        {/* The card layout has no column headers, and select-all lives in one — so on
            a phone the operator could otherwise only tap candidates one at a time.
            Branch on the same JS query DataTable uses, not `lg:hidden`: two
            select-alls in the DOM would both answer every query by accessible name. */}
        {wide ? null : (
          <label className="flex items-center gap-[7px]">
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
            {t('neurocomment.modal.discovery.results.selectAll')}
          </label>
        )}
        <span>{t('neurocomment.modal.discovery.results.count', { count: candidates.length })}</span>
        {/* Also the only trace of how far an aborted run got ("40/300"), so it has to
            outlive the qualifying phase. */}
        {phase === 'qualifying' || qualified < total ? (
          <span role="status" className={running ? 'tb-pulse' : undefined}>
            {t('neurocomment.modal.discovery.results.qualifying', { done: qualified, total })}
          </span>
        ) : null}
        {board?.progress.last_error != null ? (
          <span role="status" className="text-danger">
            {/* An aborted run keeps whatever it collected, so the reason has to ride
                along with the rows instead of replacing them — and through the
                qualifying phase too, the longest one a run has. */}
            {t(`neurocomment.modal.discovery.results.${failed ? 'failed' : 'degraded'}`, {
              reason: t(reasonKey(board.progress.last_error), {
                defaultValue: board.progress.last_error,
              }),
            })}
          </span>
        ) : null}
        <SourceStrip sources={board?.progress.sources ?? []} />
      </div>
      <div ref={results} className="tb-scroll overflow-x-auto">
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
