// The board's gear: the three pauses, the only settings a running campaign takes. The run
// reads them live — a new value applies from the next pause, and a rest already under way
// is drawn again from the moment it began.
import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { saveChatBroadcastPaceMutation } from '@/entities/chat-broadcast';
import type { ChatBroadcastPace, ChatBroadcastSettingsRead } from '@/shared/api';
import { Button, Modal, ModalFooter, ModalHeader, Notice } from '@/shared/ui';

import { conflictCode } from '../model/errors';

import { RangeField, Row } from './settings/fields';

const MAX_SECONDS = 3600;
const MAX_MINUTES = 1440;

function paceOf(read: ChatBroadcastSettingsRead): ChatBroadcastPace {
  const { between_messages, between_chats, rest_minutes } = read.settings;
  return {
    between_messages: between_messages ?? { min: 3, max: 8 },
    between_chats: between_chats ?? { min: 30, max: 90 },
    rest_minutes: rest_minutes ?? { min: 60, max: 120 },
  };
}

const RANGES = ['between_messages', 'between_chats', 'rest_minutes'] as const;

function samePace(a: ChatBroadcastPace, b: ChatBroadcastPace): boolean {
  return RANGES.every((key) => a[key].min === b[key].min && a[key].max === b[key].max);
}

export function PaceDialog({
  read,
  onClose,
  onSaved,
}: {
  read: ChatBroadcastSettingsRead;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { t } = useTranslation();
  // What the dialog opened with: a board refetch while it is open must not make the
  // operator's edits look saved, or unsaved ones look clean.
  const [baseline] = useState(() => paceOf(read));
  const [pace, setPace] = useState(baseline);
  const dirty = !samePace(pace, baseline);
  const [conflict, setConflict] = useState(false);
  const save = useMutation(saveChatBroadcastPaceMutation());
  const sec = t('chatBroadcast.settings.pace.sec');
  const loop = read.settings.loop ?? true;

  // A refused save leaves Save on: the server re-reads the campaign on every call, so a
  // retry meets the fresh one. The notice stays until the next attempt answers.
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
    <Modal onClose={onClose} dirty={dirty} size="form" label={t('chatBroadcast.paceDialog.title')}>
      {(close) => (
        <>
          <ModalHeader
            title={t('chatBroadcast.paceDialog.title')}
            subtitle={t('chatBroadcast.paceDialog.subtitle')}
          />
          {conflict ? (
            <div className="px-6 pt-4" role="alert">
              <Notice tone="danger">{t('chatBroadcast.paceDialog.conflict')}</Notice>
            </div>
          ) : null}
          <fieldset disabled={save.isPending} className="m-0 min-w-0 border-0 px-6 py-4">
            <Row
              first
              label={t('chatBroadcast.settings.pace.betweenMessages')}
              hint={t('chatBroadcast.settings.pace.betweenMessagesHint')}
              example={t('chatBroadcast.settings.pace.betweenMessagesExample')}
            >
              <RangeField
                value={pace.between_messages}
                unit={sec}
                max={MAX_SECONDS}
                label={t('chatBroadcast.settings.pace.betweenMessages')}
                onChange={(value) => {
                  setPace((current) => ({ ...current, between_messages: value }));
                }}
              />
            </Row>
            <Row
              label={t('chatBroadcast.settings.pace.betweenChats')}
              hint={t('chatBroadcast.settings.pace.betweenChatsHint')}
              example={t('chatBroadcast.settings.pace.betweenChatsExample')}
            >
              <RangeField
                value={pace.between_chats}
                unit={sec}
                max={MAX_SECONDS}
                label={t('chatBroadcast.settings.pace.betweenChats')}
                onChange={(value) => {
                  setPace((current) => ({ ...current, between_chats: value }));
                }}
              />
            </Row>
            {loop ? (
              <Row
                label={t('chatBroadcast.settings.pace.rest')}
                hint={t('chatBroadcast.paceDialog.restHint')}
              >
                <RangeField
                  value={pace.rest_minutes}
                  unit={t('chatBroadcast.settings.pace.min')}
                  max={MAX_MINUTES}
                  label={t('chatBroadcast.settings.pace.rest')}
                  onChange={(value) => {
                    setPace((current) => ({ ...current, rest_minutes: value }));
                  }}
                />
              </Row>
            ) : null}
          </fieldset>
          <ModalFooter>
            <Button size="sm" onClick={close}>
              {t('chatBroadcast.paceDialog.cancel')}
            </Button>
            <Button variant="primary" size="sm" disabled={save.isPending} onClick={confirm}>
              {t('chatBroadcast.paceDialog.save')}
            </Button>
          </ModalFooter>
        </>
      )}
    </Modal>
  );
}
