import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { AccountFolder } from '@/shared/api';
import { mutationErrorText } from '@/shared/lib';
import {
  Button,
  ConfirmModal,
  Icon,
  IconButton,
  Input,
  Modal,
  ModalFooter,
  ModalHeader,
} from '@/shared/ui';

import type { FolderActions } from '../model/useFolderActions';

export type FolderDialogState =
  { kind: 'create' } | { kind: 'settings' | 'rename' | 'delete'; folderId: string } | null;

// The backend's own limit (schemas/account_folders.py).
const NAME_MAX = 40;

function isNameTaken(error: unknown): boolean {
  if (typeof error !== 'object' || error === null || !('error' in error)) return false;
  const detail = (error as { error: { message?: unknown } | null }).error;
  return detail?.message === 'folder_name_taken';
}

function FolderNameForm({
  title,
  initial,
  others,
  onSave,
  onClose,
}: {
  title: string;
  initial: string;
  others: readonly AccountFolder[];
  onSave: (name: string) => Promise<unknown>;
  onClose: () => void;
}) {
  const { t, i18n } = useTranslation();
  const [name, setName] = useState(initial);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  // After Modal's own effect, which focuses the dialog box: this one runs later (it
  // belongs to the dialog's parent), so the field ends up with the caret.
  useEffect(() => {
    input.current?.focus();
  }, []);

  const save = () => {
    const trimmed = name.trim();
    const folded = trimmed.toLocaleLowerCase(i18n.language);
    if (!trimmed) {
      setError(t('accounts.folders.nameRequired'));
      return;
    }
    if (others.some((folder) => folder.name.toLocaleLowerCase(i18n.language) === folded)) {
      setError(t('accounts.folders.nameTaken'));
      return;
    }
    setSaving(true);
    onSave(trimmed).then(onClose, (failure: unknown) => {
      setSaving(false);
      setError(isNameTaken(failure) ? t('accounts.folders.nameTaken') : mutationErrorText(failure));
    });
  };

  return (
    <Modal size="form" label={title} onClose={onClose} dirty={name !== initial}>
      {(close) => (
        <>
          <ModalHeader title={title}>
            <IconButton
              size="md"
              tone="neutral"
              className="ml-auto"
              aria-label={t('accounts.folders.close')}
              onClick={close}
            >
              <Icon name="close" size={16} />
            </IconButton>
          </ModalHeader>
          <div className="px-6 py-6">
            <label>
              <span className="mb-2 block type-small">{t('accounts.folders.nameLabel')}</span>
              <Input
                ref={input}
                value={name}
                maxLength={NAME_MAX}
                placeholder={t('accounts.folders.namePlaceholder')}
                invalid={error !== ''}
                onChange={(event) => {
                  setName(event.target.value);
                  setError('');
                }}
                onKeyDown={(event) => {
                  if (event.key !== 'Enter') return;
                  event.preventDefault();
                  save();
                }}
              />
            </label>
            {error ? (
              <p role="alert" className="mt-2 type-small text-danger">
                {error}
              </p>
            ) : null}
          </div>
          <ModalFooter>
            <Button onClick={close}>{t('accounts.folders.cancel')}</Button>
            <Button variant="primary" loading={saving} onClick={save}>
              {t('accounts.folders.save')}
            </Button>
          </ModalFooter>
        </>
      )}
    </Modal>
  );
}

// Create, the settings menu of one folder (rename / delete), the rename form and the
// delete question — one dialog at a time, driven by the page.
export function FolderDialog({
  state,
  onChange,
  folders,
  actions,
}: {
  state: FolderDialogState;
  onChange: (state: FolderDialogState) => void;
  folders: readonly AccountFolder[];
  actions: FolderActions;
}) {
  const { t } = useTranslation();
  if (state === null) return null;
  const close = () => {
    onChange(null);
  };

  if (state.kind === 'create') {
    return (
      <FolderNameForm
        title={t('accounts.folders.create')}
        initial=""
        others={folders}
        onSave={actions.createFolder}
        onClose={close}
      />
    );
  }

  const folder = folders.find((item) => item.folder_id === state.folderId);
  if (!folder) return null;

  if (state.kind === 'rename') {
    return (
      <FolderNameForm
        title={t('accounts.folders.renameTitle')}
        initial={folder.name}
        others={folders.filter((item) => item.folder_id !== folder.folder_id)}
        onSave={(name) => actions.renameFolder(folder.folder_id, name)}
        onClose={close}
      />
    );
  }

  if (state.kind === 'delete') {
    return (
      <ConfirmModal
        title={t('accounts.folders.deleteTitle')}
        body={t('accounts.folders.deleteBody', { name: folder.name })}
        confirmLabel={t('accounts.folders.delete')}
        cancelLabel={t('accounts.folders.cancel')}
        onClose={close}
        onConfirm={() => actions.deleteFolder(folder.folder_id)}
      />
    );
  }

  return (
    <Modal size="form" label={folder.name} onClose={close}>
      <ModalHeader title={folder.name}>
        <IconButton
          size="md"
          tone="neutral"
          className="ml-auto"
          aria-label={t('accounts.folders.close')}
          onClick={close}
        >
          <Icon name="close" size={16} />
        </IconButton>
      </ModalHeader>
      <div className="flex flex-col gap-3 px-6 py-6">
        <Button
          className="justify-start"
          onClick={() => {
            onChange({ kind: 'rename', folderId: folder.folder_id });
          }}
        >
          <Icon name="pencil" size={16} />
          {t('accounts.folders.rename')}
        </Button>
        <Button
          className="justify-start text-danger-deep"
          onClick={() => {
            onChange({ kind: 'delete', folderId: folder.folder_id });
          }}
        >
          <Icon name="trash" size={16} />
          {t('accounts.folders.delete')}
        </Button>
      </div>
    </Modal>
  );
}
