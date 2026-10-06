// Exactly the bulk editor's strip: "+" opens the same fleet picker, the chosen accounts
// sit as avatars, and the cross on hover takes one out.
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { AccountAvatar, accountDisplayName, BulkAccountPicker } from '@/entities/account';
import type { AccountRead } from '@/shared/api';
import { Icon, IconButton } from '@/shared/ui';

import { Eyebrow } from './fields';

export function AccountsSection({
  ids,
  fleet,
  onChange,
}: {
  ids: string[];
  fleet: AccountRead[];
  onChange: (ids: string[]) => void;
}) {
  const { t } = useTranslation();
  const [pickerOpen, setPickerOpen] = useState(false);
  const byId = new Map(fleet.map((account) => [account.account_id, account]));
  return (
    <section>
      <Eyebrow
        title={t('chatBroadcast.settings.accounts.title')}
        caption={t('chatBroadcast.settings.accounts.selected', { count: ids.length })}
      />
      <div className="flex items-center gap-md">
        <IconButton
          size="sm"
          aria-label={t('chatBroadcast.settings.accounts.add')}
          onClick={() => {
            setPickerOpen(true);
          }}
        >
          <Icon name="plus" size={16} />
        </IconButton>
        <div className="tb-scroll flex flex-1 items-center gap-sm overflow-x-auto py-hair">
          {ids.map((id) => {
            const account = byId.get(id);
            const name = account === undefined ? id : accountDisplayName(account);
            return (
              <span key={id} className="group relative shrink-0" title={name}>
                {account === undefined ? (
                  <span className="flex size-tile items-center justify-center rounded-full bg-canvas text-content-muted type-label">
                    ?
                  </span>
                ) : (
                  <AccountAvatar
                    account={account}
                    className="size-tile rounded-full"
                    fallbackClassName="bg-canvas text-content-muted type-label"
                  />
                )}
                <IconButton
                  size="sm"
                  shape="circle"
                  aria-label={t('chatBroadcast.settings.accounts.remove', { name })}
                  onClick={() => {
                    onChange(ids.filter((other) => other !== id));
                  }}
                  className="absolute -right-hair -top-hair bg-surface-card opacity-0 transition-opacity focus-visible:opacity-100 group-hover:opacity-100"
                >
                  <Icon name="close" size={16} />
                </IconButton>
              </span>
            );
          })}
        </div>
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
    </section>
  );
}
