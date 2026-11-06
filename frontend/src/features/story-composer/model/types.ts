export type StoryEntityType =
  'bold' | 'italic' | 'underline' | 'strike' | 'spoiler' | 'code' | 'text_url';

// Offsets and lengths count UTF-16 code units, as Telegram does: an emoji outside the
// BMP is two units, which is also what `String.prototype.length` reports.
export interface StoryEntity {
  type: StoryEntityType;
  offset: number;
  length: number;
  url?: string;
}

export type LinkStickerStyle = 'white' | 'dark' | 'glass' | 'accent';

// x, y: the centre of the plate in percent of the 9:16 canvas. scale 0.5..2.5.
// rotation: degrees clockwise, in (-180, 180].
export interface LinkSticker {
  id: string;
  url: string;
  label: string;
  style: LinkStickerStyle;
  x: number;
  y: number;
  scale: number;
  rotation: number;
}

export type StoryPeriodHours = 6 | 12 | 24 | 48;
export type StoryPrivacyPreset = 'contacts' | 'close_friends' | 'public';

export interface StoryDesign {
  caption: string;
  entities: StoryEntity[];
  period_hours: StoryPeriodHours;
  pinned: boolean;
  protect_content: boolean;
  privacy_preset: StoryPrivacyPreset;
  link_stickers: LinkSticker[];
}

export type PremiumFeature = 'period' | 'link_stickers' | 'long_caption' | 'formatting';

export type StoryMedia =
  | { kind: 'image'; url: string }
  | { kind: 'video'; url: string }
  | { kind: 'collage'; urls: string[]; layout?: string };

export interface CaptionValue {
  text: string;
  entities: StoryEntity[];
}

export type WireLinkSticker = Omit<LinkSticker, 'id'>;
export type WireStoryDesign = Omit<StoryDesign, 'link_stickers'> & {
  link_stickers: WireLinkSticker[];
};
