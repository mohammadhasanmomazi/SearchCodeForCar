import os
import logging
from dotenv import load_dotenv
from telebot import TeleBot
from flask import Flask, request

# Load environment variables
load_dotenv()

# Configuration
BOT_TOKEN = os.getenv('BOT_TOKEN')
MODE = os.getenv('MODE', 'POLLING').upper()
WEBHOOK_URL = os.getenv('WEBHOOK_URL')
WEBHOOK_PORT = int(os.getenv('WEBHOOK_PORT', 8443))
WEBHOOK_PATH = os.getenv('WEBHOOK_PATH', '/webhook')
SECRET_KEY = os.getenv('SECRET_KEY')

# Validate configuration
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is required in .env file")

# Set up logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Initialize bot
bot = TeleBot(BOT_TOKEN)

# Flask app for webhook
app = Flask(__name__)

# Bot handlers


@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(
        message, "Welcome to the Search Code for Car Bot! Use /help for commands.")


@bot.message_handler(commands=['help'])
def help_command(message):
    bot.reply_to(
        message, "Available commands:\n/start - Start the bot\n/help - Show this help")


@bot.message_handler(func=lambda message: True)
def echo_all(message):
    logger.info(
        f"Echoing message from {message.from_user.username}: {message.text}")
    bot.reply_to(message, message.text)

# Webhook handler


@app.route(WEBHOOK_PATH, methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = bot.process_new_updates(
            [bot._telebot_types.Update.de_json(json_string)])
        return 'OK', 200
    else:
        return 'Bad Request', 400


def main():
    if MODE == 'WEBHOOK':
        if not WEBHOOK_URL:
            raise ValueError("WEBHOOK_URL is required when MODE=WEBHOOK")
        logger.info("Starting bot in WEBHOOK mode")
        # Set webhook
        bot.remove_webhook()
        bot.set_webhook(url=WEBHOOK_URL + WEBHOOK_PATH)
        # Run Flask app
        app.run(host='0.0.0.0', port=WEBHOOK_PORT)
    else:
        logger.info("Starting bot in POLLING mode")
        bot.remove_webhook()
        bot.polling()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error(f"Error starting bot: {e}")
        raise
