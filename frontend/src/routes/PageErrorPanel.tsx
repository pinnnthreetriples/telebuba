import { i18n } from '@/shared/i18n';

// Every page's error boundary (the router's `defaultErrorComponent`). It renders at
// the crashing page's own match, so AppShell — the nav, and "Log out" — stays around
// it: one bad row is not a dead app. Without it the child routes had no boundary at
// all and any render error bubbled to the protected layout's, which announced a
// failed SESSION check, stripped the nav and offered a sign-in that navigates back
// to the same crashing page. Only translated strings are rendered — never the thrown
// error, which can carry request detail.
export function PageErrorPanel() {
  return (
    <div role="alert" className="p-8 text-[13px] text-ink">
      {i18n.t('shell.pageError')}
    </div>
  );
}
