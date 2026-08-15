// The shell's six destinations, shared by the desktop top bar (AppNav) and the
// mobile drawer (NavDrawer) so the two can never drift apart. `key` indexes
// `nav.*` in the locale files.
export const NAV_LINKS = [
  { to: '/', key: 'accounts' },
  { to: '/warming', key: 'warming' },
  { to: '/neurocomment', key: 'neurocomment' },
  { to: '/neuroshilling', key: 'neuroshilling' },
  { to: '/logs', key: 'logs' },
  { to: '/settings', key: 'settings' },
] as const;
