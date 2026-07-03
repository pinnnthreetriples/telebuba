import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { updateWarmingSettingsMutation, warmingSettingsQueryOptions } from '@/entities/warming';
import type { WarmingSettings } from '@/shared/api';

import { Modal } from '@/shared/ui';

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

function Switch({ on, label, onToggle }: { on: boolean; label: string; onToggle: () => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      onClick={onToggle}
      className={`relative h-[26px] w-[44px] shrink-0 rounded-full transition-colors ${on ? 'bg-primary' : 'bg-[#cbc9c4]'}`}
    >
      <span
        className={`absolute top-[3px] block h-5 w-5 rounded-full bg-white shadow-[0_1px_3px_rgba(0,0,0,0.3)] transition-transform duration-[420ms] [transition-timing-function:cubic-bezier(.34,1.35,.5,1)] ${on ? 'translate-x-[21px]' : 'translate-x-[3px]'}`}
      />
    </button>
  );
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
    <div className="flex items-start justify-between gap-[14px]">
      <div className="min-w-0 flex-1">
        <div className="text-[13px] font-semibold">{title}</div>
        <div className="mt-[2px] text-[11.5px] leading-[1.45] text-ink-subtle">{desc}</div>
      </div>
      <Switch on={on} label={title} onToggle={onToggle} />
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
          // Preserve the server's non-behaviour fields untouched.
          max_daily_actions: settings?.max_daily_actions ?? 0,
          gemini_model: settings?.gemini_model,
          gemini_api_key: null,
          clear_gemini_key: false,
        },
      },
      {
        onSettled: () => {
          void queryClient.invalidateQueries();
          onClose();
        },
      },
    );
  };

  return (
    <Modal onClose={onClose} z={72} className="w-[540px]">
      <div className="flex items-center gap-[11px] border-b border-[#f0eeeb] px-6 pb-[15px] pt-5">
        <span className="flex h-[34px] w-[34px] shrink-0 items-center justify-center rounded-[10px] bg-[#eef4ff] text-primary">
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
          <div className="text-[16px] font-bold">{t('warming.cfg.title')}</div>
          <div className="mt-[2px] text-[12.5px] text-ink-subtle">{phone}</div>
        </div>
      </div>

      <div className="px-6 pb-5 pt-[18px]">
        <div className="mb-[14px] text-[11px] font-semibold uppercase tracking-[0.04em] text-ink-subtle">
          {t('warming.cfg.behaviorTitle')}
        </div>
        <div className="flex flex-col gap-4">
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

        <div className="my-[18px] h-px bg-[#f0eeeb]" />

        <div className="mb-[14px] text-[11px] font-semibold uppercase tracking-[0.04em] text-ink-subtle">
          {t('warming.cfg.limitsTitle')}
        </div>
        <div className="flex flex-col gap-4">
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
          <div className="tb-fadeup mt-[14px] rounded-[11px]">
            <div className="mb-[10px] text-right text-[11.5px] font-semibold text-ink-muted">
              {t('warming.cfg.quietHours')}
            </div>
            <div className="flex items-center justify-end gap-[10px]">
              <input
                value={from}
                onChange={(e) => {
                  setFrom(e.target.value);
                }}
                inputMode="numeric"
                maxLength={5}
                aria-label={t('warming.cfg.quietFrom')}
                className="w-[64px] rounded-[10px] border border-[#dedcd8] bg-white px-[11px] py-2 text-center text-[14px] font-semibold tabular-nums outline-none"
              />
              <span className="shrink-0 text-[13px] text-[#b5b3ae]">–</span>
              <input
                value={to}
                onChange={(e) => {
                  setTo(e.target.value);
                }}
                inputMode="numeric"
                maxLength={5}
                aria-label={t('warming.cfg.quietTo')}
                className="w-[64px] rounded-[10px] border border-[#dedcd8] bg-white px-[11px] py-2 text-center text-[14px] font-semibold tabular-nums outline-none"
              />
            </div>
            <div className="mt-[9px] text-right text-[11px] leading-[1.4] text-[#b5b3ae]">
              {t('warming.cfg.quietNote')}
            </div>
          </div>
        ) : null}
      </div>

      <div className="border-t border-[#f0eeeb] px-6 pb-5 pt-[15px]">
        <div className="mb-[14px] flex gap-[6px] rounded-[10px] bg-[#f0eeeb] p-[3px]">
          <button
            type="button"
            title={t('warming.cfg.scopeOneNote')}
            onClick={() => {
              setScope('one');
            }}
            className={`flex-1 rounded-[8px] py-[7px] text-[12.5px] font-medium transition-colors ${scope === 'one' ? 'bg-white text-ink shadow-sm' : 'text-ink-muted'}`}
          >
            {t('warming.cfg.scopeOne')}
          </button>
          <button
            type="button"
            onClick={() => {
              setScope('all');
            }}
            className={`flex-1 rounded-[8px] py-[7px] text-[12.5px] font-medium transition-colors ${scope === 'all' ? 'bg-white text-ink shadow-sm' : 'text-ink-muted'}`}
          >
            {t('warming.cfg.scopeAll')}
          </button>
        </div>
        {scope === 'one' ? (
          <div className="mb-[12px] text-[11.5px] leading-[1.45] text-[#c47d12]">
            {t('warming.cfg.scopeOneNote')}
          </div>
        ) : null}
        <div className="flex gap-2">
          <button
            type="button"
            disabled={save.isPending || scope === 'one'}
            onClick={onSave}
            className="flex-1 rounded-full bg-primary px-[14px] py-[10px] text-[13px] font-semibold text-white transition-colors hover:bg-[#0057db] disabled:opacity-50"
          >
            {t('warming.cfg.save')}
          </button>
          <button
            type="button"
            onClick={onClose}
            className="flex-1 rounded-full border border-line-input bg-white px-[14px] py-[10px] text-[13px] font-medium text-ink"
          >
            {t('warming.cfg.cancel')}
          </button>
        </div>
      </div>
    </Modal>
  );
}
