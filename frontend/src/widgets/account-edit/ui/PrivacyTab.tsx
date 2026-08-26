import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  accountPrivacyQueryOptions,
  setAccountPrivacyMutation,
  setAllAccountsPrivacyMutation,
} from '@/entities/account';
import type { AccountPrivacyUpdateRequest, PrivacySettingsResult } from '@/shared/api';
import { Button, ConfirmModal, Notice } from '@/shared/ui';

import { envelopeMessage } from './_channelsShared';
import { PrivacyLevelRow, type PrivacyLevel, type PrivacyShown } from './PrivacyLevelRow';

// The profile modal's privacy tab: the three Telegram privacy keys that decide
// whether STRANGERS see the avatar and the bio we upload. Restricted keys are
// why a correctly uploaded photo still shows as a letter placeholder, so the
// primary action here opens those two at once. Like the channels tab, this tab
// owns its own query and stays outside the profile-snapshot busy scrim.
const KEYS = ['profile_photo', 'bio', 'last_seen'] as const;

// Photo + bio only. `last_seen` is deliberately NOT in the one-click action:
// opening it publishes a warming account's online schedule to any observer,
// and Telegram's reciprocity rule then also changes what the account itself
// can see. The per-row control still sets it, deliberately.
const OPEN_TO_ALL: AccountPrivacyUpdateRequest = {
  profile_photo: 'everybody',
  bio: 'everybody',
};

// 'unknown' is a level Telegram holds but the dashboard does not model, so it
// can never be sent back: those keys ride as null (= leave unchanged).
const sendable = (level: PrivacyShown | undefined): PrivacyLevel | null =>
  level != null && level !== 'unknown' ? level : null;

function fleetBody(settings: PrivacySettingsResult): AccountPrivacyUpdateRequest {
  return {
    profile_photo: sendable(settings.profile_photo),
    bio: sendable(settings.bio),
    last_seen: sendable(settings.last_seen),
  };
}

// Anything the fleet would receive that is not "everybody" narrows the whole
// farm — including the accounts that were fine.
const restrictive = (settings: PrivacySettingsResult): boolean =>
  KEYS.some((key) => {
    const level = sendable(settings[key]);
    return level !== null && level !== 'everybody';
  });

export function PrivacyTab({ accountId }: { accountId: string }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const readOpts = accountPrivacyQueryOptions({ path: { account_id: accountId } });
  const privacy = useQuery(readOpts);
  const apply = useMutation(setAccountPrivacyMutation());
  const applyAll = useMutation(setAllAccountsPrivacyMutation());
  const [confirmFleet, setConfirmFleet] = useState(false);
  // A write that APPLIED but whose re-read Telegram refused. Not an error
  // state: the rows keep the last known levels and a fresh read is already in
  // flight — blanking the tab here would tell the operator the write failed
  // and invite a second press.
  const [writeReadError, setWriteReadError] = useState<string | null>(null);

  // Every reason the backend hands this tab is a stable code, not display text:
  // a `failed` outcome carries a gateway code (accounts.profile.code.*), a
  // `skipped` one the AccountStatus that disqualified the account
  // (accounts.status.*), and a refused read its own code. Rendered raw, the
  // fleet report read "acc-3 — пропущен (unauthorized)". An unknown value (a
  // read reason like `RPC: <Class>`) still shows as-is.
  const reasonText = (value: string, retryAfterSeconds?: number | null): string =>
    t([`accounts.profile.code.${value}`, `accounts.status.${value}`], {
      defaultValue: value,
      // The rate-limit family (flood_wait, slow_mode_wait, premium_wait)
      // interpolates a duration. Only a fleet outcome carries one —
      // `AccountPrivacyOutcome.retry_after_seconds`, the wait the server was
      // actually given — so «повторите через 30 с» is real advice there. The
      // read and write-read paths have no such field on their schemas, and they
      // keep the '?' the global mutation toast uses.
      s: retryAfterSeconds ?? '?',
    });

  const settings = privacy.data?.settings ?? null;
  // Three shapes of read failure, one banner. A refused read arrives as
  // {settings:null, error:code} with HTTP 200; a rejected request carries the
  // /api/v1 envelope — but a 500 {"detail":…} or a dead fetch does NOT, so
  // `isError` (like the channels tab) must still produce a reason, or the tab
  // renders the hint paragraph and nothing else.
  const readError =
    privacy.data?.error ?? (privacy.isError ? envelopeMessage(privacy.error) : null);
  const reason =
    readError != null
      ? reasonText(readError)
      : privacy.isError
        ? t('accounts.profile.privacy.noReason')
        : null;

  const refreshRead = () => {
    void queryClient.invalidateQueries({ queryKey: readOpts.queryKey });
  };

  // A later successful read replaces the last-known levels with live ones, so
  // the "applied but couldn't re-read" notice must not outlive it.
  const readAt = privacy.dataUpdatedAt;
  const hasSettings = privacy.data?.settings != null;
  useEffect(() => {
    if (hasSettings) setWriteReadError(null);
  }, [readAt, hasSettings]);

  const write = (body: AccountPrivacyUpdateRequest) => {
    // A new write invalidates the previous fleet report: its counts describe a
    // state this write is about to replace.
    applyAll.reset();
    setWriteReadError(null);
    apply.mutate(
      { path: { account_id: accountId }, body },
      {
        onSuccess: (view) => {
          // The write answers with the re-read live state, so seed the cache
          // with it — the backend already paid for that read. A REFUSED
          // re-read comes back 200 too, as {settings:null, error:code}:
          // seeding THAT would wipe the rows and both buttons and blame the
          // read for a write that worked.
          if (view.settings != null) {
            queryClient.setQueryData(readOpts.queryKey, view);
            return;
          }
          setWriteReadError(
            view.error != null ? reasonText(view.error) : t('accounts.profile.privacy.noReason'),
          );
          refreshRead();
        },
        // A partial write (key 1 applied, key 2 flooded) rejects, and the rows
        // would otherwise show pre-write values forever.
        onError: refreshRead,
      },
    );
  };

  // The fleet dialog spells out the levels it is about to push. Without them one
  // click could propagate a restricted profile — the very state that hides the
  // avatar — across the whole fleet from a generic "apply to all?" prompt. Keys
  // that ride as null (unknown) are left out: naming them would be a lie.
  const fleetSummary = (current: PrivacySettingsResult) =>
    KEYS.flatMap((key) => {
      const level = sendable(current[key]);
      return level === null
        ? []
        : [
            `${t(`accounts.profile.privacy.row.${key}`)} — ${t(
              `accounts.profile.privacy.level.${level}`,
            )}`,
          ];
    }).join(' · ');

  const fleetPrompt = (current: PrivacySettingsResult) =>
    [
      t('accounts.profile.privacy.applyAllBody', { levels: fleetSummary(current) }),
      restrictive(current) ? t('accounts.profile.privacy.applyAllWarn') : null,
      t('accounts.profile.privacy.applyAllIrreversible'),
    ]
      .filter((part): part is string => part !== null)
      .join(' ');

  const bulk = applyAll.data;
  const sweeping = applyAll.isPending;
  const writing = apply.isPending;
  // An all-null body is a 422 by contract, so a fleet apply needs at least one
  // level the dashboard can actually express — and the levels on screen must be
  // trustworthy: with a read error showing, the rows are stale (staleTime is 0,
  // so a tab-away-and-back refetch can fail over live data) and one press would
  // push those stale levels to every account.
  const canFleet =
    settings != null && reason == null && KEYS.some((key) => sendable(settings[key]) !== null);
  // A per-account write and the fleet sweep both write to THIS account, so they
  // must never overlap: the loser's value silently wins on screen.
  const locked = writing || sweeping;

  return (
    <div>
      <div className="mb-md type-prose">{t('accounts.profile.privacy.hint')}</div>

      {privacy.isPending && (
        <div
          role="status"
          aria-label={t('accounts.profile.privacy.loading')}
          className="flex justify-center py-2xl"
        >
          <span className="tb-spin inline-block size-chip rounded-full border-2 border-line border-t-primary" />
        </div>
      )}

      {reason != null && (
        <Notice
          tone="danger"
          className="mb-lg flex items-center justify-between gap-md"
          role="alert"
        >
          <span>{t('accounts.profile.privacy.loadError', { reason })}</span>
          <Button
            size="xs"
            variant="danger"
            className="bg-white"
            onClick={() => {
              void privacy.refetch();
            }}
          >
            {t('accounts.profile.privacy.retry')}
          </Button>
        </Notice>
      )}

      {writeReadError != null && (
        <div
          role="status"
          aria-live="polite"
          // Prose on an amber surface, so `ink-body` rather than `ink-muted`: moving this
          // notice onto `warning-tint` left the muted grey at 4.26:1, just under the AA
          // floor it used to clear at 4.53:1 on the literal it replaced.
          className="mb-lg rounded-lg border border-line bg-warning-tint px-md py-md text-body text-ink-body"
        >
          {t('accounts.profile.privacy.writeReadError', { reason: writeReadError })}
        </div>
      )}

      {settings && (
        <>
          <div className="flex flex-col gap-sm">
            {KEYS.map((key) => (
              <PrivacyLevelRow
                key={key}
                label={t(`accounts.profile.privacy.row.${key}`)}
                current={settings[key] ?? 'unknown'}
                busy={locked}
                onPick={(level) => {
                  // Only the key the operator touched: every non-null field
                  // costs its own account.setPrivacy round trip.
                  write({ [key]: level });
                }}
              />
            ))}
          </div>

          <div className="mt-lg flex flex-wrap items-center gap-sm">
            <Button
              variant="primary"
              size="sm"
              disabled={locked}
              onClick={() => {
                write(OPEN_TO_ALL);
              }}
            >
              {t('accounts.profile.privacy.openAll')}
            </Button>
            <Button
              size="sm"
              disabled={locked || !canFleet}
              onClick={() => {
                setConfirmFleet(true);
              }}
            >
              {/* The confirm dialog closes on Escape / backdrop while the sweep
                  keeps running for minutes, so the button label is the only
                  remaining trace of it — it has to say so. */}
              {sweeping
                ? t('accounts.profile.privacy.applyAllPending')
                : t('accounts.profile.privacy.applyAll')}
            </Button>
          </div>
        </>
      )}

      {bulk && (
        <div
          role="status"
          aria-live="polite"
          className="mt-lg rounded-lg border border-line bg-white px-lg py-md text-body"
        >
          <div className="flex flex-wrap gap-x-lg gap-y-tight">
            <span>{t('accounts.profile.privacy.bulkOk', { n: bulk.ok })}</span>
            <span className={bulk.failed > 0 ? 'text-danger' : undefined}>
              {t('accounts.profile.privacy.bulkFailed', { n: bulk.failed })}
            </span>
            <span className="text-ink-subtle">
              {t('accounts.profile.privacy.bulkSkipped', { n: bulk.skipped })}
            </span>
          </div>
          {bulk.outcomes.some((outcome) => outcome.status !== 'ok') && (
            <ul className="mt-sm flex flex-col gap-tight border-t border-line-row pt-sm text-tiny text-ink-subtle">
              {/* Both non-ok kinds are listed with their reason: a skipped
                  account carries the status that disqualified it, and "3
                  skipped" with no names is not actionable. */}
              {bulk.outcomes
                .filter((outcome) => outcome.status !== 'ok')
                .map((outcome) => (
                  <li
                    key={outcome.account_id}
                    className={outcome.status === 'failed' ? 'truncate text-danger' : 'truncate'}
                  >
                    {[
                      t(
                        outcome.status === 'failed'
                          ? 'accounts.profile.privacy.outcomeFailed'
                          : 'accounts.profile.privacy.outcomeSkipped',
                        {
                          id: outcome.account_id,
                          reason:
                            outcome.error != null
                              ? reasonText(outcome.error, outcome.retry_after_seconds)
                              : t('accounts.profile.privacy.noReason'),
                        },
                      ),
                      // setPrivacy is one call per key with no rollback, so a
                      // FAILED account is not necessarily unchanged: with
                      // applied:["profile_photo"] its avatar is already public.
                      // For a feature about visibility that fact must be on
                      // screen, not implied by "failed".
                      outcome.applied && outcome.applied.length > 0
                        ? t('accounts.profile.privacy.outcomeApplied', {
                            keys: outcome.applied
                              .map((key) =>
                                t(`accounts.profile.privacy.row.${key}`, { defaultValue: key }),
                              )
                              .join(', '),
                          })
                        : null,
                    ]
                      .filter((part): part is string => part !== null)
                      .join(' · ')}
                  </li>
                ))}
            </ul>
          )}
        </div>
      )}

      {confirmFleet && settings ? (
        <ConfirmModal
          title={t('accounts.profile.privacy.applyAllTitle')}
          body={fleetPrompt(settings)}
          confirmLabel={t('accounts.profile.privacy.applyAllConfirm')}
          cancelLabel={t('accounts.profile.privacy.cancel')}
          onClose={() => {
            setConfirmFleet(false);
          }}
          onConfirm={() =>
            applyAll
              .mutateAsync({ body: fleetBody(settings) })
              // The sweep writes to THIS account too, so what the rows show is
              // no longer sourced from the last read. Finally, not then: a
              // partially applied sweep changed accounts as well.
              .finally(refreshRead)
          }
        />
      ) : null}
    </div>
  );
}
