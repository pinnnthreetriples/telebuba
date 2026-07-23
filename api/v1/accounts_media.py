"""Cacheable profile-thumbnail image endpoints — binary, not JSON.

Split out of ``accounts.py``: serving raw bytes with ETag/Cache-Control
headers is a distinct concern from the JSON CRUD routes there (no Pydantic
response model, no OpenAPI schema — the SPA loads these via ``<img src>``,
never the typed client). Included on the same protected router list as
``accounts.router`` in ``api/v1/__init__.py``, so auth is unchanged: a plain
``<img src="/api/v1/...">`` sends the session cookie automatically.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi import status as http_status

from core.config import settings
from services import accounts

if TYPE_CHECKING:
    from schemas.profile_media import ProfileImage

router = APIRouter(tags=["accounts"])


def _decode_id(value: str) -> int:
    """Parse an int64 identifier carried as a string in the URL, or 400."""
    try:
        return int(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="invalid id",
        ) from exc


def _image_response(request: Request, image: ProfileImage) -> Response:
    max_age = settings.profile_media.thumb_cache_max_age_seconds
    cache_control = f"private, max-age={max_age}, immutable"
    if request.headers.get("if-none-match") == image.etag:
        return Response(
            status_code=http_status.HTTP_304_NOT_MODIFIED,
            headers={"ETag": image.etag, "Cache-Control": cache_control},
        )
    return Response(
        content=image.content,
        media_type=image.media_type,
        headers={"ETag": image.etag, "Cache-Control": cache_control},
    )


@router.get("/accounts/{account_id}/avatar", include_in_schema=False)
async def get_account_avatar(account_id: str, request: Request) -> Response:
    image = await accounts.account_avatar_image(account_id)
    if image is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="avatar not found")
    return _image_response(request, image)


@router.get("/accounts/{account_id}/profile/photos/{photo_id}/thumb", include_in_schema=False)
async def get_account_photo_thumb(account_id: str, photo_id: str, request: Request) -> Response:
    image = await accounts.account_profile_image(
        account_id, kind="photos", item_id=_decode_id(photo_id)
    )
    if image is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail="thumbnail not found"
        )
    return _image_response(request, image)


@router.get("/accounts/{account_id}/profile/stories/{story_id}/thumb", include_in_schema=False)
async def get_account_story_thumb(account_id: str, story_id: int, request: Request) -> Response:
    image = await accounts.account_profile_image(account_id, kind="stories", item_id=story_id)
    if image is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail="thumbnail not found"
        )
    return _image_response(request, image)
