"""Commenting on a channel post — the write action, the thread read, and their models.

Its own module because ``telegram_actions`` is at the file-size cap, but the grouping is
not arbitrary: these are the two halves of one loop. The read hands out DISCUSSION-GROUP
message ids and the write takes one back as ``CommentOnPost.reply_to``, and describing
both halves in one place is what stops that id being read as a channel id — the mistake
that would silently reply to whatever post happens to share the number.

``PostMediaKind`` moved here with them because it was never a description of Telegram's
media union; it classifies a post by what a comment could be made OUT of, which is this
module's subject. ``telegram_actions`` re-imports every name, so the original
``from schemas.telegram_actions import CommentOnPost`` paths keep working unchanged.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# What the post carries besides its text, classified by what a comment generator could
# actually make a comment OUT of — not by Telegram's media union. ``photo`` is the only
# kind the vision path can read; ``album`` is called out separately because a caption-less
# album item is not a missed opportunity but a duplicate of its own album's head (every
# item fires its own event, and every comment on them lands in the same discussion thread).
PostMediaKind = Literal["none", "photo", "album", "other"]


class CommentOnPost(BaseModel):
    """Post a comment under a channel post via the linked discussion group.

    Telethon's ``send_message(channel, text, comment_to=post_id)`` routes the
    message into the channel's linked group; the account must already be a
    member of that group (onboarding handles the join).

    ``reply_to`` aims the comment at another comment instead of at the post. It is an id
    in the linked group — the value ``PostCommentRecord.message_id`` reports — and it
    cannot ride along with ``comment_to``: that sugar derives the reply target itself and
    Telethon lets it win, so the gateway addresses the group directly when this is set.
    """

    action_type: Literal["comment_on_post"] = "comment_on_post"
    channel: str = Field(min_length=1)
    post_id: int
    text: str = Field(min_length=1)
    reply_to: int | None = None


class ReadPostComments(BaseModel):
    """Read a post's comment thread — and the post itself, in the same action.

    Both in one action because the caller has nowhere else to get the post: an attempt
    parked to wait for human comments is resumed from the DB minutes later, the DB does
    not store the post text, and the reply prompt needs the post as well as the comment
    it answers.
    """

    action_type: Literal["read_post_comments"] = "read_post_comments"
    channel: str = Field(min_length=1)
    post_id: int
    limit: int = Field(default=20, ge=1, le=100)


class PostCommentRecord(BaseModel):
    """One message in a post's comment thread.

    ``message_id`` is the id in the DISCUSSION GROUP, not in the channel: that is where a
    reply has to be addressed, and it is exactly what ``CommentOnPost.reply_to`` expects.
    """

    message_id: int
    sender_id: int | None = None
    text: str = ""


class ReadPostCommentsResult(BaseModel):
    """Gateway output for ``ReadPostComments`` — oldest comment first.

    Oldest-first so that "the 3rd comment" keeps naming the same comment as the thread
    grows; a newest-first list renumbers itself every time somebody posts.

    An empty ``comments`` is a normal answer — no replies yet, comments switched off, or a
    linked group that could not be resolved — and so is ``post_missing``: a post deleted
    while the attempt waited is a reason for the caller to drop the attempt, not a failure
    to report. ``post_media_kind`` is ``None`` only in that case, since there was no post
    left to classify (``"none"`` means a post that carries no media).
    """

    comments: list[PostCommentRecord]
    post_text: str = ""
    post_media_kind: PostMediaKind | None = None
    post_missing: bool = False
