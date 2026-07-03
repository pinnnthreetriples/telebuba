import type { TFunction } from 'i18next';

// Backend log event codes we localize on the client (the API is locale-neutral —
// it emits stable snake_case codes; the SPA owns the labels). Kept as an explicit
// allow-list so an unknown/new code falls back to its raw code rather than
// rendering a missing-key placeholder. Shared so the Logs page cluster reuses it.
const KNOWN_EVENTS = new Set<string>([
  // neurocomment engine / generation
  'neurocomment_posted',
  'neurocomment_post_failed',
  'neurocomment_post_skipped',
  'neurocomment_post_cooldown',
  'neurocomment_post_gated',
  'neurocomment_generation_exhausted',
  'neurocomment_no_account_available',
  'neurocomment_no_campaign',
  'neurocomment_channel_cooled',
  'neurocomment_channel_backoff',
  'neurocomment_challenge_backoff',
  'neurocomment_pipeline_failed',
  // neurocomment onboarding / runtime
  'neurocomment_onboard_resolve_failed',
  'neurocomment_onboard_retry_later',
  'neurocomment_onboard_pair_failed',
  'neurocomment_onboard_spam_probe_failed',
  'neurocomment_listener_join_failed',
  'neurocomment_runtime_reconciled',
  'neurocomment_start_onboard_failed',
  'neurocomment_sweep_failed',
  'neurocomment_settings_saved',
  // warming
  'warming_started',
  'warming_stopped',
  'warming_complete',
  'warming_target_reached',
  'warming_cycle_completed',
  'warming_cycle_not_ready',
  'warming_no_channels',
  'warming_subscribe',
  'warming_channels_added',
  'warming_channel_removed',
  'warming_channel_limit_reached',
  'warming_chat_filtered',
  'warming_chat_duplicate',
  'warming_dialogue_opened',
  'warming_dialogue_reply',
  'warming_dialogue_faded',
  'warming_promoted_to_neurocomment',
  'warming_unpromoted_from_neurocomment',
  'warming_quarantine_recovered',
  'warming_quarantine_exhausted',
  'warming_settings_saved',
  'warming_start_blocked',
  'warming_runtime_reconciled',
  'warming_unpromoted_on_restart',
  'phase_advanced',
  'spam_status_refreshed',
  // Telegram gateway actions the warming loop performs (shown in the card log).
  'telegram_set_online',
  'telegram_read_channel',
  'telegram_read_channel_failed',
  'telegram_watch_peer_stories',
  'telegram_react_to_post',
  'telegram_react_to_post_failed',
  'telegram_join_channel',
  'telegram_join_channel_failed',
  'telegram_join_discussion_group',
  'telegram_post_story',
  'telegram_post_story_failed',
  'telegram_spam_status_probe_failed',
  'telegram_pool_connect_failed',
  'telegram_pool_connect_retry',
]);

/**
 * Localize a backend log event code. Known codes resolve to `logEvent.<code>`;
 * any unmapped code (a new backend event, or a code from another domain) falls
 * back to the raw code so the row is never blank.
 */
export function eventLabel(t: TFunction, code: string): string {
  return KNOWN_EVENTS.has(code) ? t(`logEvent.${code}`) : code;
}
