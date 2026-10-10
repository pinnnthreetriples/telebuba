import { createContext, useContext, useEffect, useRef } from 'react';

// The unsaved input of a component that lives INSIDE a dialog but owns its own state —
// a wizard step, an editor section — which the dialog itself cannot see. The nearest
// enclosing Modal counts the component as dirty while `dirty` is true and asks before
// any exit, exactly as for its own `dirty` prop. Outside a Modal it does nothing, so a
// component that is also used on a page needs no second variant.
//
// A set of live registrations rather than a re-rendered flag: the guard reads it only at
// the moment of a close, so a keystroke in the step never re-renders the dialog.
export const ModalDirtyContext = createContext<Set<object> | null>(null);

export function useModalDirty(dirty: boolean): void {
  const registry = useContext(ModalDirtyContext);
  const key = useRef<object>({});
  useEffect(() => {
    if (!registry || !dirty) return;
    const own = key.current;
    registry.add(own);
    return () => {
      registry.delete(own);
    };
  }, [registry, dirty]);
}
