# app/blueprints/pago.py
# -*- coding: utf-8 -*-
"""
Blueprint 'pago': formulario de pago (GET) y procesamiento (POST).
- Valida tarjeta (Luhn/brand/CVV/vencimiento).
- Calcula el total del lado del servidor (entradas + combos).
- Inserta transacción (estado PENDIENTE -> APROBADO/RECHAZADA).
- Confirma asientos (consume holds -> reservas definitivas).
- Genera QR + PDF del comprobante.
- (Opcional) Envía email con adjunto, si EMAIL_DEBUG=0.

Requisitos en el proyecto:
- templates/pago.html
- templates/pago_ok.html
- app/service/payments.py   -> validar_tarjeta(), detectar_brand()
- app/service/qrs.py        -> generar_qr()
- app/service/pdfs.py       -> generar_comprobante_pdf()
- app/service/emailer.py    -> enviar_ticket()
- app/db.py                 -> get_conn(), confirm_seats()
- app/data/seed.py          -> COMBOS_CATALOG
- Blueprint 'archivos' con ruta: /comprobante/<id>/descargar
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import List

from flask import (
    Blueprint,
    current_app,
    flash,
    render_template,
    request,
    session,
    url_for,
)

# ====== IMPORTS ABSOLUTOS (evita E0402) ======
from app.service.payments import validar_tarjeta, detectar_brand
from app.service.qrs import generar_qr
from app.service.pdfs import generar_comprobante_pdf
from app.service.emailer import enviar_ticket
from app.db import get_conn
from app import db as db_mod
from app.data.seed import COMBOS_CATALOG

bp = Blueprint("pago", __name__)


# ===================== Helpers internos ===================== #

def _combos_from_session() -> List[dict]:
    """Obtiene los combos seleccionados (lista de dicts) desde sesión."""
    ids = [int(x) for x in session.get("combos", [])]
    idset = set(ids)
    return [c for c in COMBOS_CATALOG if c["id"] in idset]


def _seleccion_from_session() -> dict:
    """Obtiene la selección de película/función desde sesión."""
    return session.get("movie_selection", {}) or {}


def _seats_from_session() -> List[str]:
    """Obtiene los asientos seleccionados desde sesión."""
    return session.get("seats", []) or []


def _precio_entrada() -> Decimal:
    """
    Precio unitario de la entrada desde config (TICKET_PRICE).
    Por defecto, 5000.00
    """
    raw = str(current_app.config.get("TICKET_PRICE", "5000"))
    try:
        return Decimal(raw)
    except Exception:
        return Decimal("5000")


def _calcular_totales_server_side() -> tuple[Decimal, Decimal, Decimal, list[dict], list[str], dict]:
    """
    Calcula en el servidor:
    - total_entradas = precio_entrada * cantidad_asientos
    - total_combos = sum(precio combos elegidos)
    - total = total_entradas + total_combos (redondeado a 2 decimales)
    Devuelve (total_entradas, total_combos, total, combos_sel, seats, seleccion)
    """
    TWO = Decimal("0.01")
    precio_ent = _precio_entrada()
    seats = _seats_from_session()
    combos_sel = _combos_from_session()
    seleccion = _seleccion_from_session()

    total_entradas = (precio_ent * Decimal(len(seats))).quantize(TWO, rounding=ROUND_HALF_UP)
    total_combos = sum(Decimal(str(c.get("precio", 0))) for c in combos_sel)
    total_combos = Decimal(total_combos).quantize(TWO, rounding=ROUND_HALF_UP)
    total = (total_entradas + total_combos).quantize(TWO, rounding=ROUND_HALF_UP)

    return total_entradas, total_combos, total, combos_sel, seats, seleccion


# ===================== Rutas público ===================== #

@bp.route("/pago", methods=["GET", "POST"])
def pago():
    """
    GET: muestra formulario con totales calculados en servidor.
    POST: valida tarjeta, CONFIRMA ASIENTOS, finaliza transacción, genera QR/PDF y (opcional) envía email.
    """
    if request.method == "GET":
        user = session.get("user_autofill", {})
        email = user.get("email", "")
        nombre_tarjeta = f"{user.get('nombre','')} {user.get('apellido','')}".strip().upper()

        total_entradas, total_combos, total, combos, seats, seleccion = _calcular_totales_server_side()

        return render_template(
            "pago.html",
            errores=None,
            exito=None,
            email=email,
            nombre_tarjeta=nombre_tarjeta,
            seleccion=seleccion,
            seats=seats,
            combos=combos,
            monto_sugerido=f"{total:.2f}",          # Solo visual. En POST no se usa.
            total_entradas=total_entradas,
            total_combos=total_combos,
            total=total,
            precio_entrada=_precio_entrada(),
        )

    # ---------- POST: procesar pago ----------
    # SIEMPRE recalculamos del lado del servidor (NO usamos monto del form)
    total_entradas, total_combos, total, combos_sel, seats, seleccion = _calcular_totales_server_side()

    if not seats or not seleccion:
        flash("Primero elegí función y asientos.", "warning")
        return render_template(
            "pago.html",
            errores=["Falta selección de función o asientos."],
            exito=None,
            email=request.form.get("email") or "",
            nombre_tarjeta=(request.form.get("nombre_tarjeta") or "").upper(),
            seleccion=seleccion,
            seats=seats,
            combos=combos_sel,
            monto_sugerido=f"{total:.2f}",
            total_entradas=total_entradas,
            total_combos=total_combos,
            total=total,
            precio_entrada=_precio_entrada(),
        )

    # Datos del form (pero el monto viene del server)
    email = (request.form.get("email") or "").strip()
    pan = (request.form.get("pan") or "").replace(" ", "")
    nombre_tarjeta = (request.form.get("nombre_tarjeta") or "").strip()
    exp_mes = (request.form.get("exp_mes") or "").strip()
    exp_anio = (request.form.get("exp_anio") or "").strip()
    cvv = (request.form.get("cvv") or "").strip()

    # Validación tarjeta (le pasamos el total calculado)
    errores = validar_tarjeta(email, pan, nombre_tarjeta, exp_mes, exp_anio, cvv, f"{total:.2f}")
    if errores:
        return render_template(
            "pago.html",
            errores=errores,
            exito=None,
            email=email,
            nombre_tarjeta=nombre_tarjeta,
            seleccion=seleccion,
            seats=seats,
            combos=combos_sel,
            monto_sugerido=f"{total:.2f}",
            total_entradas=total_entradas,
            total_combos=total_combos,
            total=total,
            precio_entrada=_precio_entrada(),
        )

    # Datos derivados transacción
    brand = detectar_brand(pan)
    last4 = pan[-4:] if pan else ""
    now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    auth_code = f"APP-{last4}-{datetime.now().strftime('%H%M%S')}"

    # Monto en centavos (Decimal seguro)
    monto_cents = int((total * 100).to_integral_value(rounding=ROUND_HALF_UP))

    # ---------- Persistencia: crear transacción en estado PENDIENTE ----------
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO transacciones (
                usuario_email, monto_cents, brand, last4, exp_mes, exp_anio,
                estado, auth_code, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                email or None,
                monto_cents,
                brand or None,
                last4 or None,
                int(exp_mes) if exp_mes else None,
                int(exp_anio) if exp_anio else None,
                "PENDIENTE",
                auth_code,
                now_iso,
            ),
        )
        trx_id = int(cur.lastrowid or 0)
        conn.commit()
    finally:
        # No cierres la conexión aquí; usamos get_conn() por request y teardown la cierra
        pass

    # ---------- Confirmar asientos (consume holds del token) ----------
    hold_token = session.get("hold_token")
    try:
        confirmados = db_mod.confirm_seats(
            token=hold_token,
            movie_id=seleccion.get("id"),
            fecha=seleccion.get("fecha"),
            hora=seleccion.get("hora"),
            sala=seleccion.get("sala"),
            usuario_email=email or None,
            trx_id=trx_id,
        )
        if not confirmados:
            # No había holds vigentes => marcamos transacción como RECHAZADA
            conn.execute("UPDATE transacciones SET estado=? WHERE id=?", ("RECHAZADA", trx_id))
            conn.commit()
            flash("Tus asientos ya no estaban retenidos. Por favor, volvé a elegirlos.", "warning")
            return_url = url_for("venta.reserva_asientos")
            return render_template(
                "pago.html",
                errores=["Los asientos se liberaron por tiempo. Volvé a seleccionarlos."],
                exito=None,
                email=email,
                nombre_tarjeta=nombre_tarjeta,
                seleccion=seleccion,
                seats=seats,
                combos=combos_sel,
                monto_sugerido=f"{total:.2f}",
                total_entradas=total_entradas,
                total_combos=total_combos,
                total=total,
                precio_entrada=_precio_entrada(),
                volver_url=return_url,
            )

    except ValueError as e:
        # Colisión de último momento con otro usuario
        conn.execute("UPDATE transacciones SET estado=? WHERE id=?", ("RECHAZADA", trx_id))
        conn.commit()
        flash(str(e), "danger")
        return_url = url_for("venta.reserva_asientos")
        return render_template(
            "pago.html",
            errores=[str(e)],
            exito=None,
            email=email,
            nombre_tarjeta=nombre_tarjeta,
            seleccion=seleccion,
            seats=seats,
            combos=combos_sel,
            monto_sugerido=f"{total:.2f}",
            total_entradas=total_entradas,
            total_combos=total_combos,
            total=total,
            precio_entrada=_precio_entrada(),
            volver_url=return_url,
        )

    # Si llegamos aquí, los asientos quedaron confirmados.
    conn.execute("UPDATE transacciones SET estado=? WHERE id=?", ("APROBADO", trx_id))
    conn.commit()

    # ---------- Datos para comprobante ----------
    sucursal = session.get("branch") or current_app.config.get("DEFAULT_BRANCH", "-")

    # QR (payload firmado si config['QR_SIGN_SECRET'] está presente)
    qr_path = generar_qr(
        trx_id=trx_id,
        verify_url=None,  # Si luego tenés endpoint de verificación, colocalo aquí
        extra={"email": email, "auth": auth_code},
    )

    # PDF
    pdf_path = generar_comprobante_pdf(
        trx_id=trx_id,
        cliente=(nombre_tarjeta or "-"),
        email=email,
        pelicula=seleccion.get("titulo", "-"),
        fecha_funcion=seleccion.get("fecha", "-"),
        hora_funcion=seleccion.get("hora", "-"),
        sala=seleccion.get("sala", "-"),
        asientos=confirmados,  # usar los confirmados
        combos=[{"nombre": c["nombre"], "cantidad": 1, "precio": c["precio"]} for c in combos_sel],
        total=float(total),  # para la plantilla de PDF
        sucursal=sucursal,
        qr_path=qr_path,
    )

    # ---------- Email opcional ----------
    try:
        enviar_ticket(
            destino=email,
            asunto=f"Comprobante TRX #{trx_id}",
            cuerpo=(
                f"Gracias por su compra.\n\n"
                f"Sucursal: {sucursal}\n"
                f"Película: {seleccion.get('titulo','-')}\n"
                f"Fecha/Hora: {seleccion.get('fecha','-')} {seleccion.get('hora','-')}\n"
                f"Asientos: {', '.join(confirmados) if confirmados else '-'}\n"
                f"Monto: ${total:.2f}\n"
                f"Código de autorización: {auth_code}\n"
            ),
            adjunto_path=pdf_path,
        )
    except (OSError, ValueError, RuntimeError) as e:
        # En modo debug o si falla SMTP, registramos y seguimos
        current_app.logger.warning("Email no enviado (debug/SMTP): %s", e)

    # URL de descarga (ideal: servir fuera de /static en prod, con autorización)
    comprobante_url = url_for("archivos.descargar_comprobante", trx_id=trx_id)

    # Limpieza de sesión sensible
    session.pop("seats", None)
    session.pop("hold_token", None)
    session.pop("combos", None)
    session.modified = True

    return render_template(
        "pago_ok.html",
        exito="¡Pago aprobado!",
        trx_id=trx_id,
        comprobante_url=comprobante_url,
        seleccion=seleccion,
        seats=confirmados,
        combos=combos_sel,
        total=float(total),
        brand=brand,
        last4=last4,
        auth_code=auth_code,
    )
