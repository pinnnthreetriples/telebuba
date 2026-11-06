import { useState } from 'react';

import { createDefaultDesign, toWireDesign } from '@/features/story-composer';

import { isVideoFile } from './_storyShared';
import { MIN_COLLAGE_IMAGES, defaultLayoutId, isLayoutValidForCount } from './storyCollageLayouts';

const DEFAULT_DESIGN_JSON = JSON.stringify(createDefaultDesign());

// The bulk story tab's state: the picked files (one video OR up to six photos, the same
// rule as the single composer), the collage layout and ONE design shared by every account.
export function useBulkStory() {
  const [files, setFiles] = useState<File[]>([]);
  const [design, setDesign] = useState(createDefaultDesign);
  const [layout, setLayout] = useState<string | null>(null);

  const video = files.some(isVideoFile);
  const isCollage = !video && files.length >= MIN_COLLAGE_IMAGES;
  // Derived rather than synced by an effect: the count decides which layouts exist.
  const collageLayout = isCollage
    ? isLayoutValidForCount(layout, files.length)
      ? layout
      : defaultLayoutId(files.length)
    : null;

  return {
    files,
    setFiles,
    design,
    setDesign,
    layout: collageLayout,
    setLayout,
    isCollage,
    video,
    dirty: files.length > 0 || JSON.stringify(design) !== DEFAULT_DESIGN_JSON,
    // One account's publish-now form body.
    formBody: () => ({
      files,
      media_kind: video ? ('video' as const) : ('image' as const),
      design: JSON.stringify(toWireDesign(design)),
      collage_layout: collageLayout,
    }),
  };
}
