import { describe, expect, it } from 'vitest';

import { logSeverity } from './eventSeverity';

describe('logSeverity', () => {
  it('maps attempted-but-failed events to error regardless of log level', () => {
    // Logged INFO on the backend, but reads as a failure in the feed.
    expect(logSeverity({ event: 'neurocomment_generation_exhausted', status: 'success' })).toBe(
      'error',
    );
    // Logged WARNING — still a real publish failure, so red not amber.
    expect(logSeverity({ event: 'neurocomment_post_failed', status: 'warning' })).toBe('error');
    expect(logSeverity({ event: 'neurocomment_post_dropped_overloaded', status: 'success' })).toBe(
      'error',
    );
    // A deleted comment reads as a failure, even logged WARNING.
    expect(logSeverity({ event: 'neurocomment_comment_deleted', status: 'warning' })).toBe('error');
    // An account banned in a channel reads as a failure (red), logged WARNING.
    expect(logSeverity({ event: 'neurocomment_account_banned', status: 'warning' })).toBe('error');
  });

  it('maps deliberate skips / pauses / limits to warning', () => {
    expect(logSeverity({ event: 'neurocomment_post_skipped', status: 'success' })).toBe('warning');
    expect(logSeverity({ event: 'neurocomment_no_account_available', status: 'success' })).toBe(
      'warning',
    );
    expect(logSeverity({ event: 'neurocomment_no_campaign', status: 'success' })).toBe('warning');
    expect(logSeverity({ event: 'neurocomment_channel_cooled', status: 'success' })).toBe(
      'warning',
    );
  });

  it('leaves successes and neutral lifecycle events green', () => {
    expect(logSeverity({ event: 'neurocomment_posted', status: 'success' })).toBe('success');
    expect(logSeverity({ event: 'neurocomment_runtime_reconciled', status: 'success' })).toBe(
      'success',
    );
  });

  it('falls back to the level-derived status for unclassified codes', () => {
    expect(logSeverity({ event: 'some_unknown_event', status: 'error' })).toBe('error');
    expect(logSeverity({ event: 'some_unknown_event', status: 'success' })).toBe('success');
  });
});
