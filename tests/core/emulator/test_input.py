"""Tests for biomechanical input engine: Gaussian taps, Bezier swipes, and keystrokes."""

from __future__ import annotations

import itertools
import random
from typing import TYPE_CHECKING

import pytest

from core.emulator import adb
from core.emulator._errors import EmulatorError
from core.emulator._input import (
    QWERTY_NEIGHBORS,
    HumanInputEngine,
    KeystrokeEvent,
)
from core.emulator.ui_dump import UiNode

if TYPE_CHECKING:
    from tests.core.emulator.conftest import Console


def test_sample_gaussian_tap_bounds_clamping_and_distribution() -> None:
    rng = random.Random(42)  # noqa: S311 - seeded rng for deterministic test
    engine = HumanInputEngine(rng=rng)
    bounds = (100, 200, 300, 400)
    w = bounds[2] - bounds[0]
    h = bounds[3] - bounds[1]
    margin_x = 0.15 * w
    margin_y = 0.15 * h

    taps = [engine.sample_gaussian_tap(bounds) for _ in range(300)]

    for tap in taps:
        assert tap.bounds == bounds
        assert bounds[0] + margin_x <= tap.x <= bounds[2] - margin_x
        assert bounds[1] + margin_y <= tap.y <= bounds[3] - margin_y
        assert 48 <= tap.hold_ms <= 115
        assert tap.release_x - tap.x in {-1, 0, 1, 2}
        assert tap.release_y - tap.y in {-1, 0, 1, 2}

    # Verify right-thumb bias: mean x > geometric center (200), mean y > geometric center (300)
    avg_x = sum(t.x for t in taps) / len(taps)
    avg_y = sum(t.y for t in taps) / len(taps)
    assert avg_x > 200
    assert avg_y > 300


@pytest.mark.parametrize(
    "bounds",
    [
        (10, 10, 11, 11),
        (10, 10, 10, 10),
        (100, 100, 50, 50),
        (0, 0, 1, 1),
    ],
)
def test_sample_gaussian_tap_degenerate_and_inverted_bounds(
    bounds: tuple[int, int, int, int],
) -> None:
    engine = HumanInputEngine(rng=random.Random(42))  # noqa: S311
    min_x, max_x = min(bounds[0], bounds[2]), max(bounds[0], bounds[2])
    min_y, max_y = min(bounds[1], bounds[3]), max(bounds[1], bounds[3])
    for _ in range(50):
        tap = engine.sample_gaussian_tap(bounds)
        assert min_x <= tap.x <= max_x
        assert min_y <= tap.y <= max_y
        assert tap.release_x >= 0
        assert tap.release_y >= 0
        assert 48 <= tap.hold_ms <= 115
        assert tap.bounds == bounds


def test_generate_bezier_swipe_continuity_and_endpoints() -> None:
    rng = random.Random(123)  # noqa: S311
    engine = HumanInputEngine(rng=rng)
    start = (150, 800)
    end = (160, 200)

    traj = engine.generate_bezier_swipe(start, end)

    assert traj.points[0] == start
    assert traj.points[-1] == end
    assert 25 <= len(traj.points) <= 45
    assert len(traj.step_intervals_ms) == len(traj.points) - 1
    assert traj.total_duration_ms == sum(traj.step_intervals_ms)

    for interval in traj.step_intervals_ms:
        assert 8 <= interval <= 16

    # Verify trajectory continuity: consecutive points have bounded distance
    for (x1, y1), (x2, y2) in itertools.pairwise(traj.points):
        step_dist = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
        assert step_dist < 60.0

    assert len(traj.control_points) == 4
    assert traj.control_points[0] == (float(start[0]), float(start[1]))
    assert traj.control_points[3] == (float(end[0]), float(end[1]))


def test_generate_bezier_swipe_explicit_num_steps() -> None:
    engine = HumanInputEngine(rng=random.Random(99))  # noqa: S311
    traj = engine.generate_bezier_swipe((50, 50), (100, 100), num_steps=20)
    assert len(traj.points) == 20
    assert len(traj.step_intervals_ms) == 19


def test_generate_bezier_swipe_lateral_curvature_horizontal() -> None:
    engine = HumanInputEngine(rng=random.Random(42))  # noqa: S311
    start = (100, 500)
    end = (600, 500)  # dy == 0
    traj = engine.generate_bezier_swipe(start, end)

    assert traj.points[0] == start
    assert traj.points[-1] == end
    # Normal is perpendicular to horizontal swipe, so lateral displacement is in y
    p1 = traj.control_points[1]
    p2 = traj.control_points[2]
    assert abs(p1[1] - 500.0) >= 10.0
    assert abs(p2[1] - 500.0) >= 10.0
    # At least some intermediate points curve laterally off y=500
    assert any(pt[1] != 500 for pt in traj.points[1:-1])


def test_generate_bezier_swipe_lateral_curvature_vertical() -> None:
    engine = HumanInputEngine(rng=random.Random(42))  # noqa: S311
    start = (500, 100)
    end = (500, 600)  # dx == 0
    traj = engine.generate_bezier_swipe(start, end)

    assert traj.points[0] == start
    assert traj.points[-1] == end
    # Normal is perpendicular to vertical swipe, so lateral displacement is in x
    p1 = traj.control_points[1]
    p2 = traj.control_points[2]
    assert abs(p1[0] - 500.0) >= 10.0
    assert abs(p2[0] - 500.0) >= 10.0
    # At least some intermediate points curve laterally off x=500
    assert any(pt[0] != 500 for pt in traj.points[1:-1])


def test_generate_bezier_swipe_zero_distance() -> None:
    engine = HumanInputEngine(rng=random.Random(42))  # noqa: S311
    point = (200, 200)
    traj = engine.generate_bezier_swipe(point, point)

    assert traj.points[0] == point
    assert traj.points[-1] == point
    assert all(pt == point for pt in traj.points)
    assert len(traj.points) >= 2
    assert traj.total_duration_ms > 0


def test_generate_bezier_swipe_min_steps_enforced() -> None:
    engine = HumanInputEngine(rng=random.Random(42))  # noqa: S311
    traj1 = engine.generate_bezier_swipe((10, 10), (100, 100), num_steps=1)
    assert len(traj1.points) == 2
    assert len(traj1.step_intervals_ms) == 1
    assert traj1.total_duration_ms > 0

    traj0 = engine.generate_bezier_swipe((10, 10), (100, 100), num_steps=0)
    assert len(traj0.points) == 2


def test_sample_reading_pause_sec_ranges() -> None:
    rng = random.Random(7)  # noqa: S311
    engine = HumanInputEngine(rng=rng)

    samples = [engine.sample_reading_pause_sec() for _ in range(200)]
    for pause_sec, scroll_back in samples:
        assert 1.8 <= pause_sec <= 9.5
        assert isinstance(scroll_back, bool)

    scroll_backs = [s for _, s in samples]
    assert any(scroll_backs)
    assert not all(scroll_backs)


def test_generate_keystroke_sequence_without_typos() -> None:
    engine = HumanInputEngine(rng=random.Random(10))  # noqa: S311
    text = "Hello World!"
    seq = engine.generate_keystroke_sequence(text, typo_probability=0.0)

    typed_chars = [event.char for event in seq if event.key_event is None]
    assert "".join(typed_chars) == text
    assert not any(event.key_event is not None for event in seq)

    for event in seq:
        assert event.delay_ms > 0


def test_generate_keystroke_sequence_with_typos() -> None:
    rng = random.Random(42)  # noqa: S311
    engine = HumanInputEngine(rng=rng)
    seq = engine.generate_keystroke_sequence("a", typo_probability=1.0)

    assert len(seq) == 3
    assert seq[0].key_event is None
    assert seq[0].char in QWERTY_NEIGHBORS["a"]
    assert 80 <= seq[0].delay_ms <= 190

    assert seq[1].key_event == 67
    assert 300 <= seq[1].delay_ms <= 550

    assert seq[2].key_event is None
    assert seq[2].char == "a"


def test_generate_keystroke_sequence_uppercase_typo() -> None:
    rng = random.Random(42)  # noqa: S311
    engine = HumanInputEngine(rng=rng)
    seq = engine.generate_keystroke_sequence("A", typo_probability=1.0)

    assert seq[0].key_event is None
    assert seq[0].char.isupper()
    assert seq[0].char.lower() in QWERTY_NEIGHBORS["a"]


@pytest.mark.asyncio
async def test_adb_human_tap_calls_shell_swipe(console: Console) -> None:
    node = UiNode("", "", "android.widget.Button", password=False, bounds=(100, 200, 300, 400))
    engine = HumanInputEngine(rng=random.Random(42))  # noqa: S311

    await adb.human_tap("emulator-5554", node, engine=engine)

    calls = console.shell_calls()
    assert len(calls) == 1
    call = calls[0]
    assert call[:2] == ["input", "swipe"]
    x, y, rx, ry, hold = (int(v) for v in call[2:7])
    assert 100 <= x <= 300
    assert 200 <= y <= 400
    assert rx - x in {-1, 0, 1, 2}
    assert ry - y in {-1, 0, 1, 2}
    assert 48 <= hold <= 115


@pytest.mark.asyncio
async def test_adb_human_type_types_with_delays(
    console: Console,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slept: list[float] = []

    async def _mock_sleep(sec: float) -> None:
        slept.append(sec)

    monkeypatch.setattr("core.emulator.adb.asyncio.sleep", _mock_sleep)
    engine = HumanInputEngine(rng=random.Random(0))  # noqa: S311

    await adb.human_type("emulator-5554", "s3cret!", engine=engine)

    text_calls = [call for call in console.shell_calls() if call[:2] == ["input", "text"]]
    typed = [call[2] for call in text_calls]
    assert typed == ["s", "3", "c", "r", "e", "t", "\\!"]
    assert len(slept) == len(typed)
    for s in slept:
        assert s > 0


@pytest.mark.asyncio
async def test_adb_human_type_refuses_invalid_text() -> None:
    with pytest.raises(EmulatorError) as exc:
        await adb.human_type("emulator-5554", "100%пароль")
    assert exc.value.code == "emulator_text_unsupported"


@pytest.mark.asyncio
async def test_adb_human_type_with_typo_correction(
    console: Console,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _mock_sleep(_sec: float) -> None:
        return

    monkeypatch.setattr("core.emulator.adb.asyncio.sleep", _mock_sleep)

    class _TypoEngine(HumanInputEngine):
        def generate_keystroke_sequence(
            self, text: str, typo_probability: float = 0.035
        ) -> list[KeystrokeEvent]:
            _ = typo_probability
            return super().generate_keystroke_sequence(text, typo_probability=1.0)

    engine = _TypoEngine(rng=random.Random(42))  # noqa: S311
    await adb.human_type("emulator-5554", "a", engine=engine)

    calls = console.shell_calls()
    assert len(calls) == 3
    assert calls[0][:2] == ["input", "text"]
    assert calls[0][2] in QWERTY_NEIGHBORS["a"]
    assert calls[1] == ["input", "keyevent", "67"]
    assert calls[2] == ["input", "text", "a"]


@pytest.mark.asyncio
async def test_adb_human_swipe_replays_the_whole_curve_in_one_shell_call(
    console: Console,
) -> None:
    twin = HumanInputEngine(rng=random.Random(123))  # noqa: S311
    curve = twin.generate_bezier_swipe((100, 500), (100, 100), twin.rng.randint(8, 12))
    engine = HumanInputEngine(rng=random.Random(123))  # noqa: S311

    await adb.human_swipe("emulator-5554", (100, 500), (100, 100), engine=engine)

    calls = console.shell_calls()
    assert len(calls) == 1, "one adb shell for the whole gesture"
    assert 8 <= len(curve.points) <= 12, "a dozen points: each one is a process on the device"
    commands = " ".join(calls[0]).split(" ; ")
    assert commands == [
        "input motionevent DOWN 100 500",
        *(f"input motionevent MOVE {x} {y}" for x, y in curve.points[1:-1]),
        "input motionevent UP 100 100",
    ]
    assert any(x != 100 for x, _y in curve.points[1:-1]), "the finger arcs off the straight line"


@pytest.mark.asyncio
async def test_adb_human_type_typo_probability_zero(
    console: Console,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _mock_sleep(_sec: float) -> None:
        return

    monkeypatch.setattr("core.emulator.adb.asyncio.sleep", _mock_sleep)
    engine = HumanInputEngine(rng=random.Random(42))  # noqa: S311

    await adb.human_type("emulator-5554", "testing", typo_probability=0.0, engine=engine)

    text_calls = [call for call in console.shell_calls() if call[:2] == ["input", "text"]]
    del_calls = [call for call in console.shell_calls() if call[:2] == ["input", "keyevent"]]
    typed = [call[2] for call in text_calls]
    assert typed == list("testing")
    assert del_calls == []


@pytest.mark.parametrize(
    "bounds",
    [(10, 10, 11, 11), (11, 11, 12, 12), (0, 0, 1, 1), (0, 0, 2, 2), (5, 5, 8, 40)],
)
def test_a_tap_and_its_release_stay_on_the_elements_pixels(
    bounds: tuple[int, int, int, int],
) -> None:
    """Android bounds are half-open: the right and bottom edges belong to the next view."""
    engine = HumanInputEngine(rng=random.Random(3))  # noqa: S311
    x1, y1, x2, y2 = bounds
    for _ in range(200):
        tap = engine.sample_gaussian_tap(bounds)
        assert x1 <= tap.x < x2
        assert y1 <= tap.y < y2
        assert x1 <= tap.release_x < x2
        assert y1 <= tap.release_y < y2


def test_a_fling_is_quick_and_nudged_sideways_only() -> None:
    engine = HumanInputEngine(rng=random.Random(5))  # noqa: S311
    for _ in range(100):
        fling = engine.sample_fling((540, 700), (540, 1700))
        assert fling.start == (540, 700)
        assert fling.end[1] == 1700, "a vertical flick is nudged across, not along"
        assert 3 <= abs(fling.end[0] - 540) <= 12
        assert 120 <= fling.duration_ms <= 180


def test_a_zero_length_fling_stays_on_its_point() -> None:
    fling = HumanInputEngine(rng=random.Random(5)).sample_fling((10, 10), (10, 10))  # noqa: S311
    assert fling.end == (10, 10)


def test_story_view_time_stays_in_range() -> None:
    engine = HumanInputEngine(rng=random.Random(9))  # noqa: S311
    samples = [engine.sample_story_view_sec() for _ in range(300)]
    assert all(2.0 <= s <= 9.0 for s in samples)
    assert len(set(samples)) > 10


@pytest.mark.parametrize("seed", range(20))
def test_a_code_is_typed_steadily_with_a_glance_mid_six_digits(seed: int) -> None:
    engine = HumanInputEngine(rng=random.Random(seed))  # noqa: S311

    events = engine.generate_code_sequence("482913")

    assert "".join(e.char for e in events) == "482913"
    assert all(e.key_event is None for e in events)  # never a typo or a delete
    assert 400 <= events[3].delay_ms <= 600
    for idx, event in enumerate(events):
        if idx != 3:
            assert 140 <= event.delay_ms <= 280


@pytest.mark.parametrize("code", ["4829", "48291", "4829137", "48291375"])
def test_only_a_six_digit_code_gets_the_mid_code_glance(code: str) -> None:
    engine = HumanInputEngine(rng=random.Random(1))  # noqa: S311

    events = engine.generate_code_sequence(code)

    assert [e.char for e in events] == list(code)
    assert all(140 <= e.delay_ms <= 280 for e in events)


def test_digits_typed_as_text_keep_the_text_rhythm() -> None:
    """A numeric password or search is not mistaken for a code: no hidden code branch."""
    delays = [
        event.delay_ms
        for seed in range(20)
        for event in HumanInputEngine(rng=random.Random(seed)).generate_keystroke_sequence(  # noqa: S311
            "482913",
            typo_probability=0.0,
        )
    ]

    assert all(75 <= delay <= 230 for delay in delays)
    assert min(delays) < 140


@pytest.mark.asyncio
async def test_adb_human_type_as_code_uses_the_code_rhythm(
    console: Console,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slept: list[float] = []

    async def _mock_sleep(sec: float) -> None:
        slept.append(sec)

    monkeypatch.setattr("core.emulator.adb.asyncio.sleep", _mock_sleep)
    engine = HumanInputEngine(rng=random.Random(0))  # noqa: S311

    await adb.human_type("emulator-5554", "482913", engine=engine, as_code=True)

    typed = [call[2] for call in console.shell_calls() if call[:2] == ["input", "text"]]
    assert typed == list("482913")
    assert 0.4 <= slept[3] <= 0.6
    assert all(0.14 <= s <= 0.28 for i, s in enumerate(slept) if i != 3)
