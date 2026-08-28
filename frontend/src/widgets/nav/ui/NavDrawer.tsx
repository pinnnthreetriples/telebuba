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
      className="flex w-[min(84vw,300px)] flex-col"
    >
      <div className="flex h-header shrink-0 items-center justify-between border-b border-line px-lg">
        <div className="flex items-center gap-md">
          <div className="flex size-icon items-center justify-center rounded-lg bg-content-primary">
            <div className="size-node rounded-full bg-action-primary" />
          </div>
          {/* The wordmark is not a type role: it is one mark rendered in two places (this
              bar and the drawer), not a kind of text the app has. Naming it would put a
              rung with a single wearer in the canon, and borrowing another role's name
              would make that name lie — it wore `type-dialog-title` for exactly as long
              as it took to read it back. Hand-written, and staying that way. */}
          <span className="text-title font-bold tracking-[-0.01em]">Telebuba</span>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label={t('shell.closeMenu')}
          className="-mr-sm flex size-touch items-center justify-center rounded-full text-content-muted"
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
            className={`flex min-h-touch items-center rounded-lg px-md text-body font-medium transition-colors ${
              activeIdx === index ? 'bg-info-tint text-info-strong' : 'text-content-muted'
            }`}
          >
            {t(`nav.${link.key}`)}
          </Link>
        ))}
      </nav>
    </Modal>
  );
}
