// Pure formatting for the live progress strip — no React, no server calls. Lives in a
// .ts module because react-refresh/only-export-components forbids non-component
// exports from .tsx (see model/discovery.ts).
import type { DiscoveryStream } from '@/shared/api';

// The generated client inlines the state union on `DiscoveryStream.state` rather than
// naming it — there is no standalone `DiscoveryStreamState` export to import.
type DiscoveryStreamState = NonNullable<DiscoveryStream['state']>;

// A stream that has left the pool: the run continues on the rest, but the operator
// needs to know who dropped and why.
const OUT_STATES: readonly DiscoveryStreamState[] = ['flooded', 'cooling', 'dead', 'offline'];

export function isOutState(state: DiscoveryStreamState | undefined): boolean {
  return state != null && OUT_STATES.includes(state);
}

export function streamsOut(streams: readonly DiscoveryStream[]): DiscoveryStream[] {
  return streams.filter((stream) => isOutState(stream.state));
}

export function allOut(streams: readonly DiscoveryStream[]): boolean {
  return streams.length > 0 && streams.every((stream) => isOutState(stream.state));
}

// The status dot's fill, the nearest existing background token per state — the same
// three-tone convention AccountsTable's proxy dot and trust bar already use
// (bg-success / bg-danger / a neutral fill), plus `tb-pulse` for the one state that is
// actively spending a read right now.
const DOT_TONE: Record<DiscoveryStreamState, string> = {
  idle: 'bg-content-subtle',
  waiting: 'bg-content-subtle',
  reading: 'bg-action-primary tb-pulse',
  done: 'bg-success',
  capped: 'bg-success',
  flooded: 'bg-danger',
  cooling: 'bg-danger',
  dead: 'bg-danger',
  offline: 'bg-danger',
};

export function dotTone(state: DiscoveryStreamState | undefined): string {
  return DOT_TONE[state ?? 'idle'];
}

// The i18n key for a stream's plain-Russian state label — `neurocomment.modal.discovery
// .progress.state.<state>` (see ru.json/en.json).
export function stateLabelKey(state: DiscoveryStreamState | undefined): string {
  return `neurocomment.modal.discovery.progress.state.${state ?? 'idle'}`;
}

export type EtaParts = { unit: 'seconds' | 'minutes'; value: number };

/** `eta_seconds` rounded for display: null passes through (no ETA shown); >= 90s
 * rounds to whole minutes; under that, to the nearest 5 seconds. The component picks
 * the i18n key (`progress.etaSeconds` / `progress.etaMinutes`) from `unit`. */
export function formatEta(etaSeconds: number | null | undefined): EtaParts | null {
  if (etaSeconds == null) return null;
  if (etaSeconds >= 90) return { unit: 'minutes', value: Math.round(etaSeconds / 60) };
  return { unit: 'seconds', value: Math.round(etaSeconds / 5) * 5 };
}
