// «Категории»: saved lists of chats, shown under the campaign's own chips. Pressing one
// COPIES its links into the campaign (nothing stays linked); «Сохранить как категорию»
// keeps the campaign's current list for the next one; «Управлять» opens the editor.
import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { createChatCollectionMutation } from '@/entities/chat-broadcast';
import { Badge, Button, InlineChipEditor, useModalDirty } from '@/shared/ui';

import { mergeTargets } from '../../model/collections';

import { ChatCollectionsModal } from './ChatCollectionsModal';
import { useCollections } from './useCollections';

export function ChatCollections({
  targets,
  onChange,
}: {
  targets: string[];
  onChange: (next: string[]) => void;
}) {
  const { t } = useTranslation();
  const { items, refresh } = useCollections();
  const create = useMutation(createChatCollectionMutation());
  const [naming, setNaming] = useState(false);
  const [name, setName] = useState('');
  const [managing, setManaging] = useState(false);
  // A name typed but not saved is input the dialog around cannot see.
  useModalDirty(name.trim() !== '');

  const stopNaming = () => {
    setNaming(false);
    setName('');
  };
  const saveAs = () => {
    // A refusal (the name is taken) keeps the editor open: the toast says why.
    void create
      .mutateAsync({ body: { name: name.trim(), targets } })
      .then(refresh)
      .then(stopNaming)
      .catch(() => undefined);
  };

  return (
    <div className="flex flex-wrap items-center gap-2 border-t border-canvas pt-2">
      <span className="type-small-medium">{t('chatBroadcast.collections.title')}</span>
      {items.length === 0 ? (
        <span className="type-small">{t('chatBroadcast.collections.empty')}</span>
      ) : null}
      {items.map((collection) => {
        const fresh = mergeTargets(targets, collection.targets).length > targets.length;
        return (
          <Button
            key={collection.collection_id}
            size="sm"
            disabled={!fresh}
            title={t(
              fresh ? 'chatBroadcast.collections.addHint' : 'chatBroadcast.collections.addedHint',
            )}
            aria-label={t('chatBroadcast.collections.add', { name: collection.name })}
            onClick={() => {
              onChange(mergeTargets(targets, collection.targets));
            }}
          >
            {collection.name}
            <Badge size="xs">{collection.targets.length}</Badge>
          </Button>
        );
      })}
      <div className="flex-1" />
      {naming ? (
        <InlineChipEditor
          value={name}
          onChange={setName}
          onConfirm={saveAs}
          onCancel={stopNaming}
          disabled={create.isPending}
          placeholder={t('chatBroadcast.collections.namePlaceholder')}
          inputLabel={t('chatBroadcast.collections.name')}
          confirmLabel={t('chatBroadcast.collections.saveConfirm')}
          cancelLabel={t('chatBroadcast.settings.cancel')}
        />
      ) : (
        <Button
          size="sm"
          variant="ghost"
          disabled={targets.length === 0}
          onClick={() => {
            setNaming(true);
          }}
        >
          {t('chatBroadcast.collections.saveAs')}
        </Button>
      )}
      {items.length > 0 ? (
        <Button
          size="sm"
          variant="ghost"
          onClick={() => {
            setManaging(true);
          }}
        >
          {t('chatBroadcast.collections.manage')}
        </Button>
      ) : null}
      {managing ? (
        <ChatCollectionsModal
          items={items}
          refresh={refresh}
          onClose={() => {
            setManaging(false);
          }}
        />
      ) : null}
    </div>
  );
}
