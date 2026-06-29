import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Modal } from '@/shared/ui';

const BEHAVIOR = ['reactions', 'join', 'chat'] as const;
const LIMITS = ['readiness', 'quietHours'] as const;
type Toggle = (typeof BEHAVIOR)[number] | (typeof LIMITS)[number];

// The design's pill switch (track + 20px sliding thumb, 18px travel).
function Switch({ checked, onChange }: { checked: boolean; onChange: () => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={onChange}
      className={`tb-sw relative h-[26px] w-[44px] shrink-0 rounded-full transition-colors ${checked ? 'bg-primary' : 'bg-[#cbc9c4]'}`}
    >
      <span
        className={`tb-sw-thumb absolute top-[3px] block h-5 w-5 rounded-full bg-white shadow-[0_1px_3px_rgba(0,0,0,0.3)] transition-transform ${checked ? 'translate-x-[21px]' : 'translate-x-[3px]'}`}
      />
    </button>
  );
}

function ToggleRow({
  toggle,
  checked,
  onChange,
}: {
  toggle: Toggle;
  checked: boolean;
  onChange: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex items-start justify-between gap-[14px]">
      <div className="min-w-0 flex-1">
        <div className="text-[13px] font-semibold">{t(`warming.cfg.${toggle}.title`)}</div>
        <div className="mt-[2px] text-[11.5px] leading-[1.45] text-ink-subtle">
          {t(`warming.cfg.${toggle}.desc`)}
        </div>
      </div>
      <Switch checked={checked} onChange={onChange} />
    </div>
  );
}

const CLOCK = (
  <svg
    width="14"
    height="14"
    viewBox="0 0 24 24"
    fill="none"
    stroke="#9A9893"
    strokeWidth="2"
    className="mr-1 shrink-0"
  >
    <circle cx="12" cy="12" r="9" />
    <path d="M12 7v5l3 2" />
  </svg>
);

// The design's warming-config modal: behaviour + limits toggle groups, a
// conditional quiet-hours time picker, scope tabs, and save/cancel.
export function WarmConfigModal({ phone, onClose }: { phone: string; onClose: () => void }) {
  const { t } = useTranslation();
  const [flags, setFlags] = useState<Record<Toggle, boolean>>({
    reactions: true,
    join: true,
    chat: false,
    readiness: true,
    quietHours: true,
  });
  const [scope, setScope] = useState<'one' | 'all'>('one');
  const [from, setFrom] = useState({ h: '23', m: '00' });
  const [to, setTo] = useState({ h: '08', m: '00' });
  const flip = (key: Toggle) => {
    setFlags((f) => ({ ...f, [key]: !f[key] }));
  };

  const time = (value: { h: string; m: string }, set: (v: { h: string; m: string }) => void) => (
    <div className="tb-time flex items-center gap-[3px] rounded-[10px] border border-line-input bg-white px-[11px] py-2">
      {CLOCK}
      <input
        value={value.h}
        onChange={(e) => {
          set({ ...value, h: e.target.value });
        }}
        inputMode="numeric"
        maxLength={2}
        className="w-[22px] border-none bg-transparent text-center text-[14px] font-semibold tabular-nums outline-none"
      />
      <span className="text-[14px] font-semibold text-ink-subtle">:</span>
      <input
        value={value.m}
        onChange={(e) => {
          set({ ...value, m: e.target.value });
        }}
        inputMode="numeric"
        maxLength={2}
        className="w-[22px] border-none bg-transparent text-center text-[14px] font-semibold tabular-nums outline-none"
      />
    </div>
  );

  const tab = (active: boolean) =>
    `flex-1 rounded-[8px] py-[7px] text-[12.5px] font-medium transition-colors ${active ? 'bg-white text-ink shadow-sm' : 'text-ink-muted'}`;

  return (
    <Modal onClose={onClose} z={72} className="max-h-[88vh] w-[540px] overflow-y-auto">
      <div className="flex items-center gap-[11px] border-b border-[#f0eeeb] px-6 pb-[15px] pt-5">
        <span className="flex h-[34px] w-[34px] shrink-0 items-center justify-center rounded-[10px] bg-primary-tint text-primary">
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
          {t('warming.cfg.behavior')}
        </div>
        <div className="flex flex-col gap-4">
          {BEHAVIOR.map((key) => (
            <ToggleRow
              key={key}
              toggle={key}
              checked={flags[key]}
              onChange={() => {
                flip(key);
              }}
            />
          ))}
        </div>

        <div className="my-[18px] h-px bg-[#f0eeeb]" />

        <div className="mb-[14px] text-[11px] font-semibold uppercase tracking-[0.04em] text-ink-subtle">
          {t('warming.cfg.limits')}
        </div>
        <div className="flex flex-col gap-4">
          {LIMITS.map((key) => (
            <ToggleRow
              key={key}
              toggle={key}
              checked={flags[key]}
              onChange={() => {
                flip(key);
              }}
            />
          ))}
        </div>

        {flags.quietHours ? (
          <div className="mt-[14px] rounded-[11px] [animation:fadeup_0.28s_ease]">
            <div className="mb-[10px] text-right text-[11.5px] font-semibold text-ink-muted">
              {t('warming.cfg.quietLabel')}
            </div>
            <div className="flex items-center justify-end gap-[10px]">
              {time(from, setFrom)}
              <span className="shrink-0 text-[13px] text-[#b5b3ae]">–</span>
              {time(to, setTo)}
            </div>
            <div className="mt-[9px] text-right text-[11px] leading-[1.4] text-[#b5b3ae]">
              {t('warming.cfg.quietHint')}
            </div>
          </div>
        ) : null}
      </div>

      <div className="border-t border-[#f0eeeb] px-6 pb-5 pt-[15px]">
        <div className="mb-[14px] flex gap-[6px] rounded-[10px] bg-[#f0eeeb] p-[3px]">
          <button
            type="button"
            onClick={() => {
              setScope('one');
            }}
            className={tab(scope === 'one')}
          >
            {t('warming.cfg.scopeOne')}
          </button>
          <button
            type="button"
            onClick={() => {
              setScope('all');
            }}
            className={tab(scope === 'all')}
          >
            {t('warming.cfg.scopeAll')}
          </button>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={onClose}
            className="flex-1 rounded-full border border-primary bg-primary px-[14px] py-[10px] text-[13px] font-semibold text-white transition-colors hover:bg-[#0057db]"
          >
            {t('warming.cfg.save')}
          </button>
          <button
            type="button"
            onClick={onClose}
            className="flex-1 rounded-full border border-line-input bg-white px-[14px] py-[10px] text-[13px] font-medium text-ink transition-colors hover:border-[#c8c6c2] hover:bg-[#f7f6f4]"
          >
            {t('warming.cfg.cancel')}
          </button>
        </div>
      </div>
    </Modal>
  );
}
