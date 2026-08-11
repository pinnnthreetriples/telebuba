import type { LogEntry } from '@/shared/api';

// Where the six-stage rail stands, read off the real activity log instead of a
// hardcoded index — the same move WarmingBoard made when its decorative step was
// dropped. The engine writes a stable code at every step of a post's life, so the
// newest line that names a stage IS where the pipeline just was.
//
// Matched on the code and exactly where a prefix would collide
// (`neurocomment_post_received` is a detection, `_post_skipped` a filter verdict).
// First match wins, so the list is ordered late-stage first.
//
// Only codes the PER-POST path emits may appear here. Two omissions are deliberate and
// must stay omitted:
//   * the whole `challenge_*` / `captcha_*` family — those come from the onboarding join
//     (`_classify.solve_if_present`) and from the deletion sweep's captcha pass, never
//     from a post. Mapping them to «Капча» made pressing Start green-check every earlier
//     step and claim a comment was generated and being sent, with no post in flight.
//     «Капча» therefore stays dark until the post path itself grows a captcha event.
//   * `neurocomment_no_campaign` — written ABOVE `post_received`, for a post on a channel
//     with no active campaign. Calling it a filter verdict green-checked a detection that
//     never happened; the listener did see it, so it belongs to stage 0.
//   * `neurocomment_account_banned` — the one branch of the outcome ladder that is NOT
//     post-only: `bans.confirm_group_ban_and_leave` writes it from the onboarding join too
//     (`_classify.py`), and the row carries no post id to tell the two apart. Left out, a
//     ban falls through to that post's own `generation_started` — stale by one stage for up
//     to a minute, which beats asserting a comment was sent during onboarding.
const STAGE_OF: [RegExp, number][] = [
  // Every post-only branch of `_outcomes._classify_post`, which writes exactly one terminal
  // row per attempt — miss one and that outcome parks the rail on «Генерация» for a minute.
  [
    /^neurocomment_(posted|posted_after_reclaim|posted_row_missing|post_(failed|gated|access_lost|cooldown|unavailable|ban_unconfirmed|commit_failed))$/,
    5,
  ],
  [/^neurocomment_telegram_comment_on_post$/, 5],
  [/^neurocomment_(generation_|claim_lost_before_send$)/, 3],
  [/^neurocomment_(post_skipped|channel_cooled|no_account_available)$/, 2],
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
