import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import type {
  DiscoveryBoard,
  DiscoveryCandidate,
  DiscoveryChannelVerdict,
  DiscoverySourceReport,
} from '@/shared/api';
import {
  Badge,
  Button,
  Icon,
  SegmentedControl,
  useWideContainer,
  type BadgeTone,
} from '@/shared/ui';
import { cn } from '@/shared/lib/cn';

import {
  compareCandidates,
  formatSubscribers,
  isPrivateRef,
  isSelectable,
  selectableChannels,
} from '../model/discovery';
import { SearchProgress } from './SearchProgress';

const CHECKBOX = 'size-spinner shrink-0 accent-action-primary disabled:opacity-40';

const SOURCE_STATE = {
  ran: 'sourceRan',
  failed: 'sourceFailed',
  skipped: 'sourceSkipped',
} as const;

// Reasons are deliberately locale-neutral codes, and printing them raw put strings like
// "seed_unusable" in front of the operator. Unmapped codes fall back to the code itself,
// so a new one degrades to the old behaviour rather than to nothing.
const reasonKey = (reason: string) => `neurocomment.modal.discovery.results.reason.${reason}`;

// Only the whole-view filter, so unexported is fine — react-refresh only minds a
// value export from a component file, and this never leaves the module.
type ResultsFilter = 'eligible' | 'all';

/** One line per source: what it returned, and what survived into the list.
 *
 * The operator could watch a run reach "done" and never learn that one of its sources
 * had contributed nothing — because the board carried a single error string and a
 * skipped source carried none at all. A source crediting `0` here is that missing signal.
 */
function SourceStrip({ sources }: { sources: DiscoverySourceReport[] }) {
  const { t } = useTranslation();
  if (sources.length === 0) return null;
  return (
    <p className="type-caption">
      {sources
        .map((report) => {
          const name = t(`neurocomment.modal.discovery.source.${report.source}`);
          const kept = report.kept ?? 0;
          const { exclusive } = report;
          let line = t(`neurocomment.modal.discovery.results.${SOURCE_STATE[report.state]}`, {
            source: name,
            kept,
            hits: report.hits ?? 0,
          });
          // "50 of 60" hid the case where all 50 were duplicates of another source and
          // every row this one found alone was cut by the cap. Only on a MEASURED count:
          // an absent field is not a zero, and "(only here: 0)" beside "50 of 60" would
          // claim every one of the 50 was a duplicate.
          if (typeof exclusive === 'number' && exclusive !== kept) {
            line += ` ${t('neurocomment.modal.discovery.results.sourceExclusive', { exclusive })}`;
          }
          // The run's read budget stopped this wave, so its counts are a floor rather
          // than a total — "20 of 20" would otherwise read as a source read to the end.
          // Only for a source that RAN: on a skipped one it composed "not queried
          // (stopped early) — the read budget ran out", where a source nobody asked did
          // not stop early and two of the three clauses said the same thing.
          if (report.truncated === true && report.state === 'ran') {
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

/** The run's own qualification progress ("N/M"), isolated in its own component so a
 * future progress strip can replace it without touching the layout around it. */
function QualifyingCaption({
  phase,
  qualified,
  total,
  running,
}: {
  phase: string;
  qualified: number;
  total: number;
  running: boolean;
}) {
  const { t } = useTranslation();
  // Also the only trace of how far an aborted run got ("40/300"), so it has to outlive
  // the qualifying phase.
  if (phase !== 'qualifying' && qualified >= total) return null;
  return (
    <span role="status" className={cn('type-caption', running && 'tb-pulse')}>
      {t('neurocomment.modal.discovery.results.qualifying', { done: qualified, total })}
    </span>
  );
}

/** Why a row cannot be adopted, in the words the comments cell shows instead of a
 * badge — or `null` when the badge itself already tells the truth (a comments-off
 * row's red "нет" already says why it is not selectable).
 *
 * `in_campaign`/`taken_by_other_campaign` are deliberately NOT handled here: the
 * subtitle already names them (see the `deviations` list in `Row`), and the row still
 * carries a real qualification worth showing — repeating the same reason word in the
 * comments cell too just says the same sentence twice. */
function nonSelectReasonKey(candidate: DiscoveryCandidate): string | null {
  // A group carries no comments verdict at all, and a private (`id:`) row loses its
  // access badge after a restart — the adopt endpoint refuses both regardless of
  // whatever qualification they show, so the badge would be answering a question
  // that is not why the row is dead.
  if (candidate.kind === 'group' || isPrivateRef(candidate.channel)) return 'notAdoptable';
  return null;
}

/** The comments badge's tone and text key, plus whether it should pulse. */
function commentBadgeKey(
  candidate: DiscoveryCandidate,
  running: boolean,
): { tone: BadgeTone; key: string; pulse: boolean } {
  if (candidate.qualification === 'comments_on')
    return { tone: 'success', key: 'badgeOn', pulse: false };
  if (candidate.qualification === 'comments_off') {
    return { tone: 'danger', key: 'badgeOff', pulse: false };
  }
  if (candidate.qualification === 'pending' && running) {
    return { tone: 'neutral', key: 'badgePending', pulse: true };
  }
  // Pending-but-settled (never probed, run over) and 'unknown' (probed, unanswerable)
  // read as the same thing in this compact view: the operator's next move for both is
  // "re-run to find out".
  return { tone: 'neutral', key: 'badgeUnchecked', pulse: false };
}

/** The caveat keys a verdict's explicit (non-null) gates spell out, plain-Russian words
 * rather than the gate names themselves. Every field is tri-state, so `null` — Telegram
 * never answered — stays silent rather than guessing either way. */
function caveatKeys(verdict: DiscoveryChannelVerdict | null | undefined): string[] {
  if (verdict == null) return [];
  const keys: string[] = [];
  if (verdict.group_slowmode_enabled === true) keys.push('slowMode');
  if (verdict.join_to_send === true) keys.push('joinToSend');
  if (verdict.join_request === true) keys.push('joinRequest');
  // Same caveat text for both: the operator's next move is the same either way, and a
  // channel Telegram flags as both would otherwise repeat itself.
  if (verdict.scam === true || verdict.fake === true) keys.push('scam');
  if (verdict.restricted === true) keys.push('restricted');
  if (verdict.can_send_messages === false) keys.push('cantWrite');
  return keys;
}

/** The comments cell: the real reason a dead row is dead, or the badge plus its
 * caveats when the row's own qualification is worth showing. */
function CommentsCell({ candidate, running }: { candidate: DiscoveryCandidate; running: boolean }) {
  const { t } = useTranslation();
  const reason = nonSelectReasonKey(candidate);
  if (reason != null) {
    return (
      <span className="type-caption">{t(`neurocomment.modal.discovery.results.${reason}`)}</span>
    );
  }
  const badge = commentBadgeKey(candidate, running);
  const caveats = caveatKeys(candidate.verdict).map((key) =>
    t(`neurocomment.modal.discovery.results.caveat.${key}`),
  );
  return (
    <div className="flex flex-col items-start gap-xs">
      <Badge tone={badge.tone} className={badge.pulse ? 'tb-pulse' : undefined}>
        {t(`neurocomment.modal.discovery.results.${badge.key}`)}
      </Badge>
      {caveats.length > 0 ? (
        <span className="type-caption text-warning-deep">{caveats.join(' · ')}</span>
      ) : null}
    </div>
  );
}

/** One candidate: a checkbox, a title/subtitle cell, subscribers and the comments
 * cell — stacked on a narrow container instead of the wide layout's single row. */
function Row({
  candidate,
  wide,
  selected,
  onToggle,
  running,
}: {
  candidate: DiscoveryCandidate;
  wide: boolean;
  selected: boolean;
  onToggle: (channel: string) => void;
  running: boolean;
}) {
  const { t, i18n } = useTranslation();
  const selectable = isSelectable(candidate);
  const privateRow = isPrivateRef(candidate.channel);
  const displayName = privateRow
    ? t('neurocomment.modal.discovery.results.privateChannel')
    : candidate.channel;
  const titleText =
    candidate.title != null && candidate.title !== '' ? candidate.title : displayName;

  // The handle stays its own leaf node rather than joining the flat string below: it is
  // the one piece of this row a dozen other suites already query for by exact text
  // (`screen.getByText('@good')`), and folding it into the joined caption would break
  // every one of them for a purely visual change.
  const handle = privateRow ? displayName : `@${candidate.channel}`;
  const deviations: string[] = [];
  if (candidate.language) {
    deviations.push(
      t(`neurocomment.modal.discovery.results.language.${candidate.language}`, {
        defaultValue: candidate.language,
      }),
    );
  }
  // Only the deviations from the norm — a channel with open access is never named as
  // one, so "канал"/"открытый" never appear here.
  if (candidate.kind === 'group')
    deviations.push(t('neurocomment.modal.discovery.results.kind.group'));
  if (candidate.access === 'join_request') {
    deviations.push(t('neurocomment.modal.discovery.results.access.join_request'));
  }
  if (candidate.access === 'subscription') {
    deviations.push(t('neurocomment.modal.discovery.results.access.subscription'));
  }
  if (candidate.in_campaign === true)
    deviations.push(t('neurocomment.modal.discovery.results.inCampaign'));
  if (candidate.taken_by_other_campaign === true) {
    deviations.push(t('neurocomment.modal.discovery.results.takenElsewhere'));
  }
  if (candidate.uncounted === true)
    deviations.push(t('neurocomment.modal.discovery.results.uncounted'));

  const checkbox = (
    <input
      type="checkbox"
      checked={selected}
      disabled={!selectable}
      onChange={() => {
        onToggle(candidate.channel);
      }}
      aria-label={t('neurocomment.modal.discovery.results.select', { channel: displayName })}
      // Says why the box is dead where the row's own text may not: the adopt endpoint
      // itself refuses a group and a private channel ('not_adoptable').
      title={
        privateRow || candidate.kind === 'group'
          ? t('neurocomment.modal.discovery.results.notAdoptable')
          : undefined
      }
      className={CHECKBOX}
    />
  );

  const subscribersText = formatSubscribers(candidate.subscribers, i18n.language || 'ru');
  const subscribersCell = (
    <span className="w-number shrink-0 text-right tabular-nums">{subscribersText}</span>
  );
  const commentsCell = <CommentsCell candidate={candidate} running={running} />;

  return (
    <div
      className={cn(
        'flex flex-col gap-xs border-t border-line-row py-sm',
        !selectable && 'text-content-subtle',
      )}
    >
      <div className="flex items-center gap-md">
        <div className="flex w-action shrink-0 items-center justify-center">{checkbox}</div>
        <div className="min-w-0 flex-1">
          <div className={cn('truncate type-label', !selectable && 'text-content-subtle')}>
            {titleText}
          </div>
          <div className="truncate type-caption">
            <span>{handle}</span>
            {deviations.length > 0 ? ` · ${deviations.join(' · ')}` : null}
          </div>
        </div>
        {wide ? (
          <>
            {subscribersCell}
            <div className="w-menu shrink-0">{commentsCell}</div>
          </>
        ) : null}
      </div>
      {wide ? null : (
        <div className="flex items-center gap-md">
          {subscribersCell}
          {/* min-w-0: a flex item's default min-width is its content's, and a long
              caveat line (e.g. three joined with " · ") would otherwise refuse to
              shrink and push the row past the viewport instead of wrapping. */}
          <div className="min-w-0 flex-1">{commentsCell}</div>
        </div>
      )}
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
  const { t } = useTranslation();
  // Must be the container query DataTable itself uses, not the viewport one: this list
  // lives in a 926px modal whose padding leaves it 890px, 10px over the 880px table/card
  // floor — so on a narrower viewport the list renders stacked while a viewport query
  // would still say "wide", and the select-all below would go missing.
  const results = useRef<HTMLDivElement>(null);
  const wide = useWideContainer(results);
  const [filter, setFilter] = useState<ResultsFilter>('eligible');
  const [detailsOpen, setDetailsOpen] = useState(false);

  const candidates = board?.candidates ?? [];
  // The eligible set is the SAME regardless of which view is showing: "Подходящие"
  // only hides ineligible rows, it never adds eligible ones, so select-all's target
  // is exactly this list in both views.
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
  // The live per-account progress a new-enough backend serves while a stage runs —
  // absent on an old backend, or before the first frame of a run lands. Both
  // `SearchProgress` call sites fall back to their plain-text predecessor when it
  // is missing, rather than rendering nothing.
  const work = board?.progress.work ?? null;
  const qualifyingStrip = running && phase === 'qualifying' && work != null;
  const qualified = board?.progress.qualified ?? 0;
  const total = board?.progress.total ?? 0;
  const commentsOn = board?.progress.comments_on ?? 0;
  // Rows the operator's own filters cut, summed over the reasons: without it a narrow
  // filter and an empty Telegram both read as "found 3".
  const filtered = Object.values(board?.progress.filtered ?? {}).reduce((sum, n) => sum + n, 0);
  const sources = board?.progress.sources ?? [];
  const lastError = board?.progress.last_error ?? null;
  const sourcesFailed = sources.some((report) => report.state === 'failed');
  const hasProblem = sourcesFailed || lastError != null;
  const problemText =
    lastError != null
      ? t(`neurocomment.modal.discovery.results.${failed ? 'failed' : 'degraded'}`, {
          reason: t(reasonKey(lastError), { defaultValue: lastError }),
        })
      : t('neurocomment.modal.discovery.results.sourcesFailed');

  // A run that stored nothing (a rate limit left the PREVIOUS search's rows on screen)
  // or one the candidate cap cut short: the count in the "Все" segment is not simply
  // "found", so that has to be said somewhere. `candidates.length` — the "Все" side —
  // is what the note is about either way.
  const staleOrCappedCaption =
    board?.progress.stale_candidates === true
      ? t('neurocomment.modal.discovery.results.countStale', { count: candidates.length })
      : board?.progress.capped === true
        ? t('neurocomment.modal.discovery.results.countCapped', { count: candidates.length })
        : null;

  const displayed = (filter === 'all' ? candidates : candidates.filter(isSelectable))
    .slice()
    .sort(compareCandidates);

  // Candidates are replaced only after the whole search stage, so any rows still on
  // screen while it runs belong to the PREVIOUS run — never show them as results.
  // role=status on every transient state: a search runs 30s+, so a screen-reader
  // operator has to be told when it finishes or fails without polling the list.
  //
  // A closure rather than early returns, so the measured wrapper at the bottom is in
  // EVERY branch: the container hook measures once per ref, on its first commit, and the
  // first commit here is almost always «Ищем каналы…». A ref that only existed once rows
  // arrived was never measured, and a ~960px viewport got two select-alls.
  const body = () => {
    if (loading) {
      // An old backend (or the first frame before the run's own state exists) has
      // no `work` yet — the plain-text line it always showed stays the fallback.
      if (work != null) return <SearchProgress work={work} phase="searching" />;
      return (
        <p role="status" className="py-page text-center type-prose">
          {t('neurocomment.modal.discovery.results.searching')}
        </p>
      );
    }

    // Only with nothing to fall back on: a failed refetch leaves status 'error' with the
    // cached frame intact, and blanking the list would take N rows and every tick the
    // operator has made with it.
    if (errored && candidates.length === 0) {
      return (
        <p role="status" className="py-page text-center text-body text-danger">
          {t('neurocomment.modal.discovery.results.error')}
        </p>
      );
    }

    if (failed && candidates.length === 0) {
      return (
        <p role="status" className="py-page text-center text-body text-danger">
          {t('neurocomment.modal.discovery.results.failed', {
            reason: lastError == null ? '' : t(reasonKey(lastError), { defaultValue: lastError }),
          })}
        </p>
      );
    }

    if (candidates.length === 0) {
      return (
        <p className="py-page text-center type-prose">
          {t('neurocomment.modal.discovery.results.empty')}
        </p>
      );
    }

    const selectAll = (
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
    );

    return (
      <div className="flex flex-col gap-md">
        {qualifyingStrip && work != null ? <SearchProgress work={work} phase="qualifying" /> : null}
        <div className="flex flex-wrap items-center gap-sm">
          <SegmentedControl
            value={filter}
            onChange={setFilter}
            variant="pill"
            ariaLabel={t('neurocomment.modal.discovery.results.filterLabel')}
            options={[
              {
                value: 'eligible',
                label: t('neurocomment.modal.discovery.results.filterEligible', {
                  count: eligible.length,
                }),
              },
              {
                value: 'all',
                label: t('neurocomment.modal.discovery.results.filterAll', {
                  count: candidates.length,
                }),
              },
            ]}
          />
          {filtered > 0 ? (
            <span className="type-caption">
              {t('neurocomment.modal.discovery.results.filtered', { count: filtered })}
            </span>
          ) : null}
          {/* The run's yield beyond what the segmented control already says, once
              nothing else will change it — suppressed when it would just repeat N. */}
          {settled && qualified > 0 && commentsOn !== eligible.length ? (
            <span className="type-caption">
              {t('neurocomment.modal.discovery.results.commentsOn', { count: commentsOn })}
            </span>
          ) : null}
          {/* The strip's own header line already carries "N из M каналов", so the
              two would otherwise repeat the same count side by side. */}
          {qualifyingStrip ? null : (
            <QualifyingCaption
              phase={phase}
              qualified={qualified}
              total={total}
              running={running}
            />
          )}
          {hasProblem ? (
            <div className="ml-auto flex items-center gap-sm">
              <span
                role="status"
                className="flex items-center gap-xs type-caption text-warning-deep"
              >
                <Icon name="alert-triangle" size={14} className="shrink-0" />
                {problemText}
              </span>
              <Button
                variant="ghost"
                size="xs"
                onClick={() => {
                  setDetailsOpen((open) => !open);
                }}
              >
                {t('neurocomment.modal.discovery.results.detailsToggle')}
              </Button>
            </div>
          ) : null}
        </div>

        {staleOrCappedCaption != null ? (
          <span className="type-caption">{staleOrCappedCaption}</span>
        ) : null}

        {/* The source report only: `problemText` above already said the run's own
            failed/degraded reason once, so repeating it here would say the same
            sentence twice for the price of one click. */}
        {detailsOpen ? (
          <div className="flex flex-col gap-xs">
            <SourceStrip sources={sources} />
          </div>
        ) : null}

        {wide ? (
          <div className="flex items-center gap-md type-caption">
            <div className="flex w-action shrink-0 items-center justify-center">{selectAll}</div>
            <span className="flex-1">{t('neurocomment.modal.discovery.results.colChannel')}</span>
            <span className="w-number shrink-0 text-right">
              {t('neurocomment.modal.discovery.results.colSubscribers')}
            </span>
            <span className="w-menu shrink-0">
              {t('neurocomment.modal.discovery.results.colComments')}
            </span>
          </div>
        ) : (
          // The stacked layout has no column headers, and select-all lives in one — so
          // on a phone the operator could otherwise only tap candidates one at a time.
          <label className="flex items-center gap-sm type-caption">
            {selectAll}
            {t('neurocomment.modal.discovery.results.selectAll')}
          </label>
        )}

        <div>
          {displayed.map((candidate) => (
            <Row
              key={candidate.channel}
              candidate={candidate}
              wide={wide}
              selected={selected.has(candidate.channel)}
              onToggle={onToggle}
              running={running}
            />
          ))}
        </div>

        {sources.length > 0 ? (
          <div className="flex flex-wrap items-center justify-between gap-sm border-t border-line-row pt-sm type-caption">
            <span>
              {t('neurocomment.modal.discovery.results.sourcesPrefix')}{' '}
              {sources
                .map((report) =>
                  [
                    t(`neurocomment.modal.discovery.source.${report.source}`, {
                      defaultValue: report.source,
                    }),
                    report.kept ?? 0,
                  ].join(' '),
                )
                .join(' · ')}
            </span>
            <Button
              variant="ghost"
              size="xs"
              onClick={() => {
                setDetailsOpen((open) => !open);
              }}
            >
              {t('neurocomment.modal.discovery.results.detailsFooterToggle')}
            </Button>
          </div>
        ) : null}
      </div>
    );
  };

  return <div ref={results}>{body()}</div>;
}
