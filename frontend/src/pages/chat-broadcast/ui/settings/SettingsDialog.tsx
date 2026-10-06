// The settings dialog: the neuroshilling campaign shell (header with the name and the
// "not saved" pill, sections, a footer with Save). Save does not close it — it opens the
// check window, whose Confirm writes the draft on the version the dialog read.
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
        body: bodyOf(draft, read.updated_at),
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
    <Modal onClose={onClose} size="table" label={t('chatBroadcast.settings.subtitle')}>
      <div className="flex items-center gap-md border-b border-line-row px-2xl pb-lg pt-xl">
        <div className="min-w-0">
          <div className="truncate type-dialog-title">{draft.name}</div>
          <div className="mt-hair type-caption">{t('chatBroadcast.settings.subtitle')}</div>
        </div>
        <div className="flex-1" />
        {dirty ? (
          <span className="shrink-0 rounded-full bg-warning-tint px-md py-xs text-tiny font-semibold text-warning-deep">
            {t('chatBroadcast.settings.unsaved')}
          </span>
        ) : null}
      </div>
      {running || conflict ? (
        <div className="px-2xl pt-lg" role="alert">
          <Notice tone={conflict ? 'danger' : 'info'}>
            {conflict ? t('chatBroadcast.settings.conflict') : t('chatBroadcast.settings.running')}
          </Notice>
        </div>
      ) : null}
      <fieldset disabled={running || save.isPending} className="m-0 min-w-0 border-0 p-0">
        <div className="flex min-w-0 flex-col gap-2xl px-2xl py-xl">
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
      <div className="flex flex-wrap items-center justify-end gap-sm border-t border-line-row px-2xl py-lg">
        <Button size="sm" onClick={onClose}>
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
    </Modal>
  );
}
