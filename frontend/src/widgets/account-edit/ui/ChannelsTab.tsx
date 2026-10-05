import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { accountChannelsQueryOptions, deleteAccountChannelMutation } from '@/entities/account';
import type { ChannelView } from '@/shared/api';
import { Button, ConfirmModal, Icon, IconButton, Spinner } from '@/shared/ui';

import { channelErrorText } from './_channelsShared';
import { RetryNotice } from './RetryNotice';
import { ChannelCreateModal } from './ChannelCreateModal';
import { ChannelEditModal } from './ChannelEditModal';
import { DashedEmptyAction } from './_shared';

// The profile modal's channels tab: the account's own channels — list, create,
// edit (opens the channel editor with the posts panel) and confirmed delete.
// Channels have their own queries; the tab does not participate in the
// profile-snapshot busy scrim.
export function ChannelsTab({ accountId }: { accountId: string }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const listOpts = accountChannelsQueryOptions({ path: { account_id: accountId } });
  const channels = useQuery(listOpts);
  const deleteChannel = useMutation(deleteAccountChannelMutation());
  const [createOpen, setCreateOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<ChannelView | null>(null);

  const invalidate = () => queryClient.invalidateQueries({ queryKey: listOpts.queryKey });
  const items = channels.data?.items ?? [];

  return (
    <div>
      <div className="mb-3 type-body text-content-subtle">{t('accounts.channel.hint')}</div>

      {channels.isPending && (
        <div
          role="status"
          aria-label={t('accounts.channel.loading')}
          className="flex justify-center py-6"
        >
          <Spinner size="md" />
        </div>
      )}

      {/* Без `mb-lg` у уведомления: на ошибке `isSuccess` ложно и список пуст, то есть под
          ним не стоит ничего. */}
      {channels.isError && (
        <RetryNotice
          message={channelErrorText(channels.error, t, t('accounts.channel.loadError'))}
          label={t('accounts.channel.retry')}
          onRetry={() => {
            void channels.refetch();
          }}
        />
      )}

      {items.length > 0 && (
        <div className="flex flex-col gap-2">
          {items.map((channel) => (
            <div
              key={channel.channel_id}
              className="flex items-center gap-4 rounded-md border border-line px-4 py-3"
            >
              <div className="min-w-0 flex-1">
                <div className="truncate type-h3">{channel.title}</div>
                <div className="mt-1 flex items-center gap-2 type-small">
                  <span
                    className={`rounded-sm px-1 py-px font-medium ${
                      channel.username != null
                        ? 'bg-info-tint text-info-strong'
                        : 'bg-canvas text-content-muted'
                    }`}
                  >
                    {channel.username != null
                      ? t('accounts.channel.publicBadge')
                      : t('accounts.channel.privateBadge')}
                  </span>
                  {channel.username != null && (
                    <span className="truncate">@{channel.username}</span>
                  )}
                  {channel.participants_count != null && (
                    <span>
                      {t('accounts.channel.participants', { n: channel.participants_count })}
                    </span>
                  )}
                </div>
              </div>
              <Button
                size="xs"
                className="rounded-full hover:border-info-line hover:text-action-primary"
                onClick={() => {
                  setEditingId(channel.channel_id);
                }}
              >
                {t('accounts.channel.edit')}
              </Button>
              <IconButton
                size="sm"
                shape="circle"
                onClick={() => {
                  setConfirmDelete(channel);
                }}
                aria-label={t('accounts.channel.delete')}
              >
                <Icon name="close" size={16} />
              </IconButton>
            </div>
          ))}
        </div>
      )}

      {channels.isSuccess && (
        <div className={items.length > 0 ? 'mt-3' : undefined}>
          <DashedEmptyAction
            idleLabel={items.length === 0 ? t('accounts.channel.empty') : undefined}
            actionLabel={t('accounts.channel.create')}
            onClick={() => {
              setCreateOpen(true);
            }}
          />
        </div>
      )}

      {createOpen && (
        <ChannelCreateModal
          accountId={accountId}
          onClose={() => {
            setCreateOpen(false);
          }}
          onCreated={(channelId) => {
            // Straight into the editor for the fresh channel (avatar + first
            // post are usually the next step).
            setCreateOpen(false);
            if (channelId !== null) setEditingId(channelId);
          }}
        />
      )}
      {editingId !== null && (
        <ChannelEditModal
          accountId={accountId}
          channelId={editingId}
          onClose={() => {
            setEditingId(null);
            // The editor may have renamed the channel or changed its avatar.
            void invalidate();
          }}
        />
      )}
      {confirmDelete ? (
        <ConfirmModal
          title={t('accounts.channel.deleteTitle')}
          body={t('accounts.channel.deleteBody')}
          confirmLabel={t('accounts.channel.deleteConfirm')}
          cancelLabel={t('accounts.channel.cancel')}
          onClose={() => {
            setConfirmDelete(null);
          }}
          onConfirm={() =>
            deleteChannel
              .mutateAsync({
                path: { account_id: accountId, channel_id: confirmDelete.channel_id },
              })
              // finally, not then: even a failed delete may have removed the
              // channel — re-pull either way; the rejection still propagates
              // so the dialog stays open (global toast reports it).
              .finally(invalidate)
          }
        />
      ) : null}
    </div>
  );
}
