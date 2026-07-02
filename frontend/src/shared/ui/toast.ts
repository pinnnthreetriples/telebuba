// Minimal, dependency-free toast store: a module-level queue the non-React
// layers (e.g. the MutationCache onError) can push to, rendered by <Toaster/>
// (in toast.tsx) mounted once at the app root.
export interface Toast {
  id: number;
  message: string;
}

type Listener = (toasts: Toast[]) => void;

const DURATION_MS = 5000;

let toasts: Toast[] = [];
let nextId = 0;
const listeners = new Set<Listener>();

function emit(): void {
  for (const listener of listeners) listener(toasts);
}

function dismiss(id: number): void {
  toasts = toasts.filter((toast) => toast.id !== id);
  emit();
}

export function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function getToasts(): Toast[] {
  return toasts;
}

/** Queue a transient error message. Safe to call outside React. */
export function toastError(message: string): void {
  const id = nextId++;
  toasts = [...toasts, { id, message }];
  emit();
  setTimeout(() => {
    dismiss(id);
  }, DURATION_MS);
}
