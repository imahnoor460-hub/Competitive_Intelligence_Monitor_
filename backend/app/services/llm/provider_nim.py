import re

from openai import OpenAI
from pydantic import BaseModel, ValidationError

from app.core.config import settings
from app.services.llm.client import LLMCallResult, EmbedResult, LLMOutputError

_MAX_ATTEMPTS = 2
_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(text: str) -> str:
    """Models sometimes wrap JSON in ```json fences despite instructions
    not to — take the outermost {...} block rather than trusting the
    whole response body to be bare JSON.
    """
    match = _JSON_BLOCK.search(text)
    if not match:
        raise LLMOutputError(f"No JSON object found in model output: {text!r}")
    return match.group(0)


class NIMProvider:
    """Wraps NVIDIA NIM's OpenAI-compatible chat/embeddings API. Swapping
    to a different provider later means adding another LLMClient
    implementation, not touching any call site.
    """

    def __init__(self, api_key: str, base_url: str, chat_model: str, embed_model: str):
        # An explicit timeout, because the SDK's default is 600s. Every call
        # here happens inside a background job that holds one of two worker
        # slots, so a provider that accepts a request and then goes quiet
        # would stall a check for ten minutes and read to the user as a job
        # that is simply stuck. Callers already treat any provider failure as
        # best-effort, so a timeout degrades the same way a 500 does.
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=settings.llm_request_timeout,
        )
        self._chat_model = chat_model
        self._embed_model = embed_model

    def complete(
        self, system: str, user: str, response_model: type[BaseModel]
    ) -> LLMCallResult:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

        last_error: Exception | None = None

        for _ in range(_MAX_ATTEMPTS):
            response = self._client.chat.completions.create(
                model=self._chat_model,
                messages=messages,
                temperature=0,
            )
            raw_text = response.choices[0].message.content or ""

            try:
                parsed = response_model.model_validate_json(_extract_json(raw_text))
                usage = response.usage
                return LLMCallResult(
                    value=parsed,
                    model=self._chat_model,
                    prompt_tokens=usage.prompt_tokens if usage else 0,
                    completion_tokens=usage.completion_tokens if usage else 0,
                )
            except (ValidationError, LLMOutputError) as exc:
                last_error = exc
                messages.append({"role": "assistant", "content": raw_text})
                messages.append({
                    "role": "user",
                    "content": (
                        f"That was not valid JSON matching the required schema "
                        f"({exc}). Respond with ONLY the corrected JSON object, "
                        f"no other text."
                    ),
                })

        raise LLMOutputError(
            f"Model did not return valid structured output after {_MAX_ATTEMPTS} attempts: {last_error}"
        )

    def embed(self, texts: list[str]) -> EmbedResult:
        # NIM's asymmetric retrieval embedding models (e.g. NV-EmbedQA) require
        # input_type ("query" vs "passage") — we only ever embed stored
        # documents for symmetric similarity search (never a live search
        # query against them), so "passage" is correct for every call site.
        response = self._client.embeddings.create(
            model=self._embed_model,
            input=texts,
            extra_body={"input_type": "passage"},
        )
        vectors = [item.embedding for item in response.data]
        usage = response.usage

        return EmbedResult(
            vectors=vectors,
            model=self._embed_model,
            prompt_tokens=usage.prompt_tokens if usage else 0,
        )
