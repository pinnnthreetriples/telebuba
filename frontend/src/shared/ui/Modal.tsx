import { type ReactNode, type RefObject, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';

import { surface } from '@/shared/design-system';
import { cn } from '@/shared/lib/cn';

import { ConfirmCard } from './ConfirmCard';
import { ModalDirtyContext } from './useModalDirty';

// Everything a keyboard can land on inside the dialog (for the Tab trap).
const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

// Mount order of open modals. Every Modal listens for Escape on `document`, so
// without this a nested dialog's Escape would also close its parent — only the
// topmost (last-registered) modal should handle it.
const modalStack: object[] = [];

// body.overflow as it was before the FIRST dialog locked it. Module-level, not
// per-instance: a second dialog opening over the first captures 'hidden', because the
// first already wrote it. Whichever instance restores LAST then decides the final
// value, and cleanup order follows document order, so a confirm rendered as the later
// sibling of its parent dialog (Modal's own discard-changes confirm, below) restores
// 'hidden' and leaves the page permanently unscrollable.
// Note it is the SIBLING case that bites. A dialog genuinely nested in another's
// `children` would be fine on its own — mount effects run child-first, so it captures
// the real value before its parent locks anything.
// Gating both the capture and the restore on an empty stack makes the ordering
// irrelevant: only the true 0→1 and 1→0 transitions touch this.
let overflowBeforeLock = '';

// Presets rather than two free `className` props: both halves set the same
// properties (radius, animation, height), and a caller's `rounded-none` beside the
// base `rounded-lg` would depend on Tailwind's emit order, which is no guarantee.
//
// A card taller than the viewport scrolls via the OVERLAY, never via the card. Both
// alternatives are wrong: `overflow-y-auto` on the card computes `overflow-x` to
// `auto` as well, so it clips absolutely-positioned children meant to escape it
// (ListenerEditModal's dropdown; WarmDaysModal's nowrap `.tb-tip-pop`, ~1000px wide at
// opacity 0, which turns into a permanent horizontal scrollbar) — and no cap at all
// leaves a tall dialog clipped at BOTH ends with nothing to scroll, since the overlay
// is `fixed` and `body.overflow` is locked while it is open.
// `m-auto` on the card rather than `items-center` on the overlay: centring a flex item
// with `align-items` makes the overflowing top unreachable once the container scrolls,
// whereas auto margins centre it and still yield to the scroll.
// Поверхность диалога приходит из `recipes/surfaces.ts` — того же набора, что у Card и у
// выпадающей панели. Шторка её не берёт: она прилегает к краю экрана, поэтому у неё нет
// ни радиуса, ни тени, и «поверхность диалога» описывала бы её неверно.
const SHELL = {
  center: {
    overlay: 'justify-center overflow-y-auto overscroll-contain p-4 sm:p-6',
    card: `m-auto tb-arrive ${surface('dialog')}`,
  },
  'drawer-left': {
    overlay: 'items-stretch justify-start',
    // Ширина шторки живёт ЗДЕСЬ, а не приходит от вызывающего. Она не одна из четырёх
    // ступеней ниже — это доля экрана с потолком, — и шторка в приложении одна, поэтому
    // её ширина принадлежит варианту, а не месту вызова. Пока она приходила классом,
    // `className` нельзя было закрыть.
    card: 'flex h-full w-[min(84vw,300px)] flex-col overflow-y-auto overscroll-contain bg-surface-card tb-drawerin',
  },
  // Полноэкранный просмотр фото и сторис: тёмная сцена вместо вуали и без поверхности
  // карточки — медиа само себе поверхность. Вариантом, а не своим порталом, чтобы стек
  // Escape закрывал только просмотр, а ProfileModal под ним оставался открытым.
  viewer: {
    overlay: 'items-stretch justify-stretch bg-term',
    card: 'relative flex h-full w-full items-center justify-center',
  },
} as const;

// Четыре ширины диалога, и это ровно та шкала, ради которой она есть: 22 модалки тратили
// 11 ширин. `confirm` — вопрос с двумя кнопками, `form` — диалог, который заполняют,
// `panel` — со списком или табами, `table` — построенный вокруг таблицы.
//
// Ступенью, а не классом: `size="form"` был открытым концом. Через него приходила
// не только ширина — четыре сайта унесли из скопированной строки чужой `z={75}` и
// `w-[460px]`, — и он же позволял передать ЛЮБОЙ класс диалогу, включая тот, что спорит с
// его собственной поверхностью. Шторка ширину теперь не передаёт (см. выше), поэтому
// `className` у диалога больше нет вовсе.
const SIZE = {
  confirm: 'w-confirm',
  form: 'w-form',
  panel: 'w-panel',
  table: 'w-table',
} as const;

// The design's modal shell: a fixed dimmed backdrop (ovfade) centering a white
// card (fadeup). Backdrop-click and Escape close; the card stops propagation.
// Focus moves into the dialog on open, Tab cycles inside it, and the
// previously-focused element gets focus back on close.
//
// The backdrop is `bg-veil`, one colour for every dialog. It used to be an unbounded
// `backdrop?: number` composed into `rgba(11,11,12,${backdrop})` — the app's only
// inline style-object colour, and a continuous knob where the design has one value.
// Four of the twenty-two call sites had it at 0.45: AddStoryModal, which is where the
// design source's single 0.45 landed, and three that copied AddStoryModal's whole
// `<Modal>` line — its `z={75}` and its `w-[460px]` came along with it. Nothing about
// those four asks for more dark than the rest (ProfileModal, the one full of
// photographs, was on the default), so the second value went rather than acquiring a
// name it could not defend.
//
// Every dialog sits on the same `z-dialog` rung of the config's five-layer
// ladder. There is no per-modal z any more: the six hand-picked values it used to
// take (60 for the drawer, 70/72/75/80 for nesting depth) were encoding the order
// the portal already produces — a nested dialog renders inside its parent, so it
// mounts later and is appended to document.body after it, which at equal z-index
// paints it on top. Toasts are the one thing that must clear an open dialog, and
// they say so with their own rung above this one rather than by mount order.
function ModalShell({
  onClose,
  children,
  size,
  variant,
  label,
  dialogRef,
}: {
  onClose: () => void;
  children: ReactNode;
  size: keyof typeof SIZE;
  variant: keyof typeof SHELL;
  label: string;
  dialogRef: RefObject<HTMLDivElement | null>;
}) {
  const idRef = useRef<object>({});

  // Register in the modal stack for the lifetime of this dialog (mount/unmount
  // only) so the Escape handler can tell whether it is the topmost one.
  useEffect(() => {
    const id = idRef.current;
    // The overlay scrolls, so the page behind it must not: on a phone a scrollable
    // body lets the backdrop drag away under the dialog, and a nested scroll chain
    // hands the overscroll to the page. Only the first dialog locks and only the last
    // unlocks, so a second one closing can't unlock early.
    if (modalStack.length === 0) overflowBeforeLock = document.body.style.overflow;
    modalStack.push(id);
    document.body.style.overflow = 'hidden';
    return () => {
      const index = modalStack.indexOf(id);
      if (index !== -1) modalStack.splice(index, 1);
      if (modalStack.length === 0) document.body.style.overflow = overflowBeforeLock;
    };
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      // A widget inside that already used the key (an inline editor cancelling itself)
      // marks it `defaultPrevented`; the dialog stays.
      if (event.defaultPrevented) return;
      if (event.key === 'Escape' && modalStack[modalStack.length - 1] === idRef.current) {
        onClose();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
    };
  }, [onClose]);

  // Focus the dialog on open; hand focus back to the opener on close. `dialogRef` is a
  // stable ref object owned by Modal, so this still runs on mount/unmount only.
  useEffect(() => {
    const previous = document.activeElement;
    dialogRef.current?.focus();
    return () => {
      if (previous instanceof HTMLElement) previous.focus();
    };
  }, [dialogRef]);

  // Minimal Tab trap: wrap from the last focusable to the first and back.
  const onTrapTab = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'Tab') return;
    const node = dialogRef.current;
    if (!node) return;
    // An inert element still MATCHES the selector but cannot take focus. The
    // .tb-dd dropdowns are inert while closed, so one sitting at either END of
    // this list would make the wrap's .focus() a silent browser no-op — with
    // preventDefault already called, that freezes Tab inside the dialog.
    const focusables = [...node.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(
      (element) => element.closest('[inert]') === null,
    );
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (!first || !last) return;
    if (event.shiftKey && (document.activeElement === first || document.activeElement === node)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  return createPortal(
    <div
      role="presentation"
      onClick={onClose}
      className={cn('fixed inset-0 z-dialog flex bg-veil tb-ovfade', SHELL[variant].overlay)}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={label}
        tabIndex={-1}
        onKeyDown={onTrapTab}
        onClick={(event) => {
          event.stopPropagation();
        }}
        className={cn(
          'max-w-full outline-hidden',
          SHELL[variant].card,
          variant === 'center' && SIZE[size],
        )}
      >
        {children}
      </div>
    </div>,
    document.body,
  );
}

// The guard against losing edits. A dialog that holds unsaved input passes `dirty`, and
// then NO dismissal throws the input away silently: backdrop, Escape and every in-body
// Cancel/× (which get the guarded close as the render-prop argument of `children`) open
// a stacked "close without saving?" question instead. A clean dialog closes at once, as
// before; a save that closes the dialog calls the caller's own `onClose`, not the guarded
// one, so it never asks.
//
// One mechanism here rather than a confirm per dialog: ProfileModal and ChannelEditModal
// each carried their own copy, and the twenty other dialogs that hold a form had none,
// so a stray click on the veil lost a half-filled campaign without a word.
//
// The question is a second shell rendered as a SIBLING of this one, so it sits on top of
// the modal stack (only it handles Escape — Escape on it means "stay") and the scroll
// lock unwinds through the stack-gated `overflowBeforeLock` above. Staying hands focus
// back into the dialog: after a backdrop click the confirm would otherwise return it to
// the body it was taken from.
//
// A component deep in the body that holds its own input (a wizard step) adds to `dirty`
// through `useModalDirty`, without lifting its state into the dialog.
//
// `locked` closes every exit — a write in flight whose result the dialog must still
// receive (an upload loop, a publish) — and outranks `dirty`: a locked dialog neither
// closes nor asks, and a question already open when the lock lands is withdrawn.
export function Modal({
  onClose,
  children,
  size = 'confirm',
  variant = 'center',
  label,
  dirty = false,
  locked = false,
}: {
  onClose: () => void;
  // Either the body, or a function of the guarded close for the body's own Cancel/×
  // buttons — those must ask exactly as the backdrop does.
  children: ReactNode | ((close: () => void) => ReactNode);
  // Ширина диалога. Шторка её игнорирует: её ширина принадлежит варианту.
  size?: keyof typeof SIZE;
  variant?: keyof typeof SHELL;
  // Accessible name for the dialog — REQUIRED, not optional: while it was
  // optional 20 of the 21 call sites left it out and a screen reader announced a
  // nameless "dialog". Every one of them already renders a title; pass that.
  label: string;
  // The dialog holds input that closing would lose: compare the draft to what it opened
  // with, never "the operator touched something".
  dirty?: boolean;
  locked?: boolean;
}) {
  const { t } = useTranslation();
  const dialogRef = useRef<HTMLDivElement>(null);
  const confirmRef = useRef<HTMLDivElement>(null);
  const [asking, setAsking] = useState(false);
  const refocus = useRef(false);
  const [dirtyParts] = useState(() => new Set<object>());

  const close = () => {
    if (locked) return;
    if (dirty || dirtyParts.size > 0) setAsking(true);
    else onClose();
  };
  const stay = () => {
    refocus.current = true;
    setAsking(false);
  };

  // Runs after the confirm's own cleanup has handed focus back to whatever held it when
  // the question opened — passive unmount effects precede mount effects in one commit.
  useEffect(() => {
    if (asking || !refocus.current) return;
    refocus.current = false;
    const node = dialogRef.current;
    if (node && !node.contains(document.activeElement)) node.focus();
  }, [asking]);

  // A write that starts while the question is open locks every exit: the question goes
  // with them, and its «Закрыть без сохранения» must not throw the dialog away mid-write.
  useEffect(() => {
    if (locked) setAsking(false);
  }, [locked]);

  const title = t('dialog.discard.title');
  return (
    <>
      <ModalShell onClose={close} size={size} variant={variant} label={label} dialogRef={dialogRef}>
        <ModalDirtyContext value={dirtyParts}>
          {typeof children === 'function' ? children(close) : children}
        </ModalDirtyContext>
      </ModalShell>
      {asking && !locked ? (
        <ModalShell
          onClose={stay}
          size="confirm"
          variant="center"
          label={title}
          dialogRef={confirmRef}
        >
          <ConfirmCard
            title={title}
            body={t('dialog.discard.body')}
            cancelLabel={t('dialog.discard.stay')}
            confirmLabel={t('dialog.discard.confirm')}
            onCancel={stay}
            onConfirm={() => {
              setAsking(false);
              if (!locked) onClose();
            }}
          />
        </ModalShell>
      ) : null}
    </>
  );
}
