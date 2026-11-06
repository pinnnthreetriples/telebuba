import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { premiumLoss } from '../model/design';
import type { StoryDesign, StoryMedia } from '../model/types';
import { CaptionEditor } from './CaptionEditor';
import { PremiumHint } from './PremiumHint';
import { StickerPanel } from './StickerPanel';
import { StoryCanvas } from './StoryCanvas';
import { StoryOptions } from './StoryOptions';

// The whole story editor: preview with link stickers, the sticker list, the caption, the
// publishing options and the Premium warning. It edits one `StoryDesign` and holds
// nothing but which sticker is selected.
export function StoryComposer({
  media,
  design,
  onChange,
  premium,
  nonPremiumCount,
  disabled = false,
}: {
  media: StoryMedia | null;
  design: StoryDesign;
  onChange: (design: StoryDesign) => void;
  // true / false: the target account's Premium status; null: not known.
  premium: boolean | null;
  // Bulk: how many of the targeted accounts lack Premium.
  nonPremiumCount?: number;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  const [selectedId, setSelectedId] = useState<string | null>(null);

  return (
    <div
      role="group"
      aria-label={t('storyComposer.composer.label')}
      className="flex flex-col gap-4 md:flex-row"
    >
      <div className="w-menu shrink-0 self-center md:self-start">
        <StoryCanvas
          media={media}
          stickers={design.link_stickers}
          selectedId={selectedId}
          onSelect={setSelectedId}
          disabled={disabled}
          onChange={(link_stickers) => {
            onChange({ ...design, link_stickers });
          }}
        />
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-4">
        <CaptionEditor
          value={{ text: design.caption, entities: design.entities }}
          premium={premium}
          disabled={disabled}
          onChange={(value) => {
            onChange({ ...design, caption: value.text, entities: value.entities });
          }}
        />
        <StickerPanel
          stickers={design.link_stickers}
          selectedId={selectedId}
          onSelect={setSelectedId}
          premium={premium}
          disabled={disabled}
          onChange={(link_stickers) => {
            onChange({ ...design, link_stickers });
          }}
        />
        <StoryOptions
          design={design}
          premium={premium}
          disabled={disabled}
          onChange={(patch) => {
            onChange({ ...design, ...patch });
          }}
        />
        <PremiumHint
          premium={premium}
          nonPremiumCount={nonPremiumCount}
          loss={premiumLoss(design)}
        />
      </div>
    </div>
  );
}
