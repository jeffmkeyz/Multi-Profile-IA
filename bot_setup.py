"""
Configura el botón de menú de tu bot de Telegram para que abra Multi-Perfil IA
como Mini App. Se corre UNA SOLA VEZ (o cada vez que cambies la URL).

Uso:
    BOT_TOKEN=123456:ABC... APP_URL=https://tu-app.up.railway.app python3 bot_setup.py

No usa python-telegram-bot, solo "requests" — mismo patrón que tus otros bots.
"""

import os
import sys

import requests

BOT_TOKEN = os.environ.get("BOT_TOKEN")
APP_URL = os.environ.get("APP_URL")

if not BOT_TOKEN or not APP_URL:
    print("Faltan variables. Uso:")
    print("  BOT_TOKEN=... APP_URL=https://tu-app.up.railway.app python3 bot_setup.py")
    sys.exit(1)

API = f"https://api.telegram.org/bot{BOT_TOKEN}"

resp = requests.post(
    f"{API}/setChatMenuButton",
    json={"menu_button": {"type": "web_app", "text": "Multi-Perfil IA", "web_app": {"url": APP_URL}}},
    timeout=10,
)
print("setChatMenuButton:", resp.status_code, resp.json())

# Opcional: también deja el mismo botón en el mensaje de bienvenida si el bot
# responde a /start (no obligatorio — el botón de menú ya alcanza para abrir la app).
me = requests.get(f"{API}/getMe", timeout=10).json()
print("\nBot conectado:", me.get("result", {}).get("username"))
print("Listo. Abrí un chat con el bot en Telegram y vas a ver el botón de menú junto al campo de texto.")
