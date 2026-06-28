"""NiceGUI Neurocomment page (issue #119; redesign #155-line).

UI-thin per non-negotiable #1: every handler validates input, calls a
``services.neurocomment`` function, and re-renders. No business logic here — the
campaign/channel/account wiring, onboarding, runtime start/stop and the board
read model all live in ``services/``. Mirrors the *style* of ``features/warming``
(animated pipeline rail + live status + card layout) but imports nothing from it
(no cross-feature imports — non-negotiable #1); the small keyframe set warming
keeps in its own ``__init__`` is re-stated here under a ``tb-nc-`` prefix so the
neurocomment animations travel with this page without coupling the two features.

The whole page is excluded from coverage (``pragma: no cover``) like the other
feature pages — it is exercised manually; the pure helpers it calls are unit-tested.
"""

from __future__ import annotations

from nicegui import ui

from features.neurocomment._page import render_neurocomment_page

__all__ = ["register_neurocomment_page"]

# Neurocomment pipeline animations. Registered once via ``ui.add_css(shared=True)``
# so the keyframes reach every client. ``--i`` (the 0-based step index) is set
# inline per rail node to stagger the launch cascade and the running "wave".
#
#   .tb-nc-rail              the 6-step rail container
#   .tb-nc-rail.tb-nc-on     running — connectors flow, nodes pulse + launch-pop
#   .tb-nc-conn / .tb-nc-node  one connector bar / one step circle
#   .tb-nc-dot               the live-status blinking dot
#   .tb-nc-flash             one-shot highlight when a live counter ticks up
_NC_CSS = """
@keyframes tb-nc-flow {
    0% { background-position: 0% 50%; opacity: 0.5; }
    50% { background-position: 100% 50%; opacity: 1; }
    100% { background-position: 0% 50%; opacity: 0.5; }
}
@keyframes tb-nc-flow-v {
    0% { background-position: 50% 0%; opacity: 0.5; }
    50% { background-position: 50% 100%; opacity: 1; }
    100% { background-position: 50% 0%; opacity: 0.5; }
}
@keyframes tb-nc-wave {
    0%, 100% { box-shadow: 0 0 0 0 rgba(0, 102, 255, 0); }
    50% { box-shadow: 0 0 0 7px rgba(0, 102, 255, 0.16); }
}
@keyframes tb-nc-pop {
    0% { opacity: 0; transform: scale(0.4); }
    60% { opacity: 1; transform: scale(1.14); }
    100% { opacity: 1; transform: scale(1); }
}
@keyframes tb-nc-blink {
    0%, 100% { opacity: 1; transform: scale(1); }
    50% { opacity: 0.35; transform: scale(1.5); }
}
@keyframes tb-nc-flash {
    0% { background-color: rgba(18, 161, 80, 0.30); }
    100% { background-color: transparent; }
}
.tb-nc-conn {
    height: 16px;
    width: 3px;
    margin-top: 2px;
    margin-bottom: 2px;
    border-radius: 9999px;
    background: #DCE2EC;
}
@media (min-width: 768px) {
    .tb-nc-conn {
        height: 3px;
        width: auto;
        flex: 1 1 0%;
        margin-top: 20px;
        margin-bottom: 0px;
    }
}
.tb-nc-rail.tb-nc-on .tb-nc-conn {
    background: linear-gradient(
        180deg,
        rgba(0, 102, 255, 0) 0%,
        rgba(0, 102, 255, 0.9) 50%,
        rgba(0, 102, 255, 0) 100%
    );
    background-size: 100% 200%;
    animation: tb-nc-flow-v 1.6s linear infinite;
}
@media (min-width: 768px) {
    .tb-nc-rail.tb-nc-on .tb-nc-conn {
        background: linear-gradient(
            90deg,
            rgba(0, 102, 255, 0) 0%,
            rgba(0, 102, 255, 0.9) 50%,
            rgba(0, 102, 255, 0) 100%
        );
        background-size: 200% 100%;
        animation: tb-nc-flow 1.6s linear infinite;
    }
}
.tb-nc-rail.tb-nc-on .tb-nc-node {
    animation: tb-nc-pop 0.5s ease both, tb-nc-wave 2.2s ease-in-out infinite;
    animation-delay: calc(var(--i, 0) * 0.1s), calc(0.5s + var(--i, 0) * 0.28s);
}
.tb-nc-dot { animation: tb-nc-blink 1.3s ease-in-out infinite; }
.tb-nc-flash { animation: tb-nc-flash 1.1s ease-out; }
/* Two-column shell: LEFT sidebar (col 1) + RIGHT work column (col 2). */
.tb-nc-grid {
    display: grid;
    grid-template-columns: 340px minmax(0, 1fr);
    gap: 16px;
    align-items: start;
    width: 100%;
}
.tb-nc-grid > .tb-nc-left { grid-column: 1; grid-row: 1; }
.tb-nc-grid > .tb-nc-right { grid-column: 2; grid-row: 1; }
@media (max-width: 900px) {
    .tb-nc-grid { grid-template-columns: minmax(0, 1fr); }
    .tb-nc-grid > .tb-nc-left,
    .tb-nc-grid > .tb-nc-right { grid-column: 1; grid-row: auto; }
}
.tb-nc-log {
    background: #16161A;
    border: 1px solid #2b2b2e;
    border-radius: 10px;
    padding: 12px 14px;
    max-height: 220px;
    overflow-y: auto;
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    line-height: 1.85;
    transition: border-color 0.3s ease, box-shadow 0.3s ease;
}
.tb-nc-log:focus-within,
.tb-nc-log:hover {
    border-color: #0066FF;
    box-shadow: 0 0 10px rgba(0, 102, 255, 0.2);
}
.tb-nc-log-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 2px 0;
    white-space: nowrap;
}
.tb-nc-log-time { color: #5C5C66; font-variant-numeric: tabular-nums; flex: 0 0 auto; }
.tb-nc-log-msg { color: #C9C9CE; flex: 0 0 auto; }
.tb-nc-log-kv { color: #74726E; overflow: hidden; text-overflow: ellipsis; }
.tb-nc-log-empty { color: #5C5C66; font-style: italic; }
.tb-nc-log-ok { color: #9FE6B8; }
.tb-nc-log-warn { color: #FFD27F; }
.tb-nc-log-err { color: #F08C84; }
"""

ui.add_css(_NC_CSS, shared=True)


def register_neurocomment_page() -> None:  # pragma: no cover
    @ui.page("/neurocomment", title="Telebuba — Нейрокомментинг")
    async def neurocomment_page() -> None:
        await render_neurocomment_page()
