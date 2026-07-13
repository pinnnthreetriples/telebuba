import { useEffect } from 'react';

// A stray file drop outside a dropzone (or onto a busy scrim) makes the
// browser navigate to the file, unloading the SPA — fatal mid-upload. Cancel
// dragover/drop at the window level so the default navigation never fires.
// Real dropzones handle (and cancel) these events on their own elements before
// they bubble here, so they keep working.
export function useWindowFileDropGuard(): void {
  useEffect(() => {
    const prevent = (event: DragEvent) => {
      event.preventDefault();
    };
    window.addEventListener('dragover', prevent);
    window.addEventListener('drop', prevent);
    return () => {
      window.removeEventListener('dragover', prevent);
      window.removeEventListener('drop', prevent);
    };
  }, []);
}
