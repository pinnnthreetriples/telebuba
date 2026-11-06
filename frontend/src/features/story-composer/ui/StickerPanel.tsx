import { useTranslation } from 'react-i18next';

import { cn } from '@/shared/lib/cn';
import { Badge, Button, Icon, IconButton, Input, SegmentedControl } from '@/shared/ui';

import { createSticker, stickerProblems } from '../model/design';
import type { LabelProblem } from '../model/label';
import { MAX_LINK_STICKERS, STICKER_LABEL_MAX } from '../model/limits';
import type { LinkSticker, LinkStickerStyle } from '../model/types';
import { normalizeUrl } from '../model/url';

const STYLES: readonly LinkStickerStyle[] = ['white', 'dark', 'glass', 'accent'];

const LABEL_ERROR: Record<LabelProblem, string> = {
  empty: 'storyComposer.stickers.labelEmpty',
  emoji: 'storyComposer.stickers.labelEmoji',
  too_long: 'storyComposer.stickers.labelTooLong',
};

export function StickerPanel({
  stickers,
  onChange,
  selectedId,
  onSelect,
  premium,
  disabled = false,
}: {
  stickers: LinkSticker[];
  onChange: (stickers: LinkSticker[]) => void;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  premium: boolean | null;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const full = stickers.length >= MAX_LINK_STICKERS;
  const patch = (id: string, change: Partial<LinkSticker>) => {
    onChange(stickers.map((sticker) => (sticker.id === id ? { ...sticker, ...change } : sticker)));
  };

  return (
    <section aria-label={t('storyComposer.stickers.title')} className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="type-body-medium flex min-w-0 items-center gap-2">
          {t('storyComposer.stickers.title')}
          {premium === true ? null : (
            <Badge tone="info">{t('storyComposer.stickers.premium')}</Badge>
          )}
        </h3>
        <Button
          size="sm"
          disabled={disabled || full}
          title={full ? t('storyComposer.stickers.limit', { count: MAX_LINK_STICKERS }) : undefined}
          onClick={() => {
            const sticker = createSticker(stickers.length);
            onChange([...stickers, sticker]);
            onSelect(sticker.id);
          }}
        >
          <Icon name="plus" size={14} />
          {t('storyComposer.stickers.add')}
        </Button>
      </div>
      {stickers.map((sticker, index) => {
        const problems = stickerProblems(sticker);
        const urlInvalid = sticker.url !== '' && problems.url !== null;
        const labelError = sticker.label !== '' ? problems.label : null;
        const name = t('storyComposer.stickers.item', { index: index + 1 });
        return (
          <div
            key={sticker.id}
            role="group"
            aria-label={name}
            className={cn(
              'flex flex-col gap-2 rounded-md border p-3',
              sticker.id === selectedId ? 'border-action-primary' : 'border-line',
            )}
            onFocus={() => {
              onSelect(sticker.id);
            }}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="type-small-medium">{name}</span>
              <IconButton
                size="sm"
                tone="danger"
                aria-label={`${t('storyComposer.stickers.remove')}: ${name}`}
                disabled={disabled}
                onClick={() => {
                  onChange(stickers.filter((item) => item.id !== sticker.id));
                  if (selectedId === sticker.id) onSelect(null);
                }}
              >
                <Icon name="trash" size={12} />
              </IconButton>
            </div>
            <Input
              size="sm"
              inputMode="url"
              value={sticker.url}
              disabled={disabled}
              invalid={urlInvalid}
              aria-label={`${name}: ${t('storyComposer.stickers.url')}`}
              placeholder={t('storyComposer.stickers.urlPlaceholder')}
              onChange={(event) => {
                patch(sticker.id, { url: event.target.value });
              }}
              onBlur={() => {
                const normalized = normalizeUrl(sticker.url);
                if (normalized !== null && normalized !== sticker.url) {
                  patch(sticker.id, { url: normalized });
                }
              }}
            />
            {urlInvalid ? (
              <p role="alert" className="type-small text-danger-deep">
                {t('storyComposer.stickers.urlInvalid')}
              </p>
            ) : null}
            <Input
              size="sm"
              value={sticker.label}
              disabled={disabled}
              invalid={labelError !== null}
              aria-label={`${name}: ${t('storyComposer.stickers.label')}`}
              placeholder={t('storyComposer.stickers.labelPlaceholder')}
              onChange={(event) => {
                patch(sticker.id, { label: event.target.value });
              }}
            />
            <div className="flex items-start justify-between gap-2">
              {labelError ? (
                <p role="alert" className="type-small text-danger-deep">
                  {t(LABEL_ERROR[labelError])}
                </p>
              ) : (
                <span />
              )}
              <span className="type-small shrink-0">
                {t('storyComposer.stickers.labelCounter', {
                  count: [...sticker.label].length,
                  max: STICKER_LABEL_MAX,
                })}
              </span>
            </div>
            <SegmentedControl
              variant="outline"
              className="grid grid-cols-2"
              value={sticker.style}
              disabled={disabled}
              ariaLabel={`${name}: ${t('storyComposer.stickers.style')}`}
              options={STYLES.map((style) => ({
                value: style,
                label: t(`storyComposer.stickers.styles.${style}`),
              }))}
              onChange={(style) => {
                patch(sticker.id, { style });
              }}
            />
          </div>
        );
      })}
    </section>
  );
}
