// Minimal, dependency-free toast store: a module-level queue the non-React
// layers (e.g. the MutationCache onError) can push to, rendered by <Toaster/>
// (in toast.tsx) mounted once at the app root.
//
// Two tones. An error toast is the old one: text only, gone after 5 s. A success
// toast reports an action the operator just took, so it carries a check mark, a
// close button and, optionally, one action (an undo) that dismisses the toast when used.
export interface ToastAction {
  label: string;
  onClick: () => void;
}

export interface Toast {
  id: number;
  message: string;
  tone: 'error' | 'success';
  action?: ToastAction;
}

type Listener = (toasts: Toast[]) => void;

const DURATION_MS = 5000;

let toasts: Toast[] = [];
let nextId = 0;
const listeners = new Set<Listener>();

function emit(): void {
  for (const listener of listeners) listener(toasts);
}

export function dismissToast(id: number): void {
  if (!toasts.some((toast) => toast.id === id)) return;
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

function push(toast: Omit<Toast, 'id'>): number {
  const id = nextId++;
  toasts = [...toasts, { id, ...toast }];
  emit();
  setTimeout(() => {
    dismissToast(id);
  }, DURATION_MS);
  return id;
}

/** Queue a transient error message. Safe to call outside React. */
export function toastError(message: string): void {
  push({ message, tone: 'error' });
}

/** Report a finished action, optionally with one action (e.g. undo). Returns the id. */
export function toastSuccess(message: string, action?: ToastAction): number {
  return push(action ? { message, tone: 'success', action } : { message, tone: 'success' });
}
