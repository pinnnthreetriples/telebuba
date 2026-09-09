import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  accountDisplayName,
  AccountAvatar,
  allAccountsQueryOptions,
  invalidateAccountViews,
} from '@/entities/account';
import type { AccountRead } from '@/shared/api';
import { Button, Icon, IconButton, Input, Modal, Textarea } from '@/shared/ui';

import { BulkAccountPicker } from './BulkAccountPicker';
import { BulkProgress } from './BulkProgress';
import { PROFILE_BIO_MAX, PROFILE_NAME_MAX } from './_profileShared';
import { useBulkProfile } from './useBulkProfile';

type FieldKey = 'first_name' | 'last_name' | 'bio';
const FIELDS = ['first_name', 'last_name', 'bio'] as const satisfies readonly FieldKey[];
const MAX: Record<FieldKey, number> = {
  first_name: PROFILE_NAME_MAX,
  last_name: PROFILE_NAME_MAX,
  bio: PROFILE_BIO_MAX,
};

// The profile editor's bulk twin: the same text written to many accounts at once.
//
// Every field carries its own checkbox, and an unticked field is OMITTED from the
// request rather than sent empty — the backend's field contract ("" clears, absent
// leaves unchanged) is what makes "set one bio for the fleet, touch nothing else"
// expressible. A ticked-but-empty last name or bio therefore CLEARS it, which is
// the only way to wipe a field across a batch; first name has no such state
// (Telegram has no nameless user), so an empty one blocks the apply.
//
// The username is absent by design, not forgotten: Telegram handles are unique,
// so one value cannot be given to a group at all.
export function BulkEditModal({ account, onClose }: { account: AccountRead; onClose: () => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [ids, setIds] = useState<string[]>([account.account_id]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [on, setOn] = useState<Record<FieldKey, boolean>>({
    first_name: false,
    last_name: false,
    bio: false,
  });
  const [value, setValue] = useState<Record<FieldKey, string>>({
    first_name: '',
    last_name: '',
    bio: '',
  });
  const [started, setStarted] = useState(false);
  const bulk = useBulkProfile();

  // The fleet is already in cache behind the picker; this is the same key, so
  // the chips get names without a second request.
  const fleet = useQuery(allAccountsQueryOptions());
  const byId = new Map((fleet.data?.items ?? []).map((row) => [row.account_id, row]));
  const label = (accountId: string) => {
    const row = byId.get(accountId);
    return row ? accountDisplayName(row) : accountId;
  };
  const picked = ids.map((accountId) => byId.get(accountId) ?? { account_id: accountId });

  const ticked = FIELDS.filter((key) => on[key]);
  const tooLong = ticked.some((key) => value[key].trim().length > MAX[key]);
  const noName = on.first_name && value.first_name.trim() === '';
  const running = bulk.rows.some((row) => row.state === 'queued' || row.state === 'running');
  const canApply = ids.length > 0 && ticked.length > 0 && !tooLong && !noName;

  const apply = () => {
    setStarted(true);
    const body = Object.fromEntries(ticked.map((key) => [key, value[key].trim()]));
    void bulk.run(ids, body).finally(() => {
      // The names, usernames and avatars of every account in the batch just
      // changed; the table behind this dialog is showing the old ones.
      invalidateAccountViews(queryClient);
    });
  };

  const remove = (accountId: string) => {
    setIds((prev) => prev.filter((id) => id !== accountId));
  };

  return (
    <>
      <Modal
        onClose={running ? () => undefined : onClose}
        size="panel"
        label={t('accounts.bulk.title')}
      >
        <div className="flex max-h-dialog flex-col overflow-hidden">
          <div className="flex items-center gap-lg border-b border-line-row px-xl py-xl">
            <div className="flex size-face shrink-0 items-center justify-center rounded-full bg-info-tint text-info-strong">
              <Icon name="users" size={20} />
            </div>
            <div className="min-w-0 flex-1">
              <h2 className="truncate type-dialog-title">{t('accounts.bulk.title')}</h2>
              <div className="truncate type-prose">
                {t('accounts.bulk.selected', { count: ids.length })}
              </div>
            </div>
            <IconButton
              size="md"
              onClick={onClose}
              disabled={running}
              aria-label={t('accounts.profile.close')}
              className="text-title"
            >
              ×
            </IconButton>
          </div>

          <div className="flex items-center gap-md border-b border-line-row px-xl py-md">
            <Button
              size="xs"
              variant="dashedMuted"
              disabled={started}
              onClick={() => {
                setPickerOpen(true);
              }}
            >
              <Icon name="plus" size={16} />
              {t('accounts.bulk.add')}
            </Button>
            <div className="tb-scroll flex flex-1 items-center gap-sm overflow-x-auto py-hair">
              {picked.map((row) => (
                <span key={row.account_id} className="group relative shrink-0">
                  <AccountAvatar
                    account={row}
                    className="size-tile rounded-full"
                    fallbackClassName="bg-canvas text-content-muted type-label"
                  />
                  {!started && ids.length > 1 && (
                    <button
                      type="button"
                      aria-label={t('accounts.bulk.remove', { name: label(row.account_id) })}
                      onClick={() => {
                        remove(row.account_id);
                      }}
                      className="absolute -right-hair -top-hair flex size-glyph items-center justify-center rounded-full border border-line bg-surface-card leading-none text-content-muted opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100"
                    >
                      ×
                    </button>
                  )}
                </span>
              ))}
            </div>
          </div>

          <div className="tb-scroll flex flex-1 flex-col gap-lg overflow-y-auto p-xl">
            {started ? (
              <BulkProgress rows={bulk.rows} label={label} />
            ) : (
              <>
                <div className="rounded-lg bg-info-tint px-md py-md type-prose">
                  {t('accounts.bulk.hint')}
                </div>
                {FIELDS.map((key) => (
                  <div key={key} className="flex flex-col gap-tight">
                    <button
                      type="button"
                      role="checkbox"
                      aria-checked={on[key]}
                      onClick={() => {
                        setOn((prev) => ({ ...prev, [key]: !prev[key] }));
                      }}
                      className="flex items-center gap-md text-left"
                    >
                      <span
                        className={`flex size-glyph shrink-0 items-center justify-center rounded-sm border ${on[key] ? 'border-action-primary bg-action-primary' : 'border-line bg-surface-card'}`}
                      >
                        {on[key] && <Icon name="check" size={14} className="stroke-on-action" />}
                      </span>
                      <span className="type-label">{t(`accounts.bulk.field.${key}`)}</span>
                    </button>
                    {key === 'bio' ? (
                      <Textarea
                        className="resize-none [font-family:inherit]"
                        rows={3}
                        disabled={!on[key]}
                        value={value[key]}
                        aria-label={t(`accounts.bulk.field.${key}`)}
                        onChange={(event) => {
                          setValue((prev) => ({ ...prev, [key]: event.target.value }));
                        }}
                      />
                    ) : (
                      <Input
                        disabled={!on[key]}
                        value={value[key]}
                        aria-label={t(`accounts.bulk.field.${key}`)}
                        onChange={(event) => {
                          setValue((prev) => ({ ...prev, [key]: event.target.value }));
                        }}
                      />
                    )}
                    {on[key] && value[key].trim().length > MAX[key] && (
                      <span role="alert" className="type-caption font-medium text-danger">
                        {t('accounts.bulk.tooLong', { max: MAX[key] })}
                      </span>
                    )}
                    {key === 'first_name' && noName && (
                      <span role="alert" className="type-caption font-medium text-danger">
                        {t('accounts.profile.errFirstName')}
                      </span>
                    )}
                    {on[key] && key !== 'first_name' && value[key].trim() === '' && (
                      <span className="type-caption">{t('accounts.bulk.clears')}</span>
                    )}
                  </div>
                ))}
                <div className="type-caption">{t('accounts.bulk.usernameNote')}</div>
              </>
            )}
          </div>

          <div className="flex items-center justify-end gap-sm border-t border-line-row px-xl py-lg">
            {!started && (
              <div className="mr-auto type-label">
                {t('accounts.bulk.fieldCount', { done: ticked.length, total: FIELDS.length })}
              </div>
            )}
            {started ? (
              running ? (
                <Button variant="danger" onClick={bulk.stop}>
                  {t('accounts.bulk.stop')}
                </Button>
              ) : (
                <Button variant="primary" onClick={onClose}>
                  {t('accounts.bulk.done')}
                </Button>
              )
            ) : (
              <>
                <Button onClick={onClose}>{t('accounts.profile.cancel')}</Button>
                <Button variant="primary" disabled={!canApply} onClick={apply}>
                  {t('accounts.bulk.apply', { count: ids.length })}
                </Button>
              </>
            )}
          </div>
        </div>
      </Modal>
      {pickerOpen && (
        <BulkAccountPicker
          selected={ids}
          onApply={(next) => {
            setIds(next);
            setPickerOpen(false);
          }}
          onClose={() => {
            setPickerOpen(false);
          }}
        />
      )}
    </>
  );
}
