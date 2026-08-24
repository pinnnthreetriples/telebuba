import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  updateWarmingSettingsMutation,
  warmingBoardQueryOptions,
  warmingSettingsQueryOptions,
} from '@/entities/warming';
import type { WarmingSettings } from '@/shared/api';
import { mutationErrorText } from '@/shared/lib';

import { Modal, Switch } from '@/shared/ui';

// The three behaviour toggles + the readiness gate map 1:1 onto the real,
// GLOBAL warming settings row (WarmingSettingsUpdate has no account_id). Quiet
// hours were dropped from the backend (#194-#196) so the "local time" toggle and
// its time picker are UI-only, rendered for design parity but never persisted.
type BehaviorKey = 'reactions_enabled' | 'join_enabled' | 'inter_account_chat';
const BEHAVIOR_KEYS: BehaviorKey[] = ['reactions_enabled', 'join_enabled', 'inter_account_chat'];

type Scope = 'one' | 'all';

interface Toggles {
  reactions_enabled: boolean;
  join_enabled: boolean;
  inter_account_chat: boolean;
  enforce_readiness: boolean;
  local_time: boolean;
}

function initialToggles(settings?: WarmingSettings): Toggles {
  return {
    reactions_enabled: settings?.reactions_enabled ?? true,
    join_enabled: settings?.join_enabled ?? true,
    inter_account_chat: settings?.inter_account_chat ?? false,
    enforce_readiness: settings?.enforce_readiness ?? true,
    local_time: false,
  };
}

function ToggleRow({
  title,
  desc,
  on,
  onToggle,
}: {
  title: string;
  desc: string;
  on: boolean;
  onToggle: () => void;
}) {
  return (
    <div className="flex items-start justify-between gap-lg">
      <div className="min-w-0 flex-1">
        <div className="text-lead font-semibold">{title}</div>
        <div className="mt-[2px] text-tiny leading-[1.45] text-ink-subtle">{desc}</div>
      </div>
      <Switch checked={on} label={title} onChange={onToggle} />
    </div>
  );
}

// The design's rich per-account warming config: a "Behaviour" section of
// toggles, a "Limits & safety" section, a quiet-hours picker (UI-only), and
// scope tabs. Save writes the real GLOBAL warming settings.
export function WarmConfigModal({ phone, onClose }: { phone: string; onClose: () => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const settingsQuery = useQuery(warmingSettingsQueryOptions());
  const settings = settingsQuery.data;
  const save = useMutation(updateWarmingSettingsMutation());

  // "all" is the only scope the backend can honor (settings are global); the
  // per-account scope is not yet persisted, so it starts on "all".
  const [scope, setScope] = useState<Scope>('all');
  const [toggles, setToggles] = useState<Toggles>(() => initialToggles(settings));
  // Quiet-hours are UI-only (no backing field); keep them local for parity.
  const [from, setFrom] = useState('23:00');
  const [to, setTo] = useState('08:00');

  // On a cold cache the first render has no settings, so the lazy initial state is
  // the hardcoded fallback above — and a Save from that state writes those
  // fallbacks over the stored row. Seed ONCE, when the real row lands.
  //
  // Once, not on every `settings` identity change: a refetch (the invalidation a
  // failed save still fires, or a reconnect) returns a row with a fresh
  // `updated_at`, which defeats React Query's structural sharing, so re-seeding
  // reverted the operator's unsaved edits — while the failure was still on screen.
  // `local_time` is UI-only and never in the row, so it survives the seed too.
  const seeded = useRef(false);
  useEffect(() => {
    if (!settings || seeded.current) return;
    seeded.current = true;
    setToggles((prev) => ({ ...initialToggles(settings), local_time: prev.local_time }));
  }, [settings]);

  const flip = (key: keyof Toggles) => {
    setToggles((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const onSave = () => {
    save.mutate(
      {
        body: {
          reactions_enabled: toggles.reactions_enabled,
          join_enabled: toggles.join_enabled,
          inter_account_chat: toggles.inter_account_chat,
          enforce_readiness: toggles.enforce_readiness,
          // The Gemini model and the two rate-limit knobs are deliberately ABSENT,
          // not echoed: the write path keeps every one of them on an omitted field,
          // and echoing read a cache this modal never refetches on focus, so a Save
          // from a tab left open wrote a stale row over whatever the settings page
          // had persisted since.
          gemini_api_key: null,
          clear_gemini_key: false,
        },
      },
      {
        // Close on success ONLY: onSettled fires on failure too, which closed the
        // dialog over a rejected PUT and lost the operator's input.
        onSuccess: onClose,
        onSettled: () => {
          // What this write actually touches: the settings row, and the warming
          // board — whose read model embeds those same settings
          // (WarmingBoardState.settings). NOT the whole cache: a bare
          // invalidateQueries() also refetched the accounts table, the proxies,
          // the neurocomment campaigns and every open profile snapshot.
          void queryClient.invalidateQueries({
            queryKey: warmingSettingsQueryOptions().queryKey,
          });
          void queryClient.invalidateQueries({ queryKey: warmingBoardQueryOptions().queryKey });
        },
      },
    );
  };

  return (
    <Modal onClose={onClose} className="w-[540px]" label={t('warming.cfg.title')}>
      <div className="flex items-center gap-md border-b border-line-row px-6 pb-[15px] pt-5">
        <span className="flex h-[34px] w-[34px] shrink-0 items-center justify-center rounded-lg bg-primary-tint text-primary-deep">
          <svg
            width="18"
            height="18"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
          >
            <circle cx="12" cy="12" r="3" />
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
          </svg>
        </span>
        <div>
          <div className="text-title font-bold">{t('warming.cfg.title')}</div>
          <div className="mt-[2px] text-body text-ink-subtle">{phone}</div>
        </div>
      </div>

      <div className="px-6 pb-5 pt-[18px]">
        <div className="mb-[14px] text-tiny font-semibold uppercase tracking-[0.04em] text-ink-subtle">
          {t('warming.cfg.behaviorTitle')}
        </div>
        <div className="flex flex-col gap-lg">
          {BEHAVIOR_KEYS.map((key) => (
            <ToggleRow
              key={key}
              title={t(`warming.cfg.toggle.${key}.title`)}
              desc={t(`warming.cfg.toggle.${key}.desc`)}
              on={toggles[key]}
              onToggle={() => {
                flip(key);
              }}
            />
          ))}
        </div>

        <div className="my-[18px] h-px bg-line-row" />

        <div className="mb-[14px] text-tiny font-semibold uppercase tracking-[0.04em] text-ink-subtle">
          {t('warming.cfg.limitsTitle')}
        </div>
        <div className="flex flex-col gap-lg">
          <ToggleRow
            title={t('warming.cfg.toggle.enforce_readiness.title')}
            desc={t('warming.cfg.toggle.enforce_readiness.desc')}
            on={toggles.enforce_readiness}
            onToggle={() => {
              flip('enforce_readiness');
            }}
          />
          <ToggleRow
            title={t('warming.cfg.toggle.local_time.title')}
            desc={t('warming.cfg.toggle.local_time.desc')}
            on={toggles.local_time}
            onToggle={() => {
              flip('local_time');
            }}
          />
        </div>

        {toggles.local_time ? (
          <div className="tb-fadeup mt-[14px] rounded-lg">
            <div className="mb-[10px] text-right text-tiny font-semibold text-ink-muted">
              {t('warming.cfg.quietHours')}
            </div>
            <div className="flex items-center justify-end gap-md">
              <input
                value={from}
                onChange={(e) => {
                  setFrom(e.target.value);
                }}
                inputMode="numeric"
                maxLength={5}
                aria-label={t('warming.cfg.quietFrom')}
                className="w-[64px] rounded-lg border border-line-input bg-white px-[11px] py-2 text-center text-lead font-semibold tabular-nums outline-none"
              />
              <span className="shrink-0 text-lead text-ink-subtle">–</span>
              <input
                value={to}
                onChange={(e) => {
                  setTo(e.target.value);
                }}
                inputMode="numeric"
                maxLength={5}
                aria-label={t('warming.cfg.quietTo')}
                className="w-[64px] rounded-lg border border-line-input bg-white px-[11px] py-2 text-center text-lead font-semibold tabular-nums outline-none"
              />
            </div>
            <div className="mt-[9px] text-right text-tiny leading-[1.4] text-ink-subtle">
              {t('warming.cfg.quietNote')}
            </div>
          </div>
        ) : null}
      </div>

      <div className="border-t border-line-row px-6 pb-5 pt-[15px]">
        <div className="mb-[14px] flex gap-sm rounded-lg bg-line-row p-[3px]">
          <button
            type="button"
            title={t('warming.cfg.scopeOneNote')}
            onClick={() => {
              setScope('one');
            }}
            className={`flex-1 rounded-md py-[7px] text-body font-medium transition-colors ${scope === 'one' ? 'bg-white text-ink shadow-seg' : 'text-ink-muted'}`}
          >
            {t('warming.cfg.scopeOne')}
          </button>
          <button
            type="button"
            onClick={() => {
              setScope('all');
            }}
            className={`flex-1 rounded-md py-[7px] text-body font-medium transition-colors ${scope === 'all' ? 'bg-white text-ink shadow-seg' : 'text-ink-muted'}`}
          >
            {t('warming.cfg.scopeAll')}
          </button>
        </div>
        {scope === 'one' ? (
          <div className="mb-[12px] text-tiny leading-[1.45] text-warning-deep">
            {t('warming.cfg.scopeOneNote')}
          </div>
        ) : null}
        {save.isError ? (
          // The same text the global mutation toast shows, not the generic copy:
          // this alert is the in-context report and must not be the less
          // informative of the two. Falls back to shell.mutationError itself.
          <div role="alert" className="mb-[12px] text-tiny leading-[1.45] text-danger">
            {mutationErrorText(save.error)}
          </div>
        ) : null}
        <div className="flex gap-sm">
          <button
            type="button"
            disabled={save.isPending || scope === 'one' || !settings}
            onClick={onSave}
            className="flex-1 rounded-full bg-primary px-[14px] py-[10px] text-lead font-semibold text-white transition-colors hover:bg-primary-press disabled:opacity-50"
          >
            {t('warming.cfg.save')}
          </button>
          <button
            type="button"
            onClick={onClose}
            className="flex-1 rounded-full border border-line-input bg-white px-[14px] py-[10px] text-lead font-semibold text-ink"
          >
            {t('warming.cfg.cancel')}
          </button>
        </div>
      </div>
    </Modal>
  );
}
