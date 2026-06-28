"""Neurocomment pure-display helpers — label maps, pipeline steps, board digests.

Split out of ``_page`` to keep that module within the file-size budget. This module
is pure: it holds the page's only unit-tested logic (translation maps, the static
pipeline-step table, fleet aggregation, and the anti-flicker signatures) and imports
no NiceGUI. ``_page`` re-exports everything here, so existing importers
(``_workview`` / ``_engine_panel`` / ``_explainer``) are unaffected. Covered in
``tests/features/test_neurocomment_labels.py``.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from schemas.challenge import ChallengeRow
    from schemas.neurocomment import (
        CampaignList,
        NeurocommentBoard,
        NeurocommentRuntimeStatus,
    )

# Pure-display maps (the page's only unit-tested logic).
_CHANNEL_STATUS_RU: dict[str, str] = {
    "ready": "Готов",
    "comments_off": "Комментарии выключены",
    "join_by_request": "Вступление по заявке",
    "chat_restricted": "Блок записи (Telegram)",
    "bot_challenge": "Капча бота",
    "bot_challenge_backoff": "Капча бота · пауза",
    "throttled": "Лимит исчерпан",
}
# Each status renders a distinct badge icon (Ф2 #120). ``help_outline`` is the
# fallback for an unmapped status.
_CHANNEL_STATUS_ICON: dict[str, str] = {
    "ready": "check_circle",
    "comments_off": "comments_disabled",
    "join_by_request": "pending",
    "chat_restricted": "block",
    "bot_challenge": "smart_toy",
    "bot_challenge_backoff": "hourglass_empty",
    "throttled": "speed",
}
_HEALTH_RU: dict[str, str] = {"ready": "Готов", "blocked": "Заблокирован"}
_CAMPAIGN_STATUS_RU: dict[str, str] = {
    "active": "Активна",
    "paused": "На паузе",
    "archived": "В архиве",
}


def channel_status_label(status: str) -> str:
    """Russian label for a channel-row status (fallback: raw status)."""
    return _CHANNEL_STATUS_RU.get(status, status)


def channel_status_icon(status: str) -> str:
    """Badge icon for a channel-row status (fallback: a generic help icon)."""
    return _CHANNEL_STATUS_ICON.get(status, "help_outline")


# Channel status → (badge bg, text/dot color), following the design's boardMap
# palette: ready = green, challenge = orange/amber, restriction = red, else grey.
_CHANNEL_STATUS_COLORS: dict[str, tuple[str, str]] = {
    "ready": ("#DDF7E9", "#12A150"),
    "comments_off": ("#FDE6E2", "#E5372A"),
    "chat_restricted": ("#FDE6E2", "#E5372A"),
    "join_by_request": ("#FFF0D2", "#E08700"),
    "bot_challenge": ("#FFEAD6", "#E0670A"),
    "bot_challenge_backoff": ("#FFF0D2", "#E08700"),
    "throttled": ("#FFF0D2", "#E08700"),
}
_CHANNEL_STATUS_DEFAULT_COLORS: tuple[str, str] = ("#ECEAE6", "#6B6864")


def channel_status_colors(status: str) -> tuple[str, str]:
    """(badge-bg, text-color) for a channel-row status pill (fallback: neutral grey)."""
    return _CHANNEL_STATUS_COLORS.get(status, _CHANNEL_STATUS_DEFAULT_COLORS)


def challenge_summary(row: ChallengeRow) -> str:
    """Drill-down summary of a failed challenge: raw text + buttons + Gemini reasoning."""
    buttons = " · ".join(row.button_labels) if row.button_labels else "—"
    text = row.raw_text.strip() or "(без текста)"
    base = f"{text} — [{buttons}]"
    if row.reasoning:
        base += f" · {row.reasoning}"
    return base


def counter_window_since(window: str, now: datetime) -> str:
    """ISO lower bound for the header-counter window; ``""`` (all rows) for 'all'."""
    if window == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    if window == "7d":
        return (now - timedelta(days=7)).isoformat()
    return ""


def solver_switch_key(value: bool | None) -> str:  # noqa: FBT001 - tri-state value, not a flag
    """Map a campaign's ``solver_enabled`` to the per-campaign switch key."""
    if value is None:
        return "follow"
    return "on" if value else "off"


def health_label(health: str) -> str:
    """Russian label for an account-card health (fallback: raw value)."""
    return _HEALTH_RU.get(health, health)


def campaign_status_label(status: str) -> str:
    """Russian label for a campaign status (fallback: raw status)."""
    return _CAMPAIGN_STATUS_RU.get(status, status)


def campaign_options(campaigns: CampaignList) -> dict[str, str]:
    """Switcher options: campaign id → ``name · <status>`` label."""
    return {
        c.campaign_id: f"{c.name} · {campaign_status_label(c.status)}" for c in campaigns.campaigns
    }


@dataclasses.dataclass(frozen=True, slots=True)
class PipelineStep:
    """One step of the on-post pipeline (rail + explainer): name, label, icon, detail."""

    name: str
    label: str
    icon: str
    detail: str


# Six educational steps, static + ordered to match services.neurocomment.engine.
# Labels + descriptions follow the design spec's NSTEPS / NDESC (section C.4).
PIPELINE_STEPS: tuple[PipelineStep, ...] = (
    PipelineStep(
        "post",
        "Новый пост",
        "post_add",
        "Получен новый пост в отслеживаемом канале",
    ),
    PipelineStep(
        "select",
        "Выбор аккаунта",
        "person_search",
        "Подобран подходящий аккаунт из пула",
    ),
    PipelineStep(
        "generate",
        "Генерация",
        "auto_awesome",
        "Gemini генерирует релевантный комментарий…",
    ),
    PipelineStep(
        "publish",
        "Публикация",
        "send",
        "Комментарий публикуется от имени аккаунта",
    ),
    PipelineStep(
        "monitor",
        "Проверка",
        "verified_user",
        "Проверка на бот-чек и модерацию",
    ),
    PipelineStep(
        "done",
        "Готово",
        "check_circle",
        "Комментарий принят — цикл завершён",
    ),
)


class FleetActivity(NamedTuple):
    """Fleet-wide live counters for the engine panel, summed from the board (pure)."""

    comments_last_hour: int
    comments_today: int
    ready_accounts: int
    total_accounts: int
    ready_channels: int
    total_channels: int


def fleet_activity(board: NeurocommentBoard) -> FleetActivity:
    """Aggregate the per-account / per-channel board figures into fleet totals."""
    return FleetActivity(
        comments_last_hour=sum(c.comments_last_hour for c in board.accounts),
        comments_today=sum(c.comments_today for c in board.accounts),
        ready_accounts=sum(1 for c in board.accounts if c.health == "ready"),
        total_accounts=len(board.accounts),
        ready_channels=sum(1 for r in board.channels if r.status == "ready"),
        total_channels=len(board.channels),
    )


# Channel statuses that mean a bot-challenge (captcha) is pending or backing off.
_CAPTCHA_STATUSES: frozenset[str] = frozenset({"bot_challenge", "bot_challenge_backoff"})


def board_captcha_count(board: NeurocommentBoard | None) -> int:
    """Channels currently sitting on a bot-challenge — the «капчи» stat (pure)."""
    if board is None:
        return 0
    return sum(1 for r in board.channels if r.status in _CAPTCHA_STATUSES)


def board_error_count(board: NeurocommentBoard | None) -> int:
    """Blocked accounts — the closest live «ошибок» signal on the board (pure)."""
    if board is None:
        return 0
    return sum(1 for c in board.accounts if c.health != "ready")


def relative_time(iso: str | None, now: datetime) -> str | None:
    """Human «X назад» for a past ISO timestamp; ``None`` when missing/unparseable."""
    if not iso:
        return None
    try:
        stamp = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    delta = (now - stamp).total_seconds()
    if delta < 60:  # noqa: PLR2004 - the minute boundary reads clearer inline than as a constant
        return "только что"
    if delta < 3600:  # noqa: PLR2004 - hour in seconds
        return f"{int(delta // 60)} мин назад"
    if delta < 86_400:  # noqa: PLR2004 - day in seconds
        return f"{int(delta // 3600)} ч назад"
    return f"{int(delta // 86_400)} д назад"


def runtime_status_text(status: NeurocommentRuntimeStatus) -> str:
    """Short pill label for the engine's running state."""
    if not status.running:
        return "Движок остановлен"
    if status.active_channels == 0:
        return "Движок запущен"
    return f"Слушаю каналов: {status.active_channels}"


def start_block_reason(ready_accounts: int, *, has_listener: bool) -> str | None:
    """Why «Запустить» must stay blocked, or ``None`` when the engine may start.

    Ties the operator-facing warning to the real gate: starting with no listener or
    with zero ready accounts is a silent no-op that erodes trust, so the button refuses
    and names what is missing — the «нет прогретых аккаунтов» message and the actual
    behaviour now agree.
    """
    if not has_listener:
        return "Выберите аккаунт-слушатель"
    if ready_accounts <= 0:
        return "Нет готовых аккаунтов — добавьте и онбордните их в «Настройке»."
    return None


def board_content_signature(board: NeurocommentBoard | None) -> tuple[object, ...]:
    """Digest of everything the work view renders; equal digests → skip the re-render.

    Anti-flicker: the work view polls every few seconds but only re-renders the board
    when this signature changes (mirrors warming's ``_card_signature`` gate), so an
    idle board no longer blinks.
    """
    if board is None:
        return ()
    return (
        board.solver_enabled,
        tuple((r.channel, r.status, r.ready_accounts, r.total_accounts) for r in board.channels),
        tuple(
            (
                c.account_id,
                c.health,
                c.comments_last_hour,
                c.max_comments_per_hour,
                c.comments_today,
                c.trust_score,
                c.trust_band,
                c.spam_status,
                c.last_comment_at,
            )
            for c in board.accounts
        ),
    )


def live_signature(
    status: NeurocommentRuntimeStatus,
    activity: FleetActivity,
    last_comment: str | None,
) -> tuple[object, ...]:
    """Digest of the engine panel's live section (status pill + ticker + counters).

    ``last_comment`` is the already-bucketed «X назад» string, so the relative clock
    only flips the digest about once a minute instead of every poll — no 4 s blink.
    """
    return (status.running, status.active_channels, *activity, last_comment)
