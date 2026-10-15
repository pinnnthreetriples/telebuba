import { useTranslation } from 'react-i18next';

import type { WarmingAccountState } from '@/shared/api';

// The warming journey: the current phase large, then one warm-up gradient from the
// first contact to "warmed" with a knob where the account is now, and a ruled axis
// under it — one mark per phase, numbered. The knob's place is whole phases passed
// plus the share of the current one (`progress_to_next`).
const PHASES = ['intro', 'settling', 'warming', 'active', 'warmed'] as const;
type Phase = (typeof PHASES)[number];
// Marks on the axis: every phase plus one between each pair, like a ruler.
const MARKS = PHASES.length * 2 - 1;

function isPhase(value: string | null | undefined): value is Phase {
  return (PHASES as readonly string[]).includes(value ?? '');
}

export function PhaseTrack({ account }: { account: WarmingAccountState }) {
  const { t } = useTranslation();
  const phase: Phase = isPhase(account.phase) ? account.phase : 'intro';
  const at = PHASES.indexOf(phase);
  const last = PHASES.length - 1;
  const within = Math.max(0, Math.min(1, account.progress_to_next ?? 0));
  const position = at >= last ? 1 : (at + within) / last;
  const left = `${String(Math.round(position * 1000) / 10)}%`;
  const nextPhase = at < last ? PHASES[at + 1] : undefined;
  const days = account.days_to_next_phase;
  const name = t(`warming.console.phase.${phase}`);
  return (
    <div className="rounded-md bg-canvas p-4">
      <div className="flex flex-wrap items-end justify-between gap-x-3 gap-y-1">
        <div className="min-w-0">
          {/* The phase names are written lower-case for use inside sentences; here one
              stands alone as the tile's headline. */}
          <div className="type-h2 text-content-primary">
            {name.charAt(0).toLocaleUpperCase() + name.slice(1)}
          </div>
          <div className="type-small text-content-muted">
            {t('warming.card.phaseOf', { at: at + 1, total: PHASES.length })}
          </div>
        </div>
        {nextPhase ? (
          <div className="ml-auto text-right">
            <div className="type-small text-content-muted">{t('warming.card.phaseNext')}</div>
            <div className="type-small-medium text-info-strong">
              {t(`warming.console.phase.${nextPhase}`)}
              {days != null ? ` · ${t('warming.card.inDays', { days })}` : ''}
            </div>
          </div>
        ) : null}
      </div>

      <div className="relative mt-4 h-badge">
        <div className="tb-phase-gradient absolute inset-x-0 top-1/2 h-flag -translate-y-1/2 rounded-full" />
        <span
          className="tb-slide absolute top-1/2 -translate-x-1/2 -translate-y-1/2"
          style={{ left }}
        >
          <span className="pl-pulse block size-glyph rounded-full border-4 border-surface-card bg-action-primary" />
        </span>
      </div>

      <div className="mt-3 flex justify-between">
        {Array.from({ length: MARKS }, (_, index) => {
          const major = index % 2 === 0;
          const phaseIndex = index / 2;
          const current = major && phaseIndex === at;
          return (
            <span key={index} className="flex w-flag flex-col items-center gap-1">
              <span
                className={`size-tick rounded-full ${current ? 'bg-action-primary' : 'bg-line-strong'}`}
              />
              {major ? (
                <span
                  title={t(`warming.console.phase.${PHASES[phaseIndex] ?? 'intro'}`)}
                  className={`type-small tabular-nums ${
                    current ? 'type-small-medium text-info-strong' : 'text-content-subtle'
                  }`}
                >
                  {phaseIndex + 1}
                </span>
              ) : null}
            </span>
          );
        })}
      </div>
    </div>
  );
}
