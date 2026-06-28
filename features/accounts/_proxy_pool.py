"""Proxy-pool card (design-spec §C.1.1).

DEVIATION — the design's proxy pool is a shared, app-level list of proxies an
operator can hand out to accounts; this app has no such backend. Proxies are
configured **per account** (``AccountProxy`` via the table's proxy action), so
there is no pool data to populate and no pool-add service to call. Rather than
fake data or wire a dead button, this card renders the spec's *empty state*
verbatim and points the operator at the per-account proxy editor. Pure markup;
no service calls, no state.
"""

from __future__ import annotations

from nicegui import ui

_EMPTY_STATE_HTML = """
<div style="display:flex;flex-direction:column;align-items:center;
            text-align:center;padding:34px 16px 30px;gap:10px">
  <div style="width:46px;height:46px;border-radius:14px;background:#F1EFED;
              color:#9A9893;display:flex;align-items:center;justify-content:center">
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
      <rect x="2" y="3" width="20" height="7" rx="1.6"/>
      <rect x="2" y="14" width="20" height="7" rx="1.6"/>
      <path d="M6 6.5h.01M6 17.5h.01"/></svg>
  </div>
  <div style="font-size:13.5px;font-weight:600;color:#0B0B0C">Пул пуст</div>
  <div style="font-size:12px;color:#9A9893;max-width:320px;line-height:1.5">
    Прокси настраиваются для каждого аккаунта отдельно — откройте карточку
    аккаунта и нажмите «Настройки прокси». Один прокси может обслуживать
    до&nbsp;3&nbsp;аккаунтов.
  </div>
</div>
"""


def _build_proxy_pool() -> None:  # pragma: no cover
    with ui.element("div").classes("tb-card w-full"):
        with ui.row().classes("w-full items-center").style("gap:8px"):
            ui.html('<span class="tb-title-lg">Прокси-пул</span>')
            ui.html('<span class="tb-muted">1 прокси — на 3 аккаунта</span>')
        ui.html(_EMPTY_STATE_HTML)
