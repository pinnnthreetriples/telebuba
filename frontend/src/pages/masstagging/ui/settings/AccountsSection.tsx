// Section 2: the chat broadcast's strip — "+" opens the fleet picker, the chosen accounts
// sit as avatars, the cross on hover takes one out. A spam-blocked account stays on the
// list, faded: it never works, because the people it tags get no push.
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { AccountAvatar, accountDisplayName, BulkAccountPicker } from '@/entities/account';
import type { AccountRead } from '@/shared/api';
import { Badge, Icon, IconButton } from '@/shared/ui';

import { isSpamBlocked } from '../../model/accounts';

import { Section } from './fields';

export function AccountsSection({
  ids,
  fleet,
  stories,
  onChange,
}: {
  ids: string[];
  fleet: AccountRead[];
  stories: boolean;
  onChange: (ids: string[]) => void;
}) {
  const { t } = useTranslation();
  const [pickerOpen, setPickerOpen] = useState(false);
  const byId = new Map(fleet.map((account) => [account.account_id, account]));
  const chosen = ids.map((id) => byId.get(id));
  const blocked = chosen.filter(isSpamBlocked).length;
  const free = chosen.filter(
    (account) => account !== undefined && !isSpamBlocked(account) && account.premium !== true,
  ).length;
  return (
    <Section
      n={2}
      title={t('masstagging.settings.accounts.title')}
      hint={t('masstagging.settings.accounts.hint')}
      example={stories ? t('masstagging.settings.accounts.example') : undefined}
      aside={
        <>
          {stories && free > 0 ? (
            <Badge tone="warning">{t('masstagging.freeAccounts', { count: free })}</Badge>
          ) : null}
          {blocked > 0 ? (
            <Badge tone="danger">
              {t('masstagging.settings.accounts.spamblock', { count: blocked })}
            </Badge>
          ) : null}
        </>
      }
    >
      <div className="flex items-center gap-3">
        <IconButton
          size="sm"
          aria-label={t('masstagging.settings.accounts.add')}
          onClick={() => {
            setPickerOpen(true);
          }}
        >
          <Icon name="plus" size={16} />
        </IconButton>
        {ids.length === 0 ? (
          <span className="type-body text-content-subtle">
            {t('masstagging.settings.accounts.empty')}
          </span>
        ) : (
          <div className="tb-scroll flex flex-1 items-center gap-2 overflow-x-auto py-1">
            {ids.map((id) => {
              const account = byId.get(id);
              const name = account === undefined ? id : accountDisplayName(account);
              const spam = isSpamBlocked(account);
              return (
                <span
                  key={id}
                  className={`group relative shrink-0 ${spam ? 'opacity-50' : ''}`}
                  title={spam ? t('masstagging.settings.accounts.excluded', { name }) : name}
                >
                  {account === undefined ? (
                    <span className="flex size-tile items-center justify-center rounded-full bg-canvas text-content-muted type-body-medium">
                      ?
                    </span>
                  ) : (
                    <AccountAvatar
                      account={account}
                      className="size-tile rounded-full"
                      fallbackClassName="bg-canvas text-content-muted type-body-medium"
                    />
                  )}
                  <IconButton
                    size="sm"
                    shape="circle"
                    aria-label={t('masstagging.settings.accounts.remove', { name })}
                    onClick={() => {
                      onChange(ids.filter((other) => other !== id));
                    }}
                    className="absolute -right-1 -top-1 bg-surface-card opacity-0 transition-opacity focus-visible:opacity-100 group-hover:opacity-100"
                  >
                    <Icon name="close" size={16} />
                  </IconButton>
                </span>
              );
            })}
          </div>
        )}
      </div>
      {pickerOpen ? (
        <BulkAccountPicker
          selected={ids}
          onApply={(next) => {
            onChange(next);
            setPickerOpen(false);
          }}
          onClose={() => {
            setPickerOpen(false);
          }}
        />
      ) : null}
    </Section>
  );
}
