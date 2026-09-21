"""
Tarea 26, paso 1 (17/09/2026) — La unidad que pedís contra la unidad que cotiza el precio.
✏️ Paso (c), 21/09/2026: reinterpretado sobre el código de (b). Ver "Desde el paso (c)".

Instrumento de MEDICIÓN, 100% SIN RED, sobre una captura guardada. No es test ni guard: fuera del CI.

Desde el paso (c). La fila cobra `subtotal = precio_min × envases` (Tarea 26 paso b): los envases enteros
que cubren lo pedido, o pedido ÷ esa unidad si el precio es el de la unidad de medida (el kilo de un
pesable). La pregunta pasa a ser: ¿lo que cobra el código compra lo pedido, con los envases mínimos?
  PEDIDO  = (cantidad, unidad) del ítem, leído igual que el código (`precios_vtex._pedido`, validación 7).
  ENVASE  = lo que compra UN `precio_min`: la métrica del representante (el kilo si cotiza por unidad de
            medida, el envase del título si es un artículo) o, en un pedido sin contenido, el click
            (1, salvo "un" con multiplicador).
  COMPRA  = envases × ENVASE, contra lo pedido.
Cobertura (ver `cobertura`): exacto · exacto UM · sobrante · no mínimo · no entero · no cubre ·
sin verif. · sin elegible · faltante. Que una fila cubra lo pedido NO dice que sea el producto pedido:
eso es la Tarea 22 y este instrumento no lo mide.

Hasta el paso (c) la fila cobraba `precio_min × cantidad` sin mirar `prod["unidad"]` y la pregunta era qué
compra UNA unidad de `precio_min` contra lo pedido: FACTOR = cuántas veces lo pedido cobraba el subtotal,
con los cajones A · B-mal · B-bien · B-? · C · C-mal · D-bien · D-mal (`clasificar`). Con (b) esos cajones
marcan como error lo que la regla ya escala: desde (c) son DATO (el envase contra una unidad pedida), y
salieron los escenarios S1/S2 de §3, que sacaban del total filas bien cobradas.

Separación 26 / 22 con la MISMA captura, ✏️ desde (c) solo para las filas que la regla no contesta:
¿hay algún candidato de esa cadena que el filtro de (b) aceptaría, y con qué puntaje?

No reimplementa la métrica ni el matcher: reusa `medir_pesables` (parsear, fichas) y
`medir_metrica_vivo` (lector de títulos independiente), y llama al código de producción para el resto.

Salida: 0 medición completa · 1 el instrumento no reproduce lo que dice medir.

Desde sepa_backend/:
    venv\\Scripts\\python.exe -m tests.medir_unidad_pedida --desde tests/capturas/canasta_default_2026-09-14.json
"""
import argparse
import json
import logging
import math
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import main
from fuentes import _UNIDAD_SIN_CONTENIDO
from precios_vtex import UMBRAL_MATCH, _UNIDADES_SIN_CONTENIDO, _cantidad_objetivo, _pedido, elegible, puntuar
from tests import medir_metrica_vivo as M
from tests import medir_pesables as P

RAIZ = Path(__file__).resolve().parents[1]
CAPTURA = RAIZ / "tests" / "capturas" / "canasta_default_2026-09-14.json"
CADENAS, VTEX = P.CADENAS, P.VTEX
CANASTA = main.CANASTA_DEFAULT
TOL = M.TOL                     # factor "= 1" si difiere menos de 2%

# PROXIMAS-TAREAS.md, Tarea 25, "Efecto en la canasta por defecto", columna "después".
# ✏️ 18/09: desde la Tarea 26 paso (b) son los números del código con la regla de envases (Tarea 26,
# "Paso (b) — implementado"); la columna de la Tarea 25 queda en PROXIMAS-TAREAS.md como historia.
# ✏️ 21/09, paso (c): §1–§6 ya no clasifican con la regla vieja — ver `cobertura` y ESPERADO_C. Decía:
# "§1–§4 siguen clasificando con la regla vieja (el texto "cobra" y los escenarios S1/S2 suponen
# precio × cantidad): reinterpretarlos sobre el código nuevo es el paso (c)."
ESPERADO_T25 = {
    "2026-09-14T00:26:24": {
        #              total_envase total_final Δ% sin promo Δ% final n_con_promedio
        # ✏️ Tarea 26 paso (b), 18/09 — las cinco filas. La fila cobra los envases enteros que cubren
        # lo pedido (S-escala) y el representante tiene que poder contestar lo pedido (S-filtro). Antes
        # de actualizarlas el instrumento salía con exit 1 en 21 campos: los 4 de plata y % en las 5
        # cadenas, y `n_con_promedio` de Coto. Ninguno es un cambio de la Tarea 25.
        # ✏️ Carrefour: era (143632.25, 107724.19, -7.6, -30.7, 17). Fideos 2 → 4 paquetes de 500 g,
        # gaseosa 3 → 1 botella de 3 L, carne picada 1 → 2 de 500 g, tomate "x kg" → lata de 400 g.
        "Carrefour":  (131282.25, 98461.69, -11.4, -33.6, 17),
        # ✏️ Día: era (115272.00, 86454.00, 8.7, -18.5, 16). Mismas filas que Carrefour.
        "Día":        (110345.00, 82758.75, 14.7, -14.0, 16),
        # ✏️ Vea: era (102676.00, 77007.00, -11.4, -33.6, 16). Fideos ×4, gaseosa 2,25 L ×2, carne
        # picada ×2, tomate "x kg" → lata.
        "Vea":        (93343.00, 70007.25, -17.1, -37.8, 16),
        # ✏️ Tarea 26 paso (a), 18/09: era (165942.60, 124456.95, -4.5, -28.4, 16). El fix de "1/2 Kg"
        # (leído como 2 kg) cambia el representante del azúcar: "Azúcar Rubio Azucel Orgánica 1/2 Kg"
        # $1.644,30 → "Azúcar Azucel 1kg" $1.249. No es un cambio de la Tarea 25.
        # ✏️ Tarea 26 paso (b): era (165152.00, 123864.00, -3.1, -27.3, 16). Fideos ×4, gaseosa 3 L ×1,
        # manteca (click de 6) 2 → 1 click, tomate "500 G" → lata de 400 g.
        "Chango Más": (151398.00, 113548.50, 2.1, -23.4, 16),
        # ✏️ Coto: era (124931.95, 93698.96, 33.8, 0.3, 14). Fideos ×4. Azúcar: 2 → 6 frascos de mermelada de
        # 390 g (producto equivocado, Tarea 22 — la regla lo agranda y queda visible). Gaseosa
        # "Cunnington 500cmq", sin métrica, → "Manaos 2.25l" ×2: entra al %, y `n_con_promedio` 14 → 15.
        "Coto":       (144152.95, 108114.71, 50.4, 12.8, 15),
    },
}
CAMPOS_T25 = ("total_envase", "total_final", "delta_pct_sin_promo", "delta_pct_final", "n_con_promedio")

# ✏️ 21/09, paso (c): desde (b), "2 unidad" contra un click de 6 es UN click: 1 × $3.654 = $3.654, +$2.436
# contra 2 × $609 (el precio de antes del fix de la Tarea 25). Era la tabla "Qué pasa hoy en producción" de
# la Tarea 26 —2 × $3.654 = $7.308, +$6.090—, documentada antes de (b): §5 salía "NO COINCIDE".
MANTECA_CHANGO = {"precio": 3654.0, "envases": 1.0, "subtotal": 3654.0, "precio_antes_fix": 609.0,
                  "delta": 2436.0}
# ✏️ 21/09, paso (c): desde (b) el representante del tomate en las 4 VTEX es una lata de 400 g cobrada 2
# veces (S-filtro; Tarea 26, "Paso (b) — implementado", punto 5). Antes era el tomate fresco "x kg".
TOMATE_B = {"Carrefour": 1579.0, "Día": 1535.0, "Vea": 1490.0, "Chango Más": 1179.0}

# Cajones del paso 1 (`clasificar`). ✏️ 21/09, paso (c): desde (b) son DATO —el envase contra UNA unidad
# pedida—, no error. Salieron S1 = A + D-mal y S2 = S1 + B-mal + C-mal —los escenarios de §3, que sacaban
# del total filas que (b) ya cobra bien— y BIEN = B-bien · C · D-bien.
CAJONES = ("A", "B-mal", "B-bien", "B-?", "C", "C-mal", "D-bien", "D-mal", "faltante")

# ✏️ 21/09, paso (c): la cobertura, qué compra lo que cobra el código contra lo pedido (`cobertura`).
COBERTURA = ("exacto", "exacto UM", "sobrante", "no mínimo", "no entero", "no cubre", "sin verif.",
             "sin elegible", "faltante")
EXACTO = ("exacto", "exacto UM")
MINIMOS = EXACTO + ("sobrante",)       # la plata compra lo pedido con los envases mínimos: el guard de (b)
NO_CONTESTA = ("no mínimo", "no entero", "no cubre", "sin verif.", "sin elegible")        # §6

# ✏️ 21/09, paso (c): la cobertura de esta captura con el código de (b) (backend 11c7988). Validación (8):
# si el código o la lectura cambian, sale con exit 1 en vez de medir otra cosa. Cero filas fuera de MINIMOS
# en las 5 cadenas. Las "sobrante": el aceite (3 L por 2 L) en las 5, la gaseosa de 2,25 L de Vea y Coto
# (4,5 L por 3 L), el azúcar de Coto (mermelada, Tarea 22) y la manteca de Chango Más (medialunas, Tarea 22).
ESPERADO_C = {
    "2026-09-14T00:26:24": {
        "Carrefour":  {"exacto": 18, "exacto UM": 1, "sobrante": 1},
        "Día":        {"exacto": 18, "exacto UM": 1, "sobrante": 1},
        "Vea":        {"exacto": 17, "sobrante": 2, "faltante": 1},
        "Chango Más": {"exacto": 16, "exacto UM": 2, "sobrante": 2},
        "Coto":       {"exacto": 14, "sobrante": 3, "faltante": 3},
    },
}


class InstrumentoInvalido(Exception):
    pass


# ─────────────────────────────────────────────────────────────────
#  LECTURA DEL PEDIDO Y DE LO QUE COTIZA EL PRECIO
# ─────────────────────────────────────────────────────────────────

def con_contenido(prod):
    """El pedido declara contenido (kg, litro). Misma lista que usa el matcher."""
    return (prod.get("unidad") or "").strip().lower() not in _UNIDADES_SIN_CONTENIDO


def unidad_pedida(prod):
    """(base de UNA unidad pedida, tipo), con la lectura del matcher (`_cantidad_objetivo`) ÷ cantidad."""
    if not main._extraer_cantidades_desc(f"{prod['cantidad']} {prod['unidad']}"):
        return None                        # `_cantidad_objetivo` caería al nombre del ítem
    obj = _cantidad_objetivo(prod, main._extraer_cantidades_desc)
    if not obj or not prod.get("cantidad"):
        return None
    return obj[0] / prod["cantidad"], obj[1]


def cotiza_um(of):
    """Misma precedencia que `precios_vtex._precio_por_100u`: unidad con contenido declarada por la fuente."""
    u = (of.unidad_medida or "").strip().lower()
    return bool(of.contenido and u and u not in _UNIDAD_SIN_CONTENIDO)


def click(of):
    """Cuántas unidades de `Price` cobra un `precio_min` de artículo: 1, salvo "un" con multiplicador."""
    return float(of.contenido) if of.contenido else 1.0


def txt_medida(val, tipo):
    if val is None:
        return "?"
    if tipo == "peso":
        return f"{val / 1000:g} kg" if val >= 1000 else f"{val:g} g"
    if tipo == "volumen":
        return f"{val / 1000:g} l" if val >= 1000 else f"{val:g} ml"
    if tipo == "longitud":
        return f"{val:g} m"
    return f"{val:g} u"


def clasificar(prod, f):
    """
    {"cajon", "factor", "marca", "cobra"} para una fila (o un candidato puesto como representante).
    ✏️ 21/09, paso (c): desde (b) el cajón es DATO —el envase contra UNA unidad pedida—, no lo que se cobra:
    eso lo describe `cobertura`. "cobra" sigue suponiendo `cantidad` y ya no se imprime; medir_regla_26b
    usa "cajon", "factor" y "marca".
      A       sin contenido + cotiza por unidad de medida. Sin factor: no hay artículo comparable.
      D-bien  sin contenido + artículo de un click = 1. D-mal: click ≠ 1, factor = multiplicador.
      C       con contenido + unidad de medida, factor 1 (control). C-mal: otro factor u otro tipo.
      B-bien  con contenido + artículo de exactamente una unidad pedida (1 kg para "2 kg").
      B-mal   con contenido + artículo de otro contenido: factor = contenido / unidad pedida.
      B-?     con contenido + artículo sin métrica o de otro tipo: el factor no se puede saber.
    Marcas (no reclasifican): "*" = D-bien cuyo artículo es un pack de N>1 para un pedido "unidad";
    " (pack)" = D-mal pedido en "pack", donde el click puede ser justo el pack.
    """
    of, pu = f["of"], f["pu"]
    cant = prod["cantidad"]
    unidad = (prod.get("unidad") or "").strip().lower()
    if not con_contenido(prod):
        if cotiza_um(of):
            cobra = (f"{cant:g} × {txt_medida(pu['cantidad_base'], pu['tipo'])} = "
                     f"{txt_medida(cant * pu['cantidad_base'], pu['tipo'])}") if pu else "?"
            return {"cajon": "A", "factor": None, "marca": "", "cobra": cobra}
        k = click(of)
        if M.cerca(k, 1.0, TOL):
            pack_n = (f["ref"].get("n") or 1) > 1 or bool(pu and pu["tipo"] == "count"
                                                          and pu["cantidad_base"] > 1)
            return {"cajon": "D-bien", "factor": 1.0, "marca": "*" if pack_n and unidad != "pack" else "",
                    "cobra": f"{cant:g} artículo(s)"}
        return {"cajon": "D-mal", "factor": k, "marca": " (pack)" if unidad == "pack" else "",
                "cobra": f"{cant:g} clicks × {k:g} u = {cant * k:g} u"}

    base, tipo = unidad_pedida(prod)
    um = cotiza_um(of)
    if not pu or pu["tipo"] != tipo:
        por = "sin métrica" if not pu else f"métrica en {pu['tipo']}"
        return {"cajon": "C-mal" if um else "B-?", "factor": None, "marca": "", "cobra": f"? ({por})"}
    factor = pu["cantidad_base"] / base
    ok = M.cerca(factor, 1.0, TOL)
    cajon = ("C" if ok else "C-mal") if um else ("B-bien" if ok else "B-mal")
    cobra = (f"{cant:g} × {txt_medida(pu['cantidad_base'], tipo)} = {txt_medida(cant * pu['cantidad_base'], tipo)}"
             f" (pedido {txt_medida(cant * base, tipo)})")
    return {"cajon": cajon, "factor": factor, "marca": "", "cobra": cobra}


def cobertura(prod, f, d):
    """
    ✏️ 21/09, paso (c). Qué compra lo que cobra el código, contra lo pedido. `f` es el representante
    (None = la cadena no lo tiene) y `d` la fila de la ficha de hoy (None si el producto no entra a
    ninguna ficha). {"cajon", "cobra", "env", "env_min", "compra", "pedido"}:
      exacto        artículo: los envases mínimos, enteros, y compran lo pedido (±2%).
      exacto UM     el precio es el de la unidad de medida: envases = pedido ÷ esa unidad.
      sobrante      artículo: los envases mínimos, pero compran más que lo pedido (3 L por 2 L).
      no mínimo     compra lo pedido con al menos un envase de más.
      no entero     un artículo cobrado por una fracción de envase.
      no cubre      compra menos que lo pedido, sin tolerancia hacia abajo (decisión 4).
      sin verif.    en el total sin métrica del tipo pedido: no se sabe qué compra.
      sin elegible  fuera del total: ningún candidato de la cadena contesta lo pedido (decisión 6).
      faltante      la cadena no tiene representante.
    `env_min` sale de lo pedido y del envase, no del código: es lo que el código tendría que cobrar.
    """
    if f is None:
        return {"cajon": "faltante", "cobra": "", "env": None, "env_min": None}
    if d is None or d.get("sin_elegible"):
        return {"cajon": "sin elegible", "cobra": "fuera del total y del %", "env": None, "env_min": None}
    of, pu, env = f["of"], f["pu"], d["envases"]
    um = cotiza_um(of)
    if env is None:
        return {"cajon": "sin verif.", "cobra": "en el total sin envases", "env": None, "env_min": None}
    if con_contenido(prod):
        up = unidad_pedida(prod)
        if not up or not pu or pu["tipo"] != up[1] or not pu.get("cantidad_base"):
            por = "sin métrica" if not pu else f"métrica en {pu['tipo']}"
            return {"cajon": "sin verif.", "cobra": f"{env:g} × ? ({por})", "env": env, "env_min": None}
        envase, pedido = pu["cantidad_base"], prod["cantidad"] * up[0]

        def txt(v):
            return txt_medida(v, up[1])
    elif um:
        return {"cajon": "sin verif.", "cobra": f"{env:g} × la unidad de medida, en un pedido sin contenido",
                "env": env, "env_min": None}
    else:
        envase, pedido = click(of), float(prod["cantidad"])

        def txt(v):
            return f"{v:g} u"
    compra = env * envase
    if um:
        env_min = pedido / envase
        minimo = M.cerca(env, env_min, 1e-6)            # el código redondea a 6 decimales
    else:
        env_min = float(math.ceil(pedido / envase - 1e-9))
        minimo = env == env_min
    if not um and env != math.floor(env):
        cajon = "no entero"
    elif compra < pedido * (1 - (1e-6 if um else 1e-9)):
        cajon = "no cubre"
    elif not minimo:
        cajon = "no mínimo"
    elif um:
        cajon = "exacto UM"
    else:
        cajon = "exacto" if M.cerca(compra, pedido, TOL) else "sobrante"
    return {"cajon": cajon, "cobra": f"{env:g} × {txt(envase)} = {txt(compra)} de {txt(pedido)}",
            "env": env, "env_min": env_min, "compra": compra, "pedido": pedido}


# ─────────────────────────────────────────────────────────────────
#  MATCHER Y PROXY
# ─────────────────────────────────────────────────────────────────

def score(prod, of, con_cantidad=True):
    """La misma llamada que `buscar_precios_online`; sin cantidad, para medir el bonus de tamaño."""
    return puntuar(prod, of, main.normalizar, main._extraer_cantidades_desc if con_cantidad else None)


def proxy_kw(prod, titulo):
    """Proxy MECÁNICO de mal matching: el título normalizado contiene alguna `palabras_clave` normalizada."""
    t = main.normalizar(titulo)
    return any(main.normalizar(k) in t for k in (prod.get("palabras_clave") or [prod["nombre"]]))


def candidatos(filas, c, q):
    """Lo que el matcher mira: disponibles y con precio."""
    return [f for f in filas[c].get(q, []) if f["of"].disponible and f["of"].precio > 0]


# ─────────────────────────────────────────────────────────────────
#  FICHAS
# ─────────────────────────────────────────────────────────────────

# ✏️ 21/09, paso (c): salió `correr` —las fichas sin las filas de un escenario— junto con S1/S2 (§3).

def fila_ficha(fi, nombre):
    return next((d for d in fi["detalle"] if d["producto"] == nombre), None)


# ─────────────────────────────────────────────────────────────────
#  VALIDACIÓN — el instrumento mide lo que dice medir
# ─────────────────────────────────────────────────────────────────

def validar(cap, filas, dia):
    errores = []

    # 1. Las 20 consultas de la canasta, en las 5 cadenas.
    for c in CADENAS:
        falta = [p["nombre"] for p in CANASTA if p["nombre"] not in (cap["datos"].get(c) or {})]
        if falta:
            errores.append(f"(1) {c}: la captura no trae {falta}")
    if errores:
        raise InstrumentoInvalido("\n  ".join(errores))

    precios, prom, fi_hoy = P.fichas(filas, CANASTA, "hoy", False, dia)

    # 2. La columna "después" de la Tarea 25, exacta.
    esperado = ESPERADO_T25.get(cap.get("__fecha"))
    if esperado:
        for c, vals in esperado.items():
            for campo, v in zip(CAMPOS_T25, vals):
                if fi_hoy[c][campo] != v:
                    errores.append(f"(2) {c} {campo} = {fi_hoy[c][campo]}, la Tarea 25 documenta {v}")
        estado_t25 = ("OK: reproduce exacto ESPERADO_T25 (5 campos × 5 cadenas; ✏️ desde el 18/09, los "
                      "números de la Tarea 26 paso b)")
    else:
        estado_t25 = f"NO APLICA: no hay números de referencia para la captura {cap.get('__fecha')}"

    # 3. Representantes en la captura, sin ambigüedad, y `puntuar` == match_score.
    reps = {}
    items = {p["nombre"]: p for p in CANASTA}
    for (c, q), d in precios.items():
        cand = [f for f in filas[c][q] if f["of"].disponible and f["of"].precio == d["precio_min"]
                and f["of"].ean == d["ean"] and f["pu"] == d["precio_por_100u"]]
        if not cand:
            errores.append(f"(3) {c}/{q}: representante ${d['precio_min']} ean={d['ean']} no está en la captura")
            continue
        if len({(f["of"].producto, f["of"].unidad_medida, f["of"].contenido) for f in cand}) > 1:
            errores.append(f"(3) {c}/{q}: representante ambiguo: {[f['of'].producto for f in cand]}")
            continue
        reps[(c, q)] = cand[0]
        s = score(items[q], cand[0]["of"])
        if s != d["match_score"]:
            errores.append(f"(3) {c}/{q}: puntuar = {s}, match_score de producción = {d['match_score']}")

    # 4. "Cotiza por unidad de medida" es lo mismo que la métrica que sale de la API.
    for (c, q), f in reps.items():
        pu = f["pu"]
        if pu and cotiza_um(f["of"]) != (pu.get("fuente_contenido") == "api"):
            errores.append(f"(4) {c}/{q}: cotiza_um={cotiza_um(f['of'])} pero fuente_contenido="
                           f"{pu.get('fuente_contenido')}")

    # 7. La unidad pedida se lee en peso o volumen para todos los ítems con contenido.
    # ✏️ 21/09, paso (c): y es la MISMA que usa el código para escalar (`precios_vtex._pedido`): la cobertura
    # compara contra lo pedido, así que si el instrumento lo leyera distinto mediría otra cosa.
    for p in CANASTA:
        if con_contenido(p):
            up = unidad_pedida(p)
            if not up or up[1] not in ("peso", "volumen"):
                errores.append(f"(7) {p['nombre']} ({p['cantidad']} {p['unidad']}): unidad pedida = {up}")
                continue
            ped = _pedido(p, main._extraer_cantidades_desc)
            if not ped or ped[1] != up[1] or not M.cerca(p["cantidad"] * up[0], ped[0], 1e-9):
                errores.append(f"(7) {p['nombre']}: el instrumento lee {p['cantidad'] * up[0]:g} {up[1]}, "
                               f"el código {ped}")
    if errores:
        raise InstrumentoInvalido("\n  ".join(errores[:20]))

    clases, cob = {}, {}
    for c in CADENAS:
        for p in CANASTA:
            f = reps.get((c, p["nombre"]))
            clases[(c, p["nombre"])] = clasificar(p, f) if f else {"cajon": "faltante", "factor": None,
                                                                   "marca": "", "cobra": ""}
            cob[(c, p["nombre"])] = cobertura(p, f, fila_ficha(fi_hoy[c], p["nombre"]))

    # 5. Cada subtotal es precio_unit × envases, y la suma de las filas es total_envase.
    # ✏️ Tarea 26 paso (b), 18/09: era "precio_unit × cantidad". Desde (b) la fila cobra `envases`
    # (los envases enteros que cubren lo pedido) y `cantidad` queda como lo pedido. Antes de
    # cambiarlo salía con exit 1 en las 15 filas donde envases ≠ cantidad.
    for c in CADENAS:
        fi = fi_hoy[c]
        suma = 0.0
        for d in fi["detalle"]:
            if d["precio_unit"] is not None:
                if d["subtotal"] != d["precio_unit"] * d["envases"]:
                    errores.append(f"(5) {c}/{d['producto']}: subtotal {d['subtotal']} ≠ precio × envases")
                suma += d["subtotal"]
        if abs(suma - fi["total_envase"]) > 0.005:
            errores.append(f"(5) {c}: Σ subtotales = {suma:.2f}, total_envase = {fi['total_envase']}")
        # ✏️ 21/09, paso (c): con la cobertura, que separa "sin elegible" (fuera de `n_disponibles`) de
        # "faltante". Antes con los cajones del paso 1, que no distinguían `sin_elegible`.
        n = Counter(cob[(c, p["nombre"])]["cajon"] for p in CANASTA)
        fuera = n["faltante"] + n["sin elegible"]
        if sum(n.values()) != len(CANASTA) or fuera != len(CANASTA) - fi["n_disponibles"]:
            errores.append(f"(5) {c}: cobertura {dict(n)} no cierra con {fi['n_disponibles']} disponibles")

    # 6. ✏️ 21/09, paso (c): salió. Validaba que el camino de escenarios sin quitar nada diera "hoy"; los
    # escenarios S1/S2 salieron porque sacaban del total filas que (b) ya cobra bien.

    # 8. ✏️ 21/09, paso (c): la cobertura de esta captura es la documentada (ESPERADO_C). No es vacía: con la
    # escala de (b) revertida en memoria y la (2) apagada, la (8) sola sale con error en las 5 cadenas: las 15
    # filas donde envases ≠ cantidad salen de MINIMOS (9 "no cubre", 6 "no mínimo"). Tarea 26, "Paso (c)".
    esperado_c = ESPERADO_C.get(cap.get("__fecha"))
    if esperado_c:
        for c, doc in esperado_c.items():
            n = {k: v for k, v in Counter(cob[(c, p["nombre"])]["cajon"] for p in CANASTA).items() if v}
            if n != doc:
                errores.append(f"(8) {c}: cobertura {n}, documentada {doc}")
        estado_c = ("OK: la cobertura por cadena es la documentada en el paso (c) — cero filas fuera de los "
                    "envases mínimos")
    else:
        estado_c = f"NO APLICA: no hay cobertura de referencia para la captura {cap.get('__fecha')}"
    if errores:
        raise InstrumentoInvalido("\n  ".join(errores[:20]))

    return {"precios": precios, "prom": prom, "hoy": fi_hoy, "reps": reps, "clases": clases, "cob": cob,
            "estado_t25": estado_t25, "estado_c": estado_c}


# ─────────────────────────────────────────────────────────────────
#  INFORME
# ─────────────────────────────────────────────────────────────────

def f_num(v, dec=2):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:,.{dec}f}"
    return str(v)


def txt_cotiza(of):
    if cotiza_um(of):
        return f"UM {of.contenido:g} {of.unidad_medida}"
    return f"art click={click(of):g}"


def informe_filas(ctx):
    # ✏️ 21/09, paso (c): "cobra" sale de `envases` —lo que cobra el código— y dice qué compra contra lo
    # pedido. Antes multiplicaba por `cantidad`: en las 15 filas donde envases ≠ cantidad contradecía al
    # subtotal de la misma línea ("$1.300 × 4 = $5.200 · cobra 2 × 500 g = 1 kg"). El cajón del paso 1 y
    # su factor quedan como dato.
    P.barra("§1 — FILA POR FILA: qué pidió el usuario y qué compra lo que cobra el código",
            "cotiza: UM = unidad de medida (precio del kilo) · art = artículo (click = unidades de Price por "
            "precio_min).",
            "cobra: envases × lo que compra un precio_min = lo que compra la fila, de lo pedido · * = pack de N "
            "para un pedido 'unidad'.",
            "paso 1: el cajón y el factor del paso 1 (el envase contra UNA unidad pedida), como dato.",
            "métrica vs título: `comparar` de medir_metrica_vivo (lector de títulos independiente).",
            "estado y Δ% de la ficha de HOY.")
    for p in CANASTA:
        print(f"\n  ■ {p['nombre']} — {p['cantidad']:g} {p['unidad']}"
              f"{'' if con_contenido(p) else '   (pedido SIN contenido)'}")
        for c in CADENAS:
            k, cb = ctx["clases"][(c, p["nombre"])], ctx["cob"][(c, p["nombre"])]
            f = ctx["reps"].get((c, p["nombre"]))
            if not f:
                print(f"      {c:<11} faltante")
                continue
            d = fila_ficha(ctx["hoy"][c], p["nombre"])
            fac = "—" if k["factor"] is None else f"{k['factor']:.3g}"
            if cb["cajon"] == "sin elegible":
                print(f"      {c:<11} {'sin elegible':<15} {txt_cotiza(f['of']):<15} ${f['of'].precio:>10,.2f}  "
                      f"{cb['cobra']:<44} · paso 1: {k['cajon'] + k['marca']:<9} factor {fac}")
            else:
                delta = "" if d["delta_pct"] is None else f"{d['delta_pct']:+.1f}%"
                print(f"      {c:<11} {cb['cajon'] + k['marca']:<15} {txt_cotiza(f['of']):<15} "
                      f"${f['of'].precio:>10,.2f} × {d['envases']:g} = ${d['subtotal']:>10,.2f}  "
                      f"cobra {cb['cobra']:<32} · paso 1: {k['cajon'] + k['marca']:<9} factor {fac:<5} "
                      f"· {f['clase']:<16} · {d['estado']} {delta}")
            print(f"      {'':11} {f['of'].producto[:80]}")


def conteo(ctx):
    # ✏️ 21/09, paso (c): cuenta por cobertura. Antes contaba los cajones del paso 1, que marcaban B-mal y
    # D-mal 20 filas que (b) ya cobra con los envases mínimos; quedan abajo, como dato.
    P.barra("§2 — COBERTURA POR CADENA, sobre los 20 ítems de CANASTA_DEFAULT",
            "exacto = los envases mínimos compran lo pedido (±2%) · exacto UM = pedido ÷ la unidad de medida · "
            "sobrante = los mínimos compran de más.",
            "no mínimo / no entero / no cubre = la regla de (b) mal aplicada · sin verif. = en el total sin "
            "métrica del tipo pedido.",
            "sin elegible = fuera del total por la decisión 6 (la ficha lo muestra como faltante) · * = pack de N "
            "para un pedido 'unidad' (marca, no reclasifica: decisión 5).")
    print(f"    {'':<11}" + "".join(f"{k:>13}" for k in COBERTURA) + f"{'*':>4}{'total':>7}")
    for c in CADENAS:
        n = Counter(ctx["cob"][(c, p["nombre"])]["cajon"] for p in CANASTA)
        est = sum(1 for p in CANASTA if ctx["clases"][(c, p["nombre"])]["marca"] == "*")
        print(f"    {c:<11}" + "".join(f"{n[k]:>13}" for k in COBERTURA) + f"{est:>4}{sum(n.values()):>7}")
    print("\n    Filas que no son exactas:")
    hay = False
    for caj in (k for k in COBERTURA if k not in EXACTO + ("faltante",)):
        for c in CADENAS:
            filas = [(p, ctx["cob"][(c, p["nombre"])]) for p in CANASTA
                     if ctx["cob"][(c, p["nombre"])]["cajon"] == caj]
            if filas:
                hay = True
                print(f"      {caj:<12} {c:<11} " + " · ".join(f"{p['nombre']} ({k['cobra']})" for p, k in filas))
    if not hay:
        print("      ninguna")
    print("\n    Paso 1, como dato: el envase contra UNA unidad pedida (B-mal, C-mal, D-mal: factor ≠ 1; A, B-?: "
          "sin factor):")
    for caj in ("B-mal", "C-mal", "D-mal", "A", "B-?"):
        for c in CADENAS:
            filas = [(p, ctx["clases"][(c, p["nombre"])]) for p in CANASTA
                     if ctx["clases"][(c, p["nombre"])]["cajon"] == caj]
            if filas:
                print(f"      {caj:<6} {c:<11} " + " · ".join(
                    f"{p['nombre']} " + ("?" if k["factor"] is None else f"{k['factor']:.3g}") for p, k in filas))


def pesos(ctx):
    # ✏️ 21/09, paso (c): $ y % del total por cobertura. Antes simulaba las fichas sin las filas de los
    # escenarios S1 = A + D-mal y S2 = S1 + B-mal + C-mal: S2 sacaba entre 14% y 40% de cada total —filas que
    # (b) ya cobra bien— y "daba vuelta" el orden de las cadenas. Salieron con (c).
    P.barra("§3 — CUÁNTO PESA CADA COBERTURA: $ de subtotal por cadena y % del total_envase",
            "Solo las columnas con alguna fila. faltante y sin elegible no suman: están fuera del total.",
            "sobrante $ = lo que las filas 'sobrante' pagan por contenido que no se pidió, proporcional al "
            "contenido (estimado, no un precio).")
    cajs = [k for k in COBERTURA if k not in ("faltante", "sin elegible")
            and (k in MINIMOS or any(v["cajon"] == k for v in ctx["cob"].values()))]
    print(f"\n    {'':<11}{'total_envase':>14}" + "".join(f"{k:>20}" for k in cajs) + f"{'sobrante $':>20}")
    ctx["pesos"] = {}
    for c in CADENAS:
        te = ctx["hoy"][c]["total_envase"]
        suma, sob = Counter(), 0.0
        for p in CANASTA:
            k = ctx["cob"][(c, p["nombre"])]
            if k["cajon"] in cajs:
                sub = fila_ficha(ctx["hoy"][c], p["nombre"])["subtotal"]
                suma[k["cajon"]] += sub
                if k["cajon"] == "sobrante":
                    sob += sub * (k["compra"] - k["pedido"]) / k["compra"]
        ctx["pesos"][c] = {"sobrante $": sob, **suma}

        def pct(v):
            return v / te * 100 if te else 0.0
        print(f"    {c:<11}{te:>14,.2f}" + "".join(f"{suma[k]:>12,.2f} {pct(suma[k]):>6.1f}%" for k in cajs)
              + f"{sob:>12,.2f} {pct(sob):>6.1f}%")


def piso_brecha(ctx):
    # ✏️ 21/09, paso (c): la métrica es la del guard de (b) (`test_envases.py`): filas cuya plata compra lo
    # pedido con los envases mínimos. Antes: "filas ok S1/S2" y "$ mal S1/S2" sobre los cajones del paso 1,
    # que contaban como mal lo que (b) escala: "filas ok S2" daba 76,5–80% donde el guard da 100%.
    P.barra("§4 — PISO POR CADENA Y BRECHA ENTRE LA MEJOR Y LA PEOR (CLAUDE.md: un promedio global esconde esto)",
            "n = filas en el total · mínimos = exacto + exacto UM + sobrante (lo que fija el guard de b) · exactas "
            "= exacto + exacto UM.",
            "sobrante $ = el estimado de §3 / total_envase. Faltantes y sin elegible al lado: con menos datos una "
            "cadena no puede parecer la mejor.")
    metricas = {}
    for c in CADENAS:
        cl = [ctx["cob"][(c, p["nombre"])]["cajon"] for p in CANASTA]
        n = sum(1 for k in cl if k not in ("faltante", "sin elegible"))
        te = ctx["hoy"][c]["total_envase"]
        metricas[c] = {
            "n": n, "faltantes": cl.count("faltante"), "sin elegible": cl.count("sin elegible"),
            "mínimos %": sum(1 for k in cl if k in MINIMOS) / n * 100 if n else 0.0,
            "exactas %": sum(1 for k in cl if k in EXACTO) / n * 100 if n else 0.0,
            "sobrante $ %": ctx["pesos"][c]["sobrante $"] / te * 100 if te else 0.0,
        }
    claves = ("mínimos %", "exactas %", "sobrante $ %")
    print(f"    {'':<11}{'n':>4}{'faltantes':>11}{'sin eleg.':>11}" + "".join(f"{k:>15}" for k in claves))
    for c in CADENAS:
        m = metricas[c]
        print(f"    {c:<11}{m['n']:>4}{m['faltantes']:>11}{m['sin elegible']:>11}"
              + "".join(f"{m[k]:>14.1f}%" for k in claves))
    print()
    for k in claves:
        vals = {c: metricas[c][k] for c in CADENAS}
        if len({round(v, 6) for v in vals.values()}) == 1:
            print(f"    {k:<13} las {len(CADENAS)} cadenas en {vals[CADENAS[0]]:.1f}% · brecha = 0.0 puntos")
            continue
        malo_alto = k == "sobrante $ %"
        peor = (max if malo_alto else min)(vals, key=vals.get)
        mejor = (min if malo_alto else max)(vals, key=vals.get)
        print(f"    {k:<13} piso (peor) = {peor} {vals[peor]:.1f}% · mejor = {mejor} {vals[mejor]:.1f}% · "
              f"brecha = {abs(vals[mejor] - vals[peor]):.1f} puntos")


def nombradas(ctx):
    # ✏️ 21/09, paso (c): contra lo documentado DESPUÉS de (b). La manteca salía "NO COINCIDE" porque
    # MANTECA_CHANGO era de antes de (b) (2 clicks); el tomate se describía como "2 × $/kg = 2 kg de tomate
    # fresco, no 2 latas", que era antes del S-filtro.
    P.barra("§5 — LAS DOS FILAS QUE NOMBRA LA TAREA 26, contra lo documentado después de (b)")
    items = {p["nombre"]: p for p in CANASTA}
    c, q = "Chango Más", "Manteca"
    f, cb, d = ctx["reps"].get((c, q)), ctx["cob"][(c, q)], fila_ficha(ctx["hoy"][c], q)
    if f and d and not d.get("sin_elegible"):
        m = MANTECA_CHANGO
        prev = m["precio_antes_fix"] * d["cantidad"]
        ok = (f["of"].precio == m["precio"] and d["envases"] == m["envases"] and d["subtotal"] == m["subtotal"]
              and d["subtotal"] - prev == m["delta"])
        print(f"    {c} / {q} ({d['cantidad']:g} {items[q]['unidad']}): {f['of'].producto!r} {txt_cotiza(f['of'])}")
        print(f"      {d['envases']:g} × ${f['of'].precio:,.2f} = ${d['subtotal']:,.2f} · cobra {cb['cobra']} "
              f"({cb['cajon']}) · antes del fix de la Tarea 25 {d['cantidad']:g} × ${m['precio_antes_fix']:,.0f} = "
              f"${prev:,.2f} → +${d['subtotal'] - prev:,.2f} · estado {d['estado']} · "
              f"{'COINCIDE' if ok else 'NO COINCIDE'} con lo documentado (1 click × $3.654 = $3.654, +$2.436; "
              f"medialunas, no manteca: Tarea 22)")
    else:
        print(f"    {c} / {q}: sin representante en el total en esta captura ({cb['cajon']})")
    q = "Tomate perita lata"
    print(f"\n    {q} ({items[q]['cantidad']:g} {items[q]['unidad']}), las 4 VTEX — documentado desde (b): una lata "
          f"de 400 g cobrada 2 veces (S-filtro). Antes, tomate fresco 'x kg': 2 × $/kg = 2 kg de tomate.")
    for c in VTEX:
        f, cb, d = ctx["reps"].get((c, q)), ctx["cob"][(c, q)], fila_ficha(ctx["hoy"][c], q)
        if not f or not d or d.get("sin_elegible"):
            print(f"      {c:<11} {cb['cajon']}")
            continue
        ok = f["of"].precio == TOMATE_B.get(c) and not cotiza_um(f["of"]) and d["envases"] == 2
        print(f"      {c:<11} {f['of'].producto[:34]!r:<37} {txt_cotiza(f['of']):<13} {d['envases']:g} × "
              f"${f['of'].precio:,.2f} = ${d['subtotal']:,.2f} · cobra {cb['cobra']} ({cb['cajon']}) · estado "
              f"{d['estado']} Δ {d['delta_pct']}% · {'COINCIDE' if ok else 'NO COINCIDE'} con (b)")


def separacion(ctx, filas):
    # ✏️ 21/09, paso (c): solo para las filas que la regla de (b) no contesta. Antes separaba 26/22 sobre los
    # cajones del paso 1 (A, B-mal, C-mal, D-mal, B-?), que (b) ya escala: "22" era "hay un candidato de
    # factor 1" y "26", "no lo hay". Con envases enteros el factor ya no decide.
    P.barra("§6 — SEPARACIÓN 26 / 22, solo para las filas que la regla de (b) no contesta",
            "no mínimo / no entero / no cubre: el código cobra otra cosa que los envases mínimos → bug de la regla, "
            "no es 22 ni 26.",
            f"sin verif. / sin elegible: ¿algún otro candidato pasa el filtro de (b) (`elegible`)? ≥ umbral "
            f"{UMBRAL_MATCH} → incoherente (el código lo tendría que haber elegido)",
            "  · solo < umbral → 22 (el matcher lo deja afuera) · ninguno → 26, paso (d) (la cadena no lo tiene en "
            "una forma que conteste lo pedido).",
            "Una fila que cubre lo pedido puede ser de OTRO producto (mermelada por azúcar): eso es la Tarea 22 y no "
            "se mide acá.")
    items = {p["nombre"]: p for p in CANASTA}
    ver = Counter()
    for p in CANASTA:
        for c in CADENAS:
            k = ctx["cob"][(c, p["nombre"])]
            if k["cajon"] not in NO_CONTESTA:
                continue
            prod, rep = items[p["nombre"]], ctx["reps"][(c, p["nombre"])]
            s_rep = score(prod, rep["of"])
            if k["cajon"] in ("no mínimo", "no entero", "no cubre"):
                veredicto = "bug de la regla"
                print(f"\n    [{p['nombre']}] {c} — {k['cajon']}: cobra {k['cobra']}, los mínimos son "
                      f"{f_num(k['env_min'])} → {veredicto}")
                print(f"      representante: {rep['of'].producto[:70]!r} s={s_rep} {txt_cotiza(rep['of'])}")
            else:
                cands = [f for f in candidatos(filas, c, p["nombre"]) if f is not rep]
                eleg = sorted(((score(prod, f["of"]), -f["of"].precio, f) for f in cands
                               if elegible(prod, f["of"], f["pu"], main._extraer_cantidades_desc)),
                              key=lambda x: (x[0], x[1]), reverse=True)
                arriba = [x for x in eleg if x[0] >= UMBRAL_MATCH]
                veredicto = "incoherente" if arriba else "22" if eleg else "26 (d)"
                print(f"\n    [{p['nombre']}] {c} — {k['cajon']} → {veredicto}")
                print(f"      representante: {rep['of'].producto[:70]!r} s={s_rep} {txt_cotiza(rep['of'])} "
                      f"kw={'sí' if proxy_kw(prod, rep['of'].producto) else 'no'}")
                print(f"      otros candidatos disponibles: {len(cands)} · elegibles: {len(eleg)} "
                      f"(≥ umbral: {len(arriba)})")
                if eleg:
                    s, _neg, f = eleg[0]
                    print(f"      mejor elegible: {f['of'].producto[:60]!r} ${f['of'].precio:,.2f} "
                          f"{txt_cotiza(f['of'])} s={s} {'≥' if s >= UMBRAL_MATCH else '<'} umbral · Δs vs "
                          f"representante {s - s_rep:+.3f} · kw={'sí' if proxy_kw(prod, f['of'].producto) else 'no'}")
            ver[(c, k["cajon"], veredicto)] += 1
    if not ver:
        print(f"\n    Ninguna: en las {len(CADENAS)} cadenas, toda fila con representante cubre lo pedido con los "
              f"envases mínimos.")
    print("\n  RESUMEN veredicto por cadena y cobertura:")
    for c in CADENAS:
        partes = [f"{caj} → {v}: {n}" for (cc, caj, v), n in sorted(ver.items()) if cc == c]
        print(f"    {c:<11} " + (" · ".join(partes) or "—"))


def main_cli():
    ap = argparse.ArgumentParser()
    ap.add_argument("--desde", default=str(CAPTURA), help="captura guardada (sin red)")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    logging.disable(logging.INFO)

    with open(args.desde, encoding="utf-8") as fh:
        cap = json.load(fh)
    dia = datetime.fromisoformat(cap["__fecha"]).weekday()
    filas = P.parsear(cap["datos"])
    try:
        ctx = validar(cap, filas, dia)
    except InstrumentoInvalido as e:
        print(f"ERROR (InstrumentoInvalido):\n  {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Captura: {cap['__fecha']} · {cap.get('__origen')} · día {dia} (0=lunes) · PROMOS_DEFAULT · sin Teasers")
    print(f"Canasta: CANASTA_DEFAULT, {len(CANASTA)} ítems (la captura trae {len(cap.get('__consultas') or [])} "
          f"consultas; las demás no entran)")
    print("Instrumento validado:")
    print(f"  (1) las {len(CANASTA)} consultas están en las 5 cadenas")
    print(f"  (2) {ctx['estado_t25']}")
    print(f"  (3) {len(ctx['reps'])} representantes encontrados sin ambigüedad; puntuar == match_score")
    print("  (4) cotiza por unidad de medida ⟺ métrica con fuente_contenido 'api'")
    print("  (5) subtotal = precio × envases en cada fila; Σ = total_envase; la cobertura suma 20 y cierra "
          "con n_disponibles")
    print("  (6) ✏️ salió en el paso (c), con los escenarios S1/S2 que validaba")
    print("  (7) unidad pedida en peso/volumen para los ítems en kg/litro, la misma que usa el código para escalar")
    print(f"  (8) {ctx['estado_c']}")

    informe_filas(ctx)
    conteo(ctx)
    pesos(ctx)
    piso_brecha(ctx)
    nombradas(ctx)
    separacion(ctx, filas)


if __name__ == "__main__":
    main_cli()
