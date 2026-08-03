export { cn } from './cn';
export { formatLocalTime } from './formatTime';
// Log-row presentation, in its own segment: three modules that only ever run together
// (label the event, colour it, explain why). Grouped when `eventReason` split out of
// ActivityLogCard and made shared/lib the sixteenth file — steiger's
// fsd/shared-lib-grouping asks for exactly this grouping past fifteen, and it asks for
// the RIGHT thing here, so the segment is the fix rather than switching the rule off.
export { eventLabel } from './log/eventLabel';
export { eventReason } from './log/eventReason';
export { logSeverity } from './log/eventSeverity';
export { queryClient } from './query-client';
export { useLogEventStream } from './useLogEventStream';
export type { SseStatus } from './useLogEventStream';
export { useClearedTimeouts, useTransientFeedback } from './useTransientFeedback';
export type { FeedbackResult } from './useTransientFeedback';
