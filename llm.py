"""Слой модели: один вызов, строгая схема ответа, ретраи.

Провайдер задаётся переменными окружения, а не кодом: Gemini, Groq и OpenRouter
все отдают OpenAI-совместимый /chat/completions, так что второй реализации не нужно.

Схему проверяем Pydantic-ом даже там, где провайдер обещает гарантию: гарантия
есть у constrained decoding, а у моделей, которые просто «стараются», её нет.
"""
import asyncio
import json
import os
import re

import httpx
from pydantic import BaseModel, Field, ValidationError

BASE_URL = os.getenv("LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai")
MODEL = os.getenv("LLM_MODEL", "gemini-2.0-flash")
API_KEY = os.getenv("LLM_API_KEY", "")
TIMEOUT = float(os.getenv("LLM_TIMEOUT", "45"))


class Reply(BaseModel):
    has_enough_context: bool
    clarifying_question: str | None = None
    answer: str | None = None
    why: str = ""
    used_profile_fields: list[str] = Field(default_factory=list)

    def text(self) -> str:
        """То, что реально уйдёт человеку."""
        if self.has_enough_context and self.answer:
            return self.answer.strip()
        return (self.clarifying_question or "").strip() or "Уточните, пожалуйста, что именно нужно."


class Facts(BaseModel):
    facts: list[dict] = Field(default_factory=list)


def _extract_json(raw: str) -> dict:
    """Модели любят обернуть JSON в ```json ... ``` или добавить фразу вокруг."""
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # жадный поиск от первой скобки до последней ломается, когда после JSON
        # идёт приписка со своими скобками: берём ровно один объект
        i = raw.find("{")
        if i < 0:
            raise
        return json.JSONDecoder().raw_decode(raw[i:])[0]


async def raw_call(messages: list[dict], temperature: float = 0.7, max_tokens: int = 900) -> str:
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    payload = {"model": MODEL, "messages": messages,
               "temperature": temperature, "max_tokens": max_tokens,
               # просим сам формат у провайдера: дешевле, чем ловить кривой JSON ретраями
               "response_format": {"type": "json_object"}}
    async with httpx.AsyncClient(timeout=TIMEOUT) as cl:
        r = await cl.post(f"{BASE_URL}/chat/completions", headers=headers, json=payload)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


async def ask(system: str, history: list[dict], user_text: str, tries: int = 3) -> Reply:
    """Ответ собеседнику. Роли разделены: системные правила отдельно от текста
    пользователя, иначе инъекция в реплике читается моделью как инструкция."""
    messages = [{"role": "system", "content": system}]
    for h in history:
        messages.append({"role": "assistant" if h["role"] == "bot" else "user",
                         "content": h["text"]})
    messages.append({"role": "user", "content": user_text})

    last, raw = None, ""
    for n in range(tries):
        try:
            raw = await raw_call(messages)
            return Reply(**_extract_json(raw))
        except (ValidationError, json.JSONDecodeError, KeyError) as e:
            last = e
            # без собственного ответа модели в истории она не видит, что исправляет,
            # да и два user-сообщения подряд часть провайдеров не любит
            messages.append({"role": "assistant", "content": (raw or "")[:1500]})
            messages.append({"role": "user",
                             "content": "Верни строго один JSON-объект по описанной схеме, без текста вокруг."})
        except httpx.HTTPError as e:
            last = e
            await asyncio.sleep(1.5 * (n + 1))
    # запасной сценарий: лучше честная фраза, чем падение или сырой мусор
    return Reply(has_enough_context=False,
                 clarifying_question="Не смог собрать ответ, повторите вопрос чуть иначе.",
                 why=f"сбой модели: {type(last).__name__}")


async def extract_facts(prompt: str) -> list[dict]:
    """Добыча фактов из реплики. Ошибка здесь не критична: молча возвращаем пусто,
    потому что потерять факт менее вредно, чем уронить ответ пользователю."""
    try:
        data = _extract_json(await raw_call(
            [{"role": "user", "content": prompt}], temperature=0.2, max_tokens=300))
        return [f for f in Facts(**data).facts if f.get("key") and f.get("value")]
    except Exception:
        return []
