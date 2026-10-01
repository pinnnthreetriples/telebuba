import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';

import '@/shared/i18n';
import type { ProfilePhotoView, ProfileStoryView } from '@/shared/api';

import { PhotoTab } from './PhotoTab';
import { StoriesTab } from './StoriesTab';

afterEach(() => {
  vi.useRealTimers();
});

const PHOTOS: ProfilePhotoView[] = [
  { photo_id: '1', access_hash: '1', file_reference: 'AA==', thumb_url: '/p1.jpg', is_main: true },
  { photo_id: '2', access_hash: '2', file_reference: 'AA==', thumb_url: '/p2.jpg', is_main: false },
];

function story(id: number, kind: 'image' | 'video'): ProfileStoryView {
  return {
    story_id: id,
    kind,
    caption: null,
    privacy_preset: 'contacts',
    is_pinned: false,
    views: null,
    reactions: null,
    thumb_url: `/s${id}-thumb.jpg`,
    media_url: `/s${id}-media`,
  };
}

function renderPhotos(onRemove = vi.fn()) {
  render(
    <PhotoTab
      photos={PHOTOS}
      busy={false}
      uploading={false}
      onUpload={vi.fn()}
      onRemove={onRemove}
      onMakeMain={vi.fn()}
      onSchedule={vi.fn()}
      scheduled={null}
    />,
  );
}

function renderStories(stories: ProfileStoryView[]) {
  render(
    <StoriesTab
      stories={stories}
      pinPending={false}
      onAdd={vi.fn()}
      onRemove={vi.fn()}
      onPinToggle={vi.fn()}
      scheduled={null}
    />,
  );
}

test('clicking a photo opens the viewer; ←/→ page and Escape closes it', () => {
  renderPhotos();
  fireEvent.click(screen.getAllByRole('button', { name: 'Открыть фото' })[0]!);

  const viewer = screen.getByRole('dialog', { name: 'Просмотр фото' });
  expect(viewer.querySelector('img')).toHaveAttribute('src', '/p1.jpg');
  expect(viewer).toHaveTextContent('1 / 2');

  fireEvent.keyDown(document, { key: 'ArrowRight' });
  expect(viewer.querySelector('img')).toHaveAttribute('src', '/p2.jpg');

  fireEvent.keyDown(document, { key: 'Escape' });
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

test('a click on the opened photo toggles the zoom', () => {
  renderPhotos();
  fireEvent.click(screen.getAllByRole('button', { name: 'Открыть фото' })[0]!);
  const image = screen.getByRole('dialog').querySelector('img')!;

  expect(image.style.transform).toBe('scale(1)');
  fireEvent.click(image);
  expect(image.style.transform).toBe('scale(2.5)');
  fireEvent.click(image);
  expect(image.style.transform).toBe('scale(1)');
});

test('the remove control is hover-only and does not open the viewer', () => {
  const onRemove = vi.fn();
  renderPhotos(onRemove);
  const remove = screen.getAllByRole('button', { name: 'Удалить фото' })[0]!;

  expect(remove.className).toContain('opacity-0');
  expect(remove.className).toContain('group-hover:opacity-100');
  fireEvent.click(remove);

  expect(onRemove).toHaveBeenCalledWith(PHOTOS[0]);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

test('story tile controls are hover-only and stacked in one column', () => {
  renderStories([story(1, 'image')]);
  const pin = screen.getByRole('button', { name: 'Закрепить в профиле навсегда' });
  const column = pin.parentElement!;

  expect(column.className).toContain('flex-col');
  expect(column.className).toContain('group-hover:opacity-100');
  expect(column).toHaveTextContent('Контакты');
  expect(screen.getByRole('button', { name: 'Удалить сторис' }).className).toContain('opacity-0');
});

test('a photo story plays for 5 s, advances, and the player closes after the last', () => {
  vi.useFakeTimers();
  renderStories([story(1, 'image'), story(2, 'image')]);
  fireEvent.click(screen.getAllByRole('button', { name: 'Открыть сторис' })[0]!);
  const viewer = screen.getByRole('dialog', { name: 'Просмотр сторис' });
  expect(viewer.querySelector('img')).toHaveAttribute('src', '/s1-media');

  act(() => {
    vi.advanceTimersByTime(5100);
  });
  expect(viewer.querySelector('img')).toHaveAttribute('src', '/s2-media');
  expect(screen.getAllByTestId('story-progress')[0]!.style.width).toBe('100%');

  act(() => {
    vi.advanceTimersByTime(5100);
  });
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

test('a video story plays its media and advances when it ends', () => {
  renderStories([story(1, 'video'), story(2, 'image')]);
  fireEvent.click(screen.getAllByRole('button', { name: 'Открыть сторис' })[0]!);
  const viewer = screen.getByRole('dialog');
  const video = viewer.querySelector('video')!;

  expect(video).toHaveAttribute('src', '/s1-media');
  expect(video).toHaveAttribute('poster', '/s1-thumb.jpg');
  fireEvent.ended(video);

  expect(viewer.querySelector('video')).toBeNull();
  expect(viewer.querySelector('img')).toHaveAttribute('src', '/s2-media');
});

test('a video whose media fails falls back to the poster on the photo timer', () => {
  renderStories([story(1, 'video')]);
  fireEvent.click(screen.getByRole('button', { name: 'Открыть сторис' }));
  fireEvent.error(screen.getByRole('dialog').querySelector('video')!);

  expect(screen.getByRole('dialog').querySelector('img')).toHaveAttribute('src', '/s1-thumb.jpg');
});

test('a tap on the stage pages forward', () => {
  renderStories([story(1, 'image'), story(2, 'image')]);
  fireEvent.click(screen.getAllByRole('button', { name: 'Открыть сторис' })[0]!);
  const stage = screen.getByTestId('story-stage');

  fireEvent.pointerDown(stage);
  fireEvent.pointerUp(stage);

  expect(stage.querySelector('img')).toHaveAttribute('src', '/s2-media');
});
