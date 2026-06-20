"""Per-account warming pipeline rail — the 6-step cycle visual.

UI-thin per non-negotiable #1; every function carries ``# pragma: no cover``.
The pipeline reads only the polled ``WarmingAccountState`` (no new backend
calls, no new polling) and is rendered inside every warming-column kanban
card by ``features/warming/_board.py``.

The six steps are static (online → join → read → react → chat → sleep); the
``_active_step()`` resolver picks which one is *live* right now based on
``card.state`` and ``card.last_action``. The active step pulses, its icon
spins, and a gradient connector flows from the last completed step into the
active one. The detail panel beneath shows live channel/proxy/action data for
the active step; the summary bar at the bottom shows cycle counters.

The rail is gated by the caller — ``_render_card`` only invokes
``render_cycle_pipeline`` when ``card.state != "idle"``, so idle-column cards
stay pixel-for-pixel identical to before.
"""

from __future__ import annotations

import dataclasses
import typing

from nicegui import ui  # ty: ignore[unresolved-import]

from features.warming._board_styling import (
    _PIPELINE_CONNECTOR_ACTIVE,
    _PIPELINE_CONNECTOR_DONE,
    _PIPELINE_CONNECTOR_PENDING,
    _PIPELINE_STEP_ACTIVE,
    _PIPELINE_STEP_DONE,
    _PIPELINE_STEP_ERROR,
    _PIPELINE_STEP_FLOOD,
    _PIPELINE_STEP_PENDING,
    _PIPELINE_STEP_QUAR,
    _relative_eta,
)

if typing.TYPE_CHECKING:
    from schemas.warming import WarmingAccountState


@dataclasses.dataclass(frozen=True, slots=True)
class _Step:  # pragma: no cover
    """Static metadata for one of the six warming pipeline steps.

    ``name`` is the canonical internal id (matches ``last_action`` values from
    ``services.warming``). ``label_ru`` is the short Russian caption shown on
    the rail / tooltip. ``icon`` is the unicode glyph rendered inside the
    step circle — emoji so it matches the rest of the warming card.
    """

    name: str
    label_ru: str
    icon: str


_CYCLE_STEPS: tuple[_Step, ...] = (
    _Step("online", "Онлайн", "wifi"),
    _Step("join", "Каналы", "add_circle"),
    _Step("read", "Чтение", "chrome_reader_mode"),
    _Step("react", "Реакции", "thumb_up"),
    _Step("chat", "Чат", "forum"),
    _Step("sleep", "Сон", "bedtime"),
)

# Reverse lookup so ``_active_step`` can map a ``last_action`` string straight
# to a step index in O(1). Unknown values fall back to index 0 (online) below.
_STEP_INDEX: dict[str, int] = {step.name: idx for idx, step in enumerate(_CYCLE_STEPS)}
_ERROR_DETAIL_MAX_LEN: int = 60
_SLEEP_STEP_INDEX: int = 5


def _next_active_index(card: WarmingAccountState) -> int:  # pragma: no cover
    """Return the index of the step that is active given an ``active`` state.

    Uses ``last_action`` to map to the step that was *just* completed and
    returns the *next* step in the sequence, clamped at ``_SLEEP_STEP_INDEX``
    (sleep / 5). Unknown ``last_action`` falls back to 0 (online).
    """
    last = card.last_action or ""
    if last in _STEP_INDEX:
        return min(_STEP_INDEX[last] + 1, _SLEEP_STEP_INDEX)
    return 0


def _active_step(card: WarmingAccountState) -> tuple[int | None, str]:  # pragma: no cover
    """Pick the index (0..5) and overall kind of the active step.

    Returns ``(None, "quar")`` when no step is currently active (quarantine
    halts the cycle entirely). The kind is independent of the index — it's
    the rail-wide "what flavour of activity is this?" marker (sleep / flood
    / error / quar), while the index points at which step is *live*.

    Resolution rules:
    - ``quarantine``  → ``(None, "quar")`` — entire rail dimmed.
    - ``error``       → ``(last-action-idx, "error")`` — the failing step is
                         pinned; if ``last_action`` is unknown, defaults to
                         ``online`` (idx 0) so the error has a step to land on.
    - ``flood_wait``  → ``(_SLEEP_STEP_INDEX, "flood")`` — engine paused on sleep step.
    - ``sleeping``    → ``(_SLEEP_STEP_INDEX, "sleep")`` — between-cycle cooldown.
    - ``active``      → ``(last-action-idx + 1, "active")`` — the *next* step
                         after what was just done is live. Clamped at 5
                         (sleep) so a finished-action edge case doesn't wrap.
                         Unknown ``last_action`` falls back to idx 0 (online).
    - ``idle``        → ``(None, "active")`` — caller gates; defensive only.
    """
    if card.state == "quarantine":
        return (None, "quar")
    if card.state == "error":
        return (_STEP_INDEX.get(card.last_action or "", 0), "error")
    if card.state in ("flood_wait", "sleeping"):
        kind = "flood" if card.state == "flood_wait" else "sleep"
        return (_SLEEP_STEP_INDEX, kind)
    if card.state == "active":
        return (_next_active_index(card), "active")
    return (None, "active")


def _error_tooltip(step: _Step, card: WarmingAccountState) -> str:  # pragma: no cover
    """Tooltip for an error step: truncated error detail or fallback text."""
    detail = (card.last_error or "").strip() or "без описания"
    if len(detail) > _ERROR_DETAIL_MAX_LEN:
        detail = detail[: _ERROR_DETAIL_MAX_LEN - 1] + "…"
    return f"Ошибка на «{step.label_ru}»: {detail}"


def _flood_tooltip(card: WarmingAccountState) -> str:  # pragma: no cover
    """Tooltip for a flood-wait step: remaining time or rate-limit notice."""
    remaining = _relative_eta(card.flood_wait_until)
    if remaining and remaining != "сейчас":
        return f"Flood-wait · ещё {remaining}"
    if card.flood_wait_seconds is not None:
        return f"Flood-wait · ещё {card.flood_wait_seconds} с"
    return "Flood-wait · Telegram ограничил аккаунт"


def _active_tooltip(step: _Step, card: WarmingAccountState) -> str:  # pragma: no cover
    """Tooltip for an active (live) step.

    The sleep step surfaces the next-run ETA; online does a health-check
    label; all other steps use the per-action Russian label + channel.
    """
    if step.name == "online":
        return "Проверка соединения с Telegram"
    if step.name == "sleep":
        eta = _relative_eta(card.next_run_at)
        return f"Сон до следующего цикла · {eta}" if eta else "Сон до следующего цикла"
    action_ru = {
        "join": "Подключение к каналу",
        "read": "Чтение сообщений",
        "react": "Реакция на сообщение",
        "chat": "Отправка сообщения",
    }.get(step.name, step.label_ru)
    channel = card.last_channel or "—"
    return f"{action_ru} · {channel}"


def _step_tooltip(step: _Step, card: WarmingAccountState, kind: str) -> str:  # pragma: no cover
    """Compose the per-step Russian tooltip text from the polled state.

    ``kind`` mirrors the visual class: ``done | active | pending | error |
    flood | quar``. The sleep step is special-cased to surface the next-run
    ETA so an operator knows exactly when the cycle resumes.
    """
    if kind in ("done", "pending"):
        suffix = "выполнен" if kind == "done" else "ожидает"
        return f"{step.label_ru} · {suffix}"
    if kind == "error":
        return _error_tooltip(step, card)
    if kind == "flood":
        return _flood_tooltip(card)
    if kind == "quar":
        return f"Карантинов: {card.quarantine_count}"
    return _active_tooltip(step, card)


def _connector_kind(left_idx: int, active_idx: int | None, kind: str) -> str:  # pragma: no cover
    """Pick the styling for the connector between step ``left_idx`` and ``left_idx + 1``.

    - ``done``    — the step on the left is already past.
    - ``active``  — left step just finished; the connector flowing into the
                    active step lights up (animated gradient).
    - ``pending`` — both sides are still in the future.
    """
    if active_idx is None:
        return "pending"
    if left_idx < active_idx:
        if left_idx + 1 == active_idx and kind == "active":
            return "active"
        return "done"
    return "pending"


def _step_kind(idx: int, active_idx: int | None, kind: str) -> str:  # pragma: no cover
    """Determine the visual state of step ``idx`` given the active resolution."""
    if kind == "quar":
        return "quar"
    if active_idx is None or idx > active_idx:
        return "pending"
    if idx < active_idx:
        return "done"
    return {"flood": "flood", "error": "error"}.get(kind, "active")


def render_cycle_pipeline(card: WarmingAccountState) -> None:  # pragma: no cover
    """Top-level entry point — full pipeline (rail + detail + summary).

    Caller is responsible for not calling this for idle cards (see
    ``_board._render_card``). The rendered block lives inside a single
    card element and uses vertical stacking so each section is its own line.
    """
    active_idx, kind = _active_step(card)
    with ui.column().classes("w-full gap-1.5"):
        _render_step_rail(card, active_idx, kind)
        _render_active_detail(card, active_idx, kind)
        _render_cycle_summary(card)


def _render_step_rail(  # pragma: no cover
    card: WarmingAccountState,
    active_idx: int | None,
    kind: str,
) -> None:
    """The 6-step horizontal rail with connectors between them."""
    connector_cls = {
        "done": _PIPELINE_CONNECTOR_DONE,
        "active": _PIPELINE_CONNECTOR_ACTIVE,
        "pending": _PIPELINE_CONNECTOR_PENDING,
    }
    step_cls = {
        "done": _PIPELINE_STEP_DONE,
        "active": _PIPELINE_STEP_ACTIVE,
        "pending": _PIPELINE_STEP_PENDING,
        "error": _PIPELINE_STEP_ERROR,
        "flood": _PIPELINE_STEP_FLOOD,
        "quar": _PIPELINE_STEP_QUAR,
    }
    with ui.row().classes("w-full items-center gap-1"):
        for idx, step in enumerate(_CYCLE_STEPS):
            if idx > 0:
                left_kind = _connector_kind(idx - 1, active_idx, kind)
                ui.element("div").classes(
                    f"tb-connector flex-1 h-1 rounded-full {connector_cls[left_kind]}",
                )
            sk = _step_kind(idx, active_idx, kind)
            cls = step_cls[sk]
            tooltip = _step_tooltip(step, card, sk)
            icon_extra = " tb-step-active-icon" if sk == "active" else ""
            circle = ui.element("div").classes(
                f"w-9 h-9 rounded-full flex items-center justify-center shrink-0 {cls}",
            )
            with circle:
                ui.icon(step.icon).classes(f"text-sm{icon_extra}")
            circle.tooltip(tooltip)


def _render_active_detail(  # pragma: no cover
    card: WarmingAccountState,
    active_idx: int | None,
    kind: str,
) -> None:
    """Detail panel under the rail — one line of live data for the live step."""
    if active_idx is None:
        if kind == "quar":
            text = f"Карантин ({card.quarantine_count} случаев) — цикл приостановлен"
        else:
            return
    elif active_idx == _SLEEP_STEP_INDEX and kind == "sleep":
        eta = _relative_eta(card.next_run_at)
        text = f"Сон до следующего цикла · {eta}" if eta else "Сон до следующего цикла"
    elif active_idx == _SLEEP_STEP_INDEX and kind == "flood":
        text = _flood_tooltip(card)
    else:
        text = _render_step_detail_body(card, active_idx)
    ui.label(text).classes("text-[11px] text-slate-600 px-1 truncate")


def _render_step_detail_body(  # pragma: no cover
    card: WarmingAccountState,
    step_idx: int,
) -> str:
    """Compose the detail string for a non-sleep active step.

    Pulls channel / proxy / last event straight off the polled card. Each
    piece is optional and only added when present, so the line never reads
    as a row of dashes.
    """
    step = _CYCLE_STEPS[step_idx]
    parts: list[str] = []
    if card.last_channel:
        parts.append(f"канал: {card.last_channel}")
    if card.proxy_snapshot:
        proxy = card.proxy_snapshot
        if card.proxy_country:
            proxy = f"{proxy} ({card.proxy_country})"
        parts.append(f"прокси: {proxy}")
    if card.last_event:
        parts.append(f"событие: {card.last_event}")
    if not parts:
        return f"{step.label_ru} · данные появятся в следующем опросе"
    return f"{step.label_ru} · " + " · ".join(parts)


def _render_cycle_summary(card: WarmingAccountState) -> None:  # pragma: no cover
    """Single-line cycle counter strip — cycles, daily cap, next run, trust.

    Kept on a single row with the same 11 px slate text as the rest of the
    card so the visual rhythm doesn't break. The next-run chip is suppressed
    in the error state — a stale ETA would be a false promise since reconcile
    / loop skip error'd accounts (mirrors the behaviour of the stats footer).
    """
    with ui.row().classes("w-full items-center gap-3 flex-wrap"):
        ui.label(f"🔁 {card.cycles_completed}").classes(
            "text-[11px] text-slate-600 tabular-nums",
        )
        if card.daily_cap > 0:
            ui.label(f"📊 {card.daily_actions}/{card.daily_cap}").classes(
                "text-[11px] text-slate-600 tabular-nums",
            )
        else:
            ui.label(f"📊 {card.daily_actions}").classes(
                "text-[11px] text-slate-600 tabular-nums",
            )
        if card.state != "error":
            eta = _relative_eta(card.next_run_at)
            if eta:
                ui.label(f"⏭ {eta}").classes("text-[11px] text-slate-500 tabular-nums")
        if card.trust_score is not None:
            ui.label(f"⛨ {card.trust_score}").classes(
                "text-[11px] text-slate-500 tabular-nums",
            )
