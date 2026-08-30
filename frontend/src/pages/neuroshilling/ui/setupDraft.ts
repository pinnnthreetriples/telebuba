// The setup form's shape and the two translations around it: server → draft,
// draft → the slice of the PUT this card owns. Page-local, beside the card that
// edits it, exactly like `scenarioDraft.ts`.
//
// It lives apart from `CampaignSetupCard.tsx` for the same reason: the page owns
// the draft (the board query is refetched by the log stream, so a card that
// seeded itself from props would be emptied under the operator's hands), and a
// `.tsx` that exports helpers as well as components trips
// `react-refresh/only-export-components`.
import type { NeuroshillingCampaign, NeuroshillingCampaignUpdate } from '@/shared/api';

// The ceilings on the WIRE (`schemas.neuroshilling`), so the inputs clamp to what
// the PUT accepts instead of answering 422 with an unreadable validation blob.
export const MAX_PAUSE_SECONDS = 3600;
export const MAX_MESSAGES_PER_HOUR = 60;
export const MAX_MESSAGES_PER_CHAT_PER_DAY = 50;
export const MAX_TOTAL_PER_ACCOUNT = 1000;
export const MAX_TARGETS_RAW = 8000;
export const MAX_LISTEN_MINUTES = 1440;

export interface SetupDraft {
  // Which campaign this draft belongs to, so the page can tell "the operator
  // switched campaigns" from "the same campaign was refetched".
  campaignId: string;
  targetsRaw: string;
  runMode: NonNullable<NeuroshillingCampaign['run_mode']>;
  pauseMinSeconds: number;
  pauseMaxSeconds: number;
  messagesPerHour: number;
  messagesPerChatPerDay: number;
  // null = no lifetime ceiling. Held as null rather than 0 because 0 is a value
  // the wire rejects (`ge=1`), and an empty box must not read as "none allowed".
  totalPerAccount: number | null;
  reserveEnabled: boolean;
  // The listening block. `autoresponder` picks WHICH engine writes an answer and
  // `replyToHumans` says whether a real person's message may provoke one; the
  // server requires BOTH before anything is published, so the card says so.
  autoresponder: NonNullable<NeuroshillingCampaign['autoresponder']>;
  replyToHumans: boolean;
  replyActivity: NonNullable<NeuroshillingCampaign['reply_activity']>;
  listenMinutes: number;
}

/** A whole number inside `[min, max]`, or `min` for anything unparseable. */
export function clampInt(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) return min;
  return Math.min(max, Math.max(min, Math.trunc(value)));
}

/** The targets the operator has typed, one per line or separated by whitespace.
 *
 * A CLIENT-side count for the field's badge only. The authority is
 * `NeuroshillingBoard.targets`, which is the server's normalised parse of what
 * was SAVED — that is what the launch card counts, and it is why a badge here
 * can legitimately read 3 while the launch card still says there are none.
 */
export function countTargets(raw: string): number {
  return splitTargets(raw).length;
}

/** The targets as a LIST, in the order they were typed.
 *
 * Один разбор на всё приложение: чипы показывают его элементы, счётчик — их число, а
 * строка ввода режет вставленный блок им же, поэтому вставленный из таблицы столбец
 * становится столькими чипами, сколько в нём чатов. Пока разбор жил внутри `countTargets`,
 * посчитать и показать одно и то же можно было только двумя разными способами.
 */
export function splitTargets(raw: string): string[] {
  return raw.split(/[\s,;]+/).filter(Boolean);
}

/** Server truth → editable draft. The ONLY place the two shapes meet. */
export function setupDraftOf(campaign: NeuroshillingCampaign): SetupDraft {
  return {
    campaignId: campaign.campaign_id,
    targetsRaw: campaign.targets_raw ?? '',
    runMode: campaign.run_mode ?? 'sequential',
    pauseMinSeconds: campaign.pause_min_seconds ?? 10,
    pauseMaxSeconds: campaign.pause_max_seconds ?? 20,
    messagesPerHour: campaign.messages_per_hour ?? 10,
    messagesPerChatPerDay: campaign.messages_per_chat_per_day ?? 3,
    totalPerAccount: campaign.total_per_account ?? null,
    reserveEnabled: campaign.reserve_enabled ?? false,
    autoresponder: campaign.autoresponder ?? 'off',
    replyToHumans: campaign.reply_to_humans ?? false,
    replyActivity: campaign.reply_activity ?? 'medium',
    listenMinutes: campaign.listen_minutes ?? 60,
  };
}

/** The campaign columns this card owns — the rest of the PUT is echoed by the page. */
export function setupFieldsOf(draft: SetupDraft): Partial<NeuroshillingCampaignUpdate> {
  return {
    targets_raw: draft.targetsRaw,
    run_mode: draft.runMode,
    pause_min_seconds: draft.pauseMinSeconds,
    pause_max_seconds: draft.pauseMaxSeconds,
    messages_per_hour: draft.messagesPerHour,
    messages_per_chat_per_day: draft.messagesPerChatPerDay,
    total_per_account: draft.totalPerAccount,
    reserve_enabled: draft.reserveEnabled,
    autoresponder: draft.autoresponder,
    reply_to_humans: draft.replyToHumans,
    reply_activity: draft.replyActivity,
    listen_minutes: draft.listenMinutes,
  };
}

/** How many advanced settings differ from the schema defaults — the collapse's badge.
 *
 * Counts what the operator has CHANGED rather than how many controls the panel
 * holds: a constant would say "9" on a campaign that has never been touched, and
 * the badge exists to say whether anything is hiding in there.
 *
 * `listenMinutes` is deliberately absent: it only means anything once one of the
 * three switches above it is on, and a campaign that never touched any of them
 * would otherwise carry a badge for a number nothing reads.
 */
export function advancedChangeCount(draft: SetupDraft): number {
  return [
    draft.messagesPerHour !== 10,
    draft.messagesPerChatPerDay !== 3,
    draft.totalPerAccount !== null,
    draft.reserveEnabled,
    draft.autoresponder !== 'off',
    draft.replyToHumans,
    draft.replyActivity !== 'medium',
  ].filter(Boolean).length;
}
