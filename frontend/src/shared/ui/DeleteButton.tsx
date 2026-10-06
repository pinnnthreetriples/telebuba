// Ported from Devigner UI, https://ui.devigner.cc/components/delete-button — free to use,
// commercial use included. Restyled onto Telebuba's tokens; the motion is the original's:
// the strokes draw on and retract, the lid swings open on its hinge, the confirm pill
// slides out of the tile on a layout spring, and the tick draws itself once the delete
// lands.
//
// What changed in the port, and why:
//   - Colours, radii, shadow and sizes are tokens. The tile is the `lg` control height
//     (44px, `size-touch`); the pill's buttons are the standalone icon button (28px).
//   - Glyphs are 20px and 16px at the app's one stroke weight (`iconStroke`), rather
//     than the original's heavier 24px/2.5 strokes beside our lighter Lucide set.
//   - Every press shrinks to the system's `pressScale.press` instead of four
//     per-element scales (0.9–0.97).
//   - The spinner is our `Spinner` (`tone="danger"`, the ring of an irreversible action)
//     fading in after the same 120ms grace, instead of a rotated icon.
//   - The `classNames`/`slotProps`/`renderConfirmation` escape hatches are gone: a closed
//     design system does not hand out restyling slots.
import { AnimatePresence, motion, useReducedMotion } from 'motion/react';
import { useEffect, useId, useRef } from 'react';
import type { ComponentProps, HTMLAttributes } from 'react';

import { FOCUS_RING, pressScale, radius, shadow, spring, tween } from '@/shared/design-system';
import { cn } from '@/shared/lib/cn';
import { useControllableState } from '@/shared/lib/useControllableState';

import { iconStroke } from './iconStroke';
import { Spinner } from './Spinner';

export type DeleteButtonStatus = 'idle' | 'armed' | 'pending' | 'done';

const PRESS = Number(pressScale.press);

// Geometry of the trash glyph, in its own 24-unit box, so it scales with the icon.
// The lid pivots on its left rim, opens 20.57°, and drifts back over the bin's centre
// and up off its rim while it does.
const LID_HINGE = '9.244px 7.147px';
const LID_OPEN = -20.57;
const LID_RECENTER = -2;
const LID_LIFT = -1.25;
const BIN =
  'M18.834 8.5l-.46 6.9c-.177 2.654-.266 3.981-1.13 4.79-.866.81-2.196.81-4.857.81h-.773c-2.661 0-3.992 0-4.857-.81-.865-.809-.953-2.136-1.13-4.79l-.46-6.9';
const TICKS = 'M9.5 11l.5 5M14.5 11l-.5 5';
const LID_BAR = 'M3.5 6h17';
const LID_HANDLE =
  'M6.5 6h.11a2 2 0 0 0 1.83-1.32l.034-.103.097-.291c.083-.249.125-.373.18-.479a1.5 1.5 0 0 1 1.094-.788C9.962 3 10.093 3 10.355 3h3.29c.262 0 .393 0 .51.019a1.5 1.5 0 0 1 1.094.788c.055.106.097.23.18.479l.097.291A2 2 0 0 0 17.5 6';
const CHECK = 'M5.5 12.6 9.7 17 18.5 7';
const CLOSE = 'M7.7 7.7 16.3 16.3M16.3 7.7 7.7 16.3';

// Grace before the spinner shows, so a fast delete never flashes it. Not a motion rung:
// it is a threshold under which waiting is not worth showing.
const PENDING_DELAY = 0.12;
// The pill's buttons grow in from here; the cancel button follows the confirm one.
const ENTER_SCALE = 0.9;
const CANCEL_DELAY = 0.07;

const GLYPH = {
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
  'aria-hidden': true,
} as const;

/** One stroke that draws itself on and, with `reverse`, winds back the way it came. */
function DrawnPath({ show, d, reverse = false }: { show: boolean; d: string; reverse?: boolean }) {
  const reduced = useReducedMotion() ?? false;
  const drawing = { ...tween.draw, delay: show ? tween.handoff : 0 };
  return (
    <motion.path
      d={d}
      initial={false}
      animate={{
        pathLength: show || reduced ? 1 : 0,
        pathOffset: reverse && !show && !reduced ? 1 : 0,
        opacity: show ? 1 : 0,
      }}
      transition={
        reduced
          ? tween.fade
          : {
              pathLength: drawing,
              pathOffset: drawing,
              opacity: { duration: 0, delay: show ? tween.handoff : tween.draw.duration },
            }
      }
    />
  );
}

function TrashGlyph({ show, open, mirror }: { show: boolean; open: boolean; mirror: boolean }) {
  return (
    <svg
      {...GLYPH}
      width={20}
      height={20}
      strokeWidth={iconStroke(20)}
      // The lid swings out of the 24-unit box, and an SVG root clips by default.
      className="col-start-1 row-start-1 overflow-visible"
      // The glyph is symmetric, so a flip is the whole mirror: the lid opens toward the
      // actions whichever side they sit on.
      style={mirror ? { transform: 'scaleX(-1)' } : undefined}
    >
      <DrawnPath show={show} reverse d={BIN} />
      <DrawnPath show={show} d={TICKS} />
      <motion.g
        style={{ transformOrigin: LID_HINGE }}
        initial={false}
        animate={{
          rotate: open ? LID_OPEN : 0,
          x: open ? LID_RECENTER : 0,
          y: open ? LID_LIFT : 0,
        }}
        transition={spring.swap}
      >
        <DrawnPath show={show} d={LID_BAR} />
        <DrawnPath show={show} d={LID_HANDLE} />
      </motion.g>
    </svg>
  );
}

function StrokeGlyph({ d, size, show = true }: { d: string; size: 16 | 20; show?: boolean }) {
  return (
    <svg
      {...GLYPH}
      width={size}
      height={size}
      strokeWidth={iconStroke(size)}
      className="col-start-1 row-start-1"
    >
      <DrawnPath show={show} d={d} />
    </svg>
  );
}

// Any thenable, not only a native Promise: a query client's or a test's promise-like
// should be waited on all the same.
function isThenable(value: unknown): value is PromiseLike<unknown> {
  return (
    typeof value === 'object' &&
    value !== null &&
    typeof (value as { then?: unknown }).then === 'function'
  );
}

/** React's drag and animation handlers collide with Motion's own. */
type DivProps = Omit<
  HTMLAttributes<HTMLDivElement>,
  | 'onDrag'
  | 'onDragStart'
  | 'onDragEnd'
  | 'onAnimationStart'
  | 'onAnimationEnd'
  | 'onAnimationIteration'
>;

// The ink lives here, beside the button's own white fill, rather than at the call site:
// a colour and the fill it is read on belong to one class list.
const ACTION_TONE = {
  danger: 'text-danger',
  neutral: 'text-content-primary',
} as const;

function ActionButton({
  label,
  delay,
  tone,
  children,
  ...rest
}: { label: string; delay: number; tone: keyof typeof ACTION_TONE } & Omit<
  ComponentProps<typeof motion.button>,
  'className'
>) {
  const scale = useReducedMotion() === true ? 1 : ENTER_SCALE;
  return (
    <motion.button
      type="button"
      aria-label={label}
      initial={{ opacity: 0, scale }}
      animate={{ opacity: 1, scale: 1, transition: { ...spring.swap, delay } }}
      exit={{ opacity: 0, scale }}
      whileTap={{ scale: PRESS, transition: spring.press }}
      transition={spring.swap}
      className={cn(
        'grid size-icon shrink-0 cursor-pointer place-items-center rounded-full bg-surface-card shadow-seg',
        FOCUS_RING,
        ACTION_TONE[tone],
      )}
      {...rest}
    >
      {children}
    </motion.button>
  );
}

export interface DeleteButtonProps extends DivProps {
  /** Return a promise and the tile waits on it: spinner, then tick — or back to the
   *  trash if it rejects. */
  onConfirm?: () => void | Promise<unknown>;
  /** Controlled confirmation state. */
  status?: DeleteButtonStatus;
  /** Initial confirmation state when uncontrolled. */
  defaultStatus?: DeleteButtonStatus;
  /** Fires whenever the confirmation state requests a transition. */
  onStatusChange?: (status: DeleteButtonStatus) => void;
  /** Cancel, Escape, or a second press on the tile. */
  onCancel?: () => void;
  /** Handles a rejected `onConfirm`. Without it the rejection is re-thrown. */
  onError?: (error: unknown) => void;
  /** The tile's accessible name, open or closed. */
  label?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  /** Announced, and the tile's name, while a returned promise runs. */
  pendingLabel?: string;
  /** Announced, and the tile's name, once the delete has landed. */
  doneLabel?: string;
  /** ms the tick holds before returning to the trash. 0 keeps the tick. */
  resetAfter?: number;
  /** Which side of the trash the confirm pill opens on. */
  side?: 'right' | 'left';
  disabled?: boolean;
}

/**
 * A trash tile that asks before it deletes. Press it and the lid swings open while a
 * pill with confirm (✓, red) and keep (✕) slides out; confirm runs `onConfirm`, waits on
 * its promise, draws a tick and resets. Escape, keep or a second press closes the pill
 * and puts focus back on the tile.
 */
export function DeleteButton({
  onConfirm,
  status,
  defaultStatus = 'idle',
  onStatusChange,
  onCancel,
  onError,
  label = 'Удалить',
  confirmLabel = 'Подтвердить удаление',
  cancelLabel = 'Оставить',
  pendingLabel = 'Удаляю…',
  doneLabel = 'Удалено',
  resetAfter = 1400,
  side = 'right',
  disabled = false,
  className,
  style,
  ...rest
}: DeleteButtonProps) {
  const [phase, setPhase] = useControllableState({
    value: status,
    defaultValue: defaultStatus,
    onValueChange: onStatusChange,
  });
  const onLeft = side === 'left';
  const reduced = useReducedMotion() ?? false;
  const tile = useRef<HTMLButtonElement>(null);
  const pillId = useId();
  // The pill's buttons stay tabbable under `pointer-events-none`, so a disabled control
  // stops rendering the prompt altogether.
  const armed = phase === 'armed' && !disabled;
  const busy = phase === 'pending';
  const done = phase === 'done';
  const tileLabel = busy ? pendingLabel : done ? doneLabel : label;

  // Closing unmounts whatever inside the pill had focus; take it back or it lands on
  // <body>.
  const close = () => {
    setPhase('idle');
    tile.current?.focus();
    onCancel?.();
  };

  const confirm = () => {
    if (phase !== 'armed') return;
    // Confirming unmounts the pill along with this button; the tile claims focus first.
    tile.current?.focus();
    let result: void | Promise<unknown>;
    try {
      result = onConfirm?.();
    } catch (error) {
      setPhase('idle');
      if (onError) onError(error);
      else throw error;
      return;
    }
    if (!isThenable(result)) {
      setPhase('done');
      return;
    }
    setPhase('pending');
    Promise.resolve(result).then(
      () => setPhase((p) => (p === 'pending' ? 'done' : p)),
      (error: unknown) => {
        setPhase((p) => (p === 'pending' ? 'idle' : p));
        if (onError) onError(error);
        else throw error;
      },
    );
  };

  // The cleanup stops a stale timer closing a pill that was just reopened.
  useEffect(() => {
    if (phase !== 'done' || resetAfter <= 0) return;
    const id = setTimeout(() => setPhase('idle'), resetAfter);
    return () => clearTimeout(id);
  }, [phase, resetAfter, setPhase]);

  // Otherwise re-enabling the control reopens a prompt the user never raised.
  useEffect(() => {
    if (disabled) setPhase('idle');
  }, [disabled, setPhase]);

  return (
    <motion.div
      layout={!reduced}
      transition={spring.layout}
      className={cn(
        'relative inline-flex w-max items-center bg-surface-card p-1',
        onLeft && 'flex-row-reverse',
        disabled && 'pointer-events-none opacity-50',
        className,
      )}
      // Inline rather than `rounded-lg shadow-ring`: Motion corrects a radius and a
      // shadow for the scale of a layout animation only when it owns them, and without
      // the correction both stretch while the pill opens.
      style={{ borderRadius: radius.lg, boxShadow: shadow.ring, ...style }}
      {...rest}
      onKeyDown={(e) => {
        rest.onKeyDown?.(e);
        if (e.key !== 'Escape' || !armed) return;
        e.stopPropagation();
        close();
      }}
    >
      <span aria-live="polite" className="sr-only">
        {busy ? pendingLabel : done ? doneLabel : ''}
      </span>
      <motion.button
        ref={tile}
        type="button"
        layout={!reduced}
        transition={spring.layout}
        aria-label={tileLabel}
        aria-expanded={armed}
        aria-controls={armed ? pillId : undefined}
        aria-busy={busy}
        disabled={disabled}
        onClick={() => {
          if (busy || done) return;
          if (armed) {
            close();
            return;
          }
          setPhase('armed');
        }}
        whileTap={{ scale: PRESS, transition: spring.press }}
        style={{ borderRadius: radius.md }}
        className={cn(
          'relative grid size-touch shrink-0 cursor-pointer place-items-center transition-colors hover:bg-surface',
          FOCUS_RING,
          // `success-deep`: the tick sits on the tile's hover step, where the base green is
          // 3.2:1.
          done ? 'text-success-deep' : 'text-content-primary',
        )}
      >
        <TrashGlyph show={phase === 'idle' || armed} open={armed && !reduced} mirror={onLeft} />
        <AnimatePresence initial={false}>
          {busy && (
            <motion.span
              key="spinner"
              className="col-start-1 row-start-1 grid place-items-center"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1, transition: { ...tween.fade, delay: PENDING_DELAY } }}
              exit={{ opacity: 0, transition: { duration: 0 } }}
            >
              <Spinner size="md" tone="danger" />
            </motion.span>
          )}
        </AnimatePresence>
        <StrokeGlyph d={CHECK} size={20} show={done} />
      </motion.button>
      <AnimatePresence mode="popLayout">
        {armed && (
          <motion.div
            key="actions"
            id={pillId}
            layout={!reduced}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={spring.swap}
            style={{ borderRadius: radius.md }}
            className={cn(
              'relative flex h-touch items-center gap-2 bg-canvas px-3',
              onLeft && 'flex-row-reverse',
            )}
          >
            {/* The pill's beak, pointing back at the tile it came out of. */}
            <span
              aria-hidden="true"
              className={cn(
                'pointer-events-none absolute top-1/2 size-node -translate-y-1/2 rotate-45 bg-canvas',
                onLeft ? '-right-1' : '-left-1',
              )}
            />
            <ActionButton label={confirmLabel} delay={0} onClick={confirm} tone="danger">
              <StrokeGlyph d={CHECK} size={16} />
            </ActionButton>
            <ActionButton
              label={cancelLabel}
              delay={reduced ? 0 : CANCEL_DELAY}
              // The pill opens on the safe choice: Enter right after arming keeps.
              autoFocus
              onClick={() => {
                if (phase === 'armed') close();
              }}
              tone="neutral"
            >
              <StrokeGlyph d={CLOSE} size={16} />
            </ActionButton>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}
