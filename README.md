# Multi-Perfil IA — backend + Mini App de Telegram

Mismo patrón que tu VPS Simulator: un solo Flask sirve la API y el frontend,
con SQLite, desplegado en Railway. Sin `python-telegram-bot`, solo `requests`
para el setup puntual del bot.

## Estructura

```
backend/
  app.py            # API Flask + sirve el frontend
  static/index.html # la Mini App (Telegram + toasts + countdown)
  bot_setup.py       # configura el botón de menú del bot (se corre una vez)
  requirements.txt
  Procfile
  .env.example
```

## 1) Crear el bot en Telegram (si no tenés uno para esto)

1. Hablá con **@BotFather** en Telegram.
2. `/newbot` → elegí nombre y username → te da el **token**.
3. Guardá ese token, lo necesitás en el paso 4.

## 2) Desplegar en Railway

1. Subí esta carpeta `backend/` a un repo de GitHub (o usá `railway up` directo desde la CLI sin repo).
2. En Railway: **New Project → Deploy from GitHub repo**, elegí el repo.
3. Railway detecta el `Procfile` y el `requirements.txt` solo.
4. **Importante — persistencia:** el filesystem de Railway se borra en cada redeploy. Agregá un **Volume**:
   - Settings → Volumes → Add Volume → mount path `/data`
   - Variables → agregá `DB_PATH=/data/app.db`
   - Sin esto, cada vez que redeployes perdés los perfiles y cuentas guardados.
5. Esperá el deploy y copiá la URL pública que te da Railway (algo como `https://tu-app.up.railway.app`).

## 3) Conectar el botón de la Mini App

Desde tu máquina (no hace falta que corra en Railway):

```bash
cd backend
pip install requests
BOT_TOKEN=el_token_de_botfather APP_URL=https://tu-app.up.railway.app python3 bot_setup.py
```

Abrís el chat con tu bot en Telegram y ya tenés el botón de menú "Multi-Perfil IA"
junto al campo de texto, que abre la app dentro de Telegram.

## 4) Probarlo

- Abrí el bot en Telegram → tocá el botón de menú → se abre la Mini App.
- Tu `tg_id` (tu ID de Telegram) identifica tus datos en el servidor — podés
  abrirlo desde el celu y la compu con la misma cuenta de Telegram y vas a ver
  lo mismo en los dos lados.
- Si entrás a la misma URL desde un navegador normal (sin Telegram), funciona
  igual: te asigna un ID de prueba que guarda en ese navegador, útil para
  probar antes de conectar el bot.

## Migración de datos

Si ya venías usando la versión sin servidor (`multiperfil-ia.html`, guardada
en `localStorage` del navegador), la primera vez que abras esta versión nueva
**en ese mismo navegador** te va a ofrecer migrar esos datos al servidor con
un botón — no se pisan ni se pierden solos.

## Nota de seguridad

El `tg_id` se manda tal cual desde el cliente, sin verificar la firma
("initData") que manda Telegram — igual que en tu VPS Simulator. Para uso
personal alcanza, pero si en algún momento le das acceso a más gente conviene
validar el hash HMAC de `initData` contra el `BOT_TOKEN` en el backend antes
de confiar en el `tg_id` que llega.

## Variables de entorno

Ver `.env.example`. En Railway solo necesitás declarar `DB_PATH` (apuntando
al Volume). `BOT_TOKEN`/`APP_URL` son solo para correr `bot_setup.py` una vez
desde tu máquina, no hace falta declararlas en Railway.
