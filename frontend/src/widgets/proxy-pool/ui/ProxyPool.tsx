import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  checkProxyMutation,
  deleteProxyMutation,
  proxyPoolQueryOptions,
  proxyTypeLabel,
} from '@/entities/proxy';
import type { ProxyRead } from '@/shared/api';

import { ProxyDeleteModal } from './ProxyDeleteModal';

// Proxy connectivity status → dot/label colour (design status palette). A failed
// check drops the geo flag, so this is the only cue the proxy is dead — surface
// it explicitly instead of letting the flag silently vanish.
const PROXY_STATUS_COLOR: Record<ProxyRead['status'], string> = {
  tcp_working: '#12a150',
  failed: '#c0473f',
  unknown: '#9a9893',
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
  const [busyId, setBusyId] = useState<string | null>(null);
  const [toDelete, setToDelete] = useState<ProxyRead | null>(null);

  const proxies = data?.proxies ?? [];
  const empty = proxies.length === 0;
  const invalidate = () => {
    void queryClient.invalidateQueries();
  };
  const onDelete = (id: string) => {
    setBusyId(id);
    remove.mutate(
      { path: { proxy_id: id } },
      {
        onSettled: () => {
          setBusyId(null);
          invalidate();
        },
      },
    );
  };
  const onCheck = (id: string) => {
    setBusyId(id);
    check.mutate(
      { path: { proxy_id: id } },
      {
        onSettled: () => {
          setBusyId(null);
          invalidate();
        },
      },
    );
  };

  return (
    <div className="mb-4 rounded-2xl border border-line bg-white px-[18px] py-4">
      <div className="mb-[13px] flex flex-wrap items-center justify-between gap-3">
        <div>
          <span className="text-[14px] font-semibold">{t('accounts.proxyPool.title')}</span>
          <span className="ml-2 text-[12px] text-ink-subtle">
            {t('accounts.proxyPool.subtitle')}
          </span>
        </div>
        {!empty && (
          <button
            type="button"
            onClick={onAdd}
            className="inline-flex items-center gap-[6px] rounded-full bg-primary px-[15px] py-[7px] text-[12.5px] font-medium text-white"
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
          <div className="mb-[13px] flex h-[46px] w-[46px] items-center justify-center rounded-[14px] bg-[#f1efed] text-ink-subtle">
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
          <div className="mb-1 text-[13.5px] font-semibold">
            {t('accounts.proxyPool.emptyTitle')}
          </div>
          <div className="mb-4 max-w-[300px] text-[12px] text-ink-subtle">
            {t('accounts.proxyPool.emptyBody')}
          </div>
          <button
            type="button"
            onClick={onAdd}
            className="inline-flex items-center gap-[7px] rounded-full bg-primary px-5 py-[10px] text-[13px] font-medium text-white"
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
              busy={busyId === proxy.id}
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
  const statusColor = PROXY_STATUS_COLOR[proxy.status];
  const pct = proxy.capacity > 0 ? Math.round((proxy.used / proxy.capacity) * 100) : 0;
  return (
    <div
      className={`flex flex-col gap-[9px] rounded-[13px] border px-[14px] py-[13px] ${
        problem
          ? 'border-[#f0d9d6] bg-[#fcf6f5]'
          : geoConflict
            ? 'border-[#ead9a8] bg-[#fffaf0]'
            : 'border-line bg-white'
      }`}
    >
      <div className="flex items-center gap-[9px]">
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
          <span className="flex h-4 w-[22px] shrink-0 items-center justify-center rounded-[3px] bg-[#fbecec] text-danger">
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
          <span title={geoTitle} className="h-4 w-[22px] shrink-0 rounded-[3px] bg-[#e6e5e3]" />
        )}
        <div className="min-w-0 flex-1">
          <div className="truncate text-[12.5px] font-semibold">
            {proxy.host}:{proxy.port}
          </div>
          <div className="mt-px flex items-center gap-[5px] text-[11px] text-ink-subtle">
            <span>{proxyTypeLabel(proxy.proxy_type)}</span>
            <span className="text-line-strong">·</span>
            <span
              className="inline-flex items-center gap-[4px] font-medium"
              style={{ color: statusColor }}
              title={proxy.last_error ?? undefined}
            >
              <span
                className="h-[5px] w-[5px] shrink-0 rounded-full"
                style={{ background: statusColor }}
              />
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
            <span className="tb-spin inline-block h-[12px] w-[12px] rounded-full border-2 border-[#c8c6c2] border-t-primary" />
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
          className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[#b6b4af] disabled:opacity-50"
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
          <span
            className={`text-[11.5px] font-semibold ${full ? 'text-danger' : 'text-[#2e7d55]'}`}
          >
            {proxy.used} / {proxy.capacity}
          </span>
        </div>
        <div className="h-[5px] overflow-hidden rounded-full bg-track">
          <div
            className={`h-full rounded-full ${full ? 'bg-danger' : 'bg-primary'}`}
            style={{ width: `${String(pct)}%` }}
          />
        </div>
        <div className={`mt-[5px] text-[10.5px] ${full ? 'text-danger' : 'text-[#2e7d55]'}`}>
          {full
            ? t('accounts.proxyPool.full')
            : t('accounts.proxyPool.free', { count: proxy.free })}
        </div>
      </div>
    </div>
  );
}
