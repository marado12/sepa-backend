"""
Puente entre las fuentes online y el optimizador de canasta.

Traduce la respuesta de `fuentes.py` a la MISMA estructura que devuelve
`_buscar_precios()` en main.py:

    { (cadena, nombre_canasta): {"precio_min": float, "precio_por_100u": dict|None} }

✏️ Tarea 26 (18/09): la ruta online suma `envases` (cuántas veces se cobra
`precio_min`) y `sin_elegible`. Son opcionales: una entrada sin `envases` —la ruta
SEPA, un precio manual— se cobra `precio_min × cantidad`, como siempre.

Esa igualdad de forma es deliberada: `_analizar()` y `_canasta_optima()` quedan
sin tocar. El optimizador es el ítem 1.1 del roadmap, donde ya hay una sospecha
de bug; mezclar fuente nueva con cambios ahí haría imposible saber qué rompió qué.

Los helpers de texto (`normalizar`, `extraer_cantidades`) se reciben por parámetro
en vez de importarse de main.py: evita el import circular y permite testear el
matching con stubs simples.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Callable, Optional

from fuentes import _UNIDAD_SIN_CONTENIDO, Fuente, Oferta, Resultado

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
                # ✏️ Tarea 26 (18/09): con el subtotal por envases enteros el tamaño ya no
                # tiene que parecerse a lo pedido, y la decisión 4 era sacar esta señal. Se
                # CONSERVAN las dos ramas hasta la Tarea 22, a propósito y medido
                # (tests/medir_regla_26b.py, captura de la canasta del 14/09):
                #
                # El +0,20 satura en 1,0, así que entre candidatos conmensurables EMPATA los
                # scores y le pasa la decisión al desempate por precio. Yerba mate, 1 kg,
                # Chango Más: "Yerba Mate Buen Dia 1 Kg" $2.799 (base 0,800) y "Yerba Mate
                # Mañanita 1 Kg" $5.859 (base 0,867) quedan los dos en 1,000 y gana el más
                # barato. Sin el bonus gana 0,867 y el mismo kilo cuesta $3.060 más (×2,09).
                # Sacarlo cambiaba 6 representantes, ninguno por un producto mejor.
                #
                # El ×0,7 castiga por igual a todos los candidatos cuando todos quedan lejos
                # (los fideos de 500 g para 2 kg en las 5 cadenas), y ahí no elige nada. Donde
                # elige, sacarlo crea empates que el desempate resuelve por precio del ENVASE:
                # aceite girasol, 2 L, Día, "Dia 1,5 Lt." $4.600 (0,867) contra "Natural 0,9
                # Lt." $4.369 (0,607 → 0,867): gana el envase barato y cubrir 2 L cuesta
                # 3 × $4.369 = $13.107 en vez de 2 × $4.600 = $9.200.
                #
                # Los dos los fija test_envases.py. Sacarlos es decisión de la Tarea 22, junto
                # con un desempate que compare lo que se paga por lo pedido.
                if ratio >= 1 - TOLERANCIA_CANTIDAD:
                    score = min(1.0, score + 0.20)
                elif ratio < 0.5:
                    score *= 0.7   # 500ml cuando pediste 1.5L: castiga
            elif cants:
                # Pediste algo medido en litros y esto se vende por peso (o al
                # revés). Es otra categoría de producto, no otro tamaño.
                score *= 0.5

    return round(min(score, 1.0), 3)


# Unidades que NO describen contenido: el número que las acompaña no es una
# restricción de tamaño. "1 pack" quiere decir "un paquete, el tamaño me da
# igual"; leerlo como "un ítem de contenido" hace que el paso 3 castigue todo
# candidato que declare otra medida.
#
# `pack` faltaba, y estaba en producción: `CANASTA_DEFAULT` lo usa en papel
# higiénico y en huevos. Medido sobre las 5 fixtures, con metros habilitados los
# candidatos aceptados de papel higiénico caían de 103 a 55 y el representante de
# Carrefour pasaba a ser un PORTARROLLOS de plástico de $3.500 en vez del pack de
# 320 m. En huevos, un maple de 12 puntuaba 0,513 con 'pack' y 0,733 con 'unidad'.
#
# Si alguna vez se cablea `cantidad_pack` (Tarea 17), el objetivo de tamaño sale
# de ESE campo, no de la unidad — este arreglo es compatible con esa salida.
_UNIDADES_SIN_CONTENIDO = ("unidad", "u", "pack", "")


def _cantidad_objetivo(item: dict, extraer_cantidades: Callable[[str], list]):
    """Cantidad pedida, del campo estructurado o del texto del nombre."""
    cant = item.get("cantidad")
    unidad = (item.get("unidad") or "").strip().lower()
    if cant and unidad and unidad not in _UNIDADES_SIN_CONTENIDO:
        for val, tipo in (extraer_cantidades(f"{cant} {unidad}") or []):
            return (val, tipo)
    for val, tipo in (extraer_cantidades(item.get("nombre") or "") or []):
        return (val, tipo)
    return None


# Escala de presentación por tipo: cuánto multiplicar el precio por unidad base
# y con qué etiqueta mostrarlo. Vive acá, en una sola tabla, porque antes estaba
# duplicada en main.py y en este archivo — y agregar un tipo en una sola de las
# dos es exactamente cómo `longitud` terminó sin existir en ninguna.
# Cambiar "$/100g" por "$/kg" es tocar esta tabla y nada más.
_ESCALA = {
    "peso":     (100, "$/100g"),
    "volumen":  (100, "$/100ml"),
    "longitud": (1,   "$/m"),
    "count":    (1,   "$/unidad"),
}
_UNIDAD_BASE = {"peso": "g", "volumen": "ml", "longitud": "m", "count": "u"}

TIPOS_COMPARABLES = tuple(_ESCALA)


def precio_unitario(precio: float, cantidad_base: float, tipo: str,
                    desc: str) -> Optional[dict]:
    """
    Precio por unidad de contenido, con el dato y su presentación separados.

    `precio_base` es el número con el que se comparan dos productos: precio por
    UNA unidad base (un gramo, un ml, un metro, una unidad). `valor` y `label`
    son solo cómo se muestra — hoy $/100g y $/m, mañana lo que se decida, sin
    tocar nada de lo que compara.
    """
    if not (precio > 0 and cantidad_base > 0):
        return None
    escala = _ESCALA.get(tipo)
    if not escala:
        return None
    factor, label = escala
    if tipo == "count":
        label = f"$/unidad (pack x{int(cantidad_base)})"
    return {
        "valor": round(precio / cantidad_base * factor, 2),
        "tipo": tipo,
        "label": label,
        "desc_ganadora": desc,
        "cantidad_base": round(cantidad_base, 1),
        "precio_base": round(precio / cantidad_base, 6),
        "unidad_base": _UNIDAD_BASE[tipo],
    }


# `_UNIDAD_SIN_CONTENIDO` ("un", "unidad"…) vive en fuentes.py: `_a_oferta` la necesita
# para decidir qué cotiza el precio. Con esas unidades hay que seguir leyendo el título.


def _precio_por_100u(oferta: Oferta, extraer_cantidades: Optional[Callable[[str], list]],
                     normalizar: Callable[[str], str]) -> Optional[dict]:
    """
    Misma forma que `_calcular_precio_unitario` de main.py.

    Primero lo que declara la fuente (`unidad_medida` + `contenido`: qué cubre
    `oferta.precio`); recién después se lee el título. Un pesable "x kg" no dice en
    el nombre cuánto pesa, y no hace falta: su precio es el del kilo, contenido 1 kg.
    ✏️ Hasta la Tarea 25 el contenido era `unitMultiplier` dividiendo al precio del
    kilo: el pollo `kg`/3.0 salía 3× abaratado y el tomate `kg`/0.1, 10× encarecido.
    """
    if not extraer_cantidades:
        return None

    unidad = (oferta.unidad_medida or "").strip().lower()
    if oferta.contenido and unidad and unidad not in _UNIDAD_SIN_CONTENIDO:
        for val, tipo in (extraer_cantidades(f"{oferta.contenido} {unidad}") or []):
            pu = precio_unitario(oferta.precio, val, tipo, oferta.producto)
            if pu:
                pu["fuente_contenido"] = "api"
                return pu

    for val, tipo in (extraer_cantidades(normalizar(oferta.producto)) or []):
        pu = precio_unitario(oferta.precio, val, tipo, oferta.producto)
        if pu:
            pu["fuente_contenido"] = "nombre"
            return pu
    return None


# ─────────────────────────────────────────────────────────────────
#  Tarea 26 — la unidad que pedís contra la unidad que cotiza el precio
# ─────────────────────────────────────────────────────────────────
#
# Hasta el 18/09 el subtotal era `precio_min × cantidad` sin mirar qué compra un
# `precio_min`: "Fideos, 2 kg" con un paquete de 500 g cobraba 2 paquetes (1 kg), y
# "Gaseosa cola, 3 litro" con una botella de 3 L cobraba 3 botellas (9 L). Ahora la
# fila cobra los ENVASES ENTEROS que cubren lo pedido, y solo puede representar al
# ítem un candidato que pueda contestar esa pregunta. Decisiones 1, 3 y 6 de la Tarea
# 26 (claude/PROXIMAS-TAREAS.md); medido antes en tests/medir_regla_26b.py.

def _pide_contenido(item: dict) -> bool:
    """El pedido declara contenido (kg, litro, gramos, ml). "unidad" y "pack" no."""
    return (item.get("unidad") or "").strip().lower() not in _UNIDADES_SIN_CONTENIDO


def _pedido(item: dict, extraer_cantidades: Callable[[str], list]):
    """
    (contenido total pedido en la unidad base, tipo) — "2 kg" → (2000.0, "peso").
    None si el pedido no declara contenido o la unidad no se puede leer: ahí no se
    cae al nombre del ítem como hace `_cantidad_objetivo`, porque esto fija el precio.
    """
    if not _pide_contenido(item) or not item.get("cantidad"):
        return None
    for val, tipo in (extraer_cantidades(f"{item['cantidad']} {item['unidad']}") or []):
        return (val, tipo)
    return None


def _cotiza_um(oferta: Oferta) -> bool:
    """`precio` es el de UNA unidad de medida (el kilo de un pesable). Misma precedencia que `_precio_por_100u`."""
    unidad = (oferta.unidad_medida or "").strip().lower()
    return bool(oferta.contenido and unidad and unidad not in _UNIDAD_SIN_CONTENIDO)


def _click(oferta: Oferta) -> float:
    """Cuántas unidades cobra un `precio` de artículo: 1, salvo "un" con multiplicador."""
    return float(oferta.contenido) if oferta.contenido else 1.0


def elegible(item: dict, oferta: Oferta, pu: Optional[dict],
             extraer_cantidades: Callable[[str], list]) -> bool:
    """
    ¿Este candidato puede contestar lo que pide `item`? (decisión 3: filtro, no puntaje)
      "unidad"/"pack": solo un ARTÍCULO dice cuántos — el precio del kilo no tiene con qué.
      kg/litro: hace falta métrica del MISMO tipo que lo pedido (peso con peso); el
      tamaño puede ser cualquiera, lo resuelve `envases_a_comprar`.
    """
    if not _pide_contenido(item):
        return not _cotiza_um(oferta)
    pedido = _pedido(item, extraer_cantidades)
    return bool(pu) and pedido is not None and pu.get("tipo") == pedido[1]


def envases_a_comprar(item: dict, oferta: Oferta, pu: Optional[dict],
                      extraer_cantidades: Callable[[str], list]) -> Optional[float]:
    """
    Cuántas veces se cobra `oferta.precio` para cubrir lo pedido (decisión 1):
      kg/litro + precio por unidad de medida → exacto: pedido ÷ lo que cubre el precio
          ("2 kg" contra el kilo = 2; "500 gramos" contra el kilo = 0,5).
      kg/litro + artículo con métrica del mismo tipo → envases enteros, sin tolerancia
          hacia abajo: ceil(pedido ÷ contenido del envase). 2 kg de 500 g = 4.
      "unidad"/"pack" + artículo → ceil(cantidad ÷ click). "2 unidad" de un click de 6 = 1.
      Cualquier otro caso → None: la fila no se puede escalar y no se inventa.
    Un candidato elegible siempre da un número; uno no elegible, siempre None.
    """
    if not _pide_contenido(item):
        if _cotiza_um(oferta):
            return None
        return float(math.ceil(float(item.get("cantidad") or 0) / _click(oferta) - 1e-9))
    pedido = _pedido(item, extraer_cantidades)
    if not pedido or not pu or pu.get("tipo") != pedido[1] or not pu.get("cantidad_base"):
        return None
    veces = pedido[0] / pu["cantidad_base"]
    if _cotiza_um(oferta):
        return round(veces, 6)
    return float(math.ceil(veces - 1e-9))


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

        # Mejor match por cadena. Clave (elegible, score, −precio): un candidato que no
        # puede contestar lo pedido pierde contra cualquiera que sí (Tarea 26, decisión 3);
        # entre elegibles, a igual score gana el más barato y a distinto score, el mejor
        # match — el desempate de siempre. Si NINGUNO es elegible queda el de siempre,
        # marcado `sin_elegible`. Sin `extraer_cantidades` no hay métrica: todos cuentan
        # como elegibles y la regla no se aplica.
        mejor: dict[str, tuple[tuple, float, Oferta, Optional[dict]]] = {}
        for of in r.ofertas:
            if not of.disponible or of.precio <= 0:
                continue
            s = puntuar(item, of, normalizar, extraer_cantidades)
            if s < umbral:
                continue
            pu = _precio_por_100u(of, extraer_cantidades, normalizar)
            ok = elegible(item, of, pu, extraer_cantidades) if extraer_cantidades else True
            clave = (ok, s, -of.precio)
            actual = mejor.get(of.cadena)
            if actual is None or clave > actual[0]:
                mejor[of.cadena] = (clave, s, of, pu)

        if not mejor:
            sin_match.append(nombre)
            continue

        detalle[nombre] = {
            "cadenas_con_precio": sorted(mejor),
            "mejor_score": max(s for _, s, _, _ in mejor.values()),
        }
        for cadena, (_clave, score, of, pu) in mejor.items():
            entry = {
                "precio_min": float(of.precio),
                "precio_por_100u": pu,
                # Extras que el SEPA no puede dar. `_analizar` los ignora.
                "precio_lista": of.precio_lista,
                "descuento_pct": of.descuento_pct,
                "promos": of.promos,
                "ean": of.ean,
                "match_score": score,
                "origen": of.origen,
            }
            if extraer_cantidades:
                # Tarea 26: cuántas veces se cobra `precio_min` (la `cantidad` pedida no se
                # pisa). None = la fila no se puede escalar, que con el filtro de arriba es
                # "ningún candidato elegible": no entra al total ni al %, pero su
                # `precio_por_100u` sigue votando en la mediana (decisión 6).
                entry["envases"] = envases_a_comprar(item, of, pu, extraer_cantidades)
                entry["sin_elegible"] = entry["envases"] is None
            precios[(cadena, nombre)] = entry

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
