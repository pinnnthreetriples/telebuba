import type { LogEntry } from '@/shared/api';

// Where the six-stage rail stands, read off the real activity log instead of a
// hardcoded index — the same move WarmingBoard made when its decorative step was
// dropped. The engine writes a stable code at every step of a post's life, so the
// newest line that names a stage IS where the pipeline just was.
//
// Matched on the code and exactly where a prefix would collide
// (`neurocomment_post_received` is a detection, `_post_skipped` a filter verdict).
// First match wins, so the list is ordered late-stage first.
const STAGE_OF: [RegExp, number][] = [
  [/^neurocomment_(posted|post_(failed|gated|access_lost|cooldown|unavailable))$/, 5],
  [/^neurocomment_telegram_comment_on_post$/, 5],
  [/^neurocomment_(challenge|captcha)_/, 4],
  [/^neurocomment_(generation_|claim_lost_before_send$)/, 3],
  [/^neurocomment_(post_skipped|channel_cooled|no_campaign|no_account_available)$/, 2],
  [/^neurocomment_(post_received|post_dropped_overloaded)$/, 1],
];

// Past this, a stage is history rather than activity, and the rail drops back to
// «Слушатель» — which is what the engine is really doing between posts, and what
// the banner under the rail already says.
export const STAGE_FRESH_MS = 60_000;

// `staleAt` is when the returned stage stops being current (null: nothing to
// expire). The caller uses it to re-render exactly once at that moment instead of
// ticking a timer, and never sees the rail park mid-pipeline on an idle campaign.
export function pipelineStage(
  lines: Pick<LogEntry, 'event' | 'created_at'>[],
  running: boolean,
  now: number,
): { stage: number; staleAt: number | null } {
  // Stopped engine: no stage at all, every dot hollow (unchanged behaviour).
  if (!running) return { stage: -1, staleAt: null };
  for (const line of lines) {
    // The feed is newest-first (core/repositories/logs.py orders by id desc), so the
    // first line carrying a stage is the freshest one.
    const hit = STAGE_OF.find(([code]) => code.test(line.event));
    if (!hit) continue;
    const staleAt = Date.parse(line.created_at) + STAGE_FRESH_MS;
    return { stage: staleAt > now ? hit[1] : 0, staleAt };
  }
  return { stage: 0, staleAt: null };
}
