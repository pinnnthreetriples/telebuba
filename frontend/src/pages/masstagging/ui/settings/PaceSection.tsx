// «Темп»: the last section of the dialog and the whole of the board's gear. Stories set
// mentions per story, stories a day and the pause between them; priming sets people a day
// and the pause between them. The soft start, the registry switch and "stop after" are
// shared; the flood limits fold away under «Защита от блокировок».
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { FOCUS_RING } from '@/shared/design-system';
import { Icon, SegmentedControl, Switch } from '@/shared/ui';

import { MAX_MENTIONS, type Pace } from '../../model/draft';

import { NumberField, RangeField, Row, Section } from './fields';

const MAX_MINUTES = 1440;
const MAX_SECONDS = 3600;

export function PaceSection({
  n,
  stories,
  pace,
  onPatch,
}: {
  n?: number;
  stories: boolean;
  pace: Pace;
  onPatch: (next: Partial<Pace>) => void;
}) {
  const { t } = useTranslation();
  const [safetyOpen, setSafetyOpen] = useState(false);
  const count = pace.stop_mode === 'count';
  return (
    <Section
      n={n}
      title={t('masstagging.settings.pace.title')}
      hint={t('masstagging.settings.pace.hint')}
      example={t('masstagging.settings.pace.example')}
    >
      <div>
        {stories ? (
          <>
            <Row
              first
              label={t('masstagging.settings.pace.mentions')}
              hint={t('masstagging.settings.pace.mentionsHint')}
              example={t('masstagging.settings.pace.mentionsExample')}
            >
              <NumberField
                value={pace.mentions_per_story}
                min={1}
                max={MAX_MENTIONS}
                label={t('masstagging.settings.pace.mentions')}
                onChange={(value) => {
                  onPatch({ mentions_per_story: value });
                }}
              />
            </Row>
            <Row
              label={t('masstagging.settings.pace.stories')}
              hint={t('masstagging.settings.pace.storiesHint')}
              example={t('masstagging.settings.pace.storiesExample')}
            >
              <NumberField
                value={pace.stories_per_day}
                min={1}
                max={100}
                label={t('masstagging.settings.pace.stories')}
                onChange={(value) => {
                  onPatch({ stories_per_day: value });
                }}
              />
            </Row>
            <Row
              label={t('masstagging.settings.pace.storyGap')}
              hint={t('masstagging.settings.pace.gapHint')}
              example={t('masstagging.settings.pace.storyGapExample')}
            >
              <RangeField
                value={pace.story_gap_minutes}
                unit={t('masstagging.settings.min')}
                max={MAX_MINUTES}
                label={t('masstagging.settings.pace.storyGap')}
                onChange={(value) => {
                  onPatch({ story_gap_minutes: value });
                }}
              />
            </Row>
          </>
        ) : (
          <>
            <Row
              first
              label={t('masstagging.settings.pace.dialogs')}
              hint={t('masstagging.settings.pace.dialogsHint')}
              example={t('masstagging.settings.pace.dialogsExample')}
            >
              <NumberField
                value={pace.dialogs_per_day}
                min={1}
                max={200}
                label={t('masstagging.settings.pace.dialogs')}
                onChange={(value) => {
                  onPatch({ dialogs_per_day: value });
                }}
              />
            </Row>
            <Row
              label={t('masstagging.settings.pace.dialogGap')}
              hint={t('masstagging.settings.pace.gapHint')}
              example={t('masstagging.settings.pace.dialogGapExample')}
            >
              <RangeField
                value={pace.dialog_gap_seconds}
                unit={t('masstagging.settings.sec')}
                max={MAX_SECONDS}
                label={t('masstagging.settings.pace.dialogGap')}
                onChange={(value) => {
                  onPatch({ dialog_gap_seconds: value });
                }}
              />
            </Row>
          </>
        )}
        <Row
          label={t('masstagging.settings.pace.ramp')}
          hint={t('masstagging.settings.pace.rampHint')}
          example={t('masstagging.settings.pace.rampExample')}
        >
          <Switch
            checked={pace.ramp_up}
            label={t('masstagging.settings.pace.ramp')}
            onChange={(rampUp) => {
              onPatch({ ramp_up: rampUp });
            }}
          />
        </Row>
        <Row
          label={t('masstagging.settings.pace.skipTagged')}
          hint={t('masstagging.settings.pace.skipTaggedHint')}
        >
          <Switch
            checked={pace.skip_tagged}
            label={t('masstagging.settings.pace.skipTagged')}
            onChange={(skipTagged) => {
              onPatch({ skip_tagged: skipTagged });
            }}
          />
        </Row>
        <Row
          label={t('masstagging.settings.pace.stop')}
          hint={t('masstagging.settings.pace.stopHint')}
          example={t('masstagging.settings.pace.stopExample')}
        >
          <span className="flex items-center gap-2">
            <NumberField
              value={count ? pace.stop_tags : pace.stop_minutes}
              min={0}
              max={100_000}
              label={t('masstagging.settings.pace.stop')}
              onChange={(value) => {
                onPatch(count ? { stop_tags: value } : { stop_minutes: value });
              }}
            />
            <SegmentedControl
              variant="pill"
              value={pace.stop_mode}
              ariaLabel={t('masstagging.settings.pace.stopUnit')}
              options={[
                { value: 'count', label: t('masstagging.settings.pace.stopTags') },
                { value: 'time', label: t('masstagging.settings.pace.stopMinutes') },
              ]}
              onChange={(stopMode) => {
                onPatch({ stop_mode: stopMode });
              }}
            />
          </span>
        </Row>
      </div>
      <div className="rounded-md border border-line">
        <button
          type="button"
          aria-expanded={safetyOpen}
          onClick={() => {
            setSafetyOpen((on) => !on);
          }}
          className={`flex w-full items-center gap-2 rounded-md px-4 py-3 text-left ${FOCUS_RING}`}
        >
          <Icon name="shield-check" size={16} />
          <span className="type-body-medium">{t('masstagging.settings.pace.safety')}</span>
          <span className="type-small">
            {t('masstagging.settings.pace.safetySummary', {
              seconds: pace.flood_pause_seconds,
              count: pace.floods_to_rest,
            })}
          </span>
          <span className={`ml-auto flex transition-transform ${safetyOpen ? 'rotate-180' : ''}`}>
            <Icon name="chevron-down" size={16} />
          </span>
        </button>
        {safetyOpen ? (
          <div className="border-t border-canvas px-4 pb-2">
            <Row
              first
              label={t('masstagging.settings.pace.floodPause')}
              hint={t('masstagging.settings.pace.floodPauseHint')}
              example={t('masstagging.settings.pace.floodPauseExample')}
            >
              <NumberField
                value={pace.flood_pause_seconds}
                min={60}
                max={86_400}
                unit={t('masstagging.settings.sec')}
                label={t('masstagging.settings.pace.floodPause')}
                onChange={(value) => {
                  onPatch({ flood_pause_seconds: value });
                }}
              />
            </Row>
            <Row
              label={t('masstagging.settings.pace.floodsToRest')}
              hint={t('masstagging.settings.pace.floodsToRestHint')}
              example={t('masstagging.settings.pace.floodsToRestExample')}
            >
              <NumberField
                value={pace.floods_to_rest}
                min={1}
                max={20}
                label={t('masstagging.settings.pace.floodsToRest')}
                onChange={(value) => {
                  onPatch({ floods_to_rest: value });
                }}
              />
            </Row>
          </div>
        ) : null}
      </div>
    </Section>
  );
}
