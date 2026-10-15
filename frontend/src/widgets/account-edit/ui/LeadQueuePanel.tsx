import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  addLeadsToQueueMutation,
  leadQueueQueryKey,
  leadQueueQueryOptions,
  removeQueuedLeadMutation,
  updateLeadQueueSettingsMutation,
} from '@/entities/account';
import type { LeadQueueAddResult, LeadQueueItem } from '@/shared/api';
import { Badge, type BadgeTone, Button, HelpHint, Icon, IconButton, Textarea } from '@/shared/ui';

import { leadsFrom } from '../model/leads';

const MAX_LEADS = 1000;
const LISTED = 50;
// Not yet handed to a send run: the server lets these go.
const REMOVABLE = new Set(['pending', 'found']);
type LeadStatus = NonNullable<LeadQueueItem['status']>;

const TONE: Record<LeadStatus, BadgeTone> = {
  pending: 'neutral',
  found: 'info',
  messaging: 'info',
  sent: 'success',
  not_found: 'neutral',
  failed: 'danger',
  unconfirmed: 'warning',
  skipped: 'neutral',
};
const COUNTED = ['pending', 'found', 'messaging', 'sent', 'not_found', 'failed'] as const;

export function LeadQueuePanel({ accountIds, message }: { accountIds: string[]; message: string }) {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState('');
  const [added, setAdded] = useState<LeadQueueAddResult | null>(null);

  const queue = useQuery({
    ...leadQueueQueryOptions({ query: { limit: LISTED } }),
    enabled: open,
    refetchInterval: open ? 15000 : false,
  });
  const refresh = () => queryClient.invalidateQueries({ queryKey: leadQueueQueryKey() });
  const add = useMutation(addLeadsToQueueMutation());
  const remove = useMutation(removeQueuedLeadMutation());
  const save = useMutation(updateLeadQueueSettingsMutation());

  const settings = queue.data?.settings;
  const running = settings !== undefined && !settings.paused;
  const leads = leadsFrom(input);
  const formReady = accountIds.length > 0 && message.trim().length > 0;

  const onAdd = () => {
    if (leads.length === 0 || leads.length > MAX_LEADS) return;
    void add
      .mutateAsync({ body: { leads } })
      .then((result) => {
        setAdded(result);
        setInput('');
        return refresh();
      })
      .catch(() => undefined);
  };

  // Running from the form takes its accounts and message; a pause keeps the saved ones.
  const onSave = (paused: boolean) => {
    const body = paused
      ? { account_ids: settings?.account_ids ?? [], text: settings?.text ?? '', paused }
      : { account_ids: accountIds, text: message, paused };
    void save
      .mutateAsync({ body })
      .then(() => refresh())
      .catch(() => undefined);
  };

  const onRemove = (id: number) => {
    void remove
      .mutateAsync({ path: { lead_id: id } })
      .then(() => refresh())
      .catch(() => undefined);
  };

  const counts = queue.data?.counts ?? {};
  const waitReason = queue.data?.wait_reason;
  const nextSendAt = queue.data?.next_send_at;

  return (
    <div className="border-t border-canvas pt-2">
      <button
        type="button"
        className="flex w-full items-center gap-2 type-body-medium text-content-muted hover:text-content-primary"
        aria-expanded={open}
        onClick={() => {
          setOpen((value) => !value);
        }}
      >
        <Icon name={open ? 'chevron-down' : 'chevron-right'} size={18} />
        {t('accounts.messages.queue.toggle')}
      </button>

      {open && (
        <div className="mt-3 space-y-3">
          <section className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={running ? 'success' : 'neutral'} dot>
                {running
                  ? t('accounts.messages.queue.running', { n: settings.account_ids?.length ?? 0 })
                  : t('accounts.messages.queue.paused')}
              </Badge>
              <HelpHint text={t('accounts.messages.queue.hint')} />
            </div>
            <p role="status" className="type-small tabular-nums text-content-subtle">
              {COUNTED.map((status) =>
                t(`accounts.messages.queue.count.${status}`, { n: counts[status] ?? 0 }),
              ).join(' · ')}
            </p>
            {running && waitReason && nextSendAt && (
              <p className="type-small">
                {t(`accounts.messages.waiting.${waitReason}`, {
                  time: new Date(nextSendAt).toLocaleString(i18n.language, {
                    dateStyle: 'short',
                    timeStyle: 'short',
                  }),
                })}
              </p>
            )}
            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant={running ? 'secondary' : 'primary'}
                loading={save.isPending}
                disabled={!formReady}
                onClick={() => {
                  onSave(false);
                }}
              >
                {running ? t('accounts.messages.queue.update') : t('accounts.messages.queue.start')}
              </Button>
              {running && (
                <Button
                  loading={save.isPending}
                  onClick={() => {
                    onSave(true);
                  }}
                >
                  {t('accounts.messages.queue.pause')}
                </Button>
              )}
            </div>
            {!formReady && (
              <p className="type-small text-content-subtle">
                {t('accounts.messages.queue.needForm')}
              </p>
            )}
            {save.isError && (
              <p role="alert" className="type-small text-danger-deep">
                {t('accounts.messages.queue.saveError')}
              </p>
            )}
          </section>

          <section className="space-y-2">
            <div className="flex items-center justify-between gap-3">
              <label htmlFor="lead-queue-input" className="type-body-medium text-content-secondary">
                {t('accounts.messages.queue.leads')}
              </label>
              <span
                className={`type-small tabular-nums ${leads.length > MAX_LEADS ? 'text-danger-deep' : ''}`}
              >
                {leads.length}/{MAX_LEADS}
              </span>
            </div>
            <Textarea
              id="lead-queue-input"
              value={input}
              placeholder={t('accounts.messages.queue.leadsPlaceholder')}
              onChange={(event) => {
                setInput(event.target.value);
                setAdded(null);
              }}
            />
            <Button
              loading={add.isPending}
              disabled={leads.length === 0 || leads.length > MAX_LEADS}
              onClick={onAdd}
            >
              {t('accounts.messages.queue.add')}
            </Button>
            {added !== null && (
              <p role="status" className="type-small">
                {t('accounts.messages.queue.added', {
                  n: added.added,
                  duplicates: added.duplicates,
                  invalid: added.invalid,
                })}
              </p>
            )}
            {add.isError && (
              <p role="alert" className="type-small text-danger-deep">
                {t('accounts.messages.queue.addError')}
              </p>
            )}
          </section>

          {(queue.data?.items.length ?? 0) > 0 && (
            <ul className="space-y-1" aria-label={t('accounts.messages.queue.list')}>
              {queue.data?.items.map((lead) => {
                const status: LeadStatus = lead.status ?? 'pending';
                return (
                  <li key={lead.id} className="flex items-center gap-2 type-small">
                    <span className="tabular-nums">{lead.phone}</span>
                    {lead.client_name && (
                      <span className="truncate text-content-subtle">{lead.client_name}</span>
                    )}
                    <Badge tone={TONE[status]} className="ml-auto">
                      {t(`accounts.messages.queue.status.${status}`)}
                    </Badge>
                    {lead.id != null && REMOVABLE.has(status) && (
                      <IconButton
                        size="sm"
                        tone="danger"
                        aria-label={t('accounts.messages.queue.remove', { phone: lead.phone })}
                        onClick={() => {
                          if (lead.id != null) onRemove(lead.id);
                        }}
                      >
                        <Icon name="trash" size={16} />
                      </IconButton>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
          {remove.isError && (
            <p role="alert" className="type-small text-danger-deep">
              {t('accounts.messages.queue.removeError')}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
