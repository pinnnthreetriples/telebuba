import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { accountChannelsQueryOptions, deleteAccountChannelMutation } from '@/entities/account';
import type { ChannelView } from '@/shared/api';
import { ConfirmModal } from '@/shared/ui';

import { channelErrorText } from './_channelsShared';
import { ChannelCreateModal } from './ChannelCreateModal';
import { ChannelEditModal } from './ChannelEditModal';

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
      <div className="mb-3 text-[12px] text-ink-subtle">{t('accounts.channel.hint')}</div>

      {channels.isPending && (
        <div
          role="status"
          aria-label={t('accounts.channel.loading')}
          className="flex justify-center py-6"
        >
          <span className="tb-spin inline-block h-6 w-6 rounded-full border-2 border-line-input border-t-primary" />
        </div>
      )}

      {channels.isError && (
        <div className="mb-4 flex items-center justify-between gap-3 rounded-[10px] border border-[#f0c9c5] bg-danger-tint px-3 py-[10px] text-[12.5px] text-danger">
          <span>{channelErrorText(channels.error, t, t('accounts.channel.loadError'))}</span>
          <button
            type="button"
            onClick={() => {
              void channels.refetch();
            }}
            className="shrink-0 rounded-full border border-[#f0c9c5] bg-white px-3 py-[4px] text-[12px] font-medium"
          >
            {t('accounts.channel.retry')}
          </button>
        </div>
      )}

      {channels.isSuccess && items.length === 0 && (
        <div className="rounded-[12px] border border-dashed border-line bg-white px-4 py-6 text-center text-[12.5px] text-ink-subtle">
          {t('accounts.channel.empty')}
        </div>
      )}

      {items.length > 0 && (
        <div className="flex flex-col gap-2">
          {items.map((channel) => (
            <div
              key={channel.channel_id}
              className="flex items-center gap-[13px] rounded-[12px] border border-line px-[14px] py-3"
            >
              <div className="min-w-0 flex-1">
                <div className="truncate text-[13.5px] font-semibold">{channel.title}</div>
                <div className="mt-[2px] flex items-center gap-[8px] text-[11.5px] text-ink-subtle">
                  <span
                    className={`rounded-[6px] px-[6px] py-[1px] font-medium ${
                      channel.username != null
                        ? 'bg-[#e8f1ff] text-primary'
                        : 'bg-[#f1efed] text-ink-muted'
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
              <button
                type="button"
                onClick={() => {
                  setEditingId(channel.channel_id);
                }}
                className="shrink-0 rounded-full border border-line-input bg-white px-3 py-[5px] text-[12px] font-medium text-ink hover:border-[#bfd6ff] hover:text-primary"
              >
                {t('accounts.channel.edit')}
              </button>
              <button
                type="button"
                onClick={() => {
                  setConfirmDelete(channel);
                }}
                aria-label={t('accounts.channel.delete')}
                className="h-[28px] w-[28px] shrink-0 rounded-full border border-line bg-white text-[14px] text-ink-subtle"
              >
                ×
              </button>
            </div>
          ))}
        </div>
      )}

      {!channels.isPending && (
        <button
          type="button"
          onClick={() => {
            setCreateOpen(true);
          }}
          className="mt-3 rounded-full border border-line-input bg-white px-4 py-2 text-[13px] font-medium"
        >
          {t('accounts.channel.create')}
        </button>
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
