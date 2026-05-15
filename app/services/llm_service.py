"""Google Gemini LLM service."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Generator

from flask import current_app
from google import genai
from google.genai import types


class LLMServiceError(Exception):
    """Raised when LLM API call fails after all retries."""


class LLMService:
    """Service for all AI generation using Google Gemini gemini-2.5-flash."""

    MODEL_NAME = "gemini-2.5-flash"

    def __init__(self) -> None:
        self._client = None

    def _get_client(self):
        """Create the Gemini client only when it is actually needed."""

        if self._client is not None:
            return self._client

        api_key = None
        try:
            api_key = current_app.config.get("GOOGLE_API_KEY")
        except RuntimeError:
            api_key = os.environ.get("GOOGLE_API_KEY")

        if not api_key:
            raise LLMServiceError(
                "GOOGLE_API_KEY is required to use Gemini-backed features"
            )

        self._client = genai.Client(api_key=api_key)
        return self._client

    def _build_full_prompt(self, prompt: str, system_prompt: str | None = None) -> str:
        if system_prompt:
            return f"{system_prompt}\n\n---\n\n{prompt}"
        return prompt

    def generate(self, prompt: str, system_prompt: str | None = None) -> str:
        """Standard Gemini generation without web search."""

        full_prompt = self._build_full_prompt(prompt, system_prompt)
        client = self._get_client()

        for attempt in range(3):
            try:
                response = client.models.generate_content(
                    model=self.MODEL_NAME,
                    contents=full_prompt,
                )
                return (getattr(response, "text", "") or "").strip()
            except Exception as exc:  # pylint: disable=broad-except
                current_app.logger.error("LLM generate error: %s", exc)
                if attempt < 2:
                    time.sleep(2)
                    continue
                raise LLMServiceError(str(exc)) from exc

        raise LLMServiceError("LLM generation failed")

    def generate_with_search(self, prompt: str, system_prompt: str | None = None) -> str:
        """Gemini generation with Google Search grounding."""

        full_prompt = self._build_full_prompt(prompt, system_prompt)
        client = self._get_client()

        for attempt in range(3):
            try:
                grounding_tool = types.Tool(google_search=types.GoogleSearch())
                config = types.GenerateContentConfig(tools=[grounding_tool])
                response = client.models.generate_content(
                    model=self.MODEL_NAME,
                    contents=full_prompt,
                    config=config,
                )
                return (getattr(response, "text", "") or "").strip()
            except Exception as exc:  # pylint: disable=broad-except
                current_app.logger.error("LLM generate error: %s", exc)
                if attempt < 2:
                    time.sleep(2)
                    continue
                raise LLMServiceError(str(exc)) from exc

        raise LLMServiceError("LLM grounded generation failed")

    def generate_streaming(
        self,
        prompt: str,
        system_prompt: str | None = None,
    ) -> Generator[str, None, None]:
        """Generator that yields text chunks as they stream from Gemini."""

        full_prompt = self._build_full_prompt(prompt, system_prompt)
        client = self._get_client()

        stream_method = getattr(client.models, "generate_content_stream", None)
        if callable(stream_method):
            try:
                stream = stream_method(model=self.MODEL_NAME, contents=full_prompt)
                for chunk in stream:
                    chunk_text = getattr(chunk, "text", None)
                    if chunk_text:
                        yield str(chunk_text)
                return
            except Exception as exc:  # pylint: disable=broad-except
                current_app.logger.warning(
                    "LLM streaming unavailable, falling back to non-streaming response: %s",
                    exc,
                )

        yield self.generate(prompt=prompt, system_prompt=system_prompt)

    def generate_structured(
        self,
        prompt: str,
        system_prompt: str | None = None,
        output_schema: dict | None = None,
    ) -> dict:
        """Generate and attempt to parse JSON structured output."""

        schema_hint = ""
        if output_schema:
            schema_hint = (
                "\n\nUse this JSON schema as guidance:\n"
                f"{json.dumps(output_schema, ensure_ascii=False)}"
            )

        structured_prompt = (
            f"{prompt}{schema_hint}"
            "\n\nRespond ONLY with valid JSON. No markdown code fences, "
            "no preamble, no explanation. Just the JSON object."
        )
        response_text = self.generate(structured_prompt, system_prompt=system_prompt)

        cleaned_text = response_text.strip()
        if cleaned_text.startswith("```"):
            cleaned_text = cleaned_text.removeprefix("```json").removeprefix("```").strip()
            if cleaned_text.endswith("```"):
                cleaned_text = cleaned_text[:-3].strip()

        try:
            parsed = json.loads(cleaned_text)
            if isinstance(parsed, dict):
                return parsed
            return {"data": parsed}
        except json.JSONDecodeError:
            current_app.logger.warning("Structured LLM response was not valid JSON")
            return {"raw_text": response_text}


# Module-level instance for use across the application
llm_service = LLMService()
