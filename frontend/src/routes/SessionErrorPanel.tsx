import { Link } from '@tanstack/react-router';

import { i18n } from '@/shared/i18n';

// The protected layout's error boundary. It REPLACES AppShell, so the nav — and with
// it "Log out" — is gone while it shows: the panel has to carry the way out itself.
// `reset` re-runs the failed beforeLoad, which is what a backend hiccup or a dropped
// connection needs; reloading the page (all the old copy offered) just loops while the
// backend stays down. Only translated strings are rendered — never the thrown error,
// which can carry request detail.
//
// Its own file so `router.tsx` keeps exporting no components (react-refresh).
export function SessionErrorPanel({ reset }: { reset: () => void }) {
  return (
    <div role="alert" className="p-8">
      <p className="text-[13px] text-ink">{i18n.t('shell.sessionError')}</p>
      <div className="mt-4 flex items-center gap-2">
        <button
          type="button"
          onClick={reset}
          className="rounded-full bg-primary px-[18px] py-[9px] text-[13px] font-semibold text-white"
        >
          {i18n.t('shell.sessionRetry')}
        </button>
        <Link
          to="/login"
          className="rounded-full border border-line-input bg-white px-[18px] py-[9px] text-[13px] font-medium text-ink"
        >
          {i18n.t('shell.sessionLogin')}
        </Link>
      </div>
    </div>
  );
}
