"""Which LLM writes a text: the operator's choice, else whichever provider has a key.

Every text generator asks here — neurocomment comments (image posts included, since
``deepseek-flash`` reads images), warming chat, bulk messages, neuroshilling and the
discovery keywords — so one Settings switch moves them all together. The captcha
solver is the exception: it has its own provider choice, with OpenAI among them.

Callers keep picking the gateway function themselves (``generate_text_deepseek`` or
``generate_text``) from their own ``_seams``, so tests patch it where it is used.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from core.config import settings
from schemas.gemini import GeminiRequest

if TYPE_CHECKING:
    from schemas.warming import WarmingSettingsSecret


def uses_deepseek(secret: WarmingSettingsSecret) -> bool:
    """True when DeepSeek writes: chosen and keyed, or chosen Gemini has no key.

    A chosen provider without a key hands over to the other one rather than failing —
    the Settings page warns about it. With neither key the choice stands, and the
    caller's own "no key" handling takes over.
    """
    if secret.text_llm_provider == "deepseek":
        return bool(secret.deepseek_api_key) or not secret.gemini_api_key
    return not secret.gemini_api_key and bool(secret.deepseek_api_key)


@dataclass(frozen=True)
class TextLlm:
    """The resolved provider plus everything a request needs from it."""

    use_deepseek: bool
    api_key: str
    model: str
    temperature: float
    max_output_tokens: int
    # The operator's retries and pause (Settings), for whichever provider this is.
    max_retries: int
    min_interval_seconds: float

    def request(
        self,
        prompt: str,
        *,
        max_output_tokens: int | None = None,
        image_b64: str | None = None,
        response_json_object: bool = False,
    ) -> GeminiRequest:
        """A request to this provider. Raises ``ValidationError`` on an empty key."""
        return GeminiRequest(
            api_key=self.api_key,
            prompt=prompt,
            model=self.model,
            temperature=self.temperature,
            max_output_tokens=max_output_tokens or self.max_output_tokens,
            image_b64=image_b64,
            response_json_object=response_json_object,
            max_retries=self.max_retries,
            min_interval_seconds=self.min_interval_seconds,
        )


def text_llm(secret: WarmingSettingsSecret) -> TextLlm:
    """Resolve the text LLM once, so a provider cannot change between retries."""
    deepseek = uses_deepseek(secret)
    config = settings.deepseek if deepseek else settings.gemini
    return TextLlm(
        use_deepseek=deepseek,
        api_key=secret.deepseek_api_key if deepseek else secret.gemini_api_key,
        model=settings.deepseek.model if deepseek else secret.gemini_model,
        temperature=config.temperature,
        max_output_tokens=config.max_output_tokens,
        max_retries=secret.gemini_max_retries,
        min_interval_seconds=secret.gemini_min_interval_seconds,
    )
