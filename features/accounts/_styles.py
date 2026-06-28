"""Accounts-page CSS — the bits the shared component vocabulary doesn't cover.

Everything reusable (cards, buttons, badges, inputs, the table chrome) lives in
``features/shared/components.css``; this module only adds the few accounts-only
visuals from design-spec §C.1: the status-coloured mono avatar, the expandable
search box, the alive-check button's idle/loading/ok/err states, the proxy-pool
grid, and the trust bar. Exported as ``ACCOUNTS_CSS`` and registered once by
``_page.py`` via ``ui.add_css(..., shared=True)`` — the same pattern
``features/shared/theme.py`` uses — and scoped under ``.tb-acc`` so it never
leaks into other pages.
"""

from __future__ import annotations

# Quasar paints the accounts ``q-table`` with its own borders, padding and
# header styling; the design wants the bare ``tb-table`` look inside a rounded
# card. The selectors below strip Quasar's chrome and re-apply the spec values.
ACCOUNTS_CSS = """
/* ---- Mono avatar (status-coloured initials = last 2 phone digits) -------- */
.tb-acc-av {
  width: 32px; height: 32px; border-radius: 9999px; flex-shrink: 0;
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 12px; font-weight: 600; font-variant-numeric: tabular-nums;
}
.tb-acc-av-active { background: #E8F0FF; color: #0066FF; }
.tb-acc-av-banned { background: #FBECEC; color: #C0473F; }
.tb-acc-av-spam   { background: #FBF3E2; color: #9A7B22; }
.tb-acc-av-code   { background: #EDEBE7; color: #74726E; }
.tb-acc-phone  { font-size: 13px; font-weight: 600; color: #0B0B0C; }
.tb-acc-handle { font-size: 11px; color: #9A9893; }

/* ---- Status pill --------------------------------------------------------- */
.tb-acc-pill {
  display: inline-flex; align-items: center; gap: 6px;
  border-radius: 9999px; padding: 3px 10px; font-size: 12px; font-weight: 500;
}
.tb-acc-pill .tb-acc-dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }

/* ---- Proxy cell dot + flag ----------------------------------------------- */
.tb-acc-conn { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
.tb-acc-flag {
  width: 16px; height: 12px; border-radius: 2px; flex-shrink: 0;
  background-size: cover; background-position: center;
  box-shadow: 0 0 0 1px rgba(0,0,0,.07);
}

/* ---- Trust bar ----------------------------------------------------------- */
.tb-acc-trust-track {
  width: 46px; height: 5px; border-radius: 9999px; background: #EEEDEA; overflow: hidden;
}
.tb-acc-trust-fill { height: 100%; border-radius: 9999px; }

/* ---- Round action buttons (alive-check / edit / delete) ------------------ */
.tb-acc-act {
  width: 30px; height: 30px; border-radius: 9999px;
  display: inline-flex; align-items: center; justify-content: center;
  border: 1px solid #E6E5E3; background: #fff; color: #74726E; cursor: pointer;
  transition: background .15s ease, color .15s ease, border-color .15s ease, transform .1s ease;
}
.tb-acc-act:hover { background: #F2F6FF; }
.tb-acc-act:active { transform: scale(.96); }
.tb-acc-act-edit:hover   { color: #0066FF; border-color: #BFD6FF; }
.tb-acc-act-del:hover    { color: #C0473F; border-color: #F0C9C5; background: #FBECEC; }

/* ---- Expandable search --------------------------------------------------- */
.tb-acc-search { transition: width .32s cubic-bezier(.34,1.4,.6,1); }

/* ---- Proxy-pool grid ----------------------------------------------------- */
.tb-acc-pool {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(232px, 1fr)); gap: 10px;
}

/* ---- Quasar table reset (bare tb-table look inside the card) ------------- */
.tb-acc-table .q-table__container,
.tb-acc-table .q-table { background: transparent; box-shadow: none; }
.tb-acc-table .q-table thead th {
  background: #FAF9F7; color: #9A9893; font-size: 11px; font-weight: 500;
  text-transform: uppercase; letter-spacing: .04em; padding: 11px 16px;
  border-bottom: none;
}
.tb-acc-table .q-table tbody td {
  padding: 11px 16px; border-top: 1px solid #F0EEEB;
  font-size: 12.5px; color: #3A3A3A;
}
.tb-acc-table .q-table tbody tr:first-child td { border-top: none; }
.tb-acc-table .q-table tbody tr:hover { background: #FAF9F7; }
/* Drop the multi-select checkbox column header rounding artefacts. */
.tb-acc-table .q-table__top { display: none; }
.tb-acc-table .q-table__bottom { border-top: 1px solid #F0EEEB; color: #9A9893; font-size: 12px; }
"""
