import { useEffect, useState } from 'react';

import type { StoryMedia } from '@/features/story-composer';

import { VIDEO_SUFFIXES } from './_channelsShared';

// What a story publish says it could not honour on an account without Premium. The
// API names the features; the wording is the SPA's.
export function downgradedList(
  t: (key: string, opts?: Record<string, unknown>) => string,
  features: readonly string[],
): string {
  return features.map((feature) => t(`accounts.addStory.downgraded.feature.${feature}`)).join(', ');
}

// A story takes either kind, and the browser's MIME type is not always filled in, so
// the suffix counts too (the same rule the upload gate reads).
export function isVideoFile(file: File): boolean {
  const name = file.name.toLowerCase();
  return file.type.startsWith('video') || VIDEO_SUFFIXES.some((suffix) => name.endsWith(suffix));
}

// One video OR up to `max` photos, never both. A picked video replaces whatever was
// staged; photos are appended to staged photos (a staged video gives way to them).
export function mergeStoryPick(current: File[], picked: File[], max: number): File[] {
  const video = picked.find(isVideoFile);
  if (video) return [video];
  const photos = picked.filter((file) => !isVideoFile(file));
  const staged = current.filter((file) => !isVideoFile(file));
  return [...staged, ...photos].slice(0, max);
}

// Object URLs for the picked files, revoked on change and on unmount. Kept in state and
// tagged with the list that made them: a render against a newer list sees no URLs rather
// than one frame of the wrong (or a revoked) preview.
export function usePreviewUrls(files: File[]): string[] {
  const [made, setMade] = useState<{ files: File[]; urls: string[] }>({ files: [], urls: [] });
  useEffect(() => {
    const urls = files.map((file) => URL.createObjectURL(file));
    setMade({ files, urls });
    return () => {
      for (const url of urls) URL.revokeObjectURL(url);
    };
  }, [files]);
  return made.files === files ? made.urls : [];
}

// The composer's preview of the picked files: one photo, one video, or a collage.
export function storyMediaOf(
  files: File[],
  urls: string[],
  layout: string | null,
): StoryMedia | null {
  const first = files[0];
  const firstUrl = urls[0];
  if (!first || firstUrl === undefined || urls.length !== files.length) return null;
  if (isVideoFile(first)) return { kind: 'video', url: firstUrl };
  if (files.length > 1) return { kind: 'collage', urls, ...(layout ? { layout } : {}) };
  return { kind: 'image', url: firstUrl };
}
