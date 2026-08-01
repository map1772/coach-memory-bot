"""Прогон модуля на пяти разных профилях с сохранением логов.

  python demo_run.py            прогон и запись в demo_log.md

Заказчик в приёмке просил именно это: тестовые прогоны на 5 профилях с логами,
чтобы качество персонализации можно было проверить глазами, не открывая бота.

Один и тот же набор вопросов идёт всем пятерым. Если ответы различаются только
перестановкой слов, персонализация не работает, и это будет видно сразу.
"""
import asyncio
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import llm
import prompts

PROFILES = [
    {"name": "Аня", "goal": "похудеть на 8 кг", "level": "новичок", "age": 34, "freq": "2",
     "equipment": "ничего", "limits": "болит колено при беге"},
    {"name": "Игорь", "goal": "жим лёжа 120 кг", "level": "опытный", "age": 28, "freq": "5",
     "equipment": "зал", "limits": "нет"},
    {"name": "Марина", "goal": "вернуть форму после родов", "level": "новичок", "age": 31,
     "freq": "3", "equipment": "гантели дома", "limits": "нельзя нагрузку на пресс полгода"},
    {"name": "Тимур", "goal": "пробежать полумарафон из двух часов", "level": "средний",
     "age": 41, "freq": "4", "equipment": "зал", "limits": "давление, врач разрешил кардио"},
    {"name": "Лев", "goal": "набрать 6 кг массы", "level": "средний", "age": 19, "freq": "4",
     "equipment": "гантели дома", "limits": "нет"},
]

QUESTIONS = [
    "Как мне тренироваться на этой неделе?",
    "Составь программу",                       # намеренно неполный запрос
    "Что посмотреть вечером?",                 # намеренно не по теме
]


async def main() -> None:
    if not os.getenv("LLM_API_KEY"):
        sys.exit("нет LLM_API_KEY, прогон не имеет смысла: ответы будут запасными")

    out = ["# Прогон на пяти профилях", "",
           "Один набор вопросов, пять разных людей. Второй вопрос намеренно неполный: "
           "проверяем, задаст ли бот уточняющий вопрос вместо выдуманной программы. "
           "Третий вопрос намеренно не по теме.", ""]

    for prof in PROFILES:
        head = (f"{prof['name']}, {prof['age']} лет, {prof['level']}, цель {prof['goal']}, "
                f"{prof['freq']} раза в неделю, инвентарь {prof['equipment']}, "
                f"ограничения: {prof['limits']}")
        out += [f"## {prof['name']}", "", head, ""]
        print("=" * 70)
        print(head)
        for q in QUESTIONS:
            r = await llm.ask(prompts.system_prompt(prof), [], q)
            kind = "ответ" if r.has_enough_context else "уточняющий вопрос"
            print(f"\n[{q}] -> {kind}\n{r.text()[:300]}")
            out += [f"**Вопрос:** {q}", "", f"*Реакция:* {kind}", "",
                    r.text(), "", f"*Опирался на:* {r.why}", ""]
        out.append("---")
        out.append("")

    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo_log.md"),
              "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    print("\nлог: demo_log.md")


if __name__ == "__main__":
    asyncio.run(main())
