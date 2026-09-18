"""
Tarea 26, paso 1 (17/09/2026) — La unidad que pedís contra la unidad que cotiza el precio.

Instrumento de MEDICIÓN, 100% SIN RED, sobre una captura guardada. No es test ni guard: fuera del CI.

La fila de una ficha cobra `subtotal = precio_min × cantidad` (main.py:2029) sin mirar
`prod["unidad"]`. La pregunta: ¿qué compra UNA unidad de `precio_min`, contra qué pidió el usuario?
  PEDIDO        = (cantidad, unidad) del ítem de la canasta.
  LO QUE COTIZA = (tipo, cantidad_base) de la métrica del representante, o sea lo que compra un
                  `precio_min`: con unidad de medida (fix de la Tarea 25) es el kilo; con un artículo
                  es el envase que dice el título.
  FACTOR        = cuántas veces lo pedido cobra el subtotal. 1,0 = la fila está bien.

Cajones (ver `clasificar`): A · B-mal · B-bien · B-? · C · C-mal · D-bien · D-mal · faltante.
Separación de la Tarea 22 con la MISMA captura: entre los candidatos de esa cadena para esa consulta,
¿hay alguno conmensurable con factor 1? ¿Qué puntaje le dio `puntuar()` contra el que ganó?

No reimplementa la métrica ni el matcher: reusa `medir_pesables` (parsear, fichas) y
`medir_metrica_vivo` (lector de títulos independiente), y llama al código de producción para el resto.

Salida: 0 medición completa · 1 el instrumento no reproduce lo que dice medir.

Desde sepa_backend/:
    venv\\Scripts\\python.exe -m tests.medir_unidad_pedida --desde tests/capturas/canasta_default_2026-09-14.json
"""
import argparse
import json
import logging
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import main
from fuentes import _UNIDAD_SIN_CONTENIDO
from precios_vtex import UMBRAL_MATCH, _UNIDADES_SIN_CONTENIDO, _cantidad_objetivo, puntuar
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
# §1–§4 siguen clasificando con la regla vieja (el texto "cobra" y los escenarios S1/S2 suponen
# precio × cantidad): reinterpretarlos sobre el código nuevo es el paso (c).
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

# Tarea 26, tabla de "Qué pasa hoy en producción": 2 × $3.654 = $7.308, +$6.090 contra 2 × $609.
MANTECA_CHANGO = {"precio": 3654.0, "subtotal": 7308.0, "precio_antes_fix": 609.0, "delta": 6090.0}

CAJONES = ("A", "B-mal", "B-bien", "B-?", "C", "C-mal", "D-bien", "D-mal", "faltante")
BIEN = ("B-bien", "C", "D-bien")
S1 = ("A", "D-mal")                        # la Tarea 26 tal como está escrita (con la manteca)
S2 = ("A", "D-mal", "B-mal", "C-mal")      # más la misma regla en el otro sentido


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


def factor_titulo(prod, f):
    """El factor según el lector de títulos independiente. Solo informativo: nunca reemplaza a la métrica."""
    ref = f["ref"]
    up = unidad_pedida(prod) if con_contenido(prod) else None
    if not up or ref.get("tipo") != up[1] or not ref.get("base"):
        return None
    return ref["base"] / up[0]


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
#  FICHAS: HOY Y ESCENARIOS
# ─────────────────────────────────────────────────────────────────

def correr(precios, dia, quitar=frozenset(), mediana_fija=True):
    """
    El código real (`_analizar` → `_promedios_por_producto` → `_fichas`) sin las filas `quitar`.
    Una fila quitada queda `faltante`: fuera del total y del %.
      mediana_fija=True   la fila sigue votando en la mediana de su producto.
      mediana_fija=False  también sale de la mediana.
    """
    pf = {k: v for k, v in precios.items() if k not in quitar}
    prom = main._promedios_por_producto(CANASTA, precios if mediana_fija else pf)
    res = main._analizar(CANASTA, pf, main.PROMOS_DEFAULT, dia, None)
    return {fi["cadena"]: fi for fi in main._fichas(CANASTA, pf, res, prom, list(CADENAS))}


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
    for p in CANASTA:
        if con_contenido(p):
            up = unidad_pedida(p)
            if not up or up[1] not in ("peso", "volumen"):
                errores.append(f"(7) {p['nombre']} ({p['cantidad']} {p['unidad']}): unidad pedida = {up}")
    if errores:
        raise InstrumentoInvalido("\n  ".join(errores[:20]))

    clases = {}
    for c in CADENAS:
        for p in CANASTA:
            f = reps.get((c, p["nombre"]))
            clases[(c, p["nombre"])] = clasificar(p, f) if f else {"cajon": "faltante", "factor": None,
                                                                   "marca": "", "cobra": ""}

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
        n = Counter(clases[(c, p["nombre"])]["cajon"] for p in CANASTA)
        if sum(n.values()) != len(CANASTA) or n["faltante"] != len(CANASTA) - fi["n_disponibles"]:
            errores.append(f"(5) {c}: cajones {dict(n)} no cierran con {fi['n_disponibles']} disponibles")

    # 6. El camino de escenarios, sin quitar nada, es "hoy".
    for fija in (True, False):
        if correr(precios, dia, frozenset(), fija) != fi_hoy:
            errores.append(f"(6) correr(quitar=∅, mediana_fija={fija}) ≠ hoy")
    if errores:
        raise InstrumentoInvalido("\n  ".join(errores[:20]))

    return {"precios": precios, "prom": prom, "hoy": fi_hoy, "reps": reps, "clases": clases,
            "estado_t25": estado_t25}


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
    P.barra("§1 — FILA POR FILA: qué pidió el usuario y qué compra un precio_min del representante",
            "cotiza: UM = unidad de medida (precio del kilo) · art = artículo (click = unidades de Price por "
            "precio_min).",
            "métrica vs título: `comparar` de medir_metrica_vivo (lector de títulos independiente).",
            "estado y Δ% de la ficha de HOY.")
    for p in CANASTA:
        print(f"\n  ■ {p['nombre']} — {p['cantidad']:g} {p['unidad']}"
              f"{'' if con_contenido(p) else '   (pedido SIN contenido)'}")
        for c in CADENAS:
            k = ctx["clases"][(c, p["nombre"])]
            f = ctx["reps"].get((c, p["nombre"]))
            if not f:
                print(f"      {c:<11} faltante")
                continue
            d = fila_ficha(ctx["hoy"][c], p["nombre"])
            fac = "—" if k["factor"] is None else f"{k['factor']:.3g}"
            delta = "" if d["delta_pct"] is None else f"{d['delta_pct']:+.1f}%"
            print(f"      {c:<11} {k['cajon'] + k['marca']:<13} factor {fac:<6} {txt_cotiza(f['of']):<15} "
                  f"${f['of'].precio:>10,.2f} × {d['envases']:g} = ${d['subtotal']:>10,.2f}  cobra {k['cobra']:<32} "
                  f"· {f['clase']:<16} · {d['estado']} {delta}")
            print(f"      {'':11} {f['of'].producto[:80]}")


def conteo(ctx):
    P.barra("§2 — CONTEO POR CADENA Y CAJÓN, sobre los 20 ítems de CANASTA_DEFAULT",
            "B-bien = artículo de exactamente una unidad pedida · B-? = sin factor posible (no se inventa).",
            "D-bien* = click 1, pero el título dice pack de N para un pedido 'unidad' (marca, no reclasifica).")
    print(f"    {'':<11}" + "".join(f"{k:>9}" for k in CAJONES) + f"{'D-bien*':>9}{'total':>7}")
    for c in CADENAS:
        n = Counter(ctx["clases"][(c, p["nombre"])]["cajon"] for p in CANASTA)
        est = sum(1 for p in CANASTA if ctx["clases"][(c, p["nombre"])]["marca"] == "*")
        print(f"    {c:<11}" + "".join(f"{n[k]:>9}" for k in CAJONES) + f"{est:>9}{sum(n.values()):>7}")
    print("\n    Factores de las filas mal escaladas (A no tiene factor):")
    for caj in ("B-mal", "C-mal", "D-mal", "B-?"):
        for c in CADENAS:
            filas = [(p, ctx["clases"][(c, p["nombre"])]) for p in CANASTA
                     if ctx["clases"][(c, p["nombre"])]["cajon"] == caj]
            if filas:
                print(f"      {caj:<6} {c:<11} " + " · ".join(
                    f"{p['nombre']} " + ("?" if k["factor"] is None else f"{k['factor']:.3g}")
                    for p, k in filas))


def quitar(ctx, cajones):
    return frozenset(k for k, v in ctx["clases"].items() if v["cajon"] in cajones)


def pesos(ctx, dia):
    q1, q2 = quitar(ctx, S1), quitar(ctx, S2)
    esc = [("hoy", frozenset(), True), ("S1 fija", q1, True), ("S1 sin", q1, False),
           ("S2 fija", q2, True), ("S2 sin", q2, False)]
    res = {n: correr(ctx["precios"], dia, q, fija) for n, q, fija in esc}
    ctx["esc"] = res

    P.barra("§3 — CUÁNTO PESAN: $ de subtotal de las filas mal escaladas y fichas sin ellas",
            "S1 = A + D-mal (la Tarea 26 como está escrita) · S2 = S1 + B-mal + C-mal (la regla en los dos sentidos).",
            "fija = la fila sale del total y del % pero sigue votando en la mediana · sin = sale también de la mediana.",
            "B-? no se quita en ningún escenario: su $ se muestra aparte.")
    print(f"\n    {'$ de subtotal (hoy)':<22}{'total_envase':>14}{'S1 $':>12}{'S1 %':>8}{'S2 $':>12}{'S2 %':>8}"
          f"{'B-? $':>11}{'B-? %':>8}")
    for c in CADENAS:
        te = ctx["hoy"][c]["total_envase"]
        suma = {}
        for nombre, cajs in (("S1", S1), ("S2", S2), ("B?", ("B-?",))):
            suma[nombre] = sum(fila_ficha(ctx["hoy"][c], p["nombre"])["subtotal"] for p in CANASTA
                               if ctx["clases"][(c, p["nombre"])]["cajon"] in cajs)
        print(f"    {c:<22}{te:>14,.2f}{suma['S1']:>12,.2f}{suma['S1'] / te * 100:>7.1f}%{suma['S2']:>12,.2f}"
              f"{suma['S2'] / te * 100:>7.1f}%{suma['B?']:>11,.2f}{suma['B?'] / te * 100:>7.1f}%")
        ctx.setdefault("pesos", {})[c] = suma

    nombres = list(res)
    for campo in ("total_envase", "total_final", "delta_pct_sin_promo", "delta_pct_final", "n_con_promedio",
                  "n_disponibles"):
        print(f"\n    {campo:<20}" + "".join(f"{n:>13}" for n in nombres))
        for c in CADENAS:
            print(f"    {c:<20}" + "".join(f"{f_num(res[n][c][campo], 1 if 'pct' in campo else 2):>13}"
                                           for n in nombres))

    print("\n    Orden de las cadenas (menor % primero):")
    for campo in ("delta_pct_sin_promo", "delta_pct_final"):
        base = orden(res["hoy"], campo)
        print(f"      {campo}:")
        for n in nombres:
            o = orden(res[n], campo)
            marca = "" if o == base else "   ⚠ SE DA VUELTA respecto de hoy"
            print(f"        {n:<8} " + " < ".join(f"{c} {res[n][c][campo]:+.1f}" for c in o) + marca)

    print("\n    Mediana de mercado que cambia al sacar las filas de la mediana (S2 sin vs hoy):")
    prom_sin = main._promedios_por_producto(
        CANASTA, {k: v for k, v in ctx["precios"].items() if k not in q2})
    for p in CANASTA:
        a, b = ctx["prom"].get(p["nombre"]) or {}, prom_sin.get(p["nombre"]) or {}
        if (a.get("precio_base"), a.get("n_cadenas")) != (b.get("precio_base"), b.get("n_cadenas")):
            print(f"      {p['nombre']:<20} hoy {a.get('precio_base')} {a.get('unidad_base') or ''} ×{a.get('n_cadenas')}"
                  f"  →  {b.get('precio_base')} {b.get('unidad_base') or ''} ×{b.get('n_cadenas')}")


def orden(fis, campo):
    return [c for _v, c in sorted((fi[campo], c) for c, fi in fis.items() if fi[campo] is not None)]


def piso_brecha(ctx):
    P.barra("§4 — PISO POR CADENA Y BRECHA ENTRE LA MEJOR Y LA PEOR (CLAUDE.md: un promedio global esconde esto)",
            "filas ok S1 = filas con representante fuera de A y D-mal · filas ok S2 = filas en B-bien, C o D-bien "
            "(B-? cuenta como NO verificada).",
            "$ mal = subtotal de las filas del escenario / total_envase de hoy.")
    metricas = {}
    for c in CADENAS:
        cl = [ctx["clases"][(c, p["nombre"])]["cajon"] for p in CANASTA]
        n_rep = sum(1 for k in cl if k != "faltante")
        te = ctx["hoy"][c]["total_envase"]
        metricas[c] = {
            "filas ok S1 %": (n_rep - sum(1 for k in cl if k in S1)) / n_rep * 100,
            "filas ok S2 %": sum(1 for k in cl if k in BIEN) / n_rep * 100,
            "$ mal S1 %": ctx["pesos"][c]["S1"] / te * 100,
            "$ mal S2 %": ctx["pesos"][c]["S2"] / te * 100,
        }
        metricas[c]["n_rep"] = n_rep
    claves = ("filas ok S1 %", "filas ok S2 %", "$ mal S1 %", "$ mal S2 %")
    print(f"    {'':<11}{'n_rep':>6}" + "".join(f"{k:>15}" for k in claves))
    for c in CADENAS:
        print(f"    {c:<11}{metricas[c]['n_rep']:>6}" + "".join(f"{metricas[c][k]:>14.1f}%" for k in claves))
    print()
    for k in claves:
        vals = {c: metricas[c][k] for c in CADENAS}
        peor = min(vals, key=vals.get) if "ok" in k else max(vals, key=vals.get)
        mejor = max(vals, key=vals.get) if "ok" in k else min(vals, key=vals.get)
        print(f"    {k:<15} piso (peor) = {peor} {vals[peor]:.1f}% · mejor = {mejor} {vals[mejor]:.1f}% · "
              f"brecha = {abs(vals[mejor] - vals[peor]):.1f} puntos")


def nombradas(ctx):
    P.barra("§5 — LAS DOS FILAS QUE NOMBRA LA TAREA 26, contra lo documentado")
    c, q = "Chango Más", "Manteca"
    f, k = ctx["reps"].get((c, q)), ctx["clases"][(c, q)]
    d = fila_ficha(ctx["hoy"][c], q)
    if f:
        prev = MANTECA_CHANGO["precio_antes_fix"] * d["cantidad"]
        ok = (f["of"].precio == MANTECA_CHANGO["precio"] and d["subtotal"] == MANTECA_CHANGO["subtotal"]
              and d["subtotal"] - prev == MANTECA_CHANGO["delta"])
        print(f"    {c} / {q} ({d['cantidad']:g} unidad): {f['of'].producto!r} {txt_cotiza(f['of'])}")
        print(f"      {d['envases']:g} × ${f['of'].precio:,.2f} = ${d['subtotal']:,.2f} · antes del fix "
              f"{d['cantidad']:g} × ${MANTECA_CHANGO['precio_antes_fix']:,.0f} = ${prev:,.2f} → "
              f"+${d['subtotal'] - prev:,.2f} · cajón {k['cajon']} factor {f_num(k['factor'])} · cobra {k['cobra']} · "
              f"estado {d['estado']} · {'COINCIDE' if ok else 'NO COINCIDE'} con lo documentado "
              f"(2 × $3.654 = $7.308, +$6.090 — documentado ANTES de la Tarea 26 paso b)")
    else:
        print(f"    {c} / {q}: SIN representante en esta captura")
    q = "Tomate perita lata"
    print(f"\n    {q} (2 unidad), las 4 VTEX — documentado: 2 × $/kg = 2 kg de tomate fresco, no 2 latas:")
    for c in VTEX:
        f, k = ctx["reps"].get((c, q)), ctx["clases"][(c, q)]
        if not f:
            print(f"      {c:<11} faltante")
            continue
        d = fila_ficha(ctx["hoy"][c], q)
        print(f"      {c:<11} {f['of'].producto[:34]!r:<37} {txt_cotiza(f['of']):<13} 2 × ${f['of'].precio:,.2f} = "
              f"${d['subtotal']:,.2f} · cajón {k['cajon']} · cobra {k['cobra']} · estado {d['estado']} "
              f"Δ {d['delta_pct']}%")


def separacion(ctx, filas):
    P.barra("§6 — SEPARACIÓN 26 / 22, con la misma captura y sin red",
            "conm. = candidato disponible de esa cadena y consulta que, puesto de representante, cae en B-bien, C o D-bien.",
            f"Mejor = mayor (puntuar, −precio), el desempate del matcher. Umbral {UMBRAL_MATCH}. kw = proxy mecánico: "
            "el título contiene alguna palabras_clave (substring normalizado).",
            "Veredicto mecánico: existe conm. en la captura → 22 (un matcher perfecto la arreglaba) · no existe → 26.",
            "Para B, 'factor 1' usa la lectura del prompt: '2 kg' = 2 kg en total.")
    items = {p["nombre"]: p for p in CANASTA}
    ver = Counter()
    for p in CANASTA:
        for c in CADENAS:
            k = ctx["clases"][(c, p["nombre"])]
            if k["cajon"] not in ("A", "B-mal", "C-mal", "D-mal", "B-?"):
                continue
            prod, rep = items[p["nombre"]], ctx["reps"][(c, p["nombre"])]
            s_rep = score(prod, rep["of"])
            cands = candidatos(filas, c, p["nombre"])
            conm = []
            for f in cands:
                if f is rep:
                    continue
                kf = clasificar(prod, f)
                if kf["cajon"] in BIEN:
                    conm.append((score(prod, f["of"]), -f["of"].precio, f, kf))
            conm.sort(key=lambda x: (x[0], x[1]), reverse=True)
            con_kw = [x for x in conm if proxy_kw(prod, x[2]["of"].producto)]
            veredicto = "22" if conm else "26"
            ver[(c, k["cajon"], veredicto)] += 1
            extra = ""
            if k["cajon"] in ("B-mal", "B-?", "C-mal"):
                extra = f" · bonus de tamaño del ganador {s_rep - score(prod, rep['of'], False):+.3f}"
                ft = factor_titulo(prod, rep)
                if k["cajon"] == "B-?" and ft is not None:
                    extra += f" · el título diría factor {ft:.3g} (no se usa)"
            fac = "—" if k["factor"] is None else f"{k['factor']:.3g}"
            print(f"\n    [{p['nombre']}] {c} — {k['cajon']}{k['marca']} factor {fac} → {veredicto}")
            print(f"      representante: {rep['of'].producto[:70]!r} s={s_rep} kw={'sí' if proxy_kw(prod, rep['of'].producto) else 'no'}"
                  f" · métrica vs título: {rep['clase']}{extra}")
            print(f"      candidatos disponibles: {len(cands)} · conm.: {len(conm)} (≥ umbral: "
                  f"{sum(1 for x in conm if x[0] >= UMBRAL_MATCH)}, con kw: {len(con_kw)})")
            for tag, x in (("mejor conm.", conm[0] if conm else None),
                           ("mejor conm. con kw", con_kw[0] if con_kw else None)):
                if x is None:
                    print(f"      {tag:<19}: —")
                    continue
                s, _neg, f, kf = x
                print(f"      {tag:<19}: {f['of'].producto[:60]!r} ${f['of'].precio:,.2f} {kf['cajon']} "
                      f"({kf['cobra']}) s={s} {'≥' if s >= UMBRAL_MATCH else '<'} umbral · Δs vs ganador "
                      f"{s - s_rep:+.3f} · kw={'sí' if proxy_kw(prod, f['of'].producto) else 'no'}")
    print("\n  RESUMEN veredicto por cadena y cajón:")
    for c in CADENAS:
        partes = [f"{caj} {v}: {n}" for (cc, caj, v), n in sorted(ver.items()) if cc == c]
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
    print("  (5) subtotal = precio × envases en cada fila; Σ = total_envase; cajones suman 20")
    print("  (6) escenarios sin quitar nada == hoy, con mediana fija y sin ella")
    print("  (7) unidad pedida en peso/volumen para los ítems en kg/litro")

    informe_filas(ctx)
    conteo(ctx)
    pesos(ctx, dia)
    piso_brecha(ctx)
    nombradas(ctx)
    separacion(ctx, filas)


if __name__ == "__main__":
    main_cli()
