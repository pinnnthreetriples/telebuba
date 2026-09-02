import { useMutation, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { assignProxyMutation, proxyPoolQueryOptions, proxyTypeLabel } from '@/entities/proxy';
import { Button } from '@/shared/ui';

// Step 2 "pick from pool" of the add-account wizard, for MANY accounts: each
// click hands a proxy up to its free slots' worth of the accounts still without
// one; "distribute" walks the free proxies in list order until nothing is left.
// Assigns run one at a time through `mutateAsync` — a useMutation observer has a
// single callback slot, so firing several `.mutate` at once loses all but the
// last. Refusals leave the account in `remaining` and raise the alert; the
// operator may still press Done with accounts unassigned.
export function ProxyPoolStep({
  accountIds,
  onBack,
  onDone,
  onImported,
}: {
  accountIds: string[];
  onBack: () => void;
  onDone: () => void;
  onImported: () => void;
}) {
  const { t } = useTranslation();
  const [remaining, setRemaining] = useState(accountIds);
  // Slots taken in this step, so the list reflects them before the pool
  // refetches. Stamped with the pool snapshot they were subtracted from: the
  // refetch after a batch (`onImported` invalidates the pool) already carries
  // them, and subtracting twice hid a proxy that still had a free slot.
  const [taken, setTaken] = useState<{ at: number; slots: Record<string, number> }>({
    at: 0,
    slots: {},
  });
  const [failed, setFailed] = useState(false);
  const assignProxy = useMutation(assignProxyMutation());
  const pool = useQuery(proxyPoolQueryOptions());
  const slots = taken.at === pool.dataUpdatedAt ? taken.slots : {};
  const freeProxies = (pool.data?.proxies ?? [])
    .map((proxy) => ({ ...proxy, free: proxy.free - (slots[proxy.id] ?? 0) }))
    .filter((proxy) => proxy.free > 0);
  const done = accountIds.length - remaining.length;

  const assignTo = async (targets: typeof freeProxies) => {
    let left = remaining;
    let refused = false;
    for (const proxy of targets) {
      for (const accountId of left.slice(0, proxy.free)) {
        try {
          await assignProxy.mutateAsync({
            path: { proxy_id: proxy.id },
            body: { account_id: accountId },
          });
          left = left.filter((id) => id !== accountId);
          setRemaining(left);
          setTaken((prev) => {
            const base = prev.at === pool.dataUpdatedAt ? prev.slots : {};
            return {
              at: pool.dataUpdatedAt,
              slots: { ...base, [proxy.id]: (base[proxy.id] ?? 0) + 1 },
            };
          });
        } catch {
          refused = true;
        }
      }
      if (left.length === 0) break;
    }
    setFailed(refused);
    onImported();
    // Single account keeps today's UX: a successful assign advances by itself.
    if (accountIds.length === 1 && left.length === 0) onDone();
  };

  return (
    <>
      {accountIds.length > 1 && (
        <div className="mb-lg flex items-center justify-between gap-md type-caption">
          <span className="flex flex-wrap gap-sm">
            <span>{t('accounts.addWizard.poolAssigned', { done, total: accountIds.length })}</span>
            {remaining.length > 0 && (
              <span>{t('accounts.addWizard.poolRemaining', { count: remaining.length })}</span>
            )}
          </span>
          {/* The batch action sits beside the tally, not under a long pool list. */}
          {freeProxies.length > 0 && remaining.length > 0 && (
            <Button
              variant="primary"
              size="sm"
              disabled={assignProxy.isPending}
              onClick={() => {
                void assignTo(freeProxies);
              }}
            >
              {t('accounts.addWizard.poolDistribute')}
            </Button>
          )}
        </div>
      )}
      <div className="flex flex-col gap-sm">
        {freeProxies.length === 0 ? (
          <div className="rounded-lg border border-dashed border-line bg-surface-card px-lg py-2xl text-center text-body text-content-subtle">
            {t('accounts.addWizard.poolEmpty')}
          </div>
        ) : (
          freeProxies.map((proxy) => (
            <button
              key={proxy.id}
              type="button"
              disabled={assignProxy.isPending}
              onClick={() => {
                void assignTo([proxy]);
              }}
              className="flex items-center gap-md rounded-lg border border-line bg-surface-card px-lg py-md text-left transition-colors hover:border-info-line disabled:opacity-60"
            >
              {proxy.country_code ? (
                <span
                  className={`fi fi-${proxy.country_code.toLowerCase()} block h-flag w-flag shrink-0 rounded-[3px] shadow-ring`}
                />
              ) : null}
              <span className="flex-1">
                <span className="block type-card-title">
                  {(proxy.country_code ?? '—').toUpperCase()} · {proxyTypeLabel(proxy.proxy_type)}
                </span>
                <span className="block font-mono type-caption">
                  {proxy.host}:{proxy.port}
                </span>
              </span>
              <span className="type-label text-success-deep">
                {t('accounts.addWizard.poolFree', { count: proxy.free })}
              </span>
            </button>
          ))
        )}
        {failed && (
          <div role="alert" className="type-caption text-danger">
            {t(
              accountIds.length > 1
                ? 'accounts.addWizard.proxyAssignPartial'
                : 'accounts.addWizard.proxyAssignError',
            )}
          </div>
        )}
      </div>
      <div className="mt-xl flex justify-between gap-sm">
        <Button onClick={onBack}>{t('accounts.addWizard.back')}</Button>
        <Button variant="primary" onClick={onDone} disabled={assignProxy.isPending}>
          {t('accounts.addWizard.done')}
        </Button>
      </div>
    </>
  );
}
