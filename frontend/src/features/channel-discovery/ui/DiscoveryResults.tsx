import { type ColumnDef } from '@tanstack/react-table';
import { useRef } from 'react';
import { useTranslation } from 'react-i18next';

import type {
  DiscoveryBoard,
  DiscoveryCandidate,
  DiscoveryChannelVerdict,
  DiscoverySourceReport,
} from '@/shared/api';
import { DataTable, StatusIcon, useWideContainer, type DataTableColumnMeta } from '@/shared/ui';

import { formatSubscribers, isSelectable, selectableChannels } from '../model/discovery';

const CHECKBOX = 'h-[14px] w-[14px] shrink-0 accent-primary disabled:opacity-40';

const SOURCE_STATE = {
  ran: 'sourceRan',
  failed: 'sourceFailed',
  skipped: 'sourceSkipped',
} as const;

// Reasons are deliberately locale-neutral codes, and printing them raw put strings like
// "seed_unusable" in front of the operator. Unmapped codes fall back to the code itself,
// so a new one degrades to the old behaviour rather than to nothing.
const reasonKey = (reason: string) => `neurocomment.modal.discovery.results.reason.${reason}`;

/** One line per source: what it returned, and what survived into the table.
 *
 * The operator could watch a run reach "done" and never learn that one of its sources
 * had contributed nothing — because the board carried a single error string and a
 * skipped source carried none at all. A source crediting `0` here is that missing signal.
 */
function SourceStrip({ sources }: { sources: DiscoverySourceReport[] }) {
  const { t } = useTranslation();
  if (sources.length === 0) return null;
  // Its own line, not a cell of the toolbar row: four waves each carrying counts, a
  // uniqueness note and a reason do not fit beside the found-count without collapsing
  // into an ellipsis.
  return (
    <p className="text-[11.5px] text-ink-subtle">
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
          // The run's read budget stopped this wave, so its counts are a floor rather
          // than a total — "20 of 20" would otherwise read as a source read to the end.
          if (report.truncated === true) {
            line += ` ${t('neurocomment.modal.discovery.results.sourceTruncated')}`;
          }
          // A skipped or failed source is the whole point of this strip, so it says why.
          if (report.reason == null) return line;
          return `${line} — ${t(reasonKey(report.reason), { defaultValue: report.reason })}`;
        })
        .join(' · ')}
    </p>
  );
}

// A gate the campaign cannot pass at all, versus one it can pay its way through.
const BLOCKING = new Set(['cantWrite', 'scam', 'fake', 'restricted']);

/** The gates the backend explicitly answered — and only those.
 *
 * Every field is tri-state and `null` means Telegram did not answer it (no linked group,
 * an older TL layer, a field omitted), NEVER "no". So a mark appears on an explicit
 * signal only: an unanswered field produces no mark rather than a cleared gate, which
 * would tell the operator a channel is writable when nothing ever checked it.
 */
function verdictMarks(verdict: DiscoveryChannelVerdict) {
  const marks: { key: string }[] = [];
  if (verdict.can_send_messages === false) marks.push({ key: 'cantWrite' });
  if (verdict.join_to_send === true) marks.push({ key: 'joinRequired' });
  if (verdict.join_request === true) marks.push({ key: 'joinRequest' });
  // The DISCUSSION GROUP's flag — where the comments are actually written. It carries no
  // interval (that would cost a second getFullChannel), so the mark shows none. The
  // broadcast channel's own slowmode_seconds is not carried at all: Telegram documents
  // it for supergroups, so on a channel it is never set and the mark never appeared.
  if (verdict.group_slowmode_enabled === true) marks.push({ key: 'slowMode' });
  if (verdict.scam === true) marks.push({ key: 'scam' });
  if (verdict.fake === true) marks.push({ key: 'fake' });
  if (verdict.restricted === true) marks.push({ key: 'restricted' });
  return marks;
}

function CommentsMark({ state }: { state: string }) {
  const { t } = useTranslation();
  const label = t(`neurocomment.modal.discovery.comments.${state}`);
  if (state === 'comments_on') {
    return (
      <span className="inline-flex text-success" role="img" title={label} aria-label={label}>
        <StatusIcon kind="ok" />
      </span>
    );
  }
  if (state === 'comments_off') {
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
          state === 'pending' ? 'animate-pulse' : ''
        }`}
      />
      {label}
    </span>
  );
}

function VerdictCell({ candidate, settled }: { candidate: DiscoveryCandidate; settled: boolean }) {
  const { t } = useTranslation();
  // 'pending' means never probed, which the backend keeps distinct from 'unknown'
  // (probed, unanswerable). Once the run has stopped nothing will probe it, so it has
  // to read as "not checked yet" — a re-run resolves those, unlike 'unknown'.
  const raw = candidate.qualification ?? 'pending';
  const state = settled && raw === 'pending' ? 'notChecked' : raw;
  const verdict = candidate.verdict;
  // No verdict at all: never probed in this process, or lost to a restart — the backend
  // does not persist it. Suppressed where the comments state already says "not checked",
  // which would be the same sentence twice.
  const unanswered = verdict == null && state !== 'pending' && state !== 'notChecked';
  return (
    <div className="flex flex-col items-start gap-[3px]">
      <CommentsMark state={state} />
      {unanswered ? (
        <span className="text-[11px] text-ink-subtle">
          {t('neurocomment.modal.discovery.verdict.unknown')}
        </span>
      ) : null}
      {(verdict == null ? [] : verdictMarks(verdict)).map((mark) => (
        <span
          key={mark.key}
          className={`text-[11px] ${BLOCKING.has(mark.key) ? 'text-danger' : 'text-warning'}`}
        >
          {t(`neurocomment.modal.discovery.verdict.${mark.key}`)}
        </span>
      ))}
    </div>
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
        // The whole path, not just `source`: that field names only the winner of the
        // dedup, and a channel two independent waves both reached is a far stronger
        // signal than one a single keyword turned up.
        const found = row.original.sources ?? [];
        const sources = found.length > 0 ? found : [row.original.source];
        return (
          <span className="text-[11.5px] text-ink-subtle">
            {sources
              .map((source) => t(`neurocomment.modal.discovery.source.${source}`))
              .join(' + ')}
          </span>
        );
      },
    },
    {
      id: 'comments',
      header: () => t('neurocomment.modal.discovery.results.colComments'),
      cell: ({ row }) => <VerdictCell candidate={row.original} settled={settled} />,
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
      </div>
      <SourceStrip sources={board?.progress.sources ?? []} />
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
