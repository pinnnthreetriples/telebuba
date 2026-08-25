import { Link } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';

import { Icon, Modal } from '@/shared/ui';

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
      label={t('shell.menu')}
      backdrop={0.45}
      className="flex w-[min(84vw,300px)] flex-col"
    >
      <div className="flex h-header shrink-0 items-center justify-between border-b border-line px-lg">
        <div className="flex items-center gap-md">
          <div className="flex size-icon items-center justify-center rounded-lg bg-ink">
            <div className="size-node rounded-full bg-primary" />
          </div>
          <span className="text-title font-bold tracking-[-0.01em]">Telebuba</span>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label={t('shell.closeMenu')}
          className="-mr-sm flex size-touch items-center justify-center rounded-full text-ink-muted"
        >
          <Icon name="close" size={18} />
        </button>
      </div>

      <nav className="flex flex-col gap-tight p-sm">
        {NAV_LINKS.map((link, index) => (
          <Link
            key={link.to}
            to={link.to}
            onClick={onClose}
            className={`flex min-h-touch items-center rounded-lg px-md text-lead font-medium transition-colors ${
              activeIdx === index ? 'bg-primary-tint text-primary-deep' : 'text-ink-muted'
            }`}
          >
            {t(`nav.${link.key}`)}
          </Link>
        ))}
      </nav>
    </Modal>
  );
}
