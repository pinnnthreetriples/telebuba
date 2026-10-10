import type { TFunction } from 'i18next';
import { useTranslation } from 'react-i18next';

import type { DiscoveryStream, DiscoveryWork } from '@/shared/api';
import { Badge, ProgressBar } from '@/shared/ui';
import { cn } from '@/shared/lib/cn';

import { allOut, dotTone, formatEta, stateLabelKey, streamsOut } from '../model/progress';

const P = 'neurocomment.modal.discovery.progress';

/** One account's stream: a status dot, its name, and a Premium badge when it applies.
 * `title` carries the full state (+ error) for a reader that can hover — the dot and
 * the name alone cannot say "cooling" from "dead". */
function StreamChip({ stream, t }: { stream: DiscoveryStream; t: TFunction }) {
  const label = t(stateLabelKey(stream.state));
  const title = stream.error == null ? label : `${label} · ${stream.error}`;
  return (
    <span className="inline-flex items-center gap-1" title={title}>
      <span className={cn('size-dot shrink-0 rounded-full', dotTone(stream.state))} />
      <span className="type-small">{stream.name}</span>
      {stream.premium === true ? (
        <Badge size="xs" tone="info">
          {t(`${P}.premium`)}
        </Badge>
      ) : null}
    </span>
  );
}

type Props = {
  work: DiscoveryWork;
  phase: 'searching' | 'qualifying';
};

/** The live progress strip: a header line (stage + reads/ETA), a bar, one chip per
 * account stream, and a problems line when some stream has dropped. Rendered by
 * `DiscoveryResults` in place of the static "Ищем каналы…" paragraph while stage 1
 * runs, and above the candidate list in place of the small qualifying counter while
 * stage 2 runs — see the callers for exactly when. */
export function SearchProgress({ work, phase }: Props) {
  const { t } = useTranslation();
  const done = work.done ?? 0;
  const planned = work.planned ?? 0;
  const streams = work.streams ?? [];
  const indeterminate = planned === 0;
  // A frame can land with done > planned (the estimate updates mid-run) — clamp
  // rather than let the bar overshoot or hand the progressbar a valuenow above its
  // own valuemax.
  const clampedDone = indeterminate ? 0 : Math.min(done, planned);

  const stageKey = phase === 'searching' ? `${P}.stageSearching` : `${P}.stageQualifying`;
  const stageLabel = t(stageKey);
  const countKey = phase === 'searching' ? `${P}.countSearching` : `${P}.countQualifying`;
  const countText = t(countKey, { done, planned });
  const eta = formatEta(work.eta_seconds);
  const etaKey = eta?.unit === 'minutes' ? `${P}.etaMinutes` : `${P}.etaSeconds`;
  const headerRight = eta == null ? countText : `${countText} · ${t(etaKey, { value: eta.value })}`;

  const out = streamsOut(streams);

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-2">
        {/* role=status on the STAGE only, not the whole line: the count/ETA change on
            every ~2s poll tick, and a live region around them would have a screen
            reader re-announce the strip every tick for the run's whole duration. The
            stage label changes twice per run (searching → qualifying), which is worth
            announcing. */}
        <span role="status" aria-live="polite" className="type-body-medium text-content-secondary">
          {stageLabel}
        </span>
        <span className="type-small tabular-nums">{headerRight}</span>
      </div>
      <ProgressBar
        label={stageLabel}
        value={clampedDone}
        max={planned}
        indeterminate={indeterminate}
        className="w-full"
      />
      <div className="flex flex-wrap items-center gap-3">
        {streams.map((stream) => (
          <StreamChip key={stream.account_id} stream={stream} t={t} />
        ))}
      </div>
      {out.length > 0 ? (
        <p className="type-small text-warning-deep">
          {allOut(streams)
            ? t(`${P}.allOut`)
            : t(`${P}.someOut`, {
                list: out
                  .map((stream) => `${stream.name} (${t(stateLabelKey(stream.state))})`)
                  .join(', '),
              })}
        </p>
      ) : null}
    </div>
  );
}
