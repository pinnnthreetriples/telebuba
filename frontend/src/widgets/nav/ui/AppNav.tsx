import { Link } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';

const LINKS = [
  { to: '/', key: 'accounts' },
  { to: '/warming', key: 'warming' },
  { to: '/neurocomment', key: 'neurocomment' },
  { to: '/logs', key: 'logs' },
] as const;

export function AppNav() {
  const { t } = useTranslation();
  return (
    <nav className="flex items-center gap-6 border-b border-line bg-surface px-8 py-3">
      <span className="font-semibold text-ink">Telebuba</span>
      {LINKS.map((link) => (
        <Link
          key={link.to}
          to={link.to}
          className="text-sm text-ink-muted hover:text-ink [&.active]:font-medium [&.active]:text-primary"
        >
          {t(`nav.${link.key}`)}
        </Link>
      ))}
    </nav>
  );
}
