"""Human-biomechanical input engine: Gaussian hitbox taps, Bezier swipes, stochastic typing.

Eliminates robotic geometric-center taps, linear constant-velocity swipes, and
instant clipboard pastes.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any

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
        x1, y1, x2, y2 = bounds
        w = max(4, x2 - x1)
        h = max(4, y2 - y1)

        mu_x = x1 + 0.52 * w
        mu_y = y1 + 0.54 * h
        sigma_x = w / 6.0
        sigma_y = h / 6.0

        margin_x = max(1.0, 0.15 * w)
        margin_y = max(1.0, 0.15 * h)
        min_x, max_x = x1 + margin_x, x2 - margin_x
        min_y, max_y = y1 + margin_y, y2 - margin_y
        if min_x > max_x:
            min_x = max_x = x1 + w / 2.0
        if min_y > max_y:
            min_y = max_y = y1 + h / 2.0

        raw_x = self.rng.gauss(mu_x, sigma_x)
        raw_y = self.rng.gauss(mu_y, sigma_y)
        tap_x = round(max(min_x, min(max_x, raw_x)))
        tap_y = round(max(min_y, min(max_y, raw_y)))

        # Log-normal hold duration clamped to [48, 115] ms
        hold_raw = math.exp(self.rng.gauss(math.log(74.0), 0.22))
        hold_ms = round(max(48.0, min(115.0, hold_raw)))

        slip_x = tap_x + self.rng.choice([-1, 0, 1, 2])
        slip_y = tap_y + self.rng.choice([-1, 0, 1, 2])

        return TapEvent(
            x=tap_x,
            y=tap_y,
            release_x=slip_x,
            release_y=slip_y,
            hold_ms=hold_ms,
            bounds=bounds,
        )

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
        steps = num_steps if num_steps is not None else self.rng.randint(25, 45)
        p0 = (float(start[0]), float(start[1]))
        p3 = (float(end[0]), float(end[1]))

        dx = p3[0] - p0[0]
        dy = p3[1] - p0[1]
        arc_dir = self.rng.choice([-1.0, 1.0])
        arc_mag1 = self.rng.uniform(15.0, 45.0) * arc_dir
        arc_mag2 = self.rng.uniform(15.0, 45.0) * arc_dir

        p1 = (p0[0] + 0.30 * dx + arc_mag1, p0[1] + 0.30 * dy + self.rng.uniform(-10.0, 10.0))
        p2 = (p0[0] + 0.72 * dx + arc_mag2, p0[1] + 0.72 * dy + self.rng.uniform(-10.0, 10.0))

        points: list[tuple[int, int]] = []
        intervals: list[int] = []
        for k in range(steps):
            u = k / float(max(1, steps - 1))
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

    def sample_reading_pause_sec(self) -> tuple[float, bool]:
        """Sample reading pause between swipes clamped to [1.8, 9.5]s and scroll-back flag."""
        raw = self.rng.weibullvariate(3.8, 1.7)
        pause_sec = round(max(1.8, min(9.5, raw)), 2)
        scroll_back_up = self.rng.random() < _SCROLL_BACK_UP_PROBABILITY
        return pause_sec, scroll_back_up

    def generate_keystroke_sequence(
        self,
        text: str,
        typo_probability: float = 0.035,
    ) -> list[dict[str, Any]]:
        """Generate a stochastic character-by-character typing sequence.

        - Inter-key delay: 75-230ms (250-480ms at word boundaries, uppercase, or symbols)
        - 3-4% probability of typing an adjacent QWERTY key, pausing 300-550ms,
          sending KEYCODE_DEL, and typing the intended character.
        """
        events: list[dict[str, Any]] = []
        for idx, ch in enumerate(text):
            low_ch = ch.lower()
            is_boundary = idx > 0 and (ch.isupper() or not ch.isalnum() or text[idx - 1] == " ")

            if low_ch in QWERTY_NEIGHBORS and self.rng.random() < typo_probability:
                wrong_ch = self.rng.choice(QWERTY_NEIGHBORS[low_ch])
                if ch.isupper():
                    wrong_ch = wrong_ch.upper()
                events.append(
                    {"action": "char", "value": wrong_ch, "delay_ms": self.rng.randint(80, 190)}
                )
                events.append(
                    {
                        "action": "KEYCODE_DEL",
                        "value": "BACKSPACE",
                        "delay_ms": self.rng.randint(300, 550),
                    }
                )

            delay = self.rng.randint(250, 480) if is_boundary else self.rng.randint(75, 230)
            events.append({"action": "char", "value": ch, "delay_ms": delay})

        return events


_DEFAULT_ENGINE = HumanInputEngine()


def sample_gaussian_tap(bounds: tuple[int, int, int, int]) -> TapEvent:
    return _DEFAULT_ENGINE.sample_gaussian_tap(bounds)


def generate_bezier_swipe(
    start: tuple[int, int],
    end: tuple[int, int],
    num_steps: int | None = None,
) -> SwipeTrajectory:
    return _DEFAULT_ENGINE.generate_bezier_swipe(start, end, num_steps=num_steps)


def sample_reading_pause_sec() -> tuple[float, bool]:
    return _DEFAULT_ENGINE.sample_reading_pause_sec()


def generate_keystroke_sequence(text: str) -> list[dict[str, Any]]:
    return _DEFAULT_ENGINE.generate_keystroke_sequence(text)
