# wsgi.py
# -*- coding: utf-8 -*-
"""
Punto de entrada de la aplicación (WSGI/ASGI-friendly).

- Carga variables desde .env
- Construye la app vía app.create_app()
- Expone `app` para servidores (gunicorn/waitress) y para `flask --app wsgi run`

Uso (desarrollo):
    flask --app wsgi run --debug

Uso (producción - Linux):
    gunicorn -w 2 -b 0.0.0.0:8000 wsgi:app

Uso (producción - Windows):
    waitress-serve --listen=0.0.0.0:8000 wsgi:app
"""

from __future__ import annotations

import os
from dotenv import load_dotenv

# Cargar variables de entorno desde .env si existe
load_dotenv()

# Importar la factory de la app
from app import create_app  # noqa: E402  (import tardío a propósito)

# Instancia global que usan los servidores WSGI
app = create_app()

if __name__ == "__main__":
    # Ejecución directa (solo para desarrollo)
    host = os.getenv("FLASK_RUN_HOST", "127.0.0.1")
    port = int(os.getenv("FLASK_RUN_PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "1") in {"1", "true", "True"}
    app.run(host=host, port=port, debug=debug)
    # En prod (HTTPS) conviene activar:
    # app.config["SESSION_COOKIE_SECURE"] = True