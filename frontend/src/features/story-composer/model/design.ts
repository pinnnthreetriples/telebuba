import { labelProblem, type LabelProblem } from './label';
import { CAPTION_MAX_FREE, DEFAULT_PERIOD_HOURS, MAX_LINK_STICKERS } from './limits';
import type { LinkSticker, PremiumFeature, StoryDesign, WireStoryDesign } from './types';
import { isValidUrl } from './url';

export function createDefaultDesign(): StoryDesign {
  return {
    caption: '',
    entities: [],
    period_hours: DEFAULT_PERIOD_HOURS,
    pinned: false,
    protect_content: false,
    privacy_preset: 'contacts',
    link_stickers: [],
  };
}

let counter = 0;
function newId(): string {
  counter += 1;
  const random =
    typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
      ? crypto.randomUUID()
      : `${Date.now().toString(36)}${String(counter)}`;
  return `sticker-${random}`;
}

// New stickers fan out down the lower half of the canvas so a second one does not land
// exactly on the first.
export function createSticker(existing: number): LinkSticker {
  return {
    id: newId(),
    url: '',
    label: '',
    style: 'white',
    x: 50,
    y: Math.min(85, 62 + existing * 12),
    scale: 1,
    rotation: 0,
  };
}

// Mirror of the backend policy: what an account without Premium loses from this design.
// The caption limit is a parameter so the caller can follow the server's own number.
export function premiumLoss(
  design: StoryDesign,
  freeCaptionMax: number = CAPTION_MAX_FREE,
): PremiumFeature[] {
  const lost: PremiumFeature[] = [];
  if (design.period_hours !== DEFAULT_PERIOD_HOURS) lost.push('period');
  if (design.link_stickers.length > 0) lost.push('link_stickers');
  if (design.caption.length > freeCaptionMax) lost.push('long_caption');
  if (design.entities.length > 0) lost.push('formatting');
  return lost;
}

// The design as the API takes it: the sticker ids are the editor's own handles.
export function toWireDesign(design: StoryDesign): WireStoryDesign {
  return {
    ...design,
    entities: design.entities.map((entity) => ({ ...entity })),
    link_stickers: design.link_stickers.slice(0, MAX_LINK_STICKERS).map((sticker) => ({
      url: sticker.url,
      label: sticker.label,
      style: sticker.style,
      x: sticker.x,
      y: sticker.y,
      scale: sticker.scale,
      rotation: sticker.rotation,
    })),
  };
}

export interface StickerProblems {
  url: 'invalid' | null;
  label: LabelProblem | null;
}

export function stickerProblems(sticker: LinkSticker): StickerProblems {
  return {
    url: isValidUrl(sticker.url) ? null : 'invalid',
    label: labelProblem(sticker.label),
  };
}

// True when every sticker has a usable URL and label; callers gate submit on it.
export function designIsValid(design: StoryDesign): boolean {
  return design.link_stickers.every((sticker) => {
    const problems = stickerProblems(sticker);
    return problems.url === null && problems.label === null;
  });
}
