"""Точка входа. Webhook, потому что бесплатный Render считает живым только тот
сервис, в который стучатся снаружи: long polling там засыпает через 15 минут.

/health нужен не для красоты, а чтобы внешний пингер раз в 10 минут не давал
сервису уснуть, иначе первое сообщение заказчика ждёт полминуты на разогреве.
"""
import logging
import os
import secrets

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

import db
import handlers

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

TOKEN = os.environ["BOT_TOKEN"]
# адрес сервиса Render кладёт сам, руками его вбивать незачем: имя может разъехаться
# с тем, что в render.yaml, если оно уже занято, и вебхук тогда уйдёт в никуда
BASE = os.getenv("WEBHOOK_BASE") or os.getenv("RENDER_EXTERNAL_URL", "")
PATH = "/tg/" + TOKEN.split(":")[0]
# ID бота публичен, поэтому путь секретом не является: без общего заголовка
# на этот адрес мог бы стучаться кто угодно и затирать чужие профили
SECRET = os.getenv("WEBHOOK_SECRET") or secrets.token_urlsafe(24)
PORT = int(os.getenv("PORT", "10000"))


async def health(_: web.Request) -> web.Response:
    return web.Response(text="ok")


async def on_startup(bot: Bot) -> None:
    await db.pool()
    if BASE:
        await bot.set_webhook(BASE.rstrip("/") + PATH, drop_pending_updates=True,
                              secret_token=SECRET)
        logging.info("webhook: %s", BASE.rstrip("/") + PATH)


def main() -> None:
    bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=None))
    dp = Dispatcher()
    dp.include_router(handlers.router)
    dp.startup.register(on_startup)

    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/", health)
    SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=SECRET).register(app, path=PATH)
    setup_application(app, dp, bot=bot)
    web.run_app(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
