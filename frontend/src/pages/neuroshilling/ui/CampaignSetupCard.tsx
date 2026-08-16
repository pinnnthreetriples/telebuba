import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { CollapsibleCard, HelpHint, Switch } from '@/shared/ui';

import type { SetupDraft } from './setupDraft';
import {
  advancedChangeCount,
  clampInt,
  countTargets,
  MAX_LISTEN_MINUTES,
  MAX_MESSAGES_PER_CHAT_PER_DAY,
  MAX_MESSAGES_PER_HOUR,
  MAX_PAUSE_SECONDS,
  MAX_TARGETS_RAW,
  MAX_TOTAL_PER_ACCOUNT,
} from './setupDraft';

const FIELD =
  'w-full rounded-[10px] border border-line-input bg-white px-[11px] py-[8px] text-[12.5px] outline-none focus:border-primary disabled:bg-[#f4f3f0] disabled:text-ink-subtle';
const NUMBER =
  'w-[78px] rounded-[9px] border border-line-input bg-white px-[9px] py-[6px] text-[12px] tabular-nums outline-none focus:border-primary disabled:bg-[#f4f3f0] disabled:text-ink-subtle';
const SEGMENT = 'rounded-full px-[13px] py-[5px] text-[12px] font-medium disabled:opacity-60';

// One labelled numeric box. Inline rather than a shared primitive: five of them
// live on this card and nowhere else, and `shared/ui` has no numeric field.
function NumberField({
  label,
  hint,
  value,
  min,
  max,
  placeholder,
  disabled,
  onChange,
}: {
  label: string;
  hint?: string;
  value: string;
  min: number;
  max: number;
  placeholder?: string;
  disabled?: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <div className="flex items-center gap-[7px]">
      {/* A <span>, not a <label>: the input carries its own `aria-label`, and a
          second label element for the same field only makes the name ambiguous. */}
      <span className="min-w-0 flex-1 text-[12.5px]">{label}</span>
      {hint ? <HelpHint text={hint} /> : null}
      <input
        type="number"
        min={min}
        max={max}
        value={value}
        disabled={disabled}
        placeholder={placeholder}
        aria-label={label}
        onChange={(event) => {
          onChange(event.target.value);
        }}
        className={NUMBER}
      />
    </div>
  );
}

// Card 4: everything that decides HOW the dialogue is played — where, in what
// order, and how hard.
//
// Zero hooks besides `useTranslation` and the advanced panel's own open/closed
// flag: the draft and the save live on the page, because the board query behind
// them is refetched by the log stream and a card that seeded itself from props
// would be emptied under the operator's hands.
export function CampaignSetupCard({
  draft,
  onDraft,
  dirty,
  reserveCount,
  live,
  onSave,
  busy,
}: {
  draft: SetupDraft;
  onDraft: (draft: SetupDraft) => void;
  dirty: boolean;
  // Rostered accounts still waiting in the pool. A number rather than the roster,
  // because the card takes no server data of its own.
  reserveCount: number;
  // A run in flight: the server refuses the whole PUT with `campaign_running`,
  // so the card says so instead of letting the operator collect a 409.
  live: boolean;
  onSave: () => void;
  busy: boolean;
}) {
  const { t } = useTranslation();
  const [advanced, setAdvanced] = useState(false);
  const changed = advancedChangeCount(draft);
  const targets = countTargets(draft.targetsRaw);

  return (
    <CollapsibleCard
      defaultOpen
      label={t('neuroshilling.setup.title')}
      headerClassName="px-4 py-[15px]"
      bodyClassName="px-4 pb-[15px]"
      header={<span className="text-[13px] font-semibold">{t('neuroshilling.setup.title')}</span>}
      trailing={
        dirty ? (
          <span className="shrink-0 rounded-full bg-[#fdf4e3] px-[10px] py-[3px] text-[11px] font-semibold text-warning">
            {t('neuroshilling.setup.unsaved')}
          </span>
        ) : null
      }
    >
      {live ? (
        <div className="mb-[10px] rounded-[10px] bg-[#fdf4e3] px-[11px] py-[7px] text-[11.5px] text-warning">
          {t('neuroshilling.setup.liveLocked')}
        </div>
      ) : null}

      <span className="mb-[5px] flex items-center gap-[6px] text-[12px] font-medium text-ink-muted">
        {t('neuroshilling.setup.targets.label')}
        <HelpHint text={t('neuroshilling.setup.targets.hint')} />
        <span className="ml-auto rounded-full bg-[#f4f3f0] px-[9px] py-[2px] text-[11px] font-medium tabular-nums text-ink-muted">
          {/* `n`, not `count`: an i18next `count` switches on plural forms this
              key does not carry, and Russian would need four of them to read right. */}
          {t('neuroshilling.setup.targets.count', { n: targets })}
        </span>
      </span>
      <textarea
        rows={4}
        value={draft.targetsRaw}
        maxLength={MAX_TARGETS_RAW}
        disabled={live}
        placeholder={t('neuroshilling.setup.targets.placeholder')}
        aria-label={t('neuroshilling.setup.targets.label')}
        onChange={(event) => {
          onDraft({ ...draft, targetsRaw: event.target.value });
        }}
        className={`${FIELD} mb-[14px] resize-none font-mono text-[12px] leading-[1.6]`}
      />

      <span className="mb-[5px] block text-[12px] font-medium text-ink-muted">
        {t('neuroshilling.setup.runMode.label')}
      </span>
      <div
        role="radiogroup"
        aria-label={t('neuroshilling.setup.runMode.label')}
        className="mb-[14px] grid gap-[8px] sm:grid-cols-2"
      >
        {(['sequential', 'parallel'] as const).map((mode) => {
          const unavailable = mode === 'parallel';
          const picked = draft.runMode === mode;
          return (
            <button
              key={mode}
              type="button"
              role="radio"
              aria-checked={picked}
              // Not merely hidden: the generated client types the field, and the
              // server answers 400 `run_mode_not_supported` on the save and 409 on
              // the start. Disabled with the reason under it is the only shape that
              // tells the operator that before they click.
              disabled={unavailable || live}
              onClick={() => {
                onDraft({ ...draft, runMode: mode });
              }}
              className={`rounded-[11px] border p-[11px] text-left disabled:opacity-60 ${picked ? 'border-primary bg-primary/[0.06]' : 'border-line bg-white'}`}
            >
              <span className="block text-[12.5px] font-semibold">
                {t(`neuroshilling.setup.runMode.${mode}.title`)}
              </span>
              <span className="mt-[3px] block text-[11.5px] leading-snug text-ink-subtle">
                {t(
                  unavailable
                    ? 'neuroshilling.setup.runMode.parallel.unavailable'
                    : `neuroshilling.setup.runMode.${mode}.body`,
                )}
              </span>
            </button>
          );
        })}
      </div>

      {/* «Пауза между целями, сек», not the mockup's «Пауза (мин)» / «Пауза (макс)»:
          the two numbers are a MINIMUM and a MAXIMUM, and the unit is seconds. The
          mockup's wording reads as minutes beside a value like `10с`. */}
      <span className="mb-[5px] flex items-center gap-[6px] text-[12px] font-medium text-ink-muted">
        {t('neuroshilling.setup.pause.label')}
        <HelpHint text={t('neuroshilling.setup.pause.hint')} />
      </span>
      <div className="mb-[14px] flex flex-wrap items-center gap-[9px]">
        {(['min', 'max'] as const).map((bound) => (
          <span key={bound} className="flex items-center gap-[6px] text-[11.5px] text-ink-subtle">
            {t(`neuroshilling.setup.pause.${bound}`)}
            <input
              type="number"
              min={0}
              max={MAX_PAUSE_SECONDS}
              disabled={live}
              value={bound === 'min' ? draft.pauseMinSeconds : draft.pauseMaxSeconds}
              aria-label={t(`neuroshilling.setup.pause.${bound}Label`)}
              onChange={(event) => {
                // Clamped in PAIRS: `pause_min > pause_max` is a model validator
                // error, which reaches the operator as an unreadable 422 blob.
                const value = clampInt(Number(event.target.value), 0, MAX_PAUSE_SECONDS);
                onDraft(
                  bound === 'min'
                    ? {
                        ...draft,
                        pauseMinSeconds: value,
                        pauseMaxSeconds: Math.max(value, draft.pauseMaxSeconds),
                      }
                    : {
                        ...draft,
                        pauseMaxSeconds: value,
                        pauseMinSeconds: Math.min(value, draft.pauseMinSeconds),
                      },
                );
              }}
              className={NUMBER}
            />
          </span>
        ))}
      </div>

      <button
        type="button"
        aria-expanded={advanced}
        onClick={() => {
          setAdvanced((value) => !value);
        }}
        className="mb-[10px] flex w-full items-center gap-[7px] rounded-[10px] border border-line bg-[#faf9f7] px-[11px] py-[8px] text-[12.5px] font-medium"
      >
        {t('neuroshilling.setup.advanced.title')}
        {changed > 0 ? (
          <span className="rounded-full bg-primary-tint px-[8px] py-[1px] text-[11px] font-semibold tabular-nums text-primary">
            {changed}
          </span>
        ) : null}
        <span className="ml-auto text-ink-subtle">{advanced ? '−' : '+'}</span>
      </button>

      {advanced ? (
        <div className="mb-[14px] flex flex-col gap-[10px] rounded-[11px] border border-line bg-white p-[12px]">
          <NumberField
            label={t('neuroshilling.setup.perHour.label')}
            hint={t('neuroshilling.setup.perHour.hint')}
            value={String(draft.messagesPerHour)}
            min={1}
            max={MAX_MESSAGES_PER_HOUR}
            disabled={live}
            onChange={(value) => {
              onDraft({
                ...draft,
                messagesPerHour: clampInt(Number(value), 1, MAX_MESSAGES_PER_HOUR),
              });
            }}
          />
          <NumberField
            label={t('neuroshilling.setup.perChat.label')}
            hint={t('neuroshilling.setup.perChat.hint')}
            value={String(draft.messagesPerChatPerDay)}
            min={0}
            max={MAX_MESSAGES_PER_CHAT_PER_DAY}
            disabled={live}
            onChange={(value) => {
              onDraft({
                ...draft,
                messagesPerChatPerDay: clampInt(Number(value), 0, MAX_MESSAGES_PER_CHAT_PER_DAY),
              });
            }}
          />
          <NumberField
            label={t('neuroshilling.setup.total.label')}
            hint={t('neuroshilling.setup.total.hint')}
            placeholder={t('neuroshilling.setup.total.unlimited')}
            value={draft.totalPerAccount === null ? '' : String(draft.totalPerAccount)}
            min={1}
            max={MAX_TOTAL_PER_ACCOUNT}
            disabled={live}
            onChange={(value) => {
              // Empty is "no ceiling" and travels as null. Zero would be refused by
              // the wire's `ge=1`, so an emptied box must never become one.
              onDraft({
                ...draft,
                totalPerAccount:
                  value.trim() === '' ? null : clampInt(Number(value), 1, MAX_TOTAL_PER_ACCOUNT),
              });
            }}
          />
          <div className="flex items-center gap-[8px]">
            <Switch
              checked={draft.reserveEnabled}
              disabled={live}
              label={t('neuroshilling.setup.reserve.label')}
              onChange={(value) => {
                onDraft({ ...draft, reserveEnabled: value });
              }}
            />
            <span className="text-[12.5px]">{t('neuroshilling.setup.reserve.label')}</span>
            <HelpHint text={t('neuroshilling.setup.reserve.hint')} />
            {/* The pool as it stands NOW, not as the roster was arranged: a promoted
                account has its reserve flag cleared, so this drops by one on every
                substitution and reaching zero is the warning the operator needs. */}
            <span className="ml-auto rounded-full bg-[#f4f3f0] px-[9px] py-[2px] text-[11px] font-medium tabular-nums text-ink-muted">
              {t('neuroshilling.setup.reserve.count', { n: reserveCount })}
            </span>
          </div>

          {/* The listening block: the three switches that let the run READ its
              target chats, and the window it keeps reading for. */}
          <div className="mt-[2px] flex items-center gap-[7px] border-t border-line pt-[11px] text-[12px] font-semibold">
            {t('neuroshilling.setup.listening.title')}
            <HelpHint text={t('neuroshilling.setup.listening.hint')} />
          </div>

          {(
            [
              ['autoresponder', ['off', 'neurodialog'], draft.autoresponder],
              ['replyActivity', ['calm', 'medium', 'active'], draft.replyActivity],
            ] as const
          ).map(([field, options, current]) => (
            <div key={field} className="flex flex-wrap items-center gap-[7px]">
              <span className="min-w-0 flex-1 text-[12.5px]">
                {t(`neuroshilling.setup.${field}.label`)}
              </span>
              <div
                role="radiogroup"
                aria-label={t(`neuroshilling.setup.${field}.label`)}
                className="inline-flex rounded-full border border-line-input bg-[#f4f3f0] p-[3px]"
              >
                {options.map((option) => (
                  <button
                    key={option}
                    type="button"
                    role="radio"
                    aria-checked={current === option}
                    disabled={live}
                    onClick={() => {
                      onDraft(
                        field === 'autoresponder'
                          ? { ...draft, autoresponder: option as SetupDraft['autoresponder'] }
                          : { ...draft, replyActivity: option as SetupDraft['replyActivity'] },
                      );
                    }}
                    className={`${SEGMENT} ${current === option ? 'bg-white text-ink' : 'text-ink-subtle'}`}
                  >
                    {t(`neuroshilling.setup.${field}.${option}`)}
                  </button>
                ))}
              </div>
            </div>
          ))}

          <div className="flex items-center gap-[8px]">
            <Switch
              disabled={live}
              checked={draft.replyToHumans}
              label={t('neuroshilling.setup.replyToHumans.label')}
              onChange={(value) => {
                onDraft({ ...draft, replyToHumans: value });
              }}
            />
            <span className="text-[12.5px]">{t('neuroshilling.setup.replyToHumans.label')}</span>
            <HelpHint text={t('neuroshilling.setup.replyToHumans.hint')} />
          </div>

          {/* Shown only once BOTH switches are on, because that is the only
              combination that publishes anything a stranger's message provoked —
              and it is the one thing on this page an outsider gets a say in. */}
          {draft.replyToHumans && draft.autoresponder === 'neurodialog' ? (
            <div className="rounded-[10px] bg-[#fdf4e3] px-[11px] py-[7px] text-[11.5px] leading-snug text-warning">
              {t('neuroshilling.setup.replyToHumans.warning')}
            </div>
          ) : null}

          <NumberField
            label={t('neuroshilling.setup.listen.label')}
            hint={t('neuroshilling.setup.listen.hint')}
            value={String(draft.listenMinutes)}
            min={1}
            max={MAX_LISTEN_MINUTES}
            disabled={live}
            onChange={(value) => {
              onDraft({ ...draft, listenMinutes: clampInt(Number(value), 1, MAX_LISTEN_MINUTES) });
            }}
          />
        </div>
      ) : null}

      <div className="flex items-center justify-end">
        <button
          type="button"
          disabled={busy || live || !dirty}
          onClick={onSave}
          className="rounded-full bg-primary px-[16px] py-[9px] text-[12.5px] font-semibold text-white disabled:opacity-50"
        >
          {t('neuroshilling.setup.save')}
        </button>
      </div>
    </CollapsibleCard>
  );
}
