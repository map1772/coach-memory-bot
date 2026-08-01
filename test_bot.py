"""Проверки без сети и без базы: подменяем вызов модели, а не ходим в неё.

  python test_bot.py

Смысл ровно один: поймать поломку в разборе ответа модели и в сборке профиля,
потому что именно там всё и ломается, когда модель отвечает не так, как обещала.
"""
import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8")

import llm
import prompts
from db import profile_filled


def test_json_extraction():
    good = '{"has_enough_context": true, "answer": "ок", "why": "профиль"}'
    assert llm._extract_json(good)["answer"] == "ок"
    # модель любит обернуть в блок кода
    assert llm._extract_json("```json\n" + good + "\n```")["answer"] == "ок"
    # и добавить фразу вокруг
    assert llm._extract_json("Вот ответ:\n" + good + "\nНадеюсь, помог")["answer"] == "ок"


def test_reply_text():
    r = llm.Reply(has_enough_context=True, answer="  бегайте по вторникам ")
    assert r.text() == "бегайте по вторникам"
    q = llm.Reply(has_enough_context=False, clarifying_question="Сколько вам лет?")
    assert q.text() == "Сколько вам лет?"
    # модель сказала «данных хватает», но ответ не положила
    broken = llm.Reply(has_enough_context=True, answer=None)
    assert broken.text(), "пустой ответ не должен уходить человеку"


def test_profile_render():
    prof = {"name": "Аня", "goal": "похудеть", "level": "новичок", "age": None,
            "facts": {"любит плавание": "да"}}
    out = prompts.render_profile(prof)
    assert "Аня" in out and "любит плавание" in out
    assert "возраст" not in out, "пустые поля печатать нельзя, модель начнёт их выдумывать"
    assert "не известно" in prompts.render_profile({})


def test_profile_filled():
    assert not profile_filled({})
    assert not profile_filled({"goal": "похудеть", "level": "новичок"})
    assert profile_filled({"goal": "похудеть", "level": "новичок", "freq": "3"})


def test_system_prompt_has_level_hint():
    p = prompts.system_prompt({"level": "опытный", "goal": "жим 120"})
    assert "профессиональные термины" in p
    assert "Пример разницы в тоне" in p, "без примеров тон не различается"


def test_fallback_on_garbage():
    """Модель вернула мусор дважды: человек должен получить фразу, а не исключение."""
    async def garbage(*a, **k):
        return "я не умею в json, извините"

    orig, llm.raw_call = llm.raw_call, garbage
    try:
        r = asyncio.run(llm.ask("system", [], "привет"))
    finally:
        llm.raw_call = orig
    assert r.text() and not r.has_enough_context
    assert "сбой модели" in r.why


def test_facts_survive_bad_json():
    async def garbage(*a, **k):
        return "нет фактов"

    orig, llm.raw_call = llm.raw_call, garbage
    try:
        assert asyncio.run(llm.extract_facts("prompt")) == []
    finally:
        llm.raw_call = orig


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok:", t.__name__)
    print(f"\nвсе {len(tests)} проверок пройдены")


if __name__ == "__main__":
    main()
