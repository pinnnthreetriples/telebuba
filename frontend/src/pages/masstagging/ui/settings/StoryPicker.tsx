// «Выбрать из сторис аккаунта»: the stories an account of the roster already has, taken
// into the campaign's media pool. A story id means something only to the account that owns
// it, while the pool is shared by the whole roster, so the server copies the story's file
// — what lands in the pool is a file like any upload.
import { useMutation, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { accountDisplayName, accountProfileSnapshotQueryOptions } from '@/entities/account';
import { importMasstaggingStoryMediaMutation } from '@/entities/masstagging';
import type { AccountRead, MasstaggingMediaRead } from '@/shared/api';
import {
  Button,
  CloseButton,
  EmptyState,
  Icon,
  Modal,
  ModalFooter,
  Select,
  Spinner,
} from '@/shared/ui';

const TILE_RATIO = '9 / 16';

function Stories({
  accountId,
  chosen,
  busy,
  onToggle,
}: {
  accountId: string;
  chosen: number[];
  busy: boolean;
  onToggle: (storyId: number) => void;
}) {
  const { t } = useTranslation();
  const snapshot = useQuery(
    accountProfileSnapshotQueryOptions({ path: { account_id: accountId } }),
  );
  if (snapshot.isPending) {
    return (
      <div className="flex justify-center py-16">
        <Spinner size="lg" />
      </div>
    );
  }
  // A transport failure and a Telegram refusal (a 200 carrying `error`) both read as
  // «could not look», never as «no stories».
  if (snapshot.isError || snapshot.data.error != null) {
    return (
      <div className="flex flex-col items-center gap-3 px-6 py-16 text-center">
        <p role="alert" className="type-body text-danger">
          {t('masstagging.settings.story.picker.error')}
        </p>
        <Button
          onClick={() => {
            void snapshot.refetch();
          }}
        >
          {t('masstagging.settings.story.picker.retry')}
        </Button>
      </div>
    );
  }
  const stories = snapshot.data.stories ?? [];
  if (stories.length === 0) {
    return (
      <EmptyState size="xl" className="px-6">
        {t('masstagging.settings.story.picker.empty')}
      </EmptyState>
    );
  }
  return (
    <ul className="m-0 grid list-none grid-cols-3 gap-3 p-6">
      {stories.map((story) => {
        const on = chosen.includes(story.story_id);
        return (
          <li key={story.story_id}>
            <button
              type="button"
              role="checkbox"
              aria-checked={on}
              disabled={busy}
              aria-label={t('masstagging.settings.story.picker.story', { id: story.story_id })}
              onClick={() => {
                onToggle(story.story_id);
              }}
              className={`relative flex w-full flex-col justify-end overflow-hidden rounded-md border bg-canvas bg-cover bg-center p-2 text-left disabled:opacity-60 ${on ? 'border-action-primary' : 'border-line'}`}
              style={{
                aspectRatio: TILE_RATIO,
                backgroundImage: story.thumb_url == null ? undefined : `url(${story.thumb_url})`,
              }}
            >
              <span
                className={`absolute right-2 top-2 flex size-glyph items-center justify-center rounded-sm border ${on ? 'border-action-primary bg-action-primary' : 'border-line bg-surface-card'}`}
              >
                {on && <Icon name="check" size={14} className="stroke-on-fill" />}
              </span>
              {story.kind === 'video' || story.is_pinned ? (
                <span className="absolute left-2 top-2 flex items-center gap-1 rounded-full bg-black/55 px-2 py-1 text-on-fill">
                  {story.kind === 'video' ? <Icon name="video" size={14} /> : null}
                  {story.is_pinned ? <Icon name="pin" size={14} /> : null}
                </span>
              ) : null}
              {story.caption == null || story.caption === '' ? null : (
                <span className="line-clamp-2 rounded-sm bg-black/55 px-2 py-1 text-small text-on-fill">
                  {story.caption}
                </span>
              )}
            </button>
          </li>
        );
      })}
    </ul>
  );
}

export function StoryPicker({
  campaignId,
  accountIds,
  fleet,
  onPicked,
  onClose,
}: {
  campaignId: string;
  accountIds: string[];
  fleet: AccountRead[];
  onPicked: (media: MasstaggingMediaRead) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [accountId, setAccountId] = useState(accountIds[0] ?? '');
  const [chosen, setChosen] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);
  const take = useMutation(importMasstaggingStoryMediaMutation());
  const byId = new Map(fleet.map((account) => [account.account_id, account]));

  // One by one: the account's reads queue behind its lock anyway, and a flooded account
  // is not helped by a burst. A story that did not come stays ticked for another go (the
  // toast already said why); when every one came, the dialog has nothing left to do.
  const add = async () => {
    setBusy(true);
    let failed = false;
    for (const storyId of chosen) {
      try {
        const stored = await take.mutateAsync({
          path: { campaign_id: campaignId },
          body: { account_id: accountId, story_id: storyId },
        });
        onPicked(stored);
        setChosen((current) => current.filter((id) => id !== storyId));
      } catch {
        failed = true;
      }
    }
    setBusy(false);
    if (!failed) onClose();
  };

  return (
    <Modal
      onClose={onClose}
      locked={busy}
      dirty={chosen.length > 0}
      size="panel"
      label={t('masstagging.settings.story.picker.title')}
    >
      {(close) => (
        <div className="flex max-h-dialog flex-col overflow-hidden">
          <div className="flex items-center gap-4 border-b border-canvas px-6 py-6">
            <div className="min-w-0 flex-1">
              <h2 className="truncate type-h2">{t('masstagging.settings.story.picker.title')}</h2>
              <div className="type-body text-content-subtle">
                {t('masstagging.settings.story.picker.hint')}
              </div>
            </div>
            <CloseButton onClick={close} aria-label={t('masstagging.settings.cancel')} />
          </div>
          <div className="flex items-center gap-3 border-b border-canvas px-6 py-4">
            <Select
              value={accountId}
              ariaLabel={t('masstagging.settings.story.picker.account')}
              disabled={busy}
              options={accountIds.map((id) => {
                const account = byId.get(id);
                return {
                  value: id,
                  label: account === undefined ? id : accountDisplayName(account),
                };
              })}
              onChange={(next) => {
                setAccountId(next);
                // A story id belongs to one account; ticks do not travel to another.
                setChosen([]);
              }}
            />
          </div>
          <div className="tb-scroll flex-1 overflow-y-auto">
            <Stories
              key={accountId}
              accountId={accountId}
              chosen={chosen}
              busy={busy}
              onToggle={(storyId) => {
                setChosen((current) =>
                  current.includes(storyId)
                    ? current.filter((id) => id !== storyId)
                    : [...current, storyId],
                );
              }}
            />
          </div>
          <ModalFooter>
            <Button onClick={close} disabled={busy}>
              {t('masstagging.settings.cancel')}
            </Button>
            <Button
              variant="primary"
              loading={busy}
              disabled={chosen.length === 0}
              onClick={() => {
                void add();
              }}
            >
              {t('masstagging.settings.story.picker.add', { count: chosen.length })}
            </Button>
          </ModalFooter>
        </div>
      )}
    </Modal>
  );
}
