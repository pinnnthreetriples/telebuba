import { i18n } from '@/shared/i18n';

// Every protected page's error boundary, wired per page in `router.tsx`. It renders at
// the crashing page's own match, so AppShell — the nav, and "Log out" — stays around
// it: one bad row is not a dead app. Without it the child routes had no boundary at
// all and any render error bubbled to the protected layout's, which announced a
// failed SESSION check, stripped the nav and offered a sign-in that navigates back
// to the same crashing page. The copy points at that nav, which is why this must not
// be the router-wide default: on /login there is no nav, and catching a crash there
// would only hide it from the error tracker. Only translated strings are rendered —
// never the thrown error, which can carry request detail.
export function PageErrorPanel() {
  return (
    <div role="alert" className="p-8 text-[13px] text-ink">
      {i18n.t('shell.pageError')}
    </div>
  );
}
