import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { invalidateAccountViews } from '@/entities/account';
import {
  checkProxyMutation,
  deleteProxyMutation,
  proxyPoolQueryOptions,
  proxyTypeLabel,
} from '@/entities/proxy';
import type { ProxyRead } from '@/shared/api';

import { ProxyDeleteModal } from './ProxyDeleteModal';

// Proxy connectivity status → dot/label tone (the design's status tokens, so the
// three states can't drift from the same three states elsewhere). A failed check
// drops the geo flag, so this is the only cue the proxy is dead — surface it
// explicitly instead of letting the flag silently vanish.
const PROXY_STATUS_TONE: Record<ProxyRead['status'], string> = {
  tcp_working: 'text-success',
  failed: 'text-danger',
  unknown: 'text-ink-subtle',
};

// The design's proxy-pool card: one card per pool proxy with a usage bar
// (used/capacity), or an empty-state when the pool has none. Both add buttons
// open the add-proxy modal (owned by the page). Wired to the real /proxies pool.
export function ProxyPool({ onAdd }: { onAdd: () => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { data } = useQuery(proxyPoolQueryOptions());
  const remove = useMutation(deleteProxyMutation());
  const check = useMutation(checkProxyMutation());
  // A Set, not one id: re-check and delete are per card and both can be in flight
  // at once. With a single string the second card's click cleared the first card's
  // spinner and re-enabled both its buttons mid-request, and the first response to
  // land cleared the OTHER card's spinner.
  const [busyIds, setBusyIds] = useState<ReadonlySet<string>>(new Set());
  const [toDelete, setToDelete] = useState<ProxyRead | null>(null);

  const proxies = data?.proxies ?? [];
  const empty = proxies.length === 0;
  // Deleting or re-checking a pool proxy changes the pool AND the accounts
  // riding it (proxy column, status), but nothing beyond that.
  const invalidate = () => {
    invalidateAccountViews(queryClient);
  };
  const markBusy = (id: string, busy: boolean) => {
    setBusyIds((ids) => {
      const next = new Set(ids);
      if (busy) next.add(id);
      else next.delete(id);
      return next;
    });
  };
  // mutateAsync, not mutate+onSettled: one useMutation is ONE callback slot, so
  // acting on a second card took the slot over and the first card's invalidate was
  // dropped — a re-checked proxy kept its stale status, a deleted one stayed on
  // screen. A promise per call also captures its own id, not the hook's latest
  // variables. The global mutationCache still toasts the failure; .catch only
  // keeps the rejection from escaping as unhandled.
  const runOnCard = (id: string, call: Promise<unknown>) => {
    markBusy(id, true);
    void call
      .finally(() => {
        markBusy(id, false);
        invalidate();
      })
      .catch(() => undefined);
  };
  const onDelete = (id: string) => {
    runOnCard(id, remove.mutateAsync({ path: { proxy_id: id } }));
  };
  const onCheck = (id: string) => {
    runOnCard(id, check.mutateAsync({ path: { proxy_id: id } }));
  };

  return (
    <div className="mb-4 rounded-card border border-line bg-white px-[18px] py-4">
      <div className="mb-[13px] flex flex-wrap items-center justify-between gap-3">
        <div>
          <span className="text-[13px] font-semibold">{t('accounts.proxyPool.title')}</span>
          <span className="ml-2 text-[12.5px] text-ink-subtle">
            {t('accounts.proxyPool.subtitle')}
          </span>
        </div>
        {!empty && (
          <button
            type="button"
            onClick={onAdd}
            className="inline-flex items-center gap-[7px] rounded-full bg-primary px-[15px] py-[7px] text-[11px] font-semibold text-white"
          >
            <svg
              width="13"
              height="13"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.2"
            >
              <path d="M12 5v14M5 12h14" />
            </svg>
            {t('accounts.proxyPool.add')}
          </button>
        )}
      </div>
      {empty ? (
        <div className="flex flex-col items-center justify-center px-4 pb-[30px] pt-[34px] text-center">
          <div className="mb-[13px] flex h-[46px] w-[46px] items-center justify-center rounded-lg bg-canvas text-ink-subtle">
            <svg
              width="22"
              height="22"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
            >
              <rect x="2" y="6" width="20" height="12" rx="2" />
              <path d="M6 12h.01M10 12h4" />
            </svg>
          </div>
          <div className="mb-1 text-[13px] font-semibold">{t('accounts.proxyPool.emptyTitle')}</div>
          <div className="mb-4 max-w-[300px] text-[12.5px] text-ink-subtle">
            {t('accounts.proxyPool.emptyBody')}
          </div>
          <button
            type="button"
            onClick={onAdd}
            className="inline-flex items-center gap-[7px] rounded-full bg-primary px-[22px] py-[9px] text-[13px] font-semibold text-white"
          >
            <svg
              width="15"
              height="15"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.2"
            >
              <path d="M12 5v14M5 12h14" />
            </svg>
            {t('accounts.proxyPool.emptyAdd')}
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(232px,1fr))] gap-[10px]">
          {proxies.map((proxy: ProxyRead) => (
            <ProxyCard
              key={proxy.id}
              proxy={proxy}
              busy={busyIds.has(proxy.id)}
              onDelete={() => {
                setToDelete(proxy);
              }}
              onCheck={() => {
                onCheck(proxy.id);
              }}
            />
          ))}
        </div>
      )}
      {toDelete && (
        <ProxyDeleteModal
          endpoint={`${toDelete.host}:${String(toDelete.port)}`}
          used={toDelete.used}
          onClose={() => {
            setToDelete(null);
          }}
          onConfirm={() => {
            onDelete(toDelete.id);
          }}
        />
      )}
    </div>
  );
}

function ProxyCard({
  proxy,
  busy,
  onDelete,
  onCheck,
}: {
  proxy: ProxyRead;
  busy: boolean;
  onDelete: () => void;
  onCheck: () => void;
}) {
  const { t } = useTranslation();
  const full = proxy.free <= 0;
  const failed = proxy.status === 'failed';
  const problem = full || failed;
  const geoStatus = proxy.geo_status ?? 'unknown';
  const geoConflict = geoStatus === 'conflict';
  const geoTitle = t(`accounts.proxyPool.geo.${geoStatus}`, {
    ipinfo: proxy.ipinfo_country_code ?? '—',
    maxmind: proxy.maxmind_country_code ?? '—',
  });
  const statusTone = PROXY_STATUS_TONE[proxy.status];
  const pct = proxy.capacity > 0 ? Math.round((proxy.used / proxy.capacity) * 100) : 0;
  return (
    <div
      className={`flex flex-col gap-[10px] rounded-lg border px-[14px] py-[13px] ${
        problem
          ? 'border-danger-line bg-danger-tint'
          : geoConflict
            ? 'border-[#ead9a8] bg-[#fffaf0]'
            : 'border-line bg-white'
      }`}
    >
      <div className="flex items-center gap-[10px]">
        {proxy.country_code ? (
          <span
            className={`fi fi-${proxy.country_code.toLowerCase()} h-4 w-[22px] shrink-0 rounded-[3px] shadow-[0_0_0_1px_rgba(0,0,0,0.07)]`}
            title={geoTitle}
          />
        ) : geoConflict ? (
          <span
            data-testid="geo-conflict"
            title={geoTitle}
            className="flex h-4 w-[22px] shrink-0 items-center justify-center rounded-[3px] bg-[#fff0c2] text-[#9a6700]"
          >
            <svg
              width="11"
              height="11"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.4"
            >
              <path d="M12 9v4M12 17h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
            </svg>
          </span>
        ) : failed ? (
          <span className="flex h-4 w-[22px] shrink-0 items-center justify-center rounded-[3px] bg-danger-tint text-danger">
            <svg
              width="11"
              height="11"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.4"
            >
              <path d="M12 9v4M12 17h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
            </svg>
          </span>
        ) : (
          <span title={geoTitle} className="h-4 w-[22px] shrink-0 rounded-[3px] bg-line" />
        )}
        <div className="min-w-0 flex-1">
          <div className="truncate text-[12.5px] font-semibold">
            {proxy.host}:{proxy.port}
          </div>
          <div className="mt-px flex items-center gap-[5px] text-[11px] text-ink-subtle">
            <span>{proxyTypeLabel(proxy.proxy_type)}</span>
            <span className="text-line-strong">·</span>
            <span
              className={`inline-flex items-center gap-[4px] font-medium ${statusTone}`}
              title={proxy.last_error ?? undefined}
            >
              {/* `bg-current` — the dot can never disagree with its label. */}
              <span className="h-[5px] w-[5px] shrink-0 rounded-full bg-current" />
              {t(`accounts.proxyPool.status.${proxy.status}`)}
            </span>
          </div>
        </div>
        <button
          type="button"
          onClick={onCheck}
          disabled={busy}
          aria-label={t('accounts.proxyForm.detect')}
          className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-ink-subtle disabled:opacity-50"
        >
          {busy ? (
            <span className="tb-spin inline-block h-[12px] w-[12px] rounded-full border-2 border-line-strong border-t-primary" />
          ) : (
            <svg
              width="13"
              height="13"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.9"
            >
              <path d="M21 12a9 9 0 1 1-6.2-8.6" />
              <path d="M21 3v6h-6" />
            </svg>
          )}
        </button>
        <button
          type="button"
          onClick={onDelete}
          disabled={busy}
          aria-label={t('accounts.actions.delete')}
          className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-ink-subtle disabled:opacity-50"
        >
          <svg
            width="13"
            height="13"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
          >
            <path d="M18 6 6 18M6 6l12 12" />
          </svg>
        </button>
      </div>
      <div>
        <div className="mb-[5px] flex items-center justify-between">
          <span className="text-[11px] text-ink-muted">{t('accounts.proxyPool.accounts')}</span>
          <span className={`text-[11px] font-semibold ${full ? 'text-danger' : 'text-success'}`}>
            {proxy.used} / {proxy.capacity}
          </span>
        </div>
        <div className="h-[5px] overflow-hidden rounded-full bg-track">
          <div
            className={`h-full rounded-full ${full ? 'bg-danger' : 'bg-primary'}`}
            style={{ width: `${String(pct)}%` }}
          />
        </div>
        <div className={`mt-[5px] text-[10.5px] ${full ? 'text-danger' : 'text-success'}`}>
          {full
            ? t('accounts.proxyPool.full')
            : t('accounts.proxyPool.free', { count: proxy.free })}
        </div>
      </div>
    </div>
  );
}
