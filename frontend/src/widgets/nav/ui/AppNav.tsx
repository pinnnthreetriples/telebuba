import { useMutation, useQuery } from '@tanstack/react-query';
import { Link, useNavigate, useRouterState } from '@tanstack/react-router';
import { useLayoutEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { logoutMutation, meQueryOptions } from '@/shared/auth';
import { queryClient, useLogEventStream, type SseStatus } from '@/shared/lib';

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
  const [menuOpen, setMenuOpen] = useState(false);
  const navigate = useNavigate();
  const me = useQuery(meQueryOptions());
  const logout = useMutation(logoutMutation());
  const initials = (me.data?.username ?? '').slice(0, 2).toUpperCase() || t('shell.avatarFallback');
  const [sseStatus, setSseStatus] = useState<SseStatus>('connecting');
  useLogEventStream(() => undefined, setSseStatus);
  const systemActive = sseStatus === 'open';

  const activeIdx = LINKS.findIndex((link) =>
    link.to === '/' ? pathname === '/' : pathname.startsWith(link.to),
  );

  useLayoutEffect(() => {
    const nav = navRef.current;
    if (!nav) return;
    let raf = 0;
    const move = () => {
      const active = nav.querySelectorAll('a')[activeIdx];
      if (!(active instanceof HTMLElement)) return;
      const navRect = nav.getBoundingClientRect();
      const rect = active.getBoundingClientRect();
      // The link may not be laid out yet (width 0) right after a route change /
      // before webfonts settle — retry next frame instead of committing a 0-width
      // bar that would otherwise stick (leaving a missing/stray indicator).
      if (rect.width === 0) {
        raf = requestAnimationFrame(move);
        return;
      }
      setIndicator({ left: rect.left - navRect.left, width: rect.width });
    };
    raf = requestAnimationFrame(move);
    window.addEventListener('resize', move);
    void document.fonts?.ready.then(move); // reposition once webfonts settle widths
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(move) : null;
    ro?.observe(nav);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener('resize', move);
      ro?.disconnect();
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
            className="pointer-events-none absolute left-0 top-0 h-[2px] rounded-b-[2px] bg-primary will-change-transform [transform:translateZ(0)] transition-[transform,width,opacity] duration-[450ms] [transition-timing-function:cubic-bezier(.34,1.45,.6,1)]"
            style={{
              width: indicator.width,
              // Position via GPU transform (matches the design's layoutId slide),
              // not `left` — animating `left` inside the backdrop-blur header
              // repaints the blurred region each frame and leaves ghost trails.
              // translateZ(0)/will-change keeps it on its own layer, out of the
              // header's backdrop compositing entirely.
              transform: `translateX(${String(indicator.left)}px)`,
              opacity: indicator.width ? 1 : 0,
            }}
          />
        </nav>

        <div className="flex shrink-0 items-center gap-[10px]">
          <div
            className={`flex items-center gap-[7px] rounded-full px-[11px] py-[5px] ${systemActive ? 'bg-success-tint' : 'bg-track'}`}
          >
            <span
              className={`h-[7px] w-[7px] rounded-full ${systemActive ? 'bg-success-dot' : 'bg-ink-subtle'}`}
            />
            <span
              className={`text-[12px] font-medium ${systemActive ? 'text-success' : 'text-ink-muted'}`}
            >
              {systemActive ? t('shell.systemActive') : t('shell.systemOffline')}
            </span>
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
          <div className="relative">
            <button
              type="button"
              aria-label={t('shell.account')}
              onClick={() => {
                setMenuOpen((open) => !open);
              }}
              className="flex h-[34px] w-[34px] items-center justify-center rounded-full bg-primary text-[13px] font-semibold text-white"
            >
              {initials}
            </button>
            {menuOpen ? (
              <>
                <button
                  type="button"
                  aria-hidden
                  tabIndex={-1}
                  onClick={() => {
                    setMenuOpen(false);
                  }}
                  className="fixed inset-0 z-40 cursor-default"
                />
                <div className="absolute right-0 top-[42px] z-50 w-[190px] overflow-hidden rounded-[12px] border border-line bg-white py-1 shadow-[0_8px_24px_rgba(11,11,12,0.12)]">
                  {me.data ? (
                    <div className="truncate border-b border-[#f0eeeb] px-[14px] py-[8px] text-[12px] text-ink-muted">
                      {me.data.username}
                    </div>
                  ) : null}
                  <button
                    type="button"
                    onClick={() => {
                      logout.mutate(
                        {},
                        {
                          onSuccess: () => {
                            // Drop all authed data so it can't leak on back-nav.
                            queryClient.clear();
                            void navigate({ to: '/login' });
                          },
                        },
                      );
                    }}
                    className="flex w-full items-center gap-[8px] px-[14px] py-[8px] text-left text-[13px] font-medium text-danger transition-colors hover:bg-[#faf2f1]"
                  >
                    <svg
                      width="15"
                      height="15"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    >
                      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
                      <path d="m16 17 5-5-5-5" />
                      <path d="M21 12H9" />
                    </svg>
                    {t('shell.logout')}
                  </button>
                </div>
              </>
            ) : null}
          </div>
        </div>
      </div>
    </header>
  );
}
