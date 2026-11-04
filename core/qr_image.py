"""Render text as a QR code image — pure, no I/O."""

from __future__ import annotations

import segno

# 8 px per module and a 2-module quiet zone: big enough for a phone camera to read
# off a laptop screen, small enough to keep the data URI a few kilobytes.
_QR_SCALE = 8
_QR_BORDER = 2
# Segno leaves the light modules transparent; a dark page would then show through the
# quiet zone and phone cameras would not read the code.
_QR_LIGHT = "#fff"


def render_qr_data_uri(text: str) -> str:
    """SVG data URI of a QR code for ``text`` (the SPA shows it in an ``<img>``)."""
    return segno.make(text, error="m").svg_data_uri(
        scale=_QR_SCALE,
        border=_QR_BORDER,
        light=_QR_LIGHT,
    )
