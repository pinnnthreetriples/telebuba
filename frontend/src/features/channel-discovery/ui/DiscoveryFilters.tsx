import { useId } from 'react';
import { useTranslation } from 'react-i18next';

import { HelpHint, Input, SegmentedControl, Select } from '@/shared/ui';

import { boundsInverted, type DiscoveryFormState } from '../model/discovery';
import {
  ACCESS,
  CATEGORIES,
  COMMENTS,
  KINDS,
  LANGUAGES,
  LIMIT_DEFAULT,
  LIMIT_MAX,
  LIMIT_MIN,
  normalizeForKind,
  parseLimit,
} from '../model/filters';
import { Eyebrow, Row } from './FormRow';

const P = 'neurocomment.modal.discovery.form';
const SEEN = ['hide', 'show'] as const;

type Setter = <K extends keyof DiscoveryFormState>(key: K, value: DiscoveryFormState[K]) => void;

// Категория и язык — одна и та же строка с одним Select. Список кодов привязан к полю
// типом, так что выбранное значение сверяется с ним, а не приводится через `as`.
// / One row shape for both list filters; the codes are typed by the field, so no cast.
function SelectRow<K extends 'category' | 'language'>({
  field,
  codes,
  form,
  set,
}: {
  field: K;
  codes: readonly DiscoveryFormState[K][];
  form: DiscoveryFormState;
  set: Setter;
}) {
  const { t } = useTranslation();
  return (
    <Row label={t(`${P}.${field}.label`)}>
      <div className="w-menu">
        <Select
          value={form[field]}
          ariaLabel={t(`${P}.${field}.label`)}
          options={codes.map((code) => ({ value: code, label: t(`${P}.${field}.${code}`) }))}
          onChange={(value) => {
            const code = codes.find((known) => known === value);
            if (code !== undefined) set(field, code);
          }}
        />
      </div>
    </Row>
  );
}

type Props = {
  form: DiscoveryFormState;
  onChange: (form: DiscoveryFormState) => void;
};

// Блок «Фильтры»: две колонки строк, как в настройках кампании. / The filters block.
export function DiscoveryFilters({ form, onChange }: Props) {
  const { t } = useTranslation();
  const limitId = useId();
  const seedId = useId();
  const groups = form.kind === 'groups';
  const badLimit = parseLimit(form.limit) === undefined;
  const inverted = boundsInverted(form);

  const set: Setter = (key, value) => {
    onChange({ ...form, [key]: value });
  };
  const labelled = <T extends string>(group: string, codes: readonly T[]) =>
    codes.map((code) => ({ value: code, label: t(`${P}.${group}.${code}`) }));

  // Groups have no comments verdict, so with kind 'all' the filter leaves them in.
  const commentsHint = groups
    ? t(`${P}.comments.groupsHint`)
    : form.kind === 'all' && form.comments !== 'any'
      ? t(`${P}.comments.allHint`)
      : undefined;

  return (
    <section>
      <Eyebrow title={t(`${P}.sections.filters`)} />
      <div className="grid gap-xl border-t border-line pt-lg sm:grid-cols-2 sm:gap-2xl sm:divide-x sm:divide-line">
        <div className="min-w-0 sm:pr-2xl">
          <Row first label={t(`${P}.kind.label`)}>
            <SegmentedControl
              variant="pill"
              value={form.kind}
              ariaLabel={t(`${P}.kind.label`)}
              options={labelled('kind', KINDS)}
              onChange={(kind) => {
                // Applied to the STATE so the UI never shows a disabled-yet-selected option.
                onChange(normalizeForKind({ ...form, kind }));
              }}
            />
          </Row>

          <SelectRow field="category" codes={CATEGORIES} form={form} set={set} />
          <SelectRow field="language" codes={LANGUAGES} form={form} set={set} />

          <Row label={t(`${P}.comments.label`)} hint={commentsHint}>
            <SegmentedControl
              variant="pill"
              value={form.comments}
              disabled={groups}
              ariaLabel={t(`${P}.comments.label`)}
              options={labelled('comments', COMMENTS)}
              onChange={(comments) => {
                set('comments', comments);
              }}
            />
          </Row>
        </div>

        <div className="min-w-0 sm:pl-2xl">
          <Row first label={t(`${P}.access.label`)}>
            <SegmentedControl
              variant="pill"
              value={form.access}
              ariaLabel={t(`${P}.access.label`)}
              options={ACCESS.map((access) => {
                const off = access === 'subscription' && groups;
                return {
                  value: access,
                  label: t(`${P}.access.${access}`),
                  disabled: off,
                  title: off ? t(`${P}.access.groupsHint`) : undefined,
                };
              })}
              onChange={(access) => {
                set('access', access);
              }}
            />
          </Row>

          {/* What the bounds actually do: Telegram returns a subscriber count for only
              some hits, and the rest enter the list unfiltered. The error line wraps onto
              its own row via `basis-full`. */}
          <Row label={t(`${P}.subscribers`)} hint={t(`${P}.membersHint`)}>
            <div className="flex items-center gap-sm">
              <Input
                size="xs"
                type="number"
                min={0}
                className="w-number tabular-nums"
                aria-label={t(`${P}.minSubscribers`)}
                placeholder="0"
                invalid={inverted}
                value={form.minSubscribers}
                onChange={(event) => {
                  set('minSubscribers', event.target.value);
                }}
              />
              <span className="type-caption">—</span>
              <Input
                size="xs"
                type="number"
                min={0}
                className="w-number tabular-nums"
                aria-label={t(`${P}.maxSubscribers`)}
                placeholder="∞"
                invalid={inverted}
                value={form.maxSubscribers}
                onChange={(event) => {
                  set('maxSubscribers', event.target.value);
                }}
              />
            </div>
            {/* The API refuses members_min > members_max, and canSubmit blocks it — without
                this the Search button would just go dead naming no field. */}
            {inverted ? (
              <p className="basis-full type-caption text-danger">{t(`${P}.boundsInverted`)}</p>
            ) : null}
          </Row>

          <Row label={t(`${P}.hideSeen.label`)} hint={t(`${P}.hideSeen.hint`)}>
            <SegmentedControl
              variant="pill"
              value={form.hideSeen ? 'hide' : 'show'}
              ariaLabel={t(`${P}.hideSeen.label`)}
              options={labelled('hideSeen', SEEN)}
              onChange={(seen) => {
                set('hideSeen', seen === 'hide');
              }}
            />
          </Row>

          <Row
            label={t(`${P}.limit.label`)}
            hint={t(`${P}.limit.hint`, { min: LIMIT_MIN, max: LIMIT_MAX, default: LIMIT_DEFAULT })}
            htmlFor={limitId}
          >
            <Input
              id={limitId}
              size="xs"
              type="number"
              min={LIMIT_MIN}
              max={LIMIT_MAX}
              className="w-number tabular-nums"
              placeholder={String(LIMIT_DEFAULT)}
              invalid={badLimit}
              value={form.limit}
              onChange={(event) => {
                set('limit', event.target.value);
              }}
            />
            {badLimit ? (
              <p className="basis-full type-caption text-danger">
                {t(`${P}.limit.invalid`, { min: LIMIT_MIN, max: LIMIT_MAX })}
              </p>
            ) : null}
          </Row>

          {/* The HelpHint sits OUTSIDE the <label>: nested, its prose joined the
              field's accessible name. */}
          <Row label={t(`${P}.seedChannel`)} htmlFor={seedId}>
            <HelpHint text={t(`${P}.seedChannelHint`)} />
            <Input
              id={seedId}
              size="sm"
              className="w-menu"
              value={form.seedChannel}
              placeholder={t(`${P}.seedChannelPlaceholder`)}
              onChange={(event) => {
                set('seedChannel', event.target.value);
              }}
            />
          </Row>
        </div>
      </div>
    </section>
  );
}
