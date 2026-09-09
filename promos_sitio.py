"""
Promos bancarias tomadas de las tiendas online (roadmap 4.1).

Hoy las promos viven hardcodeadas en `PROMOS_DEFAULT` (main.py) y en
`BancosScreen.jsx`. El roadmap las marca como "la deuda más urgente de este
bloque: promos vencidas = recomendaciones equivocadas".

Los `Teasers` de VTEX traen la promo vigente por producto y por cadena, con los
BIN de tarjeta. Vienen en la MISMA respuesta que el precio, así que no cuesta
una request extra.

REGLA DE PRECEDENCIA (decisión de Santiago): la promo automatizada del sitio
siempre le gana a la cargada a mano. Si el sitio dice algo sobre una cadena, eso
manda para esa cadena. Las manuales quedan como fallback de las cadenas de las
que el sitio no dijo nada.

── La trampa importante ──
"2da unidad 70%" NO es 70% de descuento. Es 70% sobre la segunda unidad, o sea
~35% si comprás dos y 0% si comprás una. Tratarlo como un descuento plano
inflaría el ahorro y le haría elegir mal al usuario. Estas promos se detectan,
se reportan, y NO se aplican al total.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)


# Bancos y billeteras reconocibles en el texto del teaser.
# El orden importa: las claves más específicas van primero para que
# "banco nacion" no matchee antes que "banco nacion mi moto" (hipotético).
_BANCOS = [
    ("Banco Nación",      ("banco nacion", "bna", "modo bna")),
    ("Banco Galicia",     ("galicia",)),
    ("Banco Santander",   ("santander",)),
    ("Banco Provincia",   ("banco provincia", "bapro", "cuenta dni")),
    ("Banco Ciudad",      ("banco ciudad",)),
    ("BBVA",              ("bbva", "frances")),
    ("Macro",             ("macro",)),
    ("ICBC",              ("icbc",)),
    ("Brubank",           ("brubank",)),
    ("Uala",              ("uala", "ualá")),
    ("Mercado Pago",      ("mercado pago", "mercadopago")),
    ("MODO",              ("modo",)),
    ("Naranja X",         ("naranja",)),
]

# Promos por unidad: el descuento NO se aplica al total de la canasta.
def _sin_acentos(t: str) -> str:
    """El sitio escribe "Banco Nación"; las claves de _BANCOS van sin acento."""
    for a, b in (("á","a"), ("é","e"), ("í","i"), ("ó","o"), ("ú","u"), ("ü","u")):
        t = t.replace(a, b)
    return t


_RE_MULTI_UNIDAD = re.compile(
    r"(2da|2do|segunda|segundo|3ra|3er|tercera|tercer)\s*(unidad|u\.)?"
    r"|(\d+\s*x\s*\d+)"                    # 2x1, 3x2
    r"|(lleva|llevando)\s*\d+",
    re.IGNORECASE,
)

_RE_PCT = re.compile(r"(?<!\d)(\d{1,3}(?:[.,]\d+)?)\s*%")
_RE_CUOTAS = re.compile(r"(\d{1,2})\s*cuotas?\s*(sin\s*inter[eé]s|s/?i)?", re.IGNORECASE)


@dataclass
class PromoSitio:
    """Una promo leída del sitio de la cadena."""
    nombre: str
    cadena: str
    descuento_pct: Optional[float] = None
    cuotas_sin_interes: int = 0
    banco: Optional[str] = None
    # "descuento" se aplica al total; "multi_unidad" y "cuotas" solo se informan.
    tipo: str = "descuento"
    productos: list[str] = field(default_factory=list)

    @property
    def aplicable_al_total(self) -> bool:
        return self.tipo == "descuento" and bool(self.descuento_pct)


def parsear_teaser(texto: str, cadena: str) -> Optional[PromoSitio]:
    """
    Convierte el nombre de un teaser en una promo estructurada.
    Devuelve None si no se le puede sacar nada útil.
    """
    if not texto or not texto.strip():
        return None
    t = texto.strip()
    bajo = _sin_acentos(t.lower())

    banco = next((nombre for nombre, claves in _BANCOS
                  if any(c in bajo for c in claves)), None)

    m_pct = _RE_PCT.search(t)
    pct = float(m_pct.group(1).replace(",", ".")) if m_pct else None

    m_cuotas = _RE_CUOTAS.search(t)
    cuotas = int(m_cuotas.group(1)) if m_cuotas and m_cuotas.group(2) else 0

    # Multi-unidad: se informa, no se aplica. Ver la nota del encabezado.
    if _RE_MULTI_UNIDAD.search(bajo):
        return PromoSitio(nombre=t, cadena=cadena, descuento_pct=pct,
                          banco=banco, tipo="multi_unidad")

    if pct is not None:
        # Un "descuento" de 100% o más es un dato corrupto, no una oferta.
        if not (0 < pct < 100):
            log.warning("[promos_sitio] descuento fuera de rango en %r: %s%%", t, pct)
            return None
        return PromoSitio(nombre=t, cadena=cadena, descuento_pct=pct,
                          banco=banco, tipo="descuento")

    if cuotas > 0:
        return PromoSitio(nombre=t, cadena=cadena, cuotas_sin_interes=cuotas,
                          banco=banco, tipo="cuotas")

    return None


def extraer(precios: dict) -> dict[str, list[PromoSitio]]:
    """
    Junta las promos del sitio desde el dict de precios, agrupadas por cadena.

    `precios` es la salida de precios_vtex.buscar_precios_online(): las entradas
    traen `promos` (los Teasers). Las del SEPA no, así que devuelve {} y el
    sistema cae solo al comportamiento anterior.
    """
    por_cadena: dict[str, dict[str, PromoSitio]] = {}

    for (cadena, producto), entry in precios.items():
        for texto in (entry.get("promos") or []):
            p = parsear_teaser(texto, cadena)
            if not p:
                continue
            vistas = por_cadena.setdefault(cadena, {})
            if p.nombre in vistas:
                vistas[p.nombre].productos.append(producto)
            else:
                p.productos = [producto]
                vistas[p.nombre] = p

    return {c: sorted(v.values(), key=lambda p: -(p.descuento_pct or 0))
            for c, v in por_cadena.items()}


def reintegro_para(cadena: str, promos: list[PromoSitio],
                   detalle_cadena: list[dict]) -> tuple[Optional[str], float, int, Optional[str]]:
    """
    Mejor promo del sitio para una cadena.

    Devuelve (nombre, reintegro, cuotas, banco).

    El reintegro se calcula SOLO sobre los productos que efectivamente traen esa
    promo, no sobre el total de la canasta: un teaser es por producto. Aplicarlo
    al total inflaría el ahorro de una canasta donde solo un ítem está en oferta.
    """
    if not promos:
        return None, 0.0, 0, None

    # Subtotal por producto, de lo que ya calculó _analizar.
    subtotales = {d["producto"]: d.get("subtotal", 0.0)
                  for d in detalle_cadena if d.get("ok")}

    mejor_nombre, mejor_r, mejor_banco = None, 0.0, None
    for p in promos:
        if not p.aplicable_al_total:
            continue
        base = sum(subtotales.get(prod, 0.0) for prod in p.productos)
        r = base * (p.descuento_pct / 100)
        if r > mejor_r:
            mejor_r, mejor_nombre, mejor_banco = r, p.nombre, p.banco

    if mejor_nombre:
        return mejor_nombre, round(mejor_r, 2), 0, mejor_banco

    # Sin descuento en pesos: informar la mejor de cuotas, o la multi-unidad.
    cuotas = [p for p in promos if p.tipo == "cuotas" and p.cuotas_sin_interes > 0]
    if cuotas:
        mejor = max(cuotas, key=lambda p: p.cuotas_sin_interes)
        return mejor.nombre, 0.0, mejor.cuotas_sin_interes, mejor.banco

    if promos:
        return promos[0].nombre, 0.0, 0, promos[0].banco

    return None, 0.0, 0, None


def a_json(promos_por_cadena: dict[str, list[PromoSitio]]) -> dict:
    """Serializa para la respuesta de la API."""
    return {
        cadena: [{
            "nombre": p.nombre,
            "descuento_pct": p.descuento_pct,
            "cuotas_sin_interes": p.cuotas_sin_interes,
            "banco": p.banco,
            "tipo": p.tipo,
            "aplicable_al_total": p.aplicable_al_total,
            "productos": sorted(set(p.productos)),
        } for p in lista]
        for cadena, lista in promos_por_cadena.items()
    }
