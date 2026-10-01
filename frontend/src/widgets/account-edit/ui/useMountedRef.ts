import { useEffect, useRef } from 'react';

// Whether the component is still mounted, for an async flow that must not act on
// a dialog the operator already closed. Set on mount as well as cleared on
// unmount: StrictMode runs one extra cleanup in development, and a ref only ever
// cleared would read "unmounted" for the component's whole life.
export function useMountedRef() {
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  return mounted;
}
