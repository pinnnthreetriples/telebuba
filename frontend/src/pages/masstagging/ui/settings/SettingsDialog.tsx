// The campaign's settings dialog, shaped like the chat broadcast's: a header with the name
// and the "not saved" pill, numbered sections, a footer with Save. «Сторис» exists only in
// the stories mode. While a run goes the dialog only shows: the run read these settings
// once, and «Темп» has its own window on the board.
import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { saveMasstaggingSettingsMutation } from '@/entities/masstagging';
import type { AccountRead, MasstaggingSettingsRead } from '@/shared/api';
import { Button, Modal, Notice } from '@/shared/ui';

import {
  bodyOf,
  type Draft,
  draftOf,
  FREE_CAPTION_LIMIT,
  paceOf,
  PREMIUM_CAPTION_LIMIT,
  sameDraft,
  type Settings,
} from '../../model/draft';
import { isSpamBlocked } from '../../model/accounts';
import { conflictCode } from '../../model/errors';

import { AccountsSection } from './AccountsSection';
import { ModeSection } from './ModeSection';
import { PaceSection } from './PaceSection';
import { StorySection } from './StorySection';
import { TargetsSection } from './TargetsSection';

export function SettingsDialog({
  read,
  fleet,
  onClose,
  onSaved,
}: {
  read: MasstaggingSettingsRead;
  fleet: AccountRead[];
  onClose: () => void;
  onSaved: (saved: MasstaggingSettingsRead) => void;
}) {
  const { t } = useTranslation();
  const [baseline] = useState<Draft>(() => draftOf(read));
  // The version the draft was built from: a refetch while the dialog is open must not
  // move the optimistic lock under the operator's edits.
  const [stamp] = useState(read.updated_at);
  const [draft, setDraft] = useState<Draft>(baseline);
  const [conflict, setConflict] = useState(false);
  const save = useMutation(saveMasstaggingSettingsMutation());
  const locked = read.status === 'running' || read.status === 'resting';
  const dirty = !sameDraft(draft, baseline);
  const settings = draft.settings;
  const stories = settings.mode === 'stories';
  const byId = new Map(fleet.map((account) => [account.account_id, account]));
  const working = draft.accountIds
    .map((id) => byId.get(id))
    .filter((account) => account !== undefined && !isSpamBlocked(account));
  const premiumOnly = working.length > 0 && working.every((account) => account?.premium === true);

  const patch = (next: Partial<Settings>) => {
    setDraft((current) => ({ ...current, settings: { ...current.settings, ...next } }));
  };

  const confirm = () => {
    void save
      .mutateAsync({ path: { campaign_id: read.campaign_id }, body: bodyOf(draft, stamp) })
      .then((saved) => {
        onSaved(saved);
        onClose();
      })
      .catch((error: unknown) => {
        if (conflictCode(error) === 'campaign_changed') setConflict(true);
      });
  };

  return (
    <Modal
      onClose={onClose}
      dirty={dirty}
      locked={save.isPending}
      size="form"
      label={t('masstagging.settings.subtitle')}
    >
      {(close) => (
        <>
          <div className="flex items-center gap-3 border-b border-canvas px-6 pb-4 pt-6">
            <div className="min-w-0">
              <div className="truncate type-h2">{draft.name}</div>
              <div className="mt-1 type-small">{t('masstagging.settings.subtitle')}</div>
            </div>
            <div className="flex-1" />
            {dirty ? (
              <span className="shrink-0 rounded-full bg-warning-tint px-3 py-1 text-small font-medium text-warning-deep">
                {t('masstagging.settings.unsaved')}
              </span>
            ) : null}
          </div>
          {locked || conflict ? (
            <div className="px-6 pt-4" role="alert">
              <Notice tone={conflict ? 'danger' : 'info'}>
                {conflict ? t('masstagging.settings.conflict') : t('masstagging.settings.running')}
              </Notice>
            </div>
          ) : null}
          <fieldset disabled={locked || save.isPending} className="m-0 min-w-0 border-0 p-0">
            <div className="flex min-w-0 flex-col gap-8 px-6 py-6">
              <ModeSection
                mode={settings.mode}
                onChange={(mode) => {
                  patch({ mode });
                }}
              />
              <AccountsSection
                ids={draft.accountIds}
                fleet={fleet}
                stories={stories}
                onChange={(accountIds) => {
                  setDraft((current) => ({ ...current, accountIds }));
                }}
              />
              <TargetsSection campaignId={read.campaign_id} settings={settings} onPatch={patch} />
              {stories ? (
                <StorySection
                  campaignId={read.campaign_id}
                  settings={settings}
                  captionLimit={premiumOnly ? PREMIUM_CAPTION_LIMIT : FREE_CAPTION_LIMIT}
                  onPatch={patch}
                  onMedia={(update) => {
                    setDraft((current) => ({
                      ...current,
                      settings: { ...current.settings, media: update(current.settings.media) },
                    }));
                  }}
                />
              ) : null}
              <PaceSection
                n={stories ? 5 : 4}
                stories={stories}
                pace={paceOf(settings)}
                onPatch={patch}
              />
            </div>
          </fieldset>
          <div className="flex flex-wrap items-center justify-end gap-2 border-t border-canvas px-6 py-4">
            <Button size="sm" onClick={close}>
              {t('masstagging.settings.cancel')}
            </Button>
            <Button
              variant="primary"
              size="sm"
              disabled={locked || conflict || !dirty || save.isPending}
              onClick={confirm}
            >
              {t('masstagging.settings.save')}
            </Button>
          </div>
        </>
      )}
    </Modal>
  );
}
