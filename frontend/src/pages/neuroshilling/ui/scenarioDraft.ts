// The scenario form's shape and the two translations around it: server → draft,
// draft → request body. Page-local, beside the card that edits it, the same way
// `pages/settings/ui/neuroSettingsForm.ts` sits beside its form.
//
// It lives apart from `ScenarioCard.tsx` because the preview card and the page
// need the same vocabulary — the role palette, the reaction set, the draft type —
// and a `.tsx` file that exports helpers as well as components trips
// `react-refresh/only-export-components`.
import type {
  NeuroshillingCampaign,
  NeuroshillingCampaignUpdate,
  NeuroshillingScenario,
  NeuroshillingScenarioUpdate,
  NeuroshillingStep,
  NeuroshillingStepInput,
} from '@/shared/api';

// The three role colours the design cycles through. ONE array, read by the
// editor's chips and by the preview's bubbles, so a role wears one colour on
// both cards.
export const ROLE_COLORS = ['#0066ff', '#12a150', '#9a7b22'] as const;

// Read off the generated client instead of retyped. The backend narrowed the
// step input's `emoji` to a Literal so the picker could be built from it, so a
// set that drifts apart from the server's now fails the typecheck rather than a
// request.
export type Reaction = NonNullable<NeuroshillingStepInput['emoji']>;
export const REACTIONS: Reaction[] = ['👍', '❤️', '🔥', '👏', '🤔', '💯', '✨', '🙌'];

// The ceilings on the WIRE (`schemas.neuroshilling_scenario`), NOT
// `settings.neuroshilling.max_roles` / `max_steps`: the configured policy is
// lower but never reaches the client, so these are the only bounds the form can
// state honestly. A scenario past the configured one is refused with
// `scenario_invalid`, which has its own copy.
export const MAX_ROLES = 10;
export const MAX_STEPS = 50;
// What ONE generation may ask the model for. Deliberately NOT the form's cap: a
// hand-written dialogue may run well past what the model is asked to draft.
export const MAX_GENERATED_STEPS = 12;
export const MAX_STEP_DELAY_SECONDS = 3600;

export interface DraftRole {
  // A client-chosen KEY, not necessarily a stored id: the server updates the role
  // in place when it recognises the value and mints a new one when it does not.
  roleId: string;
  name: string;
  description: string;
}

export interface DraftStep {
  // React's list key only, stripped before the body is built — an input step
  // carries no identity on the wire, its array index IS its position.
  key: string;
  kind: NonNullable<NeuroshillingStepInput['kind']>;
  roleId: string | null;
  text: string;
  replyToPosition: number | null;
  targetPosition: number | null;
  emoji: Reaction | null;
  delayMinSeconds: number;
  delayMaxSeconds: number;
}

export interface ScenarioDraft {
  // Which campaign this draft belongs to, so the page can tell "the operator
  // switched campaigns" from "the same campaign was refetched".
  campaignId: string;
  mode: NeuroshillingCampaign['mode'];
  topic: string;
  uniqueMessages: boolean;
  useChatContext: boolean;
  mediaMessageLink: string;
  mediaStepPosition: number | null;
  roles: DraftRole[];
  steps: DraftStep[];
}

let minted = 0;

/** A key for a row with no server identity yet; prefixed so it can never look like a stored id. */
export function mintKey(prefix: string): string {
  minted += 1;
  return `${prefix}-${String(minted)}`;
}

export function clampDelay(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.min(MAX_STEP_DELAY_SECONDS, Math.max(0, Math.trunc(value)));
}

/** mm:ss, so a dialogue that runs for minutes reads as minutes rather than as a
 * four-digit number of seconds. */
export function clock(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return `${String(minutes)}:${rest < 10 ? '0' : ''}${String(rest)}`;
}

/** The midpoint of one step's delay range — the only honest single number for a
 * pause the engine draws at random. */
export function stepMeanSeconds(step: NeuroshillingStep): number {
  return ((step.delay_min_seconds ?? 60) + (step.delay_max_seconds ?? 180)) / 2;
}

/** How long ONE playthrough of the dialogue takes. The preview prints it per
 * bubble and as a total; the launch card prints the total again on its "dialogue"
 * tile, so the arithmetic lives here rather than in two cards that could drift. */
export function dialogueSeconds(steps: NeuroshillingStep[]): number {
  return steps.reduce((sum, step) => sum + stepMeanSeconds(step), 0);
}

/** A stored emoji narrowed back to the eight the picker offers, or nothing.
 *
 * The stored column is free text on purpose — a row written before the set was
 * narrowed must still READ back. It must not be echoed into the next save, so
 * anything outside the set is dropped here rather than refused there.
 */
export function asReaction(value: string | null | undefined): Reaction | null {
  return REACTIONS.find((emoji) => emoji === value) ?? null;
}

/** Server truth → editable draft. The ONLY place the two shapes meet. */
export function draftOf(
  campaign: NeuroshillingCampaign,
  scenario: NeuroshillingScenario,
): ScenarioDraft {
  const steps = scenario.steps ?? [];
  const mediaStep = campaign.media_step_position ?? null;
  return {
    campaignId: campaign.campaign_id,
    mode: campaign.mode,
    topic: campaign.topic ?? '',
    uniqueMessages: campaign.unique_messages ?? true,
    useChatContext: campaign.use_chat_context ?? false,
    mediaMessageLink: campaign.media_message_link ?? '',
    // A stored slot on anything but a message is dropped on the way in. The card's
    // picker offers message steps only, so such a value matches no option and the
    // `<select>` falls back to its first one: "no media step" on screen while the
    // draft still holds the position and writes it back on the next save, where
    // approval refuses it as `media_step_not_message` — naming a field that reads
    // empty. Neither save endpoint reads the kind under the slot, so an operator
    // who turns that step into a reaction and saves lands exactly here.
    mediaStepPosition:
      mediaStep !== null && steps[mediaStep - 1]?.kind === 'message' ? mediaStep : null,
    roles: (scenario.roles ?? []).map((role) => ({
      roleId: role.role_id,
      name: role.name,
      description: role.description ?? '',
    })),
    steps: steps.map((step) => ({
      // The stored id doubles as the list key: it is stable across a refetch and
      // it is already unique.
      key: step.step_id,
      kind: step.kind,
      roleId: step.role_id ?? null,
      text: step.text ?? '',
      replyToPosition: step.reply_to_position ?? null,
      targetPosition: step.target_position ?? null,
      emoji: asReaction(step.emoji),
      delayMinSeconds: step.delay_min_seconds ?? 60,
      delayMaxSeconds: step.delay_max_seconds ?? 180,
    })),
  };
}

/** Roles and steps in ONE body — the server writes them in one transaction. */
export function scenarioBody(draft: ScenarioDraft): NeuroshillingScenarioUpdate {
  return {
    roles: draft.roles.map((role) => ({
      role_id: role.roleId,
      name: role.name.trim(),
      description: role.description,
    })),
    steps: draft.steps.map((step) => ({
      kind: step.kind,
      role_id: step.roleId,
      text: step.text,
      // A link only means something for the kind that owns it; carrying the other
      // one along would resurrect a stale target when a step's kind changed.
      reply_to_position: step.kind === 'message' ? step.replyToPosition : null,
      target_position: step.kind === 'reaction' ? step.targetPosition : null,
      emoji: step.kind === 'reaction' ? step.emoji : null,
      delay_min_seconds: step.delayMinSeconds,
      delay_max_seconds: step.delayMaxSeconds,
    })),
  };
}

/** The campaign columns this card owns — the rest of the PUT is echoed by the page.
 *
 * The media step is cleared with the link: `media_step_position` without a link
 * is one of the states approval refuses, and it can only be reached by editing
 * the two halves separately.
 */
export function campaignFieldsOf(draft: ScenarioDraft): Partial<NeuroshillingCampaignUpdate> {
  const link = draft.mediaMessageLink.trim();
  return {
    mode: draft.mode,
    topic: draft.topic,
    unique_messages: draft.uniqueMessages,
    use_chat_context: draft.useChatContext,
    media_message_link: link || null,
    media_step_position: link ? draft.mediaStepPosition : null,
  };
}
