import type { RecipientRules } from '@/shared/api';

export type FullRecipientRules = Required<RecipientRules>;

// The server's defaults, for the moment before the saved limits load.
export const DEFAULT_RECIPIENT_RULES: FullRecipientRules = {
  no_repeat_days: 30,
  one_account_per_client: true,
  recheck_not_found: true,
};

export function fullRecipientRules(rules: RecipientRules | undefined): FullRecipientRules {
  return { ...DEFAULT_RECIPIENT_RULES, ...rules };
}

// Skips the run reports because of whom earlier runs wrote to, not because a send failed.
export const HISTORY_SKIP_CODES = new Set(['recently_messaged', 'owned_by_other_account']);
