// Pauses, volume and rounds, safety. Every control writes the one draft; nothing here
// keeps a number of its own, so the check window shows what was set.
import { useTranslation } from 'react-i18next';

import { SegmentedControl, Switch } from '@/shared/ui';

import type { Settings } from '../../model/draft';

import { Eyebrow, NumberField, RangeField, Row } from './fields';

const APPROVAL_WAITS = [1, 3, 6, 12, 24] as const;
const JOIN_DELAYS = [0, 30, 60, 120] as const;
const MAX_SECONDS = 3600;
const MAX_MINUTES = 1440;

function Pauses({
  settings,
  onPatch,
}: {
  settings: Settings;
  onPatch: (next: Partial<Settings>) => void;
}) {
  const { t } = useTranslation();
  const sec = t('chatBroadcast.settings.pace.sec');
  return (
    <div className="min-w-0 sm:pr-2xl">
      <Eyebrow title={t('chatBroadcast.settings.pace.pauses')} />
      <Row
        first
        stack
        label={t('chatBroadcast.settings.pace.approval')}
        hint={t('chatBroadcast.settings.pace.approvalHint')}
        example={t('chatBroadcast.settings.pace.approvalExample')}
      >
        <SegmentedControl
          variant="pill"
          value={String(settings.approval_wait_hours)}
          ariaLabel={t('chatBroadcast.settings.pace.approval')}
          options={APPROVAL_WAITS.map((hours) => ({
            value: String(hours),
            label: t('chatBroadcast.settings.pace.hours', { count: hours }),
          }))}
          onChange={(value) => {
            onPatch({ approval_wait_hours: Number(value) as Settings['approval_wait_hours'] });
          }}
        />
      </Row>
      <Row
        stack
        label={t('chatBroadcast.settings.pace.joinDelay')}
        hint={t('chatBroadcast.settings.pace.joinDelayHint')}
        example={t('chatBroadcast.settings.pace.joinDelayExample')}
      >
        <SegmentedControl
          variant="pill"
          value={String(settings.join_delay_minutes)}
          ariaLabel={t('chatBroadcast.settings.pace.joinDelay')}
          options={JOIN_DELAYS.map((minutes) => ({
            value: String(minutes),
            label: t(`chatBroadcast.settings.pace.delay${String(minutes)}`),
          }))}
          onChange={(value) => {
            onPatch({ join_delay_minutes: Number(value) as Settings['join_delay_minutes'] });
          }}
        />
      </Row>
      <Row
        label={t('chatBroadcast.settings.pace.betweenChats')}
        hint={t('chatBroadcast.settings.pace.betweenChatsHint')}
        example={t('chatBroadcast.settings.pace.betweenChatsExample')}
      >
        <RangeField
          value={settings.between_chats}
          unit={sec}
          max={MAX_SECONDS}
          label={t('chatBroadcast.settings.pace.betweenChats')}
          onChange={(value) => {
            onPatch({ between_chats: value });
          }}
        />
      </Row>
      <Row
        label={t('chatBroadcast.settings.pace.betweenMessages')}
        hint={t('chatBroadcast.settings.pace.betweenMessagesHint')}
        example={t('chatBroadcast.settings.pace.betweenMessagesExample')}
      >
        <RangeField
          value={settings.between_messages}
          unit={sec}
          max={MAX_SECONDS}
          label={t('chatBroadcast.settings.pace.betweenMessages')}
          onChange={(value) => {
            onPatch({ between_messages: value });
          }}
        />
      </Row>
      <Row
        label={t('chatBroadcast.settings.pace.typing')}
        hint={t('chatBroadcast.settings.pace.typingHint')}
      >
        <Switch
          checked={settings.typing}
          onChange={(value) => {
            onPatch({ typing: value });
          }}
          label={t('chatBroadcast.settings.pace.typing')}
        />
      </Row>
    </div>
  );
}

function Volume({
  settings,
  onPatch,
}: {
  settings: Settings;
  onPatch: (next: Partial<Settings>) => void;
}) {
  const { t } = useTranslation();
  const byCount = settings.stop_mode === 'count';
  return (
    <div className="min-w-0 sm:pl-2xl">
      <Eyebrow title={t('chatBroadcast.settings.pace.volume')} />
      <Row
        first
        label={t('chatBroadcast.settings.pace.stop')}
        hint={t('chatBroadcast.settings.pace.stopHint')}
        example={t('chatBroadcast.settings.pace.stopExample')}
      >
        <SegmentedControl
          variant="pill"
          value={settings.stop_mode}
          ariaLabel={t('chatBroadcast.settings.pace.stopLabel')}
          options={[
            { value: 'count', label: t('chatBroadcast.settings.pace.stopCount') },
            { value: 'time', label: t('chatBroadcast.settings.pace.stopTime') },
          ]}
          onChange={(value) => {
            onPatch({ stop_mode: value });
          }}
        />
      </Row>
      <Row
        label={
          byCount
            ? t('chatBroadcast.settings.pace.messagesCount')
            : t('chatBroadcast.settings.pace.hoursCount')
        }
      >
        <NumberField
          value={byCount ? settings.stop_messages : settings.stop_hours}
          min={1}
          max={byCount ? 1_000_000 : 24 * 365}
          label={
            byCount
              ? t('chatBroadcast.settings.pace.messagesCount')
              : t('chatBroadcast.settings.pace.hoursCount')
          }
          onChange={(value) => {
            onPatch(byCount ? { stop_messages: value } : { stop_hours: value });
          }}
        />
      </Row>
      <Row
        label={t('chatBroadcast.settings.pace.loop')}
        hint={t('chatBroadcast.settings.pace.loopHint')}
        example={t('chatBroadcast.settings.pace.loopExample')}
      >
        <Switch
          checked={settings.loop}
          onChange={(value) => {
            onPatch({ loop: value });
          }}
          label={t('chatBroadcast.settings.pace.loop')}
        />
      </Row>
      {settings.loop ? (
        <>
          <Row label={t('chatBroadcast.settings.pace.rest')}>
            <RangeField
              value={settings.rest_minutes}
              unit={t('chatBroadcast.settings.pace.min')}
              max={MAX_MINUTES}
              label={t('chatBroadcast.settings.pace.rest')}
              onChange={(value) => {
                onPatch({ rest_minutes: value });
              }}
            />
          </Row>
          <Row
            label={t('chatBroadcast.settings.pace.rounds')}
            hint={t('chatBroadcast.settings.pace.roundsHint')}
          >
            <NumberField
              value={settings.rounds}
              min={0}
              max={10_000}
              label={t('chatBroadcast.settings.pace.rounds')}
              onChange={(value) => {
                onPatch({ rounds: value });
              }}
            />
          </Row>
        </>
      ) : null}
    </div>
  );
}

function Safety({
  settings,
  onPatch,
}: {
  settings: Settings;
  onPatch: (next: Partial<Settings>) => void;
}) {
  const { t } = useTranslation();
  const toggle = (key: 'skip_already_written' | 'skip_errors' | 'skip_deleted', label: string) => (
    <Switch
      checked={settings[key]}
      onChange={(value) => {
        onPatch({ [key]: value });
      }}
      label={label}
    />
  );
  return (
    <div className="mt-lg border-t border-line pt-lg">
      <Eyebrow title={t('chatBroadcast.settings.pace.safety')} />
      <Row
        first
        label={t('chatBroadcast.settings.pace.skipWritten')}
        hint={t('chatBroadcast.settings.pace.skipWrittenHint')}
        example={t('chatBroadcast.settings.pace.skipWrittenExample')}
      >
        {toggle('skip_already_written', t('chatBroadcast.settings.pace.skipWritten'))}
      </Row>
      <Row
        label={t('chatBroadcast.settings.pace.limit')}
        hint={t('chatBroadcast.settings.pace.limitHint')}
        example={t('chatBroadcast.settings.pace.limitExample')}
      >
        {settings.account_limit ? (
          <div className="flex items-center gap-sm">
            <NumberField
              value={settings.per_hour}
              min={1}
              max={10_000}
              label={t('chatBroadcast.settings.pace.perHour')}
              onChange={(value) => {
                onPatch({ per_hour: value });
              }}
            />
            <span className="type-caption">{t('chatBroadcast.settings.pace.perHour')}</span>
            <NumberField
              value={settings.per_day}
              min={1}
              max={100_000}
              label={t('chatBroadcast.settings.pace.perDay')}
              onChange={(value) => {
                onPatch({ per_day: value });
              }}
            />
            <span className="type-caption">{t('chatBroadcast.settings.pace.perDay')}</span>
          </div>
        ) : null}
        <Switch
          checked={settings.account_limit}
          onChange={(value) => {
            onPatch({ account_limit: value });
          }}
          label={t('chatBroadcast.settings.pace.limit')}
        />
      </Row>
      <Row
        label={t('chatBroadcast.settings.pace.skipErrors')}
        hint={t('chatBroadcast.settings.pace.skipErrorsHint')}
        example={t('chatBroadcast.settings.pace.skipErrorsExample')}
      >
        {toggle('skip_errors', t('chatBroadcast.settings.pace.skipErrors'))}
      </Row>
      <Row
        label={t('chatBroadcast.settings.pace.skipDeleted')}
        hint={t('chatBroadcast.settings.pace.skipDeletedHint')}
        example={t('chatBroadcast.settings.pace.skipDeletedExample')}
      >
        {toggle('skip_deleted', t('chatBroadcast.settings.pace.skipDeleted'))}
      </Row>
      <Row
        label={t('chatBroadcast.settings.pace.errors')}
        hint={t('chatBroadcast.settings.pace.errorsHint')}
        example={t('chatBroadcast.settings.pace.errorsExample')}
      >
        <NumberField
          value={settings.max_consecutive_errors}
          min={0}
          max={10_000}
          label={t('chatBroadcast.settings.pace.errors')}
          onChange={(value) => {
            onPatch({ max_consecutive_errors: value });
          }}
        />
      </Row>
    </div>
  );
}

export function PaceSection({
  settings,
  onPatch,
}: {
  settings: Settings;
  onPatch: (next: Partial<Settings>) => void;
}) {
  return (
    <section>
      <div className="grid gap-xl sm:grid-cols-2 sm:gap-2xl sm:divide-x sm:divide-line">
        <Pauses settings={settings} onPatch={onPatch} />
        <Volume settings={settings} onPatch={onPatch} />
      </div>
      <Safety settings={settings} onPatch={onPatch} />
    </section>
  );
}
