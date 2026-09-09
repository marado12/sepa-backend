"""
Puente entre las fuentes online y el optimizador de canasta.

Traduce la respuesta de `fuentes.py` a la MISMA estructura que devuelve
`_buscar_precios()` en main.py:

    { (cadena, nombre_canasta): {"precio_min": float, "precio_por_100u": dict|None} }

Esa igualdad de forma es deliberada: `_analizar()` y `_canasta_optima()` quedan
sin tocar. El optimizador es el ítem 1.1 del roadmap, donde ya hay una sospecha
de bug; mezclar fuente nueva con cambios ahí haría imposible saber qué rompió qué.

Los helpers de texto (`normalizar`, `extraer_cantidades`) se reciben por parámetro
en vez de importarse de main.py: evita el import circular y permite testear el
matching con stubs simples.
"""

from __future__ import annotations

import logging
import re
from typing import Callable, Optional

from fuentes import Fuente, Oferta, Resultado

log = logging.getLogger(__name__)

# Un match por debajo de esto se descarta. Preferimos "no tengo precio" antes que
# "el precio del KitKat cuando pediste leche": un precio equivocado en el
# comparador es peor que un hueco, porque el usuario no puede detectarlo.
UMBRAL_MATCH = 0.45

# Tolerancia de cantidad: 1L pedido matchea 900ml-1.1L.
TOLERANCIA_CANTIDAD = 0.15


def _tokens(texto: str, normalizar: Callable[[str], str]) -> set[str]:
    return {t for t in re.split(r"\W+", normalizar(texto)) if len(t) > 2}


def puntuar(item: dict, oferta: Oferta,
            normalizar: Callable[[str], str],
            extraer_cantidades: Optional[Callable[[str], list]] = None) -> float:
    """
    Score 0..1 de cuán bien una oferta responde a un ítem de la canasta.

    Tres señales, en orden de confianza:
      1. Solapamiento de palabras (base)
      2. Marca, si el usuario pidió una
      3. Cantidad, si se puede extraer de ambos lados
    """
    nombre = item.get("nombre") or ""
    if not nombre or not oferta.producto:
        return 0.0

    t_item = _tokens(nombre, normalizar)
    t_of = _tokens(oferta.producto, normalizar)
    if not t_item:
        return 0.0

    # 1. Cobertura + precisión.
    #    Solo cobertura no alcanza: "leche entera" contra "Oblea Leche 4 Fingers
    #    Kitkat" da 0.5 y el KitKat entraba al comparador como leche. Que el
    #    producto ofrecido traiga muchas palabras ajenas ES evidencia de que es
    #    otra cosa, así que la precisión entra como factor. Sigue pesando más la
    #    cobertura (que falten palabras del pedido es peor que que sobren).
    comunes = t_item & t_of
    if not comunes:
        return 0.0
    cobertura = len(comunes) / len(t_item)
    precision = len(comunes) / len(t_of) if t_of else 0.0
    score = cobertura * (0.6 + 0.4 * precision)

    # 2. Marca
    marca_pedida = (item.get("marca") or "").strip()
    aceptadas = [m for m in (item.get("marcas_aceptadas") or []) if m]
    if marca_pedida or aceptadas:
        candidatas = [m.lower() for m in ([marca_pedida] + aceptadas) if m]
        texto = f"{oferta.producto} {oferta.marca or ''}".lower()
        if any(c in texto for c in candidatas):
            score = min(1.0, score + 0.25)
        elif marca_pedida:
            score *= 0.6            # pidió marca y no está: penaliza, no descarta

    # 3. Cantidad
    if extraer_cantidades:
        objetivo = _cantidad_objetivo(item, extraer_cantidades)
        if objetivo:
            val_obj, tipo_obj = objetivo
            cants = extraer_cantidades(normalizar(oferta.producto)) or []
            iguales = [(v, t) for v, t in cants if t == tipo_obj and v > 0]
            if iguales:
                val, _ = iguales[0]
                ratio = min(val, val_obj) / max(val, val_obj)
                if ratio >= 1 - TOLERANCIA_CANTIDAD:
                    score = min(1.0, score + 0.20)
                elif ratio < 0.5:
                    score *= 0.7   # 500ml cuando pediste 1.5L: castiga
            elif cants:
                # Pediste algo medido en litros y esto se vende por peso (o al
                # revés). Es otra categoría de producto, no otro tamaño.
                score *= 0.5

    return round(min(score, 1.0), 3)


def _cantidad_objetivo(item: dict, extraer_cantidades: Callable[[str], list]):
    """Cantidad pedida, del campo estructurado o del texto del nombre."""
    cant = item.get("cantidad")
    unidad = (item.get("unidad") or "").strip().lower()
    if cant and unidad and unidad not in ("unidad", "u", ""):
        for val, tipo in (extraer_cantidades(f"{cant} {unidad}") or []):
            return (val, tipo)
    for val, tipo in (extraer_cantidades(item.get("nombre") or "") or []):
        return (val, tipo)
    return None


def _precio_por_100u(oferta: Oferta, extraer_cantidades: Optional[Callable[[str], list]],
                     normalizar: Callable[[str], str]) -> Optional[dict]:
    """Misma forma que `_calcular_precio_unitario` de main.py."""
    if not extraer_cantidades:
        return None
    LABEL = {"peso": "$/100g", "volumen": "$/100ml", "count": "$/unidad"}
    for val, tipo in (extraer_cantidades(normalizar(oferta.producto)) or []):
        if val > 0 and tipo in LABEL:
            if tipo == "count":
                valor, label = round(oferta.precio / val, 2), f"$/unidad (pack x{int(val)})"
            else:
                valor, label = round(oferta.precio / val * 100, 2), LABEL[tipo]
            return {
                "valor": valor,
                "tipo": tipo,
                "label": label,
                "desc_ganadora": oferta.producto,
                "cantidad_base": round(val, 1),
            }
    return None


def buscar_precios_online(
    canasta: list[dict],
    cadenas: list[str],
    fuente: Fuente,
    normalizar: Callable[[str], str],
    extraer_cantidades: Optional[Callable[[str], list]] = None,
    umbral: float = UMBRAL_MATCH,
) -> tuple[dict, dict]:
    """
    Devuelve (precios, meta).

    `precios` tiene la forma exacta que consume el optimizador.
    `meta` lleva cobertura, cadenas caídas y diagnóstico de matching, para que
    /api/comparar pueda avisarle al usuario que la comparación es parcial en vez
    de mostrarle un óptimo calculado sobre la mitad de las cadenas.
    """
    precios: dict[tuple[str, str], dict] = {}
    fallidas: dict[str, str] = {}
    consultadas: list[str] = []
    sin_match: list[str] = []
    detalle: dict[str, dict] = {}

    for item in canasta:
        nombre = item.get("nombre") or ""
        if not nombre:
            continue

        consulta = " ".join(x for x in (nombre, (item.get("marca") or "").strip()) if x)
        try:
            r: Resultado = fuente.buscar(consulta, cadenas)
        except Exception as e:                      # noqa: BLE001 — nunca tumbar la comparación
            log.error("[online] la fuente falló para '%s': %s", nombre, e)
            sin_match.append(nombre)
            continue

        fallidas.update(r.fallidas)
        for c in r.consultadas:
            if c not in consultadas:
                consultadas.append(c)

        # Mejor match por cadena
        mejor: dict[str, tuple[float, Oferta]] = {}
        for of in r.ofertas:
            if not of.disponible or of.precio <= 0:
                continue
            s = puntuar(item, of, normalizar, extraer_cantidades)
            if s < umbral:
                continue
            # A igual score gana el más barato; a distinto score gana el mejor match.
            actual = mejor.get(of.cadena)
            if actual is None or (s, -of.precio) > (actual[0], -actual[1].precio):
                mejor[of.cadena] = (s, of)

        if not mejor:
            sin_match.append(nombre)
            continue

        detalle[nombre] = {
            "cadenas_con_precio": sorted(mejor),
            "mejor_score": max(s for s, _ in mejor.values()),
        }
        for cadena, (score, of) in mejor.items():
            precios[(cadena, nombre)] = {
                "precio_min": float(of.precio),
                "precio_por_100u": _precio_por_100u(of, extraer_cantidades, normalizar),
                # Extras que el SEPA no puede dar. `_analizar` los ignora.
                "precio_lista": of.precio_lista,
                "descuento_pct": of.descuento_pct,
                "promos": of.promos,
                "ean": of.ean,
                "match_score": score,
                "origen": of.origen,
            }

    n_ok = len(consultadas) - len(fallidas)
    meta = {
        "origen_precios": "online",
        "cadenas_consultadas": consultadas,
        "cadenas_fallidas": fallidas,
        "cobertura_cadenas_pct": round(n_ok / len(consultadas) * 100, 1) if consultadas else 0.0,
        "productos_sin_match": sin_match,
        "productos_con_precio": len(canasta) - len(sin_match),
        "productos_pedidos": len(canasta),
        "detalle_match": detalle,
        "aviso": _aviso(fallidas, sin_match),
    }
    log.info("[online] %d precios | %d/%d cadenas | %d productos sin match",
             len(precios), n_ok, len(consultadas), len(sin_match))
    return precios, meta


def _aviso(fallidas: dict, sin_match: list) -> Optional[str]:
    partes = []
    if fallidas:
        partes.append(f"Sin datos de {', '.join(sorted(fallidas))}")
    if sin_match:
        muestra = ", ".join(sin_match[:3]) + ("…" if len(sin_match) > 3 else "")
        partes.append(f"No encontramos precio para: {muestra}")
    return ". ".join(partes) + "." if partes else None
