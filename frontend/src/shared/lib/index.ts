export { cn } from './cn';
export { formatLocalTime } from './formatTime';
// Everything about a log row, in its own segment: deliver it, then label it, colour it
// and explain why. Grouped when `eventReason` split out of ActivityLogCard and made
// shared/lib the sixteenth file — steiger's fsd/shared-lib-grouping asks for exactly this
// grouping past fifteen, and it asks for the RIGHT thing here, so the segment is the fix
// rather than switching the rule off. The SSE hook belongs inside it: a `log/` segment
// that excludes the thing that fetches the log rows is a segment in name only.
export { eventLabel } from './log/eventLabel';
export { eventReason } from './log/eventReason';
export { logSeverity } from './log/eventSeverity';
export { useLogEventStream } from './log/useLogEventStream';
export type { SseStatus } from './log/useLogEventStream';
export { isUnauthorized, mutationErrorText, queryClient } from './query-client';
export { useClearedTimeouts, useTransientFeedback } from './useTransientFeedback';
export type { FeedbackResult } from './useTransientFeedback';
