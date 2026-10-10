// The category editor: rename, change the chats, delete. Each category is saved on its
// own (the server replaces the whole list), and a campaign that already took its chats
// keeps them — a category is copied, not linked.
import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  deleteChatCollectionMutation,
  resolveChatBroadcastTargetsMutation,
  saveChatCollectionMutation,
} from '@/entities/chat-broadcast';
import type { ChatCollection } from '@/shared/api';
import {
  Button,
  Card,
  ChipAddButton,
  ConfirmModal,
  Icon,
  IconButton,
  InlineChipEditor,
  Input,
  Modal,
  ModalFooter,
  ModalHeader,
  Notice,
  Spinner,
  useModalDirty,
} from '@/shared/ui';

import { mergeTargets, sameTargets } from '../../model/collections';
import { splitTargets } from '../../model/draft';

import { TargetChip } from './TargetChip';

function CollectionEditor({
  collection,
  refresh,
}: {
  collection: ChatCollection;
  refresh: () => Promise<void>;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState(collection.name);
  const [targets, setTargets] = useState(collection.targets);
  const [adding, setAdding] = useState(false);
  const [entry, setEntry] = useState('');
  const [invalid, setInvalid] = useState<string[]>([]);
  const [deleting, setDeleting] = useState(false);
  const resolve = useMutation(resolveChatBroadcastTargetsMutation());
  const save = useMutation(saveChatCollectionMutation());
  const remove = useMutation(deleteChatCollectionMutation());
  const dirty = name.trim() !== collection.name || !sameTargets(targets, collection.targets);
  useModalDirty(dirty || entry.trim() !== '');

  const stopAdding = () => {
    setAdding(false);
    setEntry('');
  };
  const add = () => {
    const pasted = splitTargets(entry);
    stopAdding();
    if (pasted.length === 0) return;
    void resolve
      .mutateAsync({ body: { targets: pasted, account_ids: [] } })
      .then((answer) => {
        const refused = answer.items.filter((item) => item.error === 'invalid_target');
        setInvalid(refused.map((item) => item.raw));
        const good = answer.items.filter((item) => item.error !== 'invalid_target');
        setTargets((current) =>
          mergeTargets(
            current,
            good.map((item) => item.raw),
          ),
        );
      })
      .catch(() => undefined);
  };
  const apply = () => {
    // A refusal (the name is taken) leaves the edits in place: the toast says why.
    void save
      .mutateAsync({
        path: { collection_id: collection.collection_id },
        body: { name: name.trim(), targets },
      })
      .then(refresh)
      .catch(() => undefined);
  };

  return (
    <Card className="flex flex-col gap-3 px-4 py-4">
      <div className="flex items-center gap-2">
        <Input
          size="sm"
          className="min-w-0 flex-1"
          maxLength={64}
          value={name}
          aria-label={t('chatBroadcast.collections.name')}
          onChange={(event) => {
            setName(event.target.value);
          }}
        />
        {dirty ? (
          <span className="shrink-0 type-small">{t('chatBroadcast.settings.unsaved')}</span>
        ) : null}
        <Button
          size="sm"
          variant="primary"
          loading={save.isPending}
          disabled={!dirty || name.trim() === ''}
          aria-label={t('chatBroadcast.collections.saveOne', { name: collection.name })}
          onClick={apply}
        >
          {t('chatBroadcast.settings.save')}
        </Button>
        <IconButton
          size="sm"
          tone="danger"
          aria-label={t('chatBroadcast.collections.remove', { name: collection.name })}
          onClick={() => {
            setDeleting(true);
          }}
        >
          <Icon name="trash" size={16} />
        </IconButton>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {targets.length === 0 ? (
          <span className="type-small">{t('chatBroadcast.collections.noChats')}</span>
        ) : null}
        {targets.map((raw) => (
          <TargetChip
            key={raw}
            raw={raw}
            resolved={undefined}
            onRemove={() => {
              setTargets((current) => current.filter((other) => other !== raw));
            }}
          />
        ))}
        {adding ? (
          <InlineChipEditor
            value={entry}
            onChange={setEntry}
            onConfirm={add}
            onCancel={stopAdding}
            placeholder={t('chatBroadcast.settings.chats.placeholder')}
            inputLabel={t('chatBroadcast.settings.chats.addLabel')}
            confirmLabel={t('chatBroadcast.settings.chats.addConfirm')}
          />
        ) : (
          <ChipAddButton
            onClick={() => {
              setAdding(true);
            }}
          >
            {t('chatBroadcast.settings.chats.add')}
          </ChipAddButton>
        )}
        {resolve.isPending ? <Spinner /> : null}
      </div>
      {invalid.length > 0 ? (
        <Notice tone="warning" bordered={false}>
          {t('chatBroadcast.settings.chats.invalid', { targets: invalid.join(', ') })}
        </Notice>
      ) : null}
      {deleting ? (
        <ConfirmModal
          title={t('chatBroadcast.collections.removeTitle', { name: collection.name })}
          body={t('chatBroadcast.collections.removeBody')}
          confirmLabel={t('chatBroadcast.collections.removeConfirm')}
          cancelLabel={t('chatBroadcast.settings.cancel')}
          onClose={() => {
            setDeleting(false);
          }}
          onConfirm={() =>
            remove.mutateAsync({ path: { collection_id: collection.collection_id } }).then(refresh)
          }
        />
      ) : null}
    </Card>
  );
}

export function ChatCollectionsModal({
  items,
  refresh,
  onClose,
}: {
  items: ChatCollection[];
  refresh: () => Promise<void>;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  return (
    <Modal onClose={onClose} size="panel" label={t('chatBroadcast.collections.manageTitle')}>
      {(close) => (
        <>
          <ModalHeader
            title={t('chatBroadcast.collections.manageTitle')}
            subtitle={t('chatBroadcast.collections.manageHint')}
          />
          <div className="flex flex-col gap-3 px-6 py-6">
            {items.length === 0 ? (
              <div className="type-small">{t('chatBroadcast.collections.empty')}</div>
            ) : null}
            {items.map((collection) => (
              <CollectionEditor
                key={collection.collection_id}
                collection={collection}
                refresh={refresh}
              />
            ))}
          </div>
          <ModalFooter>
            <Button size="sm" onClick={close}>
              {t('chatBroadcast.collections.close')}
            </Button>
          </ModalFooter>
        </>
      )}
    </Modal>
  );
}
