import { useId } from 'react';
import { useTranslation } from 'react-i18next';

import { HelpHint, Input, SegmentedControl, Select } from '@/shared/ui';

import type { DiscoveryFormState } from '../model/discovery';
import {
  ACCESS,
  CATEGORIES,
  COMMENTS,
  groupsDisable,
  KINDS,
  LANGUAGES,
  LIMIT_DEFAULT,
  LIMIT_MAX,
  LIMIT_MIN,
  limitInvalid,
  type DiscoveryCategory,
  type DiscoveryLanguage,
} from '../model/filters';
import { Eyebrow, Row } from './FormRow';
import { SubscribersField } from './SubscribersField';

const P = 'neurocomment.modal.discovery.form';
const SEEN = ['hide', 'show'] as const;

type Props = {
  form: DiscoveryFormState;
  onChange: (form: DiscoveryFormState) => void;
};

// Блок «Фильтры»: две колонки строк, как в настройках кампании. / The filters block.
export function DiscoveryFilters({ form, onChange }: Props) {
  const { t } = useTranslation();
  const limitId = useId();
  const seedId = useId();
  const disable = groupsDisable(form.kind);
  const badLimit = limitInvalid(form.limit);

  const set = <K extends keyof DiscoveryFormState>(key: K, value: DiscoveryFormState[K]) => {
    onChange({ ...form, [key]: value });
  };
  const labelled = <T extends string>(group: string, codes: readonly T[]) =>
    codes.map((code) => ({ value: code, label: t(`${P}.${group}.${code}`) }));

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
                // Mirror of the server rule (groups have no comments verdict and never
                // come by subscription), applied to the STATE so the UI never shows a
                // disabled-yet-selected option.
                const groups = kind === 'groups';
                onChange({
                  ...form,
                  kind,
                  comments: groups ? 'any' : form.comments,
                  access: groups && form.access === 'subscription' ? 'any' : form.access,
                });
              }}
            />
          </Row>

          <Row label={t(`${P}.category.label`)}>
            <div className="w-menu">
              <Select
                value={form.category}
                ariaLabel={t(`${P}.category.label`)}
                options={labelled('category', CATEGORIES)}
                onChange={(value) => {
                  set('category', value as DiscoveryCategory);
                }}
              />
            </div>
          </Row>

          <Row label={t(`${P}.language.label`)}>
            <div className="w-menu">
              <Select
                value={form.language}
                ariaLabel={t(`${P}.language.label`)}
                options={labelled('language', LANGUAGES)}
                onChange={(value) => {
                  set('language', value as DiscoveryLanguage);
                }}
              />
            </div>
          </Row>

          <Row
            label={t(`${P}.comments.label`)}
            hint={disable.comments ? t(`${P}.comments.groupsHint`) : undefined}
          >
            <SegmentedControl
              variant="pill"
              value={form.comments}
              disabled={disable.comments}
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
                const off = access === 'subscription' && disable.subscription;
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
              some hits, and the rest enter the list unfiltered. */}
          <Row label={t(`${P}.subscribers`)} hint={t(`${P}.membersHint`)}>
            <SubscribersField form={form} onChange={onChange} />
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
