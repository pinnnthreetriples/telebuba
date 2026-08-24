import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { NeurocommentSettingsUpdate } from '@/shared/api';
import { IconButton, Modal, Select, toastError } from '@/shared/ui';

import {
  neurocommentSettingsQueryOptions,
  updateNeurocommentSettingsMutation,
} from '../api/campaign.queries';
import { type CommentMode, CommentModeFields } from './CommentModeFields';

// Design modal: listener-edit (L1387-1422) — pick the listener account from a
// custom dropdown, save with a check→"Сохранено" swap. Also the home of the fleet-wide
// comment mode: it is the same decision about the same listener, and here it obeys the
// modal's own contract — applied on "Сохранить", thrown away by "Отмена".
export function ListenerEditModal({
  options,
  selected,
  onClose,
  onSave,
}: {
  options: { id: string; name: string }[];
  selected: string | null;
  onClose: () => void;
  onSave: (id: string) => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const settings = useQuery(neurocommentSettingsQueryOptions());
  const saveSettings = useMutation(updateNeurocommentSettingsMutation());
  const stored = settings.data;
  const [pick, setPick] = useState(selected);
  const [saved, setSaved] = useState(false);
  // `null` means "untouched", which is also how the draft survives a read that lands after
  // the modal opened: no effect syncing query into state, and nothing to compare when the
  // operator never touched the fields.
  const [mode, setMode] = useState<CommentMode | null>(null);
  const [wait, setWait] = useState<number | null>(null);

  const finish = () => {
    setSaved(true);
    setTimeout(onClose, 650);
  };

  const save = () => {
    // The account goes through the page, which owns its own failure path; the settings PUT
    // is the one that can fail here, so it — and only it — gates the "Сохранено" swap.
    if (pick) onSave(pick);
    // Only what actually differs from the stored settings, so a Save the operator changed
    // nothing in — or one that ended back where it started — costs no request at all.
    const patch: Partial<NeurocommentSettingsUpdate> = {};
    if (stored !== undefined) {
      if (mode !== null && mode !== stored.comment_mode) patch.comment_mode = mode;
      if (wait !== null && wait !== stored.reply_wait_minutes) patch.reply_wait_minutes = wait;
    }
    if (stored === undefined || Object.keys(patch).length === 0) {
      finish();
      return;
    }
    saveSettings.mutate(
      {
        // PUT /settings replaces the limits wholesale and forbids extra keys, so the write
        // carries the stored numbers back unchanged — and `updated_at` cannot ride along.
        body: {
          max_comments_per_hour: stored.max_comments_per_hour,
          max_comments_per_channel_per_day: stored.max_comments_per_channel_per_day,
          reply_delay_min_seconds: stored.reply_delay_min_seconds,
          reply_delay_max_seconds: stored.reply_delay_max_seconds,
          min_trust_score: stored.min_trust_score,
          ...patch,
        },
      },
      {
        onSuccess: async () => {
          await queryClient.invalidateQueries({
            queryKey: neurocommentSettingsQueryOptions().queryKey,
          });
          finish();
        },
        // Stay open and say so: a check mark over a rejected PUT would send the operator
        // away believing the fleet had changed mode.
        onError: () => {
          toastError(
            t(
              patch.comment_mode === undefined
                ? 'neurocomment.mode.waitFailed'
                : 'neurocomment.mode.failed',
            ),
          );
        },
      },
    );
  };

  return (
    <Modal onClose={onClose} className="w-[440px]" label={t('neurocomment.listener.title')}>
      <div className="p-6">
        <div className="mb-[6px] flex items-center gap-md">
          <span className="flex h-[34px] w-[34px] shrink-0 items-center justify-center rounded-lg bg-primary-tint text-primary">
            <svg
              width="17"
              height="17"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <path d="M2 10v3" />
              <path d="M6 6v11" />
              <path d="M10 3v18" />
              <path d="M14 8v7" />
              <path d="M18 5v13" />
              <path d="M22 10v3" />
            </svg>
          </span>
          <div className="flex-1">
            <div className="text-title font-bold">{t('neurocomment.listener.title')}</div>
            <div className="mt-px text-body text-ink-subtle">
              {t('neurocomment.modal.listenerEdit.sub')}
            </div>
          </div>
          <IconButton
            size="md"
            aria-label={t('neurocomment.modal.close')}
            onClick={onClose}
            className="text-title"
          >
            ×
          </IconButton>
        </div>

        <div className="mb-[7px] mt-[18px] text-body font-medium text-ink-body">
          {t('neurocomment.modal.listenerEdit.account')}
        </div>
        <Select
          value={pick ?? ''}
          onChange={setPick}
          options={options.map((o) => ({ value: o.id, label: o.name }))}
          placeholder={t('neurocomment.listener.choose')}
          ariaLabel={t('neurocomment.modal.listenerEdit.account')}
        />

        {/* Until the read lands, the backend's own fallbacks, so the control never renders
            with nothing pressed — which would read as a third state. */}
        <CommentModeFields
          mode={mode ?? stored?.comment_mode ?? 'first'}
          waitMinutes={wait ?? stored?.reply_wait_minutes ?? 10}
          disabled={stored === undefined || saveSettings.isPending}
          onModeChange={setMode}
          onWaitChange={setWait}
        />

        <div className="mt-[22px] flex justify-end gap-sm">
          <button
            type="button"
            onClick={onClose}
            className="rounded-full border border-line-input bg-white px-[22px] py-[9px] text-lead font-semibold text-ink"
          >
            {t('neurocomment.modal.cancel')}
          </button>
          <button
            type="button"
            onClick={save}
            // A second click while the PUT is open would send the same body again.
            disabled={saveSettings.isPending}
            className={`rounded-full border px-[22px] py-[9px] text-lead font-semibold text-white disabled:opacity-60 ${saved ? 'border-success bg-success' : 'border-primary bg-primary'}`}
          >
            {saved ? (
              <span className="inline-flex items-center gap-sm">
                <span className="inline-flex [animation:swapin_0.3s_ease_both]">
                  <svg
                    width="15"
                    height="15"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2.4"
                  >
                    <path d="M20 6 9 17l-5-5" />
                  </svg>
                </span>
                <span className="inline-block [animation:swapin_0.3s_ease_0.09s_both]">
                  {t('neurocomment.modal.saved')}
                </span>
              </span>
            ) : (
              t('neurocomment.modal.save')
            )}
          </button>
        </div>
      </div>
    </Modal>
  );
}
