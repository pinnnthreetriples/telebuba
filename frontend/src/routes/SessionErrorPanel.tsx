import { Link, useRouter, useRouterState } from '@tanstack/react-router';

import { i18n } from '@/shared/i18n';

// The protected layout's error boundary: a failed session check, and nothing else now
// that every page carries a boundary of its own. It REPLACES AppShell, so the nav —
// and with it "Log out" — is gone while it shows: the panel has to carry the way out
// itself. Retry invalidates the router, which re-runs the failed `beforeLoad`. The
// boundary's own `reset` cannot: it only clears the boundary's state, and the
// re-mounted match reads the same stored error and throws it straight back, so the
// panel replaced itself with an identical copy and logged a second error. Reloading
// the page (all the old copy offered) just loops while the backend stays down. Only
// translated strings are rendered — never the thrown error, which can carry request
// detail.
//
// Its own file so `router.tsx` keeps exporting no components (react-refresh).
export function SessionErrorPanel() {
  const router = useRouter();
  // One retry at a time: each click starts its own invalidate → guard run, so rapid
  // clicks stacked them.
  const retrying = useRouterState({ select: (state) => state.isLoading });
  return (
    <div role="alert" className="p-8">
      <p className="text-lead text-ink">{i18n.t('shell.sessionError')}</p>
      <div className="mt-4 flex items-center gap-sm">
        <button
          type="button"
          disabled={retrying}
          onClick={() => {
            void router.invalidate();
          }}
          className="rounded-full bg-primary px-[22px] py-[9px] text-lead font-semibold text-white disabled:opacity-60"
        >
          {i18n.t('shell.sessionRetry')}
        </button>
        <Link
          to="/login"
          className="rounded-full border border-line-input bg-white px-[22px] py-[9px] text-lead font-semibold text-ink"
        >
          {i18n.t('shell.sessionLogin')}
        </Link>
      </div>
    </div>
  );
}
