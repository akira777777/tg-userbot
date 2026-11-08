"""Owner-only Telegram entry point for the Mini App."""

from __future__ import annotations

from telegram import KeyboardButton, ReplyKeyboardMarkup, Update, WebAppInfo
from telegram.ext import Application, ApplicationBuilder, CommandHandler, ContextTypes

from control.config import ControlSettings


def make_web_app_keyboard(mini_app_url: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup.from_button(
        KeyboardButton(text="Открыть панель", web_app=WebAppInfo(url=mini_app_url)),
        resize_keyboard=True,
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or user.id not in context.bot_data["owner_ids"]:
        if update.effective_message:
            await update.effective_message.reply_text("Доступ к панели ограничен.")
        return
    if update.effective_message:
        await update.effective_message.reply_text(
            "Панель управления готова. Открой её кнопкой ниже.",
            reply_markup=make_web_app_keyboard(context.bot_data["mini_app_url"]),
        )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user is None or user.id not in context.bot_data["owner_ids"]:
        return
    if update.effective_message:
        await update.effective_message.reply_text("Контроллер запущен. Открой панель через /start.")


def build_application(settings: ControlSettings) -> Application:
    application = ApplicationBuilder().token(settings.bot_token).build()
    application.bot_data["owner_ids"] = settings.owner_telegram_ids
    application.bot_data["mini_app_url"] = settings.mini_app_url
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status))
    return application


def main() -> None:
    settings = ControlSettings.from_environment()
    build_application(settings).run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
