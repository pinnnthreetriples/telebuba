import { Link, useRouterState } from '@tanstack/react-router';
import { useLayoutEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

const LINKS = [
  { to: '/', key: 'accounts' },
  { to: '/warming', key: 'warming' },
  { to: '/neurocomment', key: 'neurocomment' },
  { to: '/logs', key: 'logs' },
  { to: '/settings', key: 'settings' },
] as const;

// The design's sticky top bar (Telebuba.dc.html header): logo, nav with a
// sliding active indicator (the GSAP layoutId slide, done here by measuring the
// active link and CSS-transitioning a single underline), "system active" pill,
// bell, avatar. Reproduced with Tailwind utilities to match 1:1.
export function AppNav() {
  const { t } = useTranslation();
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const navRef = useRef<HTMLElement>(null);
  const [indicator, setIndicator] = useState({ left: 0, width: 0 });

  const activeIdx = LINKS.findIndex((link) =>
    link.to === '/' ? pathname === '/' : pathname.startsWith(link.to),
  );

  useLayoutEffect(() => {
    const nav = navRef.current;
    if (!nav) return;
    const move = () => {
      const active = nav.querySelectorAll('a')[activeIdx];
      if (active instanceof HTMLElement) {
        setIndicator({ left: active.offsetLeft, width: active.offsetWidth });
      }
    };
    move();
    window.addEventListener('resize', move);
    void document.fonts?.ready.then(move); // reposition once webfonts settle widths
    return () => {
      window.removeEventListener('resize', move);
    };
  }, [activeIdx]);

  return (
    <header className="sticky top-0 z-40 border-b border-line bg-white/85 backdrop-blur-[10px]">
      <div className="mx-auto flex h-14 max-w-[1340px] items-center gap-7 px-6">
        <div className="flex shrink-0 items-center gap-[9px]">
          <div className="flex h-[26px] w-[26px] items-center justify-center rounded-lg bg-ink">
            <div className="h-[9px] w-[9px] rounded-full bg-primary" />
          </div>
          <span className="text-[15px] font-bold tracking-[-0.01em]">Telebuba</span>
        </div>

        <nav ref={navRef} className="relative flex flex-1 items-center gap-[22px] self-stretch">
          {LINKS.map((link, index) => (
            <Link
              key={link.to}
              to={link.to}
              className={`relative flex items-center self-stretch text-[13px] font-medium transition-colors ${activeIdx === index ? 'text-ink' : 'text-ink-muted hover:text-ink'}`}
            >
              {t(`nav.${link.key}`)}
            </Link>
          ))}
          <span
            aria-hidden
            className="pointer-events-none absolute bottom-0 h-[2px] rounded-t bg-primary transition-[left,width] duration-[450ms] [transition-timing-function:cubic-bezier(.34,1.45,.6,1)]"
            style={{ left: indicator.left, width: indicator.width }}
          />
        </nav>

        <div className="flex shrink-0 items-center gap-[10px]">
          <div className="flex items-center gap-[7px] rounded-full bg-success-tint px-[11px] py-[5px]">
            <span className="h-[7px] w-[7px] rounded-full bg-success-dot" />
            <span className="text-[12px] font-medium text-success">{t('shell.systemActive')}</span>
          </div>
          <button
            type="button"
            aria-label={t('shell.notifications')}
            className="relative flex h-[34px] w-[34px] items-center justify-center rounded-full border border-line bg-white text-ink-muted"
          >
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
            >
              <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
              <path d="M13.73 21a2 2 0 0 1-3.46 0" />
            </svg>
            <span className="absolute right-[8px] top-[6px] h-[6px] w-[6px] rounded-full border-[1.5px] border-white bg-primary" />
          </button>
          <div className="flex h-[34px] w-[34px] items-center justify-center rounded-full bg-primary text-[13px] font-semibold text-white">
            ОП
          </div>
        </div>
      </div>
    </header>
  );
}
