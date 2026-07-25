import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  accountPrivacyQueryOptions,
  setAccountPrivacyMutation,
  setAllAccountsPrivacyMutation,
} from '@/entities/account';
import type { AccountPrivacyUpdateRequest, PrivacySettingsResult } from '@/shared/api';
import { ConfirmModal } from '@/shared/ui';

import { envelopeMessage } from './_channelsShared';
import { PrivacyLevelRow, type PrivacyLevel, type PrivacyShown } from './PrivacyLevelRow';

// The profile modal's privacy tab: the three Telegram privacy keys that decide
// whether STRANGERS see the avatar and the bio we upload. Restricted keys are
// why a correctly uploaded photo still shows as a letter placeholder, so the
// primary action here opens all three at once. Like the channels tab, this tab
// owns its own query and stays outside the profile-snapshot busy scrim.
const KEYS = ['profile_photo', 'bio', 'last_seen'] as const;

const OPEN_TO_ALL: AccountPrivacyUpdateRequest = {
  profile_photo: 'everybody',
  bio: 'everybody',
  last_seen: 'everybody',
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

export function PrivacyTab({ accountId }: { accountId: string }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const readOpts = accountPrivacyQueryOptions({ path: { account_id: accountId } });
  const privacy = useQuery(readOpts);
  const apply = useMutation(setAccountPrivacyMutation());
  const applyAll = useMutation(setAllAccountsPrivacyMutation());
  const [confirmFleet, setConfirmFleet] = useState(false);

  const settings = privacy.data?.settings ?? null;
  // A refused read arrives as {settings:null, error:code} with HTTP 200; a
  // genuinely broken request rejects with the /api/v1 envelope. Same banner.
  const reason = privacy.data?.error ?? envelopeMessage(privacy.error);

  // The write answers with the re-read live state, so seed the cache with it
  // instead of invalidating — the backend already paid for that read.
  const write = (body: AccountPrivacyUpdateRequest) => {
    apply.mutate(
      { path: { account_id: accountId }, body },
      {
        onSuccess: (view) => {
          queryClient.setQueryData(readOpts.queryKey, view);
        },
      },
    );
  };

  // The fleet dialog spells out the levels it is about to push. Without them one
  // click could propagate a restricted profile — the very state that hides the
  // avatar — across the whole fleet from a generic "apply to all?" prompt. Keys
  // that ride as null (unknown) are left out: naming them would be a lie.
  const fleetSummary = (settings: PrivacySettingsResult) =>
    KEYS.flatMap((key) => {
      const level = sendable(settings[key]);
      return level === null
        ? []
        : [
            `${t(`accounts.profile.privacy.row.${key}`)} — ${t(
              `accounts.profile.privacy.level.${level}`,
            )}`,
          ];
    }).join(' · ');

  const bulk = applyAll.data;
  // An all-null body is a 422 by contract, so a fleet apply needs at least one
  // level the dashboard can actually express.
  const canFleet = settings != null && KEYS.some((key) => sendable(settings[key]) !== null);

  return (
    <div>
      <div className="mb-3 text-[12px] leading-relaxed text-ink-subtle">
        {t('accounts.profile.privacy.hint')}
      </div>

      {privacy.isPending && (
        <div
          role="status"
          aria-label={t('accounts.profile.privacy.loading')}
          className="flex justify-center py-6"
        >
          <span className="tb-spin inline-block h-6 w-6 rounded-full border-2 border-line-input border-t-primary" />
        </div>
      )}

      {reason != null && (
        <div className="mb-4 flex items-center justify-between gap-3 rounded-[10px] border border-[#f0c9c5] bg-danger-tint px-3 py-[10px] text-[12.5px] text-danger">
          <span>{t('accounts.profile.privacy.loadError', { reason })}</span>
          <button
            type="button"
            onClick={() => {
              void privacy.refetch();
            }}
            className="shrink-0 rounded-full border border-[#f0c9c5] bg-white px-3 py-[4px] text-[12px] font-medium"
          >
            {t('accounts.profile.privacy.retry')}
          </button>
        </div>
      )}

      {settings && (
        <>
          <div className="flex flex-col gap-2">
            {KEYS.map((key) => (
              <PrivacyLevelRow
                key={key}
                label={t(`accounts.profile.privacy.row.${key}`)}
                current={settings[key] ?? 'unknown'}
                busy={apply.isPending}
                onPick={(level) => {
                  // Only the key the operator touched: every non-null field
                  // costs its own account.setPrivacy round trip.
                  write({ [key]: level });
                }}
              />
            ))}
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-2">
            <button
              type="button"
              disabled={apply.isPending}
              onClick={() => {
                write(OPEN_TO_ALL);
              }}
              className="rounded-full bg-primary px-4 py-2 text-[13px] font-semibold text-white disabled:opacity-60"
            >
              {t('accounts.profile.privacy.openAll')}
            </button>
            <button
              type="button"
              disabled={applyAll.isPending || !canFleet}
              onClick={() => {
                setConfirmFleet(true);
              }}
              className="rounded-full border border-line-input bg-white px-4 py-2 text-[13px] font-medium disabled:opacity-60"
            >
              {t('accounts.profile.privacy.applyAll')}
            </button>
          </div>
        </>
      )}

      {bulk && (
        <div className="mt-4 rounded-[12px] border border-line bg-white px-[14px] py-3 text-[12.5px]">
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            <span>{t('accounts.profile.privacy.bulkOk', { n: bulk.ok })}</span>
            <span className={bulk.failed > 0 ? 'text-danger' : undefined}>
              {t('accounts.profile.privacy.bulkFailed', { n: bulk.failed })}
            </span>
            <span className="text-ink-subtle">
              {t('accounts.profile.privacy.bulkSkipped', { n: bulk.skipped })}
            </span>
          </div>
          {bulk.failed > 0 && (
            <ul className="mt-2 flex flex-col gap-1 border-t border-[#f0eeeb] pt-2 text-[11.5px] text-ink-subtle">
              {bulk.outcomes
                .filter((outcome) => outcome.status === 'failed')
                .map((outcome) => (
                  <li key={outcome.account_id} className="truncate">
                    {outcome.account_id} — {outcome.error ?? t('accounts.profile.privacy.noReason')}
                  </li>
                ))}
            </ul>
          )}
        </div>
      )}

      {confirmFleet && settings ? (
        <ConfirmModal
          title={t('accounts.profile.privacy.applyAllTitle')}
          body={t('accounts.profile.privacy.applyAllBody', { levels: fleetSummary(settings) })}
          confirmLabel={t('accounts.profile.privacy.applyAllConfirm')}
          cancelLabel={t('accounts.profile.privacy.cancel')}
          onClose={() => {
            setConfirmFleet(false);
          }}
          onConfirm={() => applyAll.mutateAsync({ body: fleetBody(settings) })}
        />
      ) : null}
    </div>
  );
}
