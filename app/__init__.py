# app/__init__.py
# -*- coding: utf-8 -*-
"""
Factory de la aplicación Flask (Web_v2).

- Registra blueprints.
- Inicializa extensiones (mail, db).
- Carga configuración desde entorno (.env soportado vía wsgi.py).
- Crea esquema de BD y carpetas de runtime.
- Define filtros Jinja útiles y comandos CLI:
    * send-test-email
    * purge-comprobantes
    * purge-seat-holds

Notas:
- Para evitar que se vea el AUTH de SMTP en consola, el envío real se hace
  con Flask-Mail (ver app/service/emailer.py) y NO activamos debuglevel.
"""

from __future__ import annotations

import os
from datetime import timedelta
from flask import Flask

# Extensiones
from .extensions import mail
from app import db as db_mod  # usamos db_mod.init_app y db_mod.create_schema

# Blueprints
from .blueprints.main import bp as main_bp
from .blueprints.venta import bp as venta_bp
from .blueprints.pago import bp as pago_bp
from .blueprints.archivos import bp as archivos_bp
from .blueprints.auth import bp as auth_bp
from .blueprints.mercadopago import bp as mercadopago_bp
from .blueprints.pago_mp import bp as pago_mp_bp


def _bool_env(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}


def create_app() -> Flask:
    """
    Crea y configura la instancia de Flask.
    """
    app = Flask(
        __name__,
        static_folder="../static",       # relativo al paquete `app/`
        template_folder="../templates",  # relativo al paquete `app/`
    )

    # ----------------- Configuración ----------------- #
    # Seguridad / sesión
    app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET", "change-me-in-dev-only")
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=6)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = _bool_env("SESSION_COOKIE_SECURE", False)  # activar en prod HTTPS

    # Cache estáticos
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 60 * 60 * 24 * 7  # 7 días

    # Mail / SMTP (usado por Flask-Mail y nuestro emailer)
    app.config["MAIL_SERVER"] = os.getenv("MAIL_SERVER", "localhost")
    app.config["MAIL_PORT"] = int(os.getenv("MAIL_PORT", "25"))
    app.config["MAIL_USERNAME"] = os.getenv("MAIL_USERNAME", "")
    app.config["MAIL_PASSWORD"] = os.getenv("MAIL_PASSWORD", "")
    app.config["MAIL_USE_TLS"] = _bool_env("MAIL_USE_TLS", True)
    app.config["MAIL_USE_SSL"] = _bool_env("MAIL_USE_SSL", False)
    app.config["MAIL_DEFAULT_SENDER"] = (
        os.getenv("SENDER_NAME", "Cinema3D"),
        os.getenv("MAIL_USERNAME", ""),
    )
    # EMAIL_DEBUG=1 => NO enviamos correos, solo log informativo (ver emailer.py)
    app.config["EMAIL_DEBUG"] = _bool_env("EMAIL_DEBUG", True)
    # No exponemos AUTH en consola: no usamos debuglevel de smtplib.
    # Si alguna vez querés forzar debug SMTP, usá SMTP_DEBUG=1 y configurá SOLO en emailer.py
    app.config["SMTP_DEBUG"] = _bool_env("SMTP_DEBUG", False)

    # Negocio / rutas de archivos
    app.config["DEFAULT_BRANCH"] = os.getenv("DEFAULT_BRANCH", "Cine Pelagio B. Luna 960")
    app.config["DB_PATH"] = os.getenv("DB_PATH", "usuarios.db")
    app.config["COMPROBANTES_DIR"] = os.getenv("COMPROBANTES_DIR", "static/comprobantes")
    app.config["QR_DIR"] = os.getenv("QR_DIR", "static/qr")
    app.config["QR_SIGN_SECRET"] = os.getenv("QR_SIGN_SECRET")  # opcional

    # Parámetros de butacas (usados por reserva de asientos)
    app.config.setdefault("SEAT_ROWS", os.getenv("SEAT_ROWS", "ABCDEFGHIJ"))
    app.config.setdefault("SEAT_COLS", int(os.getenv("SEAT_COLS", "12")))
    app.config.setdefault("SEAT_MAX_PER_ORDER", int(os.getenv("SEAT_MAX_PER_ORDER", "6")))
    app.config.setdefault("HOLD_TTL_SECONDS", int(os.getenv("HOLD_TTL_SECONDS", "600")))  # 10min por defecto

    # Precio de entrada (para cálculo server-side del total)
    app.config.setdefault("TICKET_PRICE", os.getenv("TICKET_PRICE", "5000"))

    # MercadoPago
    app.config["MERCADOPAGO_ACCESS_TOKEN"] = os.getenv("MERCADOPAGO_ACCESS_TOKEN", "")
    app.config["MERCADOPAGO_PUBLIC_KEY"] = os.getenv("MERCADOPAGO_PUBLIC_KEY", "")

    # ----------------- Extensiones ----------------- #
    mail.init_app(app)     # Flask-Mail
    db_mod.init_app(app)   # registra teardown y comando `flask init-db`

    # ----------------- Blueprints ----------------- #
    app.register_blueprint(main_bp)      # "/", "/bienvenida", set/clear branch
    app.register_blueprint(venta_bp)     # "/cartelera", "/reserva-asientos", etc.
    app.register_blueprint(pago_bp)      # "/pago" (sistema con opciones MercadoPago y Tarjeta)
    app.register_blueprint(pago_mp_bp)   # "/pago-mp/" (procesamiento específico de MercadoPago)
    app.register_blueprint(archivos_bp)  # "/comprobante/<id>/descargar"
    app.register_blueprint(auth_bp)      # "/login", "/logout", "/registro"
    
    # Importar y registrar blueprint de administración
    from app.blueprints.admin import bp as admin_bp
    app.register_blueprint(admin_bp)     # "/admin/*"

    # ----------------- Filtros y context processors ----------------- #
    @app.template_filter("peso")
    def _peso(value):
        """Formatea números como moneda ARS: $ 1.234,56"""
        try:
            return f"$ {float(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        except Exception:
            return value

    @app.template_filter("from_json")
    def _from_json(value):
        """Convierte string JSON a objeto Python"""
        try:
            import json
            if isinstance(value, str):
                return json.loads(value)
            return value
        except Exception:
            return []

    # Funciones disponibles en todos los templates
    @app.context_processor
    def inject_auth_functions():
        from app.blueprints.auth import current_user, is_logged_in, is_admin
        return {
            'current_user': current_user,
            'is_logged_in': is_logged_in,
            'is_admin': is_admin
        }

    # ----------------- DB & runtime bootstrap ----------------- #
    with app.app_context():
        # Verificar y ejecutar migraciones de MercadoPago si es necesario
        from app.db_migrations import check_migration_needed, migrate_add_mercadopago_support
        
        if check_migration_needed():
            app.logger.info("Ejecutando migración de MercadoPago...")
            if migrate_add_mercadopago_support():
                app.logger.info("✅ Migración de MercadoPago completada")
            else:
                app.logger.error("❌ Error en migración de MercadoPago")
        
        # asegura tablas (usuarios, transacciones, seats, etc.)
        db_mod.create_schema()
        os.makedirs(app.config["COMPROBANTES_DIR"], exist_ok=True)
        os.makedirs(app.config["QR_DIR"], exist_ok=True)

        # Limpieza de holds vencidos al iniciar (opcional; deja todo más prolijo en dev)
        try:
            removed = db_mod.purge_expired_holds()
            if removed:
                app.logger.info("Limpieza inicial: eliminados %s seat_holds vencidos.", removed)
        except Exception as e:
            app.logger.warning("No se pudo purgar holds al inicio: %s", e)

    # ----------------- Comandos CLI útiles ----------------- #
    # 1) Enviar email de prueba (útil para validar SMTP sin mostrar AUTH en consola)
    @app.cli.command("send-test-email")
    def send_test_email():
        """
        Envía un email de prueba al destinatario indicado por TEST_EMAIL_TO.
        Si EMAIL_DEBUG=1, NO envía (sólo informa).
        Uso:
            set TEST_EMAIL_TO=destino@dominio.com
            flask --app wsgi send-test-email
        """
        from flask import current_app
        from flask_mail import Message
        from .extensions import mail as mail_ext  # evitar shadowing
        to = os.getenv("TEST_EMAIL_TO")
        if not to:
            print("⚠ Definí TEST_EMAIL_TO en el entorno para usar este comando.")
            return
        if current_app.config.get("EMAIL_DEBUG", True):
            print("ℹ EMAIL_DEBUG=1 → no se envía. Poné EMAIL_DEBUG=0 para envío real.")
            print(f"Destino sería: {to}")
            return

        msg = Message(
            subject="Prueba Cinema3D",
            recipients=[to],
            body="Esto es un email de prueba desde Cinema3D.",
        )
        try:
            mail_ext.send(msg)
            print(f"✔ Email de prueba enviado a {to}")
        except Exception as e:
            print(f"✖ Error enviando email de prueba: {e}")

    # 2) Purgar comprobantes antiguos
    @app.cli.command("purge-comprobantes")
    def purge_comprobantes():
        """
        Elimina PDFs de comprobantes más viejos que N días.
        Variables:
            PURGE_DAYS (por defecto 30)
        Uso:
            set PURGE_DAYS=60
            flask --app wsgi purge-comprobantes
        """
        from flask import current_app
        import time
        from pathlib import Path

        days = int(os.getenv("PURGE_DAYS", "30"))
        base = Path(current_app.config["COMPROBANTES_DIR"])
        if not base.exists():
            print("No hay carpeta de comprobantes.")
            return
        cutoff = time.time() - days * 24 * 3600
        removed = 0
        for f in base.glob("*.pdf"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)
                    removed += 1
            except Exception:
                pass
        print(f"✔ Eliminados {removed} PDFs (> {days} días). Carpeta: {base}")

    # 3) Purgar holds vencidos (asientos retenidos expirados)
    @app.cli.command("purge-seat-holds")
    def purge_seat_holds():
        """
        Elimina de la BD los 'seat_holds' con expires_at vencido.
        Uso:
            flask --app wsgi purge-seat-holds
        """
        try:
            n = db_mod.purge_expired_holds()
            print(f"✔ Eliminados {n} holds vencidos.")
        except Exception as e:
            print(f"✖ Error purgando holds: {e}")

    # ----------------- Logging básico ----------------- #
    if not app.debug and not app.testing:
        import logging
        from logging.handlers import RotatingFileHandler
        log_dir = os.path.join(app.root_path, "..", "logs")
        os.makedirs(log_dir, exist_ok=True)
        handler = RotatingFileHandler(
            os.path.join(log_dir, "app.log"),
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setLevel(logging.INFO)
        fmt = logging.Formatter("[%(asctime)s] %(levelname)s in %(module)s: %(message)s")
        handler.setFormatter(fmt)
        app.logger.addHandler(handler)
        app.logger.setLevel(logging.INFO)

    return app
