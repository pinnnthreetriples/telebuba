import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { NeurocommentSettingsUpdate } from '@/shared/api';
import { Button, Icon, IconButton, Modal, Select, toastError } from '@/shared/ui';

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
    <Modal onClose={onClose} className="w-confirm" label={t('neurocomment.listener.title')}>
      <div className="p-2xl">
        <div className="mb-tight flex items-center gap-md">
          <span className="flex size-tile shrink-0 items-center justify-center rounded-lg bg-primary-tint text-primary-deep">
            <Icon name="chart" size={18} />
          </span>
          <div className="flex-1">
            <div className="type-dialog-title">{t('neurocomment.listener.title')}</div>
            <div className="mt-px type-prose">{t('neurocomment.modal.listenerEdit.sub')}</div>
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

        <div className="mb-sm mt-xl type-label">{t('neurocomment.modal.listenerEdit.account')}</div>
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

        <div className="mt-2xl flex justify-end gap-sm">
          <Button onClick={onClose}>{t('neurocomment.modal.cancel')}</Button>
          <Button
            variant="primary"
            onClick={save}
            // A second click while the PUT is open would send the same body again.
            loading={saveSettings.isPending}
            className={saved ? 'border-success-deep bg-success-deep hover:bg-success-deep' : ''}
          >
            {saved ? (
              <span className="inline-flex items-center gap-sm">
                <span className="inline-flex [animation:swapin_0.3s_ease_both]">
                  <Icon name="check" size={16} />
                </span>
                <span className="inline-block [animation:swapin_0.3s_ease_0.09s_both]">
                  {t('neurocomment.modal.saved')}
                </span>
              </span>
            ) : (
              t('neurocomment.modal.save')
            )}
          </Button>
        </div>
      </div>
    </Modal>
  );
}
