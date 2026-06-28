"""The shared top-nav is the single source of truth for cross-page links.

Regression: the neurocomment page was registered on its route but its link was
missing from three of the four hand-rolled page headers, so it was reachable
only by typing the URL. Centralising the nav makes "page exists but isn't
linked" a test failure here instead of a silently-missing menu item.
"""

from __future__ import annotations

from features.shared.nav import NAV_LINKS, nav_link_html


def test_nav_links_cover_every_registered_page() -> None:
    routes = {route for _, route in NAV_LINKS}
    assert {"/", "/warming", "/neurocomment", "/logs", "/settings"} <= routes


def test_nav_routes_are_unique() -> None:
    routes = [route for _, route in NAV_LINKS]
    assert len(routes) == len(set(routes))


def test_active_route_is_emphasised() -> None:
    active = nav_link_html("Нейрокомментинг", "/neurocomment", "/neurocomment")
    inactive = nav_link_html("Нейрокомментинг", "/neurocomment", "/")
    assert "font-weight:600" in active
    assert "font-weight:600" not in inactive
    assert "font-weight:500" in inactive
    assert active != inactive
