import { useMutation, useQuery } from '@tanstack/react-query';
import { Link, useNavigate, useRouterState } from '@tanstack/react-router';
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { logoutMutation, meQueryOptions } from '@/shared/auth';
import { queryClient, useLogEventStream, type SseStatus } from '@/shared/lib';
import { IconButton } from '@/shared/ui';

import { NAV_LINKS as LINKS } from './links';
import { NavDrawer } from './NavDrawer';

// The design's sticky top bar (Telebuba.dc.html header): logo, nav with a
// sliding active indicator (the GSAP layoutId slide, done here by measuring the
// active link and CSS-transitioning a single underline), "system active" pill,
// bell, avatar. Reproduced with Tailwind utilities to match 1:1.
// Below `lg` the horizontal nav is display:none and a hamburger opens NavDrawer;
// the bell and avatar grow to 44px touch targets. ponytail: the in-card icon buttons
// on pages and in modals stay as they are — 30px (AccountsTable's ACTION_BTN, the
// per-row check/edit/delete) up to 38px, so up to 14px short of the 44px guideline.
// Bumping them globally would reflow every dense card; the row actions are the ones
// to fix first if this ever bites.
export function AppNav() {
  const { t } = useTranslation();
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const navRef = useRef<HTMLElement>(null);
  const [indicator, setIndicator] = useState({ left: 0, width: 0 });
  const [menuOpen, setMenuOpen] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
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
    let retries = 0;
    const move = () => {
      const active = nav.querySelectorAll('a')[activeIdx];
      if (!(active instanceof HTMLElement)) return;
      const navRect = nav.getBoundingClientRect();
      const rect = active.getBoundingClientRect();
      // The link may not be laid out yet (width 0) right after a route change /
      // before webfonts settle — retry next frame instead of committing a 0-width
      // bar that would otherwise stick (leaving a missing/stray indicator).
      // Bounded, because a 0 width is also *permanent* whenever the nav generates
      // no boxes at all — display:none below `lg` — and an unbounded retry would
      // then re-arm rAF every frame for the rest of the session. A handful of
      // frames covers the transient case; fonts.ready and the ResizeObserver below
      // re-trigger this independently once real widths exist.
      if (rect.width === 0) {
        if (retries >= 10) return;
        retries += 1;
        raf = requestAnimationFrame(move);
        return;
      }
      retries = 0;
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

  // Close the drawer on any route change. The per-link onClick covers a tap, but
  // not back/forward — and a tap on the current route short-circuits in TanStack
  // <Link> without ever firing that handler.
  useEffect(() => {
    setDrawerOpen(false);
  }, [pathname]);

  // …and close it when the viewport reaches `lg`, where the hamburger that opened it
  // is display:none and the horizontal nav is back. A 1024x768 tablet crosses this
  // on rotation, which would otherwise leave a full-screen backdrop over the desktop
  // nav with page scroll still locked.
  useEffect(() => {
    const mql = window.matchMedia('(min-width: 1024px)');
    const close = () => {
      if (mql.matches) setDrawerOpen(false);
    };
    mql.addEventListener('change', close);
    return () => {
      mql.removeEventListener('change', close);
    };
  }, []);

  return (
    <header className="sticky top-0 z-sticky border-b border-line bg-white/85 backdrop-blur-[10px]">
      <div className="mx-auto flex h-14 max-w-[1340px] items-center gap-md px-lg lg:gap-3xl lg:px-2xl">
        <IconButton
          size="touch"
          aria-label={t('shell.menu')}
          aria-expanded={drawerOpen}
          onClick={() => {
            setDrawerOpen(true);
          }}
          className="lg:hidden"
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
            <path d="M4 7h16M4 12h16M4 17h16" />
          </svg>
        </IconButton>

        <div className="flex shrink-0 items-center gap-md">
          <div className="flex h-[26px] w-[26px] items-center justify-center rounded-lg bg-ink">
            <div className="h-[9px] w-[9px] rounded-full bg-primary" />
          </div>
          <span className="text-title font-bold tracking-[-0.01em]">Telebuba</span>
        </div>

        <nav
          ref={navRef}
          className="relative hidden flex-1 items-center gap-lg self-stretch lg:flex"
        >
          {LINKS.map((link, index) => (
            <Link
              key={link.to}
              to={link.to}
              className={`relative flex items-center self-stretch text-lead font-medium transition-colors ${activeIdx === index ? 'text-ink' : 'text-ink-muted hover:text-ink'}`}
            >
              {t(`nav.${link.key}`)}
            </Link>
          ))}
          <span
            aria-hidden
            className="pointer-events-none absolute left-0 top-0 h-[2px] rounded-b-[2px] bg-primary will-change-transform [transform:translateZ(0)] transition-[transform,width,opacity] duration-reveal ease-spring"
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

        {/* ml-auto: the hidden nav no longer contributes the flex-1 that pushed
            this cluster right below `lg`. */}
        <div className="ml-auto flex shrink-0 items-center gap-md">
          <div
            className={`flex items-center gap-sm rounded-full px-md py-md lg:px-md lg:py-tight ${systemActive ? 'bg-success-tint' : 'bg-track'}`}
          >
            <span
              className={`h-[7px] w-[7px] rounded-full ${systemActive ? 'bg-success-dot' : 'bg-ink-subtle'}`}
            />
            {/* sr-only, not `hidden`: below `lg` only the dot shows, but the text has
                to stay in the accessibility tree — a display:none span leaves the
                status with no readable text at all. sr-only is position:absolute, so
                it also leaves the flex row and the pill keeps its dot-only shape.
                No role="status" here: EventSource reconnects on every blip, and a live
                region in the app shell would announce each one on every route. */}
            <span
              className={`sr-only text-body font-medium lg:not-sr-only ${systemActive ? 'text-success-deep' : 'text-ink-muted'}`}
            >
              {systemActive ? t('shell.systemActive') : t('shell.systemOffline')}
            </span>
          </div>
          <button
            type="button"
            aria-label={t('shell.notifications')}
            className="relative flex h-11 w-11 items-center justify-center rounded-full border border-line bg-white text-ink-muted lg:h-[34px] lg:w-[34px]"
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
            <span className="absolute right-[11px] top-[9px] h-[6px] w-[6px] rounded-full border-[1.5px] border-white bg-primary lg:right-[8px] lg:top-[6px]" />
          </button>
          <div className="relative">
            <button
              type="button"
              aria-label={t('shell.account')}
              onClick={() => {
                setMenuOpen((open) => !open);
              }}
              className="flex h-11 w-11 items-center justify-center rounded-full bg-primary text-lead font-semibold text-white lg:h-[34px] lg:w-[34px]"
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
                  className="fixed inset-0 z-raised cursor-default"
                />
                <div className="absolute right-0 top-[48px] z-pop w-[190px] overflow-hidden rounded-lg border border-line bg-white py-xs shadow-pop lg:top-[42px]">
                  {me.data ? (
                    <div className="truncate border-b border-line-row px-md py-sm text-body text-ink-muted">
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
                    className="flex w-full items-center gap-sm px-md py-sm text-left text-lead font-medium text-danger-deep transition-colors max-lg:min-h-[44px] hover:bg-danger-tint"
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

      {drawerOpen ? (
        <NavDrawer
          activeIdx={activeIdx}
          onClose={() => {
            setDrawerOpen(false);
          }}
        />
      ) : null}
    </header>
  );
}
