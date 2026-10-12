import { useMutation } from '@tanstack/react-query';
import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { spamCheckAccountMutation } from '@/entities/account';
import { HEADING_ICON_TILE } from '@/shared/design-system';
import { Button, Icon, Modal, SegmentedControl } from '@/shared/ui';

import { GeoConsentNotice, PoolGeoNotice } from './GeoNotice';
import { RangeSlider } from './RangeSlider';
import { StartDelayField } from './StartDelayField';

const MIN = 1;
// The slider's usual end; a longer configured default widens it (see `max` below).
const MAX = 30;
const PRESETS = [7, 14, 30];
// A dot per preset (plus the floor), not per day: thirty dots on one track read as noise.
const TICKS = [MIN, ...PRESETS];
// Only until the board answers; the board's `default_target_days` is the real default.
const FALLBACK_DAYS = 14;

type ActivityPersona = 'calm' | 'normal' | 'active';
const PERSONAS: ActivityPersona[] = ['calm', 'normal', 'active'];

type SpamState = 'idle' | 'loading' | 'clean' | 'limited';

const DEFAULT_PERSONA: ActivityPersona = 'normal';

// The first cycle's default rest: a preset, inside the old cold-start spread.
const DEFAULT_DELAY_HOURS = 3;

// Who the dialog starts: one account (with its @SpamBot pre-check), or the whole idle
// pool, where every account takes the same length, persona and delay window.
type StartTarget =
  | {
      accountId: string;
      phone: string;
      // A phone/proxy country mismatch not yet accepted for this pair (item 2.2).
      geo?: { phoneCountry: string; proxyCountry: string };
      // «Вернуть в прогрев»: the worked days the continued stint keeps (item 2.5).
      resumeDays?: number;
      poolAccountIds?: undefined;
      poolSkipped?: undefined;
      poolGeoMismatch?: undefined;
    }
  | {
      poolAccountIds: string[];
      // Idle accounts left out because they are not ready.
      poolSkipped?: number;
      // How many of `poolAccountIds` still need a geo consent.
      poolGeoMismatch?: number;
      accountId?: undefined;
      phone?: undefined;
      geo?: undefined;
      resumeDays?: undefined;
    };

// What the dialog hands back. `acceptGeo` is the consent to every mismatch it showed;
// `onlyGeoMatching` is the pool's «Только совпадающие».
export interface StartDialogChoice {
  days: number;
  persona: ActivityPersona;
  delayHours: number;
  acceptGeo: boolean;
  onlyGeoMatching: boolean;
}

// The design's "warm account" modal: a draggable day-length slider (1–30) that opens
// on the length a start would run anyway (the board's `default_target_days`), quick
// presets, an activity persona (cadence), the first cycle's delay, and a real
// @SpamBot pre-check. "Запустить пул" opens the same dialog for every idle account.
export function WarmDaysModal({
  defaultDays = FALLBACK_DAYS,
  onClose,
  onConfirm,
  ...target
}: StartTarget & {
  defaultDays?: number;
  onClose: () => void;
  onConfirm: (choice: StartDialogChoice) => void;
}) {
  const pool = target.poolAccountIds !== undefined;
  const resume = target.resumeDays !== undefined;
  const { t } = useTranslation();
  // The configured default is never cut down: a 45-day `warmed_min_days` would
  // otherwise start a 30-day stint just because the operator pressed Start. A resume
  // opens one day past what the stint already worked, so it does not finish at once.
  const initialDays = Math.max(MIN, Math.round(defaultDays), (target.resumeDays ?? 0) + 1);
  const max = Math.max(MAX, initialDays);
  const [days, setDays] = useState(initialDays);
  const [persona, setPersona] = useState<ActivityPersona>(DEFAULT_PERSONA);
  const [delayHours, setDelayHours] = useState(DEFAULT_DELAY_HOURS);
  const [geoAccepted, setGeoAccepted] = useState(false);
  const poolMismatch = target.poolGeoMismatch ?? 0;
  // Consent is given only to a warning the dialog actually showed: the ticked box of a
  // single start, or the pool's «Запустить все».
  const confirm = (onlyGeoMatching: boolean) => {
    const acceptGeo = pool ? poolMismatch > 0 && !onlyGeoMatching : geoAccepted;
    onConfirm({ days, persona, delayHours, acceptGeo, onlyGeoMatching });
    onClose();
  };
  // A length, cadence or delay picked and not yet started.
  const dirty =
    days !== initialDays || persona !== DEFAULT_PERSONA || delayHours !== DEFAULT_DELAY_HOURS;
  const [spam, setSpam] = useState<SpamState>('idle');
  const spamMutation = useMutation(spamCheckAccountMutation());
  const spamTipId = useId();
  const personaTipId = useId();
  const title = pool
    ? t('warming.pool.title')
    : resume
      ? t('warming.resume.title')
      : t('warming.days.title');
  const dayCount = (n: number) => `${String(n)} ${t('warming.days.label', { count: n })}`;

  // Real @SpamBot probe against this account; the result is shown on the pill.
  const runSpamCheck = (accountId: string) => {
    setSpam('loading');
    spamMutation.mutate(
      { path: { account_id: accountId } },
      {
        onSuccess: (verdict) => {
          setSpam(verdict.status === 'clean' ? 'clean' : 'limited');
        },
        onError: () => {
          setSpam('limited');
        },
      },
    );
  };

  return (
    <Modal onClose={onClose} dirty={dirty} size="confirm" label={title}>
      {(close) => (
        <div className="p-6">
          <div className="mb-1 flex items-start gap-3">
            <div className={HEADING_ICON_TILE}>
              <svg
                width="17"
                height="17"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.9"
              >
                <circle cx="12" cy="12" r="3.4" />
                <path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M19.1 4.9 17 7M7 17l-2.1 2.1" />
              </svg>
            </div>
            <div className="flex-1 type-h2">{title}</div>
            {target.accountId === undefined ? null : (
              <span className="tb-tip inline-flex shrink-0">
                {/* Already a tab stop, so `:focus-within` reveals the bubble for free; the
                `aria-describedby` is what names it. See app/styles/index.css. */}
                <button
                  type="button"
                  aria-describedby={spamTipId}
                  disabled={spam === 'loading'}
                  onClick={() => {
                    runSpamCheck(target.accountId);
                  }}
                  className={`inline-flex items-center gap-2 rounded-full border bg-surface-card px-3 py-1 text-body font-medium disabled:opacity-60 ${
                    spam === 'clean'
                      ? 'border-success text-success-deep'
                      : spam === 'limited'
                        ? 'border-danger text-danger'
                        : 'border-line text-content-muted'
                  }`}
                >
                  <Icon name="shield-check" size={14} />
                  {spam === 'loading'
                    ? t('warming.days.spamChecking')
                    : spam === 'clean'
                      ? t('warming.days.spamClean')
                      : spam === 'limited'
                        ? t('warming.days.spamLimited')
                        : t('warming.days.spamCheck')}
                </button>
                <span id={spamTipId} role="tooltip" className="tb-tip-pop">
                  {t('warming.days.spamTip')}
                </span>
              </span>
            )}
          </div>
          <div className="mb-6 type-body text-content-muted">
            {target.poolAccountIds !== undefined
              ? t('warming.pool.subtitle', { count: target.poolAccountIds.length })
              : resume
                ? t('warming.resume.subtitle', { phone: target.phone, count: target.resumeDays })
                : t('warming.days.subtitle', { phone: target.phone })}
            {target.poolSkipped ? (
              <div className="mt-1 text-warning-deep">
                {t('warming.pool.skipped', { count: target.poolSkipped })}
              </div>
            ) : null}
          </div>

          <div className="mb-6 text-center">
            <div className="text-h1 font-medium leading-none text-action-primary">{days}</div>
            <div className="mt-1 type-body text-content-muted">
              {t('warming.days.label', { count: days })}
            </div>
          </div>

          <div className="mb-6">
            <RangeSlider
              value={days}
              min={MIN}
              max={max}
              ticks={TICKS}
              label={t('warming.days.title')}
              valueText={dayCount}
              onChange={setDays}
            />
          </div>

          <SegmentedControl
            variant="outline"
            className="mb-6"
            // The presets are numbers and the control keys on strings, so the value it
            // carries is the number's own text; the handler puts the number back.
            value={String(days)}
            options={PRESETS.map((n) => ({
              value: String(n),
              label: dayCount(n),
            }))}
            onChange={(value) => {
              setDays(Number(value));
            }}
          />

          <div className="mb-2 flex items-center gap-2 type-body-medium">
            {t('warming.persona.label')}
            <span className="tb-tip inline-flex">
              <button
                type="button"
                aria-label={t('warming.persona.label')}
                aria-describedby={personaTipId}
                className="inline-flex size-glyph items-center justify-center rounded-full border border-line text-small font-medium text-content-subtle"
              >
                ?
              </button>
              <span id={personaTipId} role="tooltip" className="tb-tip-pop">
                {t('warming.persona.tip')}
              </span>
            </span>
          </div>
          <SegmentedControl
            variant="outline"
            className="mb-6"
            value={persona}
            ariaLabel={t('warming.persona.label')}
            options={PERSONAS.map((p) => ({
              value: p,
              label: (
                <>
                  <div className="type-body-medium">{t(`warming.persona.${p}.name`)}</div>
                  <div className="mt-1 type-small">{t(`warming.persona.${p}.hint`)}</div>
                </>
              ),
            }))}
            onChange={(p) => {
              setPersona(p);
            }}
          />

          <StartDelayField
            hours={delayHours}
            accountIds={target.poolAccountIds ?? [target.accountId]}
            spread={pool}
            onChange={setDelayHours}
          />

          {target.geo ? (
            <GeoConsentNotice
              phoneCountry={target.geo.phoneCountry}
              proxyCountry={target.geo.proxyCountry}
              accepted={geoAccepted}
              onAcceptedChange={setGeoAccepted}
            />
          ) : null}
          {pool && poolMismatch > 0 ? (
            <PoolGeoNotice count={poolMismatch} total={target.poolAccountIds.length} />
          ) : null}

          {pool && poolMismatch > 0 ? (
            // The pool's one consent: start everyone (accepting each pair), only the
            // accounts whose geo matches, or nothing.
            <div className="flex flex-wrap justify-end gap-2">
              <Button
                variant="primary"
                onClick={() => {
                  confirm(false);
                }}
              >
                {t('warming.geo.startAll')}
              </Button>
              <Button
                disabled={poolMismatch >= target.poolAccountIds.length}
                onClick={() => {
                  confirm(true);
                }}
              >
                {t('warming.geo.onlyMatching')}
              </Button>
              <Button onClick={close}>{t('warming.days.cancel')}</Button>
            </div>
          ) : (
            <div className="flex justify-end gap-2">
              <Button
                variant="primary"
                // A shown geo warning needs its tick; the server never refuses either way.
                disabled={target.geo !== undefined && !geoAccepted}
                onClick={() => {
                  confirm(false);
                }}
              >
                {pool
                  ? t('warming.pool.start')
                  : resume
                    ? t('warming.resume.start')
                    : t('warming.days.start')}
              </Button>
              <Button onClick={close}>{t('warming.days.cancel')}</Button>
            </div>
          )}
        </div>
      )}
    </Modal>
  );
}
