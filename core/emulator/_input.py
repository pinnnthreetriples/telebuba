"""Human-biomechanical input engine: Gaussian hitbox taps, Bezier swipes, stochastic typing.

Eliminates robotic geometric-center taps, linear constant-velocity swipes, and
instant clipboard pastes.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

QWERTY_NEIGHBORS: dict[str, str] = {
    "a": "qwsz",
    "b": "vghn",
    "c": "xdfv",
    "d": "ersfxc",
    "e": "wsdr",
    "f": "rtgvcd",
    "g": "tyhbvf",
    "h": "yujnbg",
    "i": "ujko",
    "j": "uikmnh",
    "k": "ijlm",
    "l": "okp",
    "m": "njk",
    "n": "bhjm",
    "o": "iklp",
    "p": "ol",
    "q": "wa",
    "r": "edft",
    "s": "wedxza",
    "t": "rfgy",
    "u": "yhji",
    "v": "cfgb",
    "w": "qase",
    "x": "zsdc",
    "y": "tghu",
    "z": "asx",
}

_SCROLL_BACK_UP_PROBABILITY = 0.12
# A scroll's thumb, as shares of the screen: a right thumb starts right of the centre,
# within this much of its spot on either axis, and drifts sideways a little on the way.
_THUMB_X = 0.56
_THUMB_X_SPREAD = 0.08
_THUMB_Y_SPREAD = 0.04
_THUMB_DRIFT = 0.03
_OTP_MIN_LEN = 4
_OTP_MAX_LEN = 8
_SIX_DIGIT_LEN = 6
_SIX_DIGIT_MID = 3


@dataclass(frozen=True, slots=True)
class TapEvent:
    x: int
    y: int
    release_x: int
    release_y: int
    hold_ms: int
    bounds: tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class SwipeTrajectory:
    points: list[tuple[int, int]]
    step_intervals_ms: list[int]
    total_duration_ms: int
    control_points: list[tuple[float, float]]


@dataclass(frozen=True, slots=True)
class FlingEvent:
    start: tuple[int, int]
    end: tuple[int, int]
    duration_ms: int


@dataclass(frozen=True, slots=True)
class KeystrokeEvent:
    char: str
    delay_ms: int
    key_event: int | None = None


_KEYCODE_DEL: int = 67


def _on_element(value: int, low: int, high: int) -> int:
    """Clamp to ``[low, high)``: Android bounds leave the right and bottom edge out."""
    return max(low, min(high - 1, value)) if high > low else low


class HumanInputEngine:
    """Mathematical synthesizer for human-like touch and typing biomechanics."""

    def __init__(self, rng: random.Random | None = None) -> None:
        self.rng = rng or random.Random()  # nosec B311  # noqa: S311

    def sample_gaussian_tap(self, bounds: tuple[int, int, int, int]) -> TapEvent:
        """Sample a touch point inside UI element bounds [x1, y1, x2, y2].

        - Right-thumb shifted center: mu_x = x1 + 0.52 * W, mu_y = y1 + 0.54 * H
        - Standard deviation: sigma_x = W / 6, sigma_y = H / 6
        - Clamped to inner safe zone (15% padding from element edges)
        - Log-normal hold duration in [48, 115] ms with 1-2px micro-slip on release
        """
        bx1, by1, bx2, by2 = bounds
        x1, x2 = min(bx1, bx2), max(bx1, bx2)
        y1, y2 = min(by1, by2), max(by1, by2)
        tap_x = self._sample_axis(x1, x2, 0.52)
        tap_y = self._sample_axis(y1, y2, 0.54)

        # Log-normal hold duration clamped to [48, 115] ms
        hold_raw = math.exp(self.rng.gauss(math.log(74.0), 0.22))
        hold_ms = round(max(48.0, min(115.0, hold_raw)))

        slip_x = _on_element(tap_x + self.rng.choice([-1, 0, 1, 2]), x1, x2)
        slip_y = _on_element(tap_y + self.rng.choice([-1, 0, 1, 2]), y1, y2)

        return TapEvent(
            x=tap_x,
            y=tap_y,
            release_x=slip_x,
            release_y=slip_y,
            hold_ms=hold_ms,
            bounds=bounds,
        )

    def _sample_axis(self, low: int, high: int, bias: float) -> int:
        """One coordinate on the element's pixels: Gaussian around ``bias``, 15% margin."""
        size = high - low
        if size <= 1:
            return low
        margin = min(max(1.0, 0.15 * size), size / 2.0)
        raw = self.rng.gauss(low + bias * size, size / 6.0)
        return _on_element(round(max(low + margin, min(high - margin, raw))), low, high)

    @staticmethod
    def _cubic_bezier(
        t: float,
        p0: tuple[float, float],
        p1: tuple[float, float],
        p2: tuple[float, float],
        p3: tuple[float, float],
    ) -> tuple[float, float]:
        """Evaluate B(t) = (1-t)^3 P0 + 3(1-t)^2 t P1 + 3(1-t) t^2 P2 + t^3 P3."""
        u = 1.0 - t
        b0 = u**3
        b1 = 3.0 * (u**2) * t
        b2 = 3.0 * u * (t**2)
        b3 = t**3
        bx = b0 * p0[0] + b1 * p1[0] + b2 * p2[0] + b3 * p3[0]
        by = b0 * p0[1] + b1 * p1[1] + b2 * p2[1] + b3 * p3[1]
        return bx, by

    @staticmethod
    def _sigmoid_ease(u: float, steepness: float = 5.8) -> float:
        """Normalize logistic sigmoid on [0, 1] for Ease-In / Ease-Out finger acceleration."""
        low = 1.0 / (1.0 + math.exp(steepness * 0.5))
        high = 1.0 / (1.0 + math.exp(-steepness * 0.5))
        val = 1.0 / (1.0 + math.exp(-steepness * (u - 0.5)))
        return (val - low) / (high - low)

    def generate_bezier_swipe(
        self,
        start: tuple[int, int],
        end: tuple[int, int],
        num_steps: int | None = None,
    ) -> SwipeTrajectory:
        """Build a cubic Bezier swipe trajectory.

        - 15-45px lateral arc on control points P1, P2 (natural thumb curvature)
        - Sigmoid Ease-In / Ease-Out parameterization (25-45 points)
        - 8-16ms per-segment intervals
        """
        steps = max(2, num_steps if num_steps is not None else self.rng.randint(25, 45))
        p0 = (float(start[0]), float(start[1]))
        p3 = (float(end[0]), float(end[1]))

        dx = p3[0] - p0[0]
        dy = p3[1] - p0[1]
        dist = math.hypot(dx, dy)

        if dist < 1.0:
            nx, ny = 0.0, 0.0
            tx, ty = 0.0, 0.0
            arc_mag1 = 0.0
            arc_mag2 = 0.0
            jitter1 = 0.0
            jitter2 = 0.0
        else:
            nx = -dy / dist
            ny = dx / dist
            tx = dx / dist
            ty = dy / dist
            arc_dir = self.rng.choice([-1.0, 1.0])
            arc_mag1 = self.rng.uniform(15.0, 45.0) * arc_dir
            arc_mag2 = self.rng.uniform(15.0, 45.0) * arc_dir
            jitter1 = self.rng.uniform(-10.0, 10.0)
            jitter2 = self.rng.uniform(-10.0, 10.0)

        p1 = (
            p0[0] + 0.30 * dx + arc_mag1 * nx + jitter1 * tx,
            p0[1] + 0.30 * dy + arc_mag1 * ny + jitter1 * ty,
        )
        p2 = (
            p0[0] + 0.72 * dx + arc_mag2 * nx + jitter2 * tx,
            p0[1] + 0.72 * dy + arc_mag2 * ny + jitter2 * ty,
        )

        points: list[tuple[int, int]] = []
        intervals: list[int] = []
        for k in range(steps):
            u = k / float(steps - 1)
            t = self._sigmoid_ease(u, steepness=5.8)
            bx, by = self._cubic_bezier(t, p0, p1, p2, p3)
            points.append((round(bx), round(by)))
            if k < steps - 1:
                intervals.append(self.rng.randint(8, 16))

        return SwipeTrajectory(
            points=points,
            step_intervals_ms=intervals,
            total_duration_ms=sum(intervals),
            control_points=[p0, p1, p2, p3],
        )

    def _between(self, low: float, high: float) -> float:
        """Gaussian around the middle of ``[low, high]``, clamped to it."""
        middle, spread = (low + high) / 2.0, (high - low) / 2.0
        return max(low, min(high, self.rng.gauss(middle, spread / 2.0)))

    def sample_scroll(
        self,
        size: tuple[int, int],
        start_y: float,
        length: tuple[float, float],
        *,
        downward: bool,
    ) -> tuple[tuple[int, int], tuple[int, int]]:
        """Where a scroll's finger lands and lifts on a ``size`` (width, height) screen.

        - Start: right of the centre (a right thumb) +-8% of the width, ``start_y``
          +-4% of the height
        - Travel: ``length`` (shares of the height), down or up, drifting +-3% sideways
        """
        width, height = size
        x = width * self._between(_THUMB_X - _THUMB_X_SPREAD, _THUMB_X + _THUMB_X_SPREAD)
        y = height * self._between(start_y - _THUMB_Y_SPREAD, start_y + _THUMB_Y_SPREAD)
        travel = height * self._between(*length) * (1 if downward else -1)
        end_x = x + width * self._between(-_THUMB_DRIFT, _THUMB_DRIFT)
        return (round(x), round(y)), (round(end_x), round(y + travel))

    def sample_reading_pause_sec(self) -> tuple[float, bool]:
        """Sample reading pause between swipes clamped to [1.8, 9.5]s and scroll-back flag."""
        raw = self.rng.weibullvariate(3.8, 1.7)
        pause_sec = round(max(1.8, min(9.5, raw)), 2)
        scroll_back_up = self.rng.random() < _SCROLL_BACK_UP_PROBABILITY
        return pause_sec, scroll_back_up

    def sample_fling(self, start: tuple[int, int], end: tuple[int, int]) -> FlingEvent:
        """A quick flick Android turns into scroll inertia.

        - 120-180 ms from start to end (a slower ``input swipe`` scrolls without a fling)
        - End point nudged 3-12 px sideways, so no two flicks land on the same line
        """
        dx, dy = end[0] - start[0], end[1] - start[1]
        dist = math.hypot(dx, dy)
        nudge = self.rng.uniform(3.0, 12.0) * self.rng.choice([-1.0, 1.0])
        nx, ny = (-dy / dist, dx / dist) if dist >= 1.0 else (0.0, 0.0)
        return FlingEvent(
            start=start,
            end=(round(end[0] + nudge * nx), round(end[1] + nudge * ny)),
            duration_ms=self.rng.randint(120, 180),
        )

    def sample_story_view_sec(self) -> float:
        """Sample how long one story is looked at: log-normal around 4 s, in [2.0, 9.0]."""
        raw = math.exp(self.rng.gauss(math.log(4.0), 0.35))
        return round(max(2.0, min(9.0, raw)), 2)

    def generate_keystroke_sequence(
        self,
        text: str,
        typo_probability: float = 0.035,
    ) -> list[KeystrokeEvent]:
        """Generate a stochastic character-by-character typing sequence.

        - Inter-key delay: 75-230ms (250-480ms at word boundaries, uppercase, or symbols)
        - For 4-8 digit OTP codes: Gaussian 140-280ms per digit, 400-600ms pause in the
          middle of 6-digit codes (XXX ... XXX).
        - 3-4% probability of typing an adjacent QWERTY key, pausing 300-550ms,
          sending KEYCODE_DEL, and typing the intended character.
        """
        if text.isdigit() and _OTP_MIN_LEN <= len(text) <= _OTP_MAX_LEN:
            otp_events: list[KeystrokeEvent] = []
            for idx, ch in enumerate(text):
                if len(text) == _SIX_DIGIT_LEN and idx == _SIX_DIGIT_MID:
                    delay = self.rng.randint(400, 600)
                else:
                    delay = round(self._between(140.0, 280.0))
                otp_events.append(KeystrokeEvent(char=ch, delay_ms=delay))
            return otp_events

        events: list[KeystrokeEvent] = []
        for idx, ch in enumerate(text):
            low_ch = ch.lower()
            is_boundary = idx > 0 and (ch.isupper() or not ch.isalnum() or text[idx - 1] == " ")

            if low_ch in QWERTY_NEIGHBORS and self.rng.random() < typo_probability:
                wrong_ch = self.rng.choice(QWERTY_NEIGHBORS[low_ch])
                if ch.isupper():
                    wrong_ch = wrong_ch.upper()
                events.append(
                    KeystrokeEvent(
                        char=wrong_ch,
                        delay_ms=self.rng.randint(80, 190),
                    )
                )
                events.append(
                    KeystrokeEvent(
                        char="",
                        delay_ms=self.rng.randint(300, 550),
                        key_event=_KEYCODE_DEL,
                    )
                )

            delay = self.rng.randint(250, 480) if is_boundary else self.rng.randint(75, 230)
            events.append(KeystrokeEvent(char=ch, delay_ms=delay))

        return events
