import { Link } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';

import { Modal } from '@/shared/ui';

import { NAV_LINKS } from './links';

// The mobile nav: below `lg` the top bar's horizontal <nav> is display:none and a
// hamburger opens this instead. It rides on Modal's 'drawer-left' variant purely
// to inherit the dialog machinery already there — portal, Escape stack, Tab trap,
// focus restore, body scroll lock — rather than reimplementing all of it.
// z=60 sits below every content dialog (70/72/75/80) so a modal opened from a
// page always wins Escape.
// Links only: the avatar and its dropdown stay in the header at every width, so
// logout is already one tap away and needs no second call path here.
export function NavDrawer({ activeIdx, onClose }: { activeIdx: number; onClose: () => void }) {
  const { t } = useTranslation();

  return (
    <Modal
      onClose={onClose}
      variant="drawer-left"
      z={60}
      backdrop={0.45}
      className="flex w-[min(84vw,300px)] flex-col"
    >
      <div className="flex h-14 shrink-0 items-center justify-between border-b border-line px-4">
        <div className="flex items-center gap-[9px]">
          <div className="flex h-[26px] w-[26px] items-center justify-center rounded-lg bg-ink">
            <div className="h-[9px] w-[9px] rounded-full bg-primary" />
          </div>
          <span className="text-[15px] font-bold tracking-[-0.01em]">Telebuba</span>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label={t('shell.closeMenu')}
          className="-mr-2 flex h-11 w-11 items-center justify-center rounded-full text-ink-muted"
        >
          <svg
            width="18"
            height="18"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
          >
            <path d="M18 6 6 18M6 6l12 12" />
          </svg>
        </button>
      </div>

      <nav className="flex flex-col gap-1 p-2">
        {NAV_LINKS.map((link, index) => (
          <Link
            key={link.to}
            to={link.to}
            onClick={onClose}
            className={`flex min-h-[44px] items-center rounded-[10px] px-3 text-[14px] font-medium transition-colors ${
              activeIdx === index ? 'bg-primary-tint text-primary' : 'text-ink-muted'
            }`}
          >
            {t(`nav.${link.key}`)}
          </Link>
        ))}
      </nav>
    </Modal>
  );
}
