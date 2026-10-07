// "Where to write": a list of links (an account joins what it is not in) or the groups
// the accounts are already in. Pasted links are checked by the server, which also opens a
// folder link to say how many chats it holds.
import { useMutation, useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { AccountAvatar, accountDisplayName } from '@/entities/account';
import {
  chatBroadcastOwnChatsQueryOptions,
  resolveChatBroadcastTargetsMutation,
} from '@/entities/chat-broadcast';
import type { AccountRead, ChatBroadcastResolvedTarget } from '@/shared/api';
import {
  Badge,
  Button,
  ChipAddButton,
  HelpHint,
  Icon,
  IconButton,
  InlineChipEditor,
  Notice,
  SegmentedControl,
  Spinner,
} from '@/shared/ui';

import type { Settings } from '../../model/draft';
import { splitTargets } from '../../model/draft';

import { Eyebrow, Row } from './fields';

type Resolved = Record<string, ChatBroadcastResolvedTarget>;

function guessKind(raw: string): ChatBroadcastResolvedTarget['kind'] {
  if (/addlist\//i.test(raw)) return 'folder';
  if (raw.includes('+') || /joinchat\//i.test(raw)) return 'invite';
  return 'public';
}

function TargetChip({
  raw,
  resolved,
  onRemove,
}: {
  raw: string;
  resolved: ChatBroadcastResolvedTarget | undefined;
  onRemove: () => void;
}) {
  const { t } = useTranslation();
  const kind = resolved?.kind ?? guessKind(raw);
  return (
    <span className="inline-flex items-center gap-2 rounded-full border border-line bg-canvas px-3 py-1 text-body text-content-secondary">
      {raw}
      {kind === 'invite' ? (
        <span title={t('chatBroadcast.settings.chats.inviteHint')}>
          <Badge tone="info">{t('chatBroadcast.settings.chats.invite')}</Badge>
        </span>
      ) : null}
      {kind === 'folder' ? (
        <span title={t('chatBroadcast.settings.chats.folderHint')}>
          <Badge tone="info">
            {resolved?.folder_count === null || resolved?.folder_count === undefined
              ? t('chatBroadcast.settings.chats.folderUnknown')
              : t('chatBroadcast.settings.chats.folder', { count: resolved.folder_count })}
          </Badge>
        </span>
      ) : null}
      <IconButton
        size="sm"
        shape="circle"
        aria-label={t('chatBroadcast.settings.chats.remove', { target: raw })}
        onClick={onRemove}
      >
        <Icon name="close" size={16} />
      </IconButton>
    </span>
  );
}

function ListTargets({
  settings,
  accountIds,
  onPatch,
}: {
  settings: Settings;
  accountIds: string[];
  onPatch: (next: Partial<Settings>) => void;
}) {
  const { t } = useTranslation();
  const resolve = useMutation(resolveChatBroadcastTargetsMutation());
  const [resolved, setResolved] = useState<Resolved>({});
  const [adding, setAdding] = useState(false);
  const [entry, setEntry] = useState('');
  const [invalid, setInvalid] = useState<string[]>([]);
  const reader = accountIds[0];

  // Folders are opened once per dialog (and again when the first account changes):
  // the count is the one thing a chip cannot tell from its own link.
  const folders = settings.targets.filter((raw) => guessKind(raw) === 'folder').join(' ');
  useEffect(() => {
    if (folders === '') return;
    void resolve
      .mutateAsync({ body: { targets: folders.split(' '), account_ids: reader ? [reader] : [] } })
      .then((answer) => {
        setResolved((current) => ({
          ...current,
          ...Object.fromEntries(answer.items.map((item) => [item.raw, item])),
        }));
      })
      .catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- re-read on the inputs only
  }, [folders, reader]);

  const add = () => {
    const pasted = splitTargets(entry).filter((raw) => !settings.targets.includes(raw));
    setAdding(false);
    setEntry('');
    if (pasted.length === 0) return;
    void resolve
      .mutateAsync({ body: { targets: pasted, account_ids: reader ? [reader] : [] } })
      .then((answer) => {
        const good = answer.items.filter((item) => item.error !== 'invalid_target');
        setInvalid(
          answer.items.filter((item) => item.error === 'invalid_target').map((item) => item.raw),
        );
        setResolved((current) => ({
          ...current,
          ...Object.fromEntries(good.map((item) => [item.raw, item])),
        }));
        onPatch({ targets: [...settings.targets, ...good.map((item) => item.raw)] });
      })
      .catch(() => undefined);
  };

  return (
    <div className="flex flex-col gap-2 pt-2">
      <div className="flex flex-wrap items-center gap-2">
        {settings.targets.map((raw) => (
          <TargetChip
            key={raw}
            raw={raw}
            resolved={resolved[raw]}
            onRemove={() => {
              onPatch({ targets: settings.targets.filter((other) => other !== raw) });
            }}
          />
        ))}
        {adding ? (
          <InlineChipEditor
            value={entry}
            onChange={setEntry}
            onConfirm={add}
            onCancel={() => {
              setAdding(false);
              setEntry('');
            }}
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
    </div>
  );
}

function OwnChats({
  settings,
  accountIds,
  fleet,
  onPatch,
}: {
  settings: Settings;
  accountIds: string[];
  fleet: AccountRead[];
  onPatch: (next: Partial<Settings>) => void;
}) {
  const { t } = useTranslation();
  const query = useQuery({
    ...chatBroadcastOwnChatsQueryOptions({ query: { account_ids: accountIds } }),
    enabled: accountIds.length > 0,
  });
  const byId = new Map(fleet.map((account) => [account.account_id, account]));
  if (accountIds.length === 0) {
    return <div className="pt-2 type-small">{t('chatBroadcast.settings.chats.ownNoAccounts')}</div>;
  }
  if (query.data === undefined) {
    return (
      <div className="flex items-center gap-2 pt-2 type-small">
        <Spinner />
        {t('chatBroadcast.settings.chats.ownLoading')}
      </div>
    );
  }
  const own = query.data;
  const excluded = new Set(settings.own_excluded);
  const unavailable = own.unavailable_account_ids
    .map((id) => (byId.get(id) ? accountDisplayName(byId.get(id) as AccountRead) : id))
    .join(', ');
  return (
    <div className="flex flex-col gap-2 pt-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="type-small">
          {t('chatBroadcast.settings.chats.ownSummary', {
            groups: own.groups.length - own.groups.filter((g) => excluded.has(g.peer_id)).length,
            accounts: accountIds.length,
            channels: own.channels_skipped,
            adminOnly: own.admin_only_skipped,
          })}
        </span>
        <HelpHint
          text={t('chatBroadcast.settings.chats.ownHint')}
          example={t('chatBroadcast.settings.chats.ownExample')}
        />
        <div className="flex-1" />
        <Button
          size="sm"
          variant="ghost"
          onClick={() => {
            void query.refetch();
          }}
        >
          <Icon name="refresh" size={12} />
          {t('chatBroadcast.settings.chats.ownRefresh')}
        </Button>
      </div>
      {unavailable === '' ? null : (
        <Notice tone="warning" bordered={false}>
          {t('chatBroadcast.settings.chats.ownUnavailable', { names: unavailable })}
        </Notice>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {own.groups.map((group) => {
          const off = excluded.has(group.peer_id);
          return (
            <span
              key={group.peer_id}
              className={`inline-flex items-center gap-2 rounded-full border border-line px-3 py-1 text-body ${off ? 'bg-surface-card text-content-subtle line-through' : 'bg-canvas text-content-secondary'}`}
            >
              {group.title}
              <span className="flex items-center">
                {group.account_ids.map((id, index) => {
                  const account = byId.get(id);
                  if (account === undefined) return null;
                  return (
                    <span
                      key={id}
                      title={
                        account.username ? `@${account.username}` : accountDisplayName(account)
                      }
                      className={`rounded-full border-2 border-surface-card ${index === 0 ? '' : '-ml-2'}`}
                    >
                      <AccountAvatar
                        account={account}
                        className="size-chip rounded-full"
                        fallbackClassName="bg-info-tint text-small font-medium text-info-strong"
                      />
                    </span>
                  );
                })}
              </span>
              <IconButton
                size="sm"
                shape="circle"
                aria-label={
                  off
                    ? t('chatBroadcast.settings.chats.include', { title: group.title })
                    : t('chatBroadcast.settings.chats.exclude', { title: group.title })
                }
                onClick={() => {
                  onPatch({
                    own_excluded: off
                      ? settings.own_excluded.filter((peer) => peer !== group.peer_id)
                      : [...settings.own_excluded, group.peer_id],
                  });
                }}
              >
                <Icon name={off ? 'plus' : 'close'} size={16} />
              </IconButton>
            </span>
          );
        })}
      </div>
    </div>
  );
}

export function ChatsSection({
  settings,
  accountIds,
  fleet,
  onPatch,
}: {
  settings: Settings;
  accountIds: string[];
  fleet: AccountRead[];
  onPatch: (next: Partial<Settings>) => void;
}) {
  const { t } = useTranslation();
  const own = settings.target_mode === 'own';
  return (
    <section>
      <Eyebrow
        title={t('chatBroadcast.settings.chats.title')}
        caption={
          own
            ? t('chatBroadcast.settings.chats.ownCaption')
            : t('chatBroadcast.settings.chats.inList', { count: settings.targets.length })
        }
        hint={
          own ? undefined : (
            <HelpHint
              text={t('chatBroadcast.settings.chats.pasteHint')}
              example={t('chatBroadcast.settings.chats.pasteExample')}
            />
          )
        }
      />
      <Row
        first
        label={t('chatBroadcast.settings.chats.mode')}
        hint={t('chatBroadcast.settings.chats.modeHint')}
        example={t('chatBroadcast.settings.chats.modeExample')}
      >
        <SegmentedControl
          variant="pill"
          value={settings.target_mode}
          ariaLabel={t('chatBroadcast.settings.chats.mode')}
          options={[
            { value: 'list', label: t('chatBroadcast.settings.chats.list') },
            { value: 'own', label: t('chatBroadcast.settings.chats.own') },
          ]}
          onChange={(value) => {
            onPatch({ target_mode: value });
          }}
        />
      </Row>
      {own ? (
        <OwnChats settings={settings} accountIds={accountIds} fleet={fleet} onPatch={onPatch} />
      ) : (
        <ListTargets settings={settings} accountIds={accountIds} onPatch={onPatch} />
      )}
    </section>
  );
}
