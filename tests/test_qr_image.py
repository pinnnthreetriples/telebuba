"""``core.qr_image`` — the QR text becomes an SVG data URI."""

from __future__ import annotations

from urllib.parse import unquote

from core.qr_image import render_qr_data_uri


def test_the_qr_is_an_svg_data_uri() -> None:
    uri = render_qr_data_uri("tg://login?token=abc")

    assert uri.startswith("data:image/svg+xml")
    assert len(uri) > 100


def test_different_text_gives_a_different_image() -> None:
    assert render_qr_data_uri("tg://login?token=a") != render_qr_data_uri("tg://login?token=b")


def test_the_light_modules_are_opaque_white() -> None:
    assert "#fff" in unquote(render_qr_data_uri("tg://login?token=a"))
