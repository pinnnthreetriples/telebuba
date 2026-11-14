// "Добавить чаты": a box for a whole pasted list. A one-line input could not take one —
// the browser drops the line breaks of a pasted column and glues `@a`, `@b` into `@a@b`.
// The list is sorted while it is typed, so the operator sees what will be added, what is
// a duplicate (dropped: the server would merge it into one chat anyway) and what is not a
// link, before pressing the button.
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Button, Modal, ModalFooter, ModalHeader, Notice, Textarea } from '@/shared/ui';

import { analyzePaste } from '../model/parsePaste';

const SHOWN = 10;

export function BulkChatPasteModal({
  current,
  allowFolders,
  onAdd,
  onClose,
}: {
  /** The list the chats go into: a chat already there is reported, not added. */
  current: readonly string[];
  /** Whether a `t.me/addlist/…` folder is a target here. */
  allowFolders: boolean;
  onAdd: (targets: string[]) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [text, setText] = useState('');
  const result = analyzePaste(text, current, { allowFolders });
  const count = result.fresh.length;

  const list = (targets: string[]) => {
    const shown = targets.slice(0, SHOWN).join(', ');
    return targets.length > SHOWN
      ? t('bulkChatPaste.more', { shown, count: targets.length - SHOWN })
      : shown;
  };

  return (
    <Modal
      onClose={onClose}
      size="form"
      label={t('bulkChatPaste.title')}
      dirty={text.trim() !== ''}
    >
      {(close) => (
        <>
          <ModalHeader title={t('bulkChatPaste.title')} subtitle={t('bulkChatPaste.hint')} />
          <div className="flex flex-col gap-3 px-6 py-6">
            <span className="type-small">
              {allowFolders ? t('bulkChatPaste.formatsFolders') : t('bulkChatPaste.formats')}
            </span>
            <Textarea
              autoFocus
              value={text}
              placeholder={t('bulkChatPaste.placeholder')}
              aria-label={t('bulkChatPaste.inputLabel')}
              onChange={(event) => {
                setText(event.target.value);
              }}
            />
            {text.trim() === '' ? null : (
              <>
                <span className="type-body-medium">{t('bulkChatPaste.fresh', { count })}</span>
                {result.existing.length > 0 ? (
                  <Notice tone="info" bordered={false}>
                    {t('bulkChatPaste.existing', {
                      count: result.existing.length,
                      targets: list(result.existing),
                    })}
                  </Notice>
                ) : null}
                {result.repeated.length > 0 ? (
                  <Notice tone="info" bordered={false}>
                    {t('bulkChatPaste.repeated', {
                      count: result.repeated.length,
                      targets: list(result.repeated),
                    })}
                  </Notice>
                ) : null}
                {result.invalid.length > 0 ? (
                  <Notice tone="warning" bordered={false}>
                    {t('bulkChatPaste.invalid', {
                      count: result.invalid.length,
                      targets: list(result.invalid),
                    })}
                  </Notice>
                ) : null}
              </>
            )}
          </div>
          <ModalFooter>
            <Button size="sm" onClick={close}>
              {t('bulkChatPaste.cancel')}
            </Button>
            <Button
              size="sm"
              variant="primary"
              disabled={count === 0}
              onClick={() => {
                onAdd(result.fresh);
                onClose();
              }}
            >
              {count === 0 ? t('bulkChatPaste.nothing') : t('bulkChatPaste.confirm', { count })}
            </Button>
          </ModalFooter>
        </>
      )}
    </Modal>
  );
}
