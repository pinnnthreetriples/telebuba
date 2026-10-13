"""A warming account's character and writing manner (Прогрев 2.0, item 4.2).

Split from ``schemas.warming`` for the file-size budget. Data contract only: the
profile is stored once per account (``warming_account_character``); the manner is
drawn by code from the account id every time and never stored.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Gender = Literal["female", "male"]
# How long the account's lines run, how many emoji it uses, how often it mistypes.
MessageLength = Literal["short", "medium"]
EmojiHabit = Literal["none", "rare", "often"]


class AccountCharacter(BaseModel):
    """Who the account is: code picks age, gender and city; AI the rest."""

    account_id: str = Field(min_length=1)
    # The phone country's language, ISO 639-1 ("es", "ru", "en").
    language: str = Field(min_length=2)
    country: str | None = None
    age: int = Field(ge=14, le=99)
    gender: Gender
    city: str | None = None
    occupation: str = Field(min_length=2, max_length=60)
    interests: list[str] = Field(min_length=3, max_length=5)


class ChatStyle(BaseModel):
    """The account's own manner, applied by code to every line it sends."""

    length: MessageLength = "medium"
    emoji: EmojiHabit = "rare"
    # Chance a line carries one swapped-letter typo.
    typo_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    # Laughs in its language («jajaja») now and then.
    laughs: bool = False
    # Drops the final period, as most people do in chats.
    no_final_period: bool = True
    # Starts lines in lowercase, the way people type fast on a phone.
    lowercase: bool = False
