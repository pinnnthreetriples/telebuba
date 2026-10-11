// Section 4 (stories only): what the person sees when they open the mention — the media
// pool (taken in turn), the caption, the Premium link with its button text, who sees the
// story, how long it stays up, and the dialog opened before the mention.
import { useMutation } from '@tanstack/react-query';
import { useRef } from 'react';
import { useTranslation } from 'react-i18next';

import { uploadMasstaggingMediaMutation } from '@/entities/masstagging';
import {
  Badge,
  HelpHint,
  Icon,
  IconButton,
  Input,
  SegmentedControl,
  Select,
  Spinner,
  Switch,
} from '@/shared/ui';

import type { Settings } from '../../model/draft';

import { Row, Section } from './fields';

type Patch = (next: Partial<Settings>) => void;

const LIFETIMES = [6, 12, 24, 48] as const;
const VIDEO = /\.(mp4|mov)$/i;

function MediaPool({
  campaignId,
  media,
  onPatch,
}: {
  campaignId: string;
  media: Settings['media'];
  onPatch: Patch;
}) {
  const { t } = useTranslation();
  const fileRef = useRef<HTMLInputElement>(null);
  const upload = useMutation(uploadMasstaggingMediaMutation());
  const add = async (files: File[]) => {
    let next = media;
    for (const file of files) {
      try {
        const stored = await upload.mutateAsync({
          path: { campaign_id: campaignId },
          body: { file },
        });
        next = [...next.filter((item) => item.media_id !== stored.media_id), stored];
      } catch {
        // The toast already said why; the files after it still get their turn.
      }
    }
    onPatch({ media: next });
  };
  return (
    <>
      <input
        ref={fileRef}
        type="file"
        multiple
        accept="image/jpeg,image/png,image/webp,video/mp4,video/quicktime"
        className="hidden"
        aria-label={t('masstagging.settings.story.drop')}
        onChange={(event) => {
          const files = Array.from(event.target.files ?? []);
          event.target.value = '';
          if (files.length > 0) void add(files);
        }}
      />
      <button
        type="button"
        onClick={() => fileRef.current?.click()}
        onDragOver={(event) => {
          event.preventDefault();
        }}
        onDrop={(event) => {
          event.preventDefault();
          void add(Array.from(event.dataTransfer.files));
        }}
        className="flex flex-col items-center gap-1 rounded-md border border-dashed border-line-strong bg-canvas px-4 py-6 text-center"
      >
        {upload.isPending ? <Spinner /> : <Icon name="upload-cloud" size={20} />}
        <span className="flex items-center gap-1 text-body">
          {t('masstagging.settings.story.drop')}
          <HelpHint
            text={t('masstagging.settings.story.dropHint')}
            example={t('masstagging.settings.story.dropExample')}
          />
        </span>
      </button>
      {media.length === 0 ? null : (
        <div className="flex flex-wrap gap-2">
          {media.map((item) => (
            <Badge key={item.media_id} size="sm" className="gap-1">
              <Icon name={VIDEO.test(item.media_id) ? 'video' : 'file'} size={12} />
              {item.name}
              <IconButton
                size="sm"
                shape="circle"
                aria-label={t('masstagging.settings.story.removeMedia', { name: item.name })}
                onClick={() => {
                  onPatch({ media: media.filter((other) => other.media_id !== item.media_id) });
                }}
              >
                <Icon name="close" size={12} />
              </IconButton>
            </Badge>
          ))}
        </div>
      )}
    </>
  );
}

export function StorySection({
  campaignId,
  settings,
  captionLimit,
  onPatch,
}: {
  campaignId: string;
  settings: Settings;
  captionLimit: number;
  onPatch: Patch;
}) {
  const { t } = useTranslation();
  return (
    <Section
      n={4}
      title={t('masstagging.settings.story.title')}
      hint={t('masstagging.settings.story.hint')}
      example={t('masstagging.settings.story.example')}
    >
      <MediaPool campaignId={campaignId} media={settings.media} onPatch={onPatch} />
      <label className="flex flex-col gap-1">
        <span className="flex items-center gap-1">
          <span className="type-body-medium">{t('masstagging.settings.story.caption')}</span>
          <HelpHint
            text={t('masstagging.settings.story.captionHint')}
            example={t('masstagging.settings.story.captionExample')}
          />
          <span className="ml-auto type-small tabular-nums">
            {settings.caption.length}/{captionLimit}
          </span>
        </span>
        <Input
          size="sm"
          value={settings.caption}
          maxLength={captionLimit}
          onChange={(event) => {
            onPatch({ caption: event.target.value });
          }}
        />
      </label>
      <label className="flex flex-col gap-1">
        <span className="flex items-center gap-1">
          <span className="type-body-medium">{t('masstagging.settings.story.link')}</span>
          <HelpHint
            text={t('masstagging.settings.story.linkHint')}
            example={t('masstagging.settings.story.linkExample')}
          />
          <Badge tone="info" className="ml-1">
            Premium
          </Badge>
        </span>
        <Input
          size="sm"
          value={settings.link}
          placeholder="https://"
          onChange={(event) => {
            onPatch({ link: event.target.value });
          }}
        />
      </label>
      {settings.link === '' ? null : (
        <label className="flex flex-col gap-1">
          <span className="type-body-medium">{t('masstagging.settings.story.linkText')}</span>
          <Input
            size="sm"
            value={settings.link_text}
            maxLength={40}
            placeholder={t('masstagging.settings.story.linkTextPlaceholder')}
            onChange={(event) => {
              onPatch({ link_text: event.target.value });
            }}
          />
        </label>
      )}
      <div>
        <Row
          first
          label={t('masstagging.settings.story.visibility')}
          hint={t('masstagging.settings.story.visibilityHint')}
        >
          <SegmentedControl
            variant="pill"
            value={settings.visibility}
            ariaLabel={t('masstagging.settings.story.visibility')}
            options={[
              { value: 'all', label: t('masstagging.settings.story.visibilityAll') },
              { value: 'contacts', label: t('masstagging.settings.story.visibilityContacts') },
            ]}
            onChange={(visibility) => {
              onPatch({ visibility });
            }}
          />
        </Row>
        <Row
          label={t('masstagging.settings.story.lifetime')}
          hint={t('masstagging.settings.story.lifetimeHint')}
          example={t('masstagging.settings.story.lifetimeExample')}
        >
          <Select
            value={String(settings.lifetime_hours)}
            ariaLabel={t('masstagging.settings.story.lifetime')}
            options={LIFETIMES.map((hours) => ({
              value: String(hours),
              label: t('masstagging.settings.story.hours', { count: hours }),
            }))}
            onChange={(value) => {
              const hours = LIFETIMES.find((option) => String(option) === value);
              if (hours !== undefined) onPatch({ lifetime_hours: hours });
            }}
          />
        </Row>
        <Row
          label={t('masstagging.settings.story.primeFirst')}
          hint={t('masstagging.settings.story.primeFirstHint')}
        >
          <Switch
            checked={settings.prime_first}
            label={t('masstagging.settings.story.primeFirst')}
            onChange={(primeFirst) => {
              onPatch({ prime_first: primeFirst });
            }}
          />
        </Row>
      </div>
    </Section>
  );
}
