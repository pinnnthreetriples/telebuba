import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { ProfilePhotoView } from '@/shared/api';
import { Icon, Modal } from '@/shared/ui';

import { ViewerButton } from './_viewerShared';

const ZOOM = 2.5;

// Full-screen photo viewer for the profile modal's photo tab. A click toggles a
// 2.5× zoom anchored at the clicked point, and while zoomed the image pans with
// the cursor; the wheel zooms in/out the same way. ←/→ page, Esc closes (via the
// Modal stack, so the ProfileModal underneath stays open).
export function PhotoViewer({
  photos,
  index,
  onClose,
}: {
  photos: ProfilePhotoView[];
  index: number;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [current, setCurrent] = useState(index);
  const [zoomed, setZoomed] = useState(false);
  const [origin, setOrigin] = useState('50% 50%');
  const photo = photos[current];
  const many = photos.length > 1;

  const go = (delta: number) => {
    setZoomed(false);
    setCurrent((i) => (i + delta + photos.length) % photos.length);
  };

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'ArrowLeft') go(-1);
      if (event.key === 'ArrowRight') go(1);
    };
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
    };
  });

  const originAt = (event: React.MouseEvent<HTMLImageElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const x = ((event.clientX - rect.left) / rect.width) * 100;
    const y = ((event.clientY - rect.top) / rect.height) * 100;
    setOrigin(`${x}% ${y}%`);
  };

  if (!photo) return null;
  return (
    <Modal variant="viewer" label={t('accounts.profile.photoViewer')} onClose={onClose}>
      <div
        role="presentation"
        className="absolute inset-0 flex items-center justify-center overflow-hidden"
        onClick={(event) => {
          if (event.target === event.currentTarget) onClose();
        }}
      >
        {photo.thumb_url && (
          <img
            src={photo.thumb_url}
            alt=""
            draggable={false}
            onClick={(event) => {
              originAt(event);
              setZoomed((z) => !z);
            }}
            onMouseMove={(event) => {
              if (zoomed) originAt(event);
            }}
            onWheel={(event) => {
              originAt(event);
              setZoomed(event.deltaY < 0);
            }}
            style={{ transform: `scale(${zoomed ? ZOOM : 1})`, transformOrigin: origin }}
            className={`max-h-full max-w-full select-none object-contain transition-transform duration-swap ${
              zoomed ? 'cursor-zoom-out' : 'cursor-zoom-in'
            }`}
          />
        )}
      </div>
      <ViewerButton
        label={t('accounts.profile.closeViewer')}
        onClick={onClose}
        className="right-lg top-lg"
      >
        <Icon name="close" size={20} />
      </ViewerButton>
      {many && (
        <>
          <ViewerButton
            label={t('accounts.profile.viewerPrev')}
            onClick={() => {
              go(-1);
            }}
            className="left-lg top-1/2 -translate-y-1/2"
          >
            <Icon name="chevron-left" size={20} />
          </ViewerButton>
          <ViewerButton
            label={t('accounts.profile.viewerNext')}
            onClick={() => {
              go(1);
            }}
            className="right-lg top-1/2 -translate-y-1/2"
          >
            <Icon name="chevron-right" size={20} />
          </ViewerButton>
          <span className="absolute bottom-lg left-1/2 -translate-x-1/2 rounded-sm bg-black/55 px-md py-hair text-small font-medium text-on-inverse">
            {current + 1} / {photos.length}
          </span>
        </>
      )}
    </Modal>
  );
}
