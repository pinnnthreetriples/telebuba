// The settings dialog: the neuroshilling campaign shell (header with the name and the
// "not saved" pill, sections, a footer with Save). Save does not close it — it opens the
// check window, whose Confirm writes the draft on the version the dialog read. Any other
// exit with an edited draft — the veil, Escape, «Отмена» — asks first (Modal's `dirty`).
import { useMutation, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  chatBroadcastOwnChatsQueryOptions,
  saveChatBroadcastSettingsMutation,
} from '@/entities/chat-broadcast';
import type { AccountRead, ChatBroadcastSettingsRead } from '@/shared/api';
import { Button, Modal, Notice } from '@/shared/ui';

import type { Draft, MessageDraft, Settings } from '../../model/draft';
import { bodyOf, draftOf, sameDraft } from '../../model/draft';
import { conflictCode } from '../../model/errors';

import { AccountsSection } from './AccountsSection';
import { ChatsSection } from './ChatsSection';
import { MessagesSection } from './MessagesSection';
import { PaceSection } from './PaceSection';
import { Preview } from './Preview';

export function SettingsDialog({
  read,
  fleet,
  onClose,
  onSaved,
}: {
  read: ChatBroadcastSettingsRead;
  fleet: AccountRead[];
  onClose: () => void;
  onSaved: (saved: ChatBroadcastSettingsRead) => void;
}) {
  const { t } = useTranslation();
  const [baseline] = useState<Draft>(() => draftOf(read));
  // The version the draft was built from: a refetch while the dialog is open must not
  // move the optimistic lock under the operator's edits.
  const [stamp] = useState(read.updated_at);
  const [draft, setDraft] = useState<Draft>(baseline);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [conflict, setConflict] = useState(false);
  const save = useMutation(saveChatBroadcastSettingsMutation());
  const running = read.status === 'running' || read.status === 'stopping';
  const dirty = !sameDraft(draft, baseline);
  const own = draft.settings.target_mode === 'own';
  const ownChats = useQuery({
    ...chatBroadcastOwnChatsQueryOptions({ query: { account_ids: draft.accountIds } }),
    enabled: own && draft.accountIds.length > 0,
  });
  const ownTitles = (ownChats.data?.groups ?? [])
    .filter((group) => !draft.settings.own_excluded.includes(group.peer_id))
    .map((group) => group.title);
  const byId = new Map(fleet.map((account) => [account.account_id, account]));
  const chosen = draft.accountIds
    .map((id) => byId.get(id))
    .filter((account): account is AccountRead => account !== undefined);

  const patch = (next: Partial<Settings>) => {
    setDraft((current) => ({ ...current, settings: { ...current.settings, ...next } }));
  };
  const setMessages = (messages: MessageDraft[]) => {
    setDraft((current) => ({ ...current, messages }));
  };

  const confirm = () => {
    void save
      .mutateAsync({
        path: { campaign_id: read.campaign_id },
        body: bodyOf(draft, stamp),
      })
      .then((saved) => {
        onSaved(saved);
        onClose();
      })
      .catch((error: unknown) => {
        setPreviewOpen(false);
        if (conflictCode(error) === 'campaign_changed') setConflict(true);
      });
  };

  return (
    <Modal
      onClose={onClose}
      dirty={dirty}
      locked={save.isPending}
      size="table"
      label={t('chatBroadcast.settings.subtitle')}
    >
      {(close) => (
        <>
          <div className="flex items-center gap-3 border-b border-canvas px-6 pb-4 pt-6">
            <div className="min-w-0">
              <div className="truncate type-h2">{draft.name}</div>
              <div className="mt-1 type-small">{t('chatBroadcast.settings.subtitle')}</div>
            </div>
            <div className="flex-1" />
            {dirty ? (
              <span className="shrink-0 rounded-full bg-warning-tint px-3 py-1 text-small font-medium text-warning-deep">
                {t('chatBroadcast.settings.unsaved')}
              </span>
            ) : null}
          </div>
          {running || conflict ? (
            <div className="px-6 pt-4" role="alert">
              <Notice tone={conflict ? 'danger' : 'info'}>
                {conflict
                  ? t('chatBroadcast.settings.conflict')
                  : t('chatBroadcast.settings.running')}
              </Notice>
            </div>
          ) : null}
          <fieldset disabled={running || save.isPending} className="m-0 min-w-0 border-0 p-0">
            <div className="flex min-w-0 flex-col gap-6 px-6 py-6">
              <AccountsSection
                ids={draft.accountIds}
                fleet={fleet}
                onChange={(accountIds) => {
                  setDraft((current) => ({ ...current, accountIds }));
                }}
              />
              <ChatsSection
                settings={draft.settings}
                accountIds={draft.accountIds}
                fleet={fleet}
                onPatch={patch}
              />
              <MessagesSection draft={draft} onPatch={patch} onMessages={setMessages} />
              <PaceSection settings={draft.settings} onPatch={patch} />
            </div>
          </fieldset>
          <div className="flex flex-wrap items-center justify-end gap-2 border-t border-canvas px-6 py-4">
            <Button size="sm" onClick={close}>
              {t('chatBroadcast.settings.cancel')}
            </Button>
            <Button
              variant="primary"
              size="sm"
              disabled={running || conflict}
              onClick={() => {
                setPreviewOpen(true);
              }}
            >
              {t('chatBroadcast.settings.save')}
            </Button>
          </div>
          {previewOpen ? (
            <Preview
              draft={draft}
              accounts={chosen}
              ownTitles={ownTitles}
              saving={save.isPending}
              onEdit={() => {
                setPreviewOpen(false);
              }}
              onConfirm={confirm}
            />
          ) : null}
        </>
      )}
    </Modal>
  );
}
