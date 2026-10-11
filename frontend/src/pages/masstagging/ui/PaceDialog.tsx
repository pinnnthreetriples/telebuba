// The board's gear: «Темп» only, and it changes on the fly — no pause needed. The run
// reads it from its next step on.
import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { saveMasstaggingPaceMutation } from '@/entities/masstagging';
import type { MasstaggingSettingsRead } from '@/shared/api';
import { Button, Modal, ModalFooter, ModalHeader, Notice } from '@/shared/ui';

import { draftOf, type Pace, paceOf, samePace } from '../model/draft';
import { conflictCode } from '../model/errors';

import { PaceSection } from './settings/PaceSection';

export function PaceDialog({
  read,
  onClose,
  onSaved,
}: {
  read: MasstaggingSettingsRead;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { t } = useTranslation();
  // What the dialog opened with: a board refetch while it is open must not make the
  // operator's edits look saved, or unsaved ones look clean.
  const [baseline] = useState<Pace>(() => paceOf(draftOf(read).settings));
  const [pace, setPace] = useState<Pace>(baseline);
  const [conflict, setConflict] = useState(false);
  const save = useMutation(saveMasstaggingPaceMutation());
  const dirty = !samePace(pace, baseline);
  const stories = (read.settings.mode ?? 'stories') === 'stories';

  const confirm = () => {
    setConflict(false);
    void save
      .mutateAsync({ path: { campaign_id: read.campaign_id }, body: pace })
      .then(() => {
        onSaved();
        onClose();
      })
      .catch((error: unknown) => {
        if (conflictCode(error) === 'campaign_changed') setConflict(true);
      });
  };

  return (
    <Modal onClose={onClose} dirty={dirty} size="form" label={t('masstagging.paceDialog.title')}>
      {(close) => (
        <>
          <ModalHeader
            title={t('masstagging.paceDialog.title')}
            subtitle={t('masstagging.paceDialog.subtitle')}
          />
          {conflict ? (
            <div className="px-6 pt-4" role="alert">
              <Notice tone="danger">{t('masstagging.paceDialog.conflict')}</Notice>
            </div>
          ) : null}
          <fieldset disabled={save.isPending} className="m-0 min-w-0 border-0 px-6 py-4">
            <PaceSection
              stories={stories}
              pace={pace}
              onPatch={(next) => {
                setPace((current) => ({ ...current, ...next }));
              }}
            />
          </fieldset>
          <ModalFooter>
            <Button size="sm" onClick={close}>
              {t('masstagging.settings.cancel')}
            </Button>
            <Button
              variant="primary"
              size="sm"
              disabled={!dirty || save.isPending}
              onClick={confirm}
            >
              {t('masstagging.settings.save')}
            </Button>
          </ModalFooter>
        </>
      )}
    </Modal>
  );
}
