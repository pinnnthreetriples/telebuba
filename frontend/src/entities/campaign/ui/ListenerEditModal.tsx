import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { NeurocommentSettingsUpdate } from '@/shared/api';
import { HEADING_ICON_TILE } from '@/shared/design-system';
import { Button, CloseButton, Icon, Modal, Select, toastError } from '@/shared/ui';

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
  onSave: (id: string) => Promise<boolean>;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const settings = useQuery(neurocommentSettingsQueryOptions());
  const saveSettings = useMutation(updateNeurocommentSettingsMutation());
  const stored = settings.data;
  const [pick, setPick] = useState(selected);
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);
  const [partialSave, setPartialSave] = useState(false);
  // `null` means "untouched", which is also how the draft survives a read that lands after
  // the modal opened: no effect syncing query into state, and nothing to compare when the
  // operator never touched the fields.
  const [mode, setMode] = useState<CommentMode | null>(null);
  const [wait, setWait] = useState<number | null>(null);

  const finish = () => {
    setSaved(true);
    setTimeout(onClose, 650);
  };

  const close = () => {
    if (!saving) onClose();
  };

  const save = async () => {
    if (saving || saveSettings.isPending) return;
    setSaving(true);
    const patch: Partial<NeurocommentSettingsUpdate> = {};
    try {
      // Only what actually differs from the stored settings, so a Save the operator changed
      // nothing in — or one that ended back where it started — costs no request at all.
      if (stored !== undefined) {
        if (mode !== null && mode !== stored.comment_mode) patch.comment_mode = mode;
        if (wait !== null && wait !== stored.reply_wait_minutes) patch.reply_wait_minutes = wait;
      }
      let settingsApplied = false;
      if (stored !== undefined && Object.keys(patch).length > 0) {
        try {
          // Only the mode pair: every field of PUT /settings is patch-shaped, and sending the
          // cached limits back would roll back a limits save made from another tab.
          const updated = await saveSettings.mutateAsync({ body: patch });
          queryClient.setQueryData(neurocommentSettingsQueryOptions().queryKey, updated);
          void queryClient.invalidateQueries({
            queryKey: neurocommentSettingsQueryOptions().queryKey,
          });
          setMode(null);
          setWait(null);
          settingsApplied = true;
        } catch {
          toastError(
            t(
              patch.comment_mode === undefined
                ? 'neurocomment.mode.waitFailed'
                : 'neurocomment.mode.failed',
            ),
          );
          return;
        }
      }
      if (pick && pick !== selected) {
        let listenerApplied = false;
        try {
          listenerApplied = await onSave(pick);
        } catch {
          // The page may report a rejected assignment by throwing or returning false.
        }
        if (!listenerApplied) {
          if (settingsApplied || partialSave) setPartialSave(true);
          else toastError(t('neurocomment.listener.saveFailed'));
          return;
        }
      }
      finish();
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal onClose={close} size="confirm" label={t('neurocomment.listener.title')}>
      <div className="p-2xl">
        <div className="mb-tight flex items-center gap-md">
          <span className={HEADING_ICON_TILE}>
            <Icon name="chart" size={18} />
          </span>
          <div className="flex-1">
            <div className="type-dialog-title">{t('neurocomment.listener.title')}</div>
            <div className="mt-px type-prose">{t('neurocomment.modal.listenerEdit.sub')}</div>
          </div>
          <CloseButton
            aria-label={t('neurocomment.modal.close')}
            onClick={close}
            disabled={saving}
          />
        </div>

        <div className="mb-sm mt-xl type-label">{t('neurocomment.modal.listenerEdit.account')}</div>
        <Select
          value={pick ?? ''}
          onChange={setPick}
          disabled={saving}
          options={options.map((o) => ({ value: o.id, label: o.name }))}
          placeholder={t('neurocomment.listener.choose')}
          ariaLabel={t('neurocomment.modal.listenerEdit.account')}
        />

        {/* Until the read lands, the backend's own fallbacks, so the control never renders
            with nothing pressed — which would read as a third state. */}
        <CommentModeFields
          mode={mode ?? stored?.comment_mode ?? 'first'}
          waitMinutes={wait ?? stored?.reply_wait_minutes ?? 10}
          disabled={stored === undefined || saving}
          onModeChange={setMode}
          onWaitChange={setWait}
        />

        {partialSave ? (
          <p role="alert" className="mt-sm type-caption text-danger">
            {t('neurocomment.modal.listenerEdit.partialSave')}
          </p>
        ) : null}

        <div className="mt-2xl flex justify-end gap-sm">
          <Button onClick={close} disabled={saving}>
            {t('neurocomment.modal.cancel')}
          </Button>
          <Button
            variant="primary"
            onClick={save}
            // A second click while the PUT is open would send the same body again.
            loading={saving}
            className={saved ? 'border-success-deep bg-success-deep hover:bg-success-deep' : ''}
          >
            {saved ? (
              <span className="inline-flex items-center gap-sm">
                <span className="inline-flex tb-swapin">
                  <Icon name="check" size={16} />
                </span>
                <span className="inline-block tb-swapin-late">{t('neurocomment.modal.saved')}</span>
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
