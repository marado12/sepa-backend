"""
Tarea 26, paso (b) (18/09/2026, sesión nocturna sin supervisión) — MEDIR la regla completa,
simulada en memoria. Instrumento de MEDICIÓN, 100% SIN RED, sobre la captura del paso 1.
No es test ni guard: fuera del CI. NO toca main.py, fuentes.py ni precios_vtex.py.

Simula, por separado y juntos, los tres cambios de las decisiones de Santiago (registro de
decisiones, Tarea 26 en PROXIMAS-TAREAS.md):

  S-escala  El subtotal deja de ser `precio_min × cantidad` y pasa a ser lo que pagarías por
            los envases enteros que cubren lo pedido (decisión 1). Reimplementado acá como
            `envases()`: para cada cajón de `medir_unidad_pedida.clasificar`,
              C/C-mal   → exacto, sin cambios (cotiza por unidad de medida)
              B-bien/B-mal → ceil(cantidad_pedida / factor) envases
              D-bien/D-mal → ceil(cantidad / click) envases
              A/B-?     → None: no se puede escalar (case 4, decisión 6, estado residual)

  S-filtro  Un candidato que no puede contestar la pregunta (el que cotiza el kilo cuando el
            pedido es "unidad", o que no tiene métrica del tipo pedido) queda inelegible como
            representante (decisión 3). Si NINGÚN candidato de esa cadena/consulta es elegible,
            se conserva la selección de hoy pero la fila queda "sin candidato elegible"
            (decisión 6: mismo tratamiento que `envases() is None`).

  S-tamaño  Se le saca a `puntuar` el bonus/penalidad de tamaño (decisión 4): el `+0,20` cuando
            el envase coincide con `cantidad × unidad` y el `×0,7` cuando está lejos. Se
            CONSERVA el `×0,5` de tipo distinto (peso contra volumen).

Cómo se llega a `_analizar`/`_fichas` sin tocarlos ni sumar nada a mano
------------------------------------------------------------------------
`_analizar`/`_fichas` calculan `subtotal = precio_min × cantidad` con la MISMA `cantidad` para
todas las cadenas de una corrida (vienen de una sola `canasta` compartida). Como S-escala
necesita una `cantidad` (en envases) que depende de CUÁL cadena y CUÁL representante, se llama
UNA VEZ POR CADENA con una copia de `CANASTA_DEFAULT` donde, solo para esa cadena y solo en las
filas escalables, `cantidad` pasa a ser el número de envases. Con eso, la fórmula sin tocar
(`precio_min × cantidad`) da exactamente `envases × precio_min`, y la del `esp` de `_fichas`
(`promedio × cantidad_base_del_envase × cantidad`) pasa a comparar "comprar la MISMA cantidad
física al precio de este representante" contra "... al precio mediano" — el Δ% por fila
mantiene el invariante que ya describe la Tarea 26 (decisión 8: "el Δ% de una fila mal
escalada está bien, esp y subtotal usan los dos cantidad_base, que se cancela") en vez de
quedar contaminado por la cantidad ORIGINAL pedida. Antes de exponer la ficha se repone
`cantidad` al valor pedido real, solo para mostrarlo — no toca ningún número de plata.
Las filas excluidas (case 4 o sin candidato elegible) se sacan de `precios` SOLO para la
llamada de esa cadena (vía `pop`, como hacía `quitar` en `medir_unidad_pedida.correr` ✏️ hasta el paso c): no
entran a `total_envase` ni al `%`, pero siguen presentes en el diccionario completo que recibe
`_promedios_por_producto`, así que siguen votando en la mediana.

`_promedios_por_producto`, `_sin_ninguna_cadena` y el `tiene` de `_fichas` se calculan siempre
con el diccionario de precios COMPLETO (las 5 cadenas), nunca con el recorte por cadena: si no,
"cuántas cadenas más lo tienen" y "n_comparables" quedarían mal en cuanto se recorta.

Validación obligatoria, primero: con los tres apagados, la salida tiene que ser IDÉNTICA,
campo por campo, a la de hoy (`medir_pesables.fichas(..., "hoy", ...)`, ya validada por
`medir_unidad_pedida.validar`). Si no lo es, exit 1 y no se reporta nada más.

Salida: 0 medición completa (incluida la validación) · 1 el instrumento no reproduce lo que
dice medir.

Desde sepa_backend/:
    venv\\Scripts\\python.exe -m tests.medir_regla_26b --desde tests/capturas/canasta_default_2026-09-14.json
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
import precios_vtex
from precios_vtex import UMBRAL_MATCH
from tests import medir_pesables as P
from tests import medir_unidad_pedida as U

RAIZ = Path(__file__).resolve().parents[1]
CAPTURA = RAIZ / "tests" / "capturas" / "canasta_default_2026-09-14.json"
CADENAS = U.CADENAS
CANASTA = U.CANASTA


class InstrumentoInvalido(Exception):
    pass


# La columna "hoy" de la medición previa (18/09, antes de implementar), que la simulación de
# "hoy" tiene que seguir dando aunque producción ya no sea "hoy".
CAMPOS_HOY = ("total_envase", "total_final", "delta_pct_sin_promo", "delta_pct_final", "n_con_promedio")
HOY_DOCUMENTADO = {
    "Carrefour":  (143632.25, 107724.19, -7.6, -30.7, 17),
    "Día":        (115272.00, 86454.00, 8.7, -18.5, 16),
    "Vea":        (102676.00, 77007.00, -11.4, -33.6, 16),
    "Chango Más": (165152.00, 123864.00, -3.1, -27.3, 16),
    "Coto":       (124931.95, 93698.96, 33.8, 0.3, 14),
}


# ─────────────────────────────────────────────────────────────────
#  S-TAMAÑO: puntuar sin el bonus/penalidad de tamaño
# ─────────────────────────────────────────────────────────────────

def puntuar_variante(item, of, normalizar, extraer_cantidades, con_bonus=True, con_castigo=True):
    """
    Copia de `precios_vtex.puntuar`, con el bonus `+0,20` (`con_bonus`) y la penalidad `×0,7`
    (`con_castigo`) de tamaño detrás de dos llaves separadas (decisión 4 de la Tarea 26; ✏️ el
    18/09 Santiago la partió: se saca solo el ×0,7 y el +0,20 se conserva hasta la Tarea 22).
    El `×0,5` de tipo distinto (peso/volumen) se CONSERVA siempre. Con las dos en True tiene que
    dar EXACTAMENTE lo mismo que `puntuar()` — se valida en `validar()` contra todos los
    candidatos de la captura.
    """
    nombre = item.get("nombre") or ""
    if not nombre or not of.producto:
        return 0.0
    t_item = precios_vtex._tokens(nombre, normalizar)
    t_of = precios_vtex._tokens(of.producto, normalizar)
    if not t_item:
        return 0.0
    comunes = t_item & t_of
    if not comunes:
        return 0.0
    cobertura = len(comunes) / len(t_item)
    precision = len(comunes) / len(t_of) if t_of else 0.0
    score = cobertura * (0.6 + 0.4 * precision)

    marca_pedida = (item.get("marca") or "").strip()
    aceptadas = [m for m in (item.get("marcas_aceptadas") or []) if m]
    if marca_pedida or aceptadas:
        candidatas = [m.lower() for m in ([marca_pedida] + aceptadas) if m]
        texto = f"{of.producto} {of.marca or ''}".lower()
        if any(c in texto for c in candidatas):
            score = min(1.0, score + 0.25)
        elif marca_pedida:
            score *= 0.6

    if extraer_cantidades:
        objetivo = precios_vtex._cantidad_objetivo(item, extraer_cantidades)
        if objetivo:
            val_obj, tipo_obj = objetivo
            cants = extraer_cantidades(normalizar(of.producto)) or []
            iguales = [(v, t) for v, t in cants if t == tipo_obj and v > 0]
            if iguales:
                val, _ = iguales[0]
                ratio = min(val, val_obj) / max(val, val_obj)
                if ratio >= 1 - precios_vtex.TOLERANCIA_CANTIDAD:
                    if con_bonus:
                        score = min(1.0, score + 0.20)
                elif ratio < 0.5:
                    if con_castigo:
                        score *= 0.7
            elif cants:
                score *= 0.5     # ×0,5 de tipo distinto: se conserva siempre (decisión 4)

    return round(min(score, 1.0), 3)


# ─────────────────────────────────────────────────────────────────
#  S-FILTRO: elegibilidad como representante
# ─────────────────────────────────────────────────────────────────

def elegible(prod, of, pu):
    """
    ¿Este candidato puede contestar la pregunta que hace `prod`?
      pedido SIN contenido (unidad/pack): solo un ARTÍCULO puede responder "cuántos" —
        uno que cotiza por unidad de medida (kg/l/m) no tiene con qué.
      pedido CON contenido (kg/l): necesita métrica DEL MISMO TIPO (peso/volumen) que lo
        pedido, sea artículo o unidad de medida — el factor puede ser ≠ 1 (eso es S-escala).
    """
    if not U.con_contenido(prod):
        return not U.cotiza_um(of)
    tipo_pedido = U.unidad_pedida(prod)
    return bool(pu) and tipo_pedido is not None and pu.get("tipo") == tipo_pedido[1]


# ─────────────────────────────────────────────────────────────────
#  SELECCIÓN DE REPRESENTANTE — la misma que `buscar_precios_online`,
#  parametrizada por S-filtro y S-tamaño
# ─────────────────────────────────────────────────────────────────

def llaves_tamano(s_tamano):
    """
    (con_bonus, con_castigo) para `puntuar_variante`.
      False   `puntuar` de hoy: +0,20 y ×0,7.
      True    decisión 4 original: sin +0,20 ni ×0,7.
      "×0,7"  decisión 4 partida por Santiago el 18/09: sin el ×0,7, CON el +0,20.
    """
    if s_tamano == "×0,7":
        return True, False
    return (False, False) if s_tamano else (True, True)


def seleccionar(canasta, filas, s_filtro=False, s_tamano=False):
    """
    Reimplementación del bucle de selección de `precios_vtex.buscar_precios_online` sobre los
    candidatos YA PARSEADOS de la captura (`medir_pesables.parsear`), en vez de llamar a una
    `Fuente`. Mismo desempate: a igual score gana el más barato.
    """
    precios, elegido, sin_elegible = {}, {}, set()
    for prod in canasta:
        nombre = prod["nombre"]
        for c in CADENAS:
            cands = U.candidatos(filas, c, nombre)
            con_bonus, con_castigo = llaves_tamano(s_tamano)
            puntuados = [
                (puntuar_variante(prod, f["of"], main.normalizar, main._extraer_cantidades_desc,
                                  con_bonus=con_bonus, con_castigo=con_castigo), f)
                for f in cands
            ]
            puntuados = [(s, f) for s, f in puntuados if s >= UMBRAL_MATCH]
            if not puntuados:
                continue
            pool, filtro_vacio = puntuados, False
            if s_filtro:
                elegibles = [(s, f) for s, f in puntuados if elegible(prod, f["of"], f["pu"])]
                if elegibles:
                    pool = elegibles
                else:
                    filtro_vacio = True
            s_final, f = max(pool, key=lambda x: (x[0], -x[1]["of"].precio))
            precios[(c, nombre)] = {
                "precio_min": float(f["of"].precio),
                "precio_por_100u": f["pu"],
                "ean": f["of"].ean,
                "match_score": s_final,
                "origen": f["of"].origen,
            }
            elegido[(c, nombre)] = f
            if filtro_vacio:
                sin_elegible.add((c, nombre))
    return precios, elegido, sin_elegible


# ─────────────────────────────────────────────────────────────────
#  S-ESCALA: envases necesarios y las fichas escaladas, por cadena
# ─────────────────────────────────────────────────────────────────

def envases(prod, k, f):
    """None si la fila no se puede escalar (case 4). Si no, cuántos envases hay que comprar."""
    cajon = k["cajon"]
    if cajon in ("C", "C-mal"):
        return prod["cantidad"]                                  # case 1: exacto
    if cajon in ("B-bien", "B-mal"):
        return math.ceil(prod["cantidad"] / k["factor"] - 1e-9)  # case 2
    if cajon in ("D-bien", "D-mal"):
        return math.ceil(prod["cantidad"] / U.click(f["of"]) - 1e-9)  # case 3
    return None                                                   # A, B-?: case 4


def clasificar_todas(canasta, precios, elegido, sin_elegible):
    out = {}
    for prod in canasta:
        nombre = prod["nombre"]
        for c in CADENAS:
            f = elegido.get((c, nombre))
            if not f:
                out[(c, nombre)] = {"cajon": "faltante", "factor": None, "envases": None,
                                     "excluida": True, "of": None, "marca": ""}
                continue
            k = U.clasificar(prod, f)
            n = envases(prod, k, f)
            excl = n is None or (c, nombre) in sin_elegible
            out[(c, nombre)] = {"cajon": k["cajon"], "factor": k["factor"], "envases": n,
                                 "excluida": excl, "of": f["of"], "marca": k["marca"]}
    return out


def fichas_escaladas(canasta, precios, clas, dia):
    prom = main._promedios_por_producto(canasta, precios)
    fichas = {}
    for c in CADENAS:
        canasta_c = [dict(p) for p in canasta]
        precios_c = dict(precios)
        for p in canasta_c:
            info = clas[(c, p["nombre"])]
            if info["cajon"] == "faltante":
                continue
            if info["excluida"]:
                precios_c.pop((c, p["nombre"]), None)
            else:
                p["cantidad"] = info["envases"]
        res_c = main._analizar(canasta_c, precios_c, main.PROMOS_DEFAULT, dia, None)
        fi = main._fichas(canasta_c, precios_c, res_c, prom, [c])[0]
        cant_real = {p["nombre"]: p["cantidad"] for p in canasta}
        for d in fi["detalle"]:
            d["cantidad"] = cant_real[d["producto"]]
        fichas[c] = fi
    return fichas


def correr_escenario(canasta, filas, dia, *, s_escala, s_filtro, s_tamano):
    precios, elegido, sin_elegible = seleccionar(canasta, filas, s_filtro=s_filtro, s_tamano=s_tamano)
    clas = clasificar_todas(canasta, precios, elegido, sin_elegible)
    if not s_escala:
        prom = main._promedios_por_producto(canasta, precios)
        res = main._analizar(canasta, precios, main.PROMOS_DEFAULT, dia, None)
        fichas = {fi["cadena"]: fi for fi in main._fichas(canasta, precios, res, prom, list(CADENAS))}
    else:
        fichas = fichas_escaladas(canasta, precios, clas, dia)
    return {"precios": precios, "elegido": elegido, "sin_elegible": sin_elegible, "clas": clas,
            "fichas": fichas}


ESCENARIOS = [
    ("hoy",               dict(s_escala=False, s_filtro=False, s_tamano=False)),
    ("solo S-escala",     dict(s_escala=True,  s_filtro=False, s_tamano=False)),
    ("S-escala+S-filtro", dict(s_escala=True,  s_filtro=True,  s_tamano=False)),
    # ✏️ 18/09 (Tarea 26 paso b, implementación): S-tamaño partido. La decisión del 18/09 era
    # sacar solo el ×0,7 y conservar el +0,20; medido este escenario, el ×0,7 también se
    # conserva (ver §9). Queda como la medición de la opción descartada.
    ("sin ×0,7",          dict(s_escala=True,  s_filtro=True,  s_tamano="×0,7")),
    ("los tres",          dict(s_escala=True,  s_filtro=True,  s_tamano=True)),
]
# Lo que quedó en producción el 18/09 (sepa_backend, Tarea 26 paso b): S-escala + S-filtro,
# `puntuar` sin tocar. `validar` exige que esta simulación dé lo mismo que el código real.
IMPLEMENTADA = "S-escala+S-filtro"
SIN_X07 = "sin ×0,7"


# ─────────────────────────────────────────────────────────────────
#  VALIDACIÓN — obligatoria y primero
# ─────────────────────────────────────────────────────────────────

def validar(filas, dia):
    errores = []

    # (1) puntuar_variante(con +0,20 y con ×0,7) == puntuar(), sobre TODOS los candidatos.
    n_cmp = 0
    for prod in CANASTA:
        for c in CADENAS:
            for f in U.candidatos(filas, c, prod["nombre"]):
                n_cmp += 1
                a = puntuar_variante(prod, f["of"], main.normalizar, main._extraer_cantidades_desc)
                b = U.score(prod, f["of"])
                if a != b:
                    errores.append(f"(1) {c}/{prod['nombre']}: puntuar_variante={a} puntuar={b} "
                                   f"({f['of'].producto!r})")
    if errores:
        raise InstrumentoInvalido("\n  ".join(errores[:15]))

    # (2) ✏️ 18/09, después de implementar: seleccionar(S-filtro) reproduce exacto el precios de
    # producción, con los mismos `sin_elegible` y los mismos envases. Antes comparaba
    # seleccionar(sin nada) contra la producción de antes de la regla.
    precios_prod = P.precios_de(filas, CANASTA, "hoy")
    precios_sel, elegido_sel, sin_eleg = seleccionar(CANASTA, filas, s_filtro=True, s_tamano=False)
    sin_prod = {k for k, v in precios_prod.items() if v.get("sin_elegible")}
    if sin_eleg != sin_prod:
        errores.append(f"(2) sin_elegible: seleccionar {sorted(sin_eleg)} ≠ producción {sorted(sin_prod)}")
    clas_sel = clasificar_todas(CANASTA, precios_sel, elegido_sel, sin_eleg)
    for k, v in precios_prod.items():
        esperado = None if clas_sel[k]["excluida"] else clas_sel[k]["envases"]
        if v.get("envases") != esperado:
            errores.append(f"(2) {k}: envases de producción {v.get('envases')} ≠ simulados {esperado}")
    claves = set(precios_prod) | set(precios_sel)
    for k in claves:
        a, b = precios_prod.get(k), precios_sel.get(k)
        if (a is None) != (b is None):
            errores.append(f"(2) {k}: producción {'tiene' if a else 'no tiene'} entrada, "
                           f"seleccionar {'tiene' if b else 'no tiene'}")
            continue
        if a is None:
            continue
        if (a["precio_min"], a["ean"], a["match_score"], a["precio_por_100u"]) != \
           (b["precio_min"], b["ean"], b["match_score"], b["precio_por_100u"]):
            errores.append(f"(2) {k}: producción {a} ≠ seleccionar {b}")
    if errores:
        raise InstrumentoInvalido("\n  ".join(errores[:15]))

    # (3) LA VALIDACIÓN OBLIGATORIA. ✏️ 18/09, después de implementar: la simulación del
    # escenario IMPLEMENTADO da, ficha por ficha, lo mismo que el código real. Antes era "con los
    # tres apagados == producción", que dejó de ser cierto por diseño.
    _precios_h, _prom_h, fi_prod = P.fichas(filas, CANASTA, "hoy", False, dia)
    ctx_impl = correr_escenario(CANASTA, filas, dia, **dict(ESCENARIOS)[IMPLEMENTADA])
    if ctx_impl["fichas"] != fi_prod:
        for c in CADENAS:
            if ctx_impl["fichas"].get(c) != fi_prod.get(c):
                errores.append(f"(3) {c}: la ficha simulada de '{IMPLEMENTADA}' ≠ la del código real")
        raise InstrumentoInvalido("\n  ".join(errores) or "(3) las fichas difieren")

    # (4) "hoy" sigue simulando el código de antes: da los números documentados en la medición
    # previa (PROXIMAS-TAREAS.md, Tarea 26, "Paso (b) — medición previa", columna "hoy").
    ctx_hoy = correr_escenario(CANASTA, filas, dia, s_escala=False, s_filtro=False, s_tamano=False)
    for c, vals in HOY_DOCUMENTADO.items():
        for campo, v in zip(CAMPOS_HOY, vals):
            if ctx_hoy["fichas"][c][campo] != v:
                errores.append(f"(4) hoy {c} {campo} = {ctx_hoy['fichas'][c][campo]}, documentado {v}")
    if errores:
        raise InstrumentoInvalido("\n  ".join(errores))

    return {"n_candidatos_comparados": n_cmp, "n_representantes": len(precios_prod)}


# ─────────────────────────────────────────────────────────────────
#  INFORME
# ─────────────────────────────────────────────────────────────────

def f_num(v, dec=2):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:,.{dec}f}"
    return str(v)


def orden(fichas, campo):
    return [c for _v, c in sorted((fi[campo], c) for c, fi in fichas.items() if fi[campo] is not None)]


def resumen_por_cadena(ctxs):
    P.barra("§1 — RESUMEN POR CADENA Y ESCENARIO",
            "hoy · solo S-escala · S-escala+S-filtro (IMPLEMENTADA) · sin ×0,7 · los tres.")
    campos = ("total_envase", "total_final", "delta_pct_sin_promo", "delta_pct_final",
              "n_con_promedio", "n_disponibles")
    nombres = [n for n, _ in ESCENARIOS]
    for campo in campos:
        print(f"\n    {campo:<20}" + "".join(f"{n:>18}" for n in nombres))
        for c in CADENAS:
            print(f"    {c:<20}" + "".join(
                f"{f_num(ctxs[n]['fichas'][c][campo], 1 if 'pct' in campo else 2):>18}" for n in nombres))


def orden_cadenas(ctxs):
    P.barra("§2 — ORDEN DE LAS CADENAS POR CADA %, CON ⚠ SI SE DA VUELTA RESPECTO DE HOY")
    for campo in ("delta_pct_sin_promo", "delta_pct_final"):
        base = orden(ctxs["hoy"]["fichas"], campo)
        print(f"\n    {campo}:")
        for n, _ in ESCENARIOS:
            o = orden(ctxs[n]["fichas"], campo)
            marca = "" if o == base else "   ⚠ SE DA VUELTA respecto de hoy"
            fichas = ctxs[n]["fichas"]
            print(f"      {n:<20} " + " < ".join(f"{c} {fichas[c][campo]:+.1f}" for c in o) + marca)


def mediana_por_producto(ctxs):
    P.barra("§3 — MEDIANA POR PRODUCTO, DONDE CAMBIA ENTRE ESCENARIOS",
            "La mediana solo puede moverse cuando cambia la SELECCIÓN (S-filtro o S-tamaño); "
            "S-escala sola no la toca.")
    proms = {}
    for n, kw in ESCENARIOS:
        proms[n] = main._promedios_por_producto(CANASTA, ctxs[n]["precios"])
    nombres = [n for n, _ in ESCENARIOS]
    for prod in CANASTA:
        nombre = prod["nombre"]
        vals = [proms[n].get(nombre) for n in nombres]
        claves = {(v.get("precio_base"), v.get("unidad_base"), v.get("n_cadenas")) if v else None
                  for v in vals}
        if len(claves) <= 1:
            continue
        print(f"\n    {nombre}:")
        for n, v in zip(nombres, vals):
            if v is None:
                print(f"      {n:<20} SIN MEDIANA (ninguna cadena tiene precio_base comparable)")
            else:
                print(f"      {n:<20} {v['precio_base']:.4f} {v['unidad_base']} × {v['n_cadenas']} cadenas")
    sin_mediana_todas = [p["nombre"] for p in CANASTA if all(proms[n].get(p["nombre"]) is None for n in nombres)]
    if sin_mediana_todas:
        print(f"\n    Sin mediana en NINGÚN escenario: {sin_mediana_todas}")


def movidas_mas_de_2x(ctxs, destino):
    """(nombre, cadena, factor, $hoy, $destino, primer escenario que ya la mueve) — hoy → `destino`."""
    fi_hoy = ctxs["hoy"]["fichas"]
    hasta = [n for n, _ in ESCENARIOS[1:]]
    hasta = hasta[:hasta.index(destino) + 1]
    movidas = []
    for c in CADENAS:
        for prod in CANASTA:
            nombre = prod["nombre"]
            d_hoy = U.fila_ficha(fi_hoy[c], nombre)
            sub_hoy = d_hoy["subtotal"] if d_hoy else 0.0
            if not sub_hoy:
                continue
            primero = None
            for n in hasta:
                d = U.fila_ficha(ctxs[n]["fichas"][c], nombre)
                sub = d["subtotal"] if d else 0.0
                factor = sub / sub_hoy
                if (factor > 2 or factor < 0.5) and primero is None:
                    primero = n
                if n == destino and (factor > 2 or factor < 0.5):
                    movidas.append((nombre, c, factor, sub_hoy, sub, primero))
    return movidas


def movimientos_grandes(ctxs):
    P.barra("§4 — FILAS CUYO SUBTOTAL SE MUEVE MÁS DE 2× RESPECTO DE HOY (en cualquier dirección)",
            "Candidatas de la Tarea 22, no se tapan. factor = subtotal_escenario / subtotal_hoy.")
    for destino in (IMPLEMENTADA, SIN_X07, "los tres"):
        movidas = movidas_mas_de_2x(ctxs, destino)
        print(f"\n    hoy → {destino}: {len(movidas)} filas")
        _imprimir_movidas(movidas)
    a = {(x[0], x[1]) for x in movidas_mas_de_2x(ctxs, SIN_X07)}
    b = {(x[0], x[1]) for x in movidas_mas_de_2x(ctxs, "los tres")}
    print(f"\n    Solo en {SIN_X07}: {sorted(a - b) or 'ninguna'}")
    print(f"    Solo en los tres: {sorted(b - a) or 'ninguna'}")


def _imprimir_movidas(movidas):
    if not movidas:
        print("    Ninguna fila se mueve más de 2×.")
    for nombre, c, factor, sub_hoy, sub, primero in sorted(movidas, key=lambda x: -abs(math.log(x[2]))):
        print(f"      {nombre:<22} {c:<11} ×{factor:6.2f}   ${sub_hoy:>10,.2f} → ${sub:>10,.2f}   "
              f"(primer escenario que ya lo mueve >2×: {primero})")


def sin_candidato_elegible(ctxs):
    P.barra("§5 — FILAS SIN CANDIDATO ELEGIBLE (S-filtro), POR CADENA",
            "Población del estado nuevo (decisión 6). El paso 1 estimó cero en esta captura: "
            "acá se confirma o se refuta con la selección real de S-filtro.")
    for n in ("S-escala+S-filtro", SIN_X07, "los tres"):
        cnt = Counter(c for c, _q in ctxs[n]["sin_elegible"])
        print(f"\n    {n}: {len(ctxs[n]['sin_elegible'])} filas — " + (dict(cnt) or "ninguna"))
        for c, q in sorted(ctxs[n]["sin_elegible"]):
            print(f"      {c:<11} {q}")


def cambios_de_representante(ctxs):
    P.barra("§6 — CAMBIOS DE REPRESENTANTE, POR TRANSICIÓN ENTRE ESCENARIOS",
            "hoy→solo S-escala nunca cambia representante (misma selección). Se listan las otras dos "
            "transiciones: qué reemplaza a qué, con precio y score.")
    transiciones = [("solo S-escala", "S-escala+S-filtro", "S-filtro"),
                    ("S-escala+S-filtro", SIN_X07, "sacar SOLO el ×0,7"),
                    (SIN_X07, "los tres", "sacar además el +0,20"),
                    ("S-escala+S-filtro", "los tres", "S-tamaño entero (+0,20 y ×0,7)")]
    for antes, despues, causa in transiciones:
        print(f"\n    {antes} → {despues}  (cambio atribuible a {causa}):")
        alguno = False
        for c in CADENAS:
            for prod in CANASTA:
                nombre = prod["nombre"]
                fa = ctxs[antes]["elegido"].get((c, nombre))
                fb = ctxs[despues]["elegido"].get((c, nombre))
                pa = fa["of"].producto if fa else None
                pb = fb["of"].producto if fb else None
                if pa == pb:
                    continue
                alguno = True
                sa = ctxs[antes]["precios"].get((c, nombre), {}).get("match_score")
                sb = ctxs[despues]["precios"].get((c, nombre), {}).get("match_score")
                print(f"      [{nombre}] {c}")
                print(f"        antes:   {pa!r:<55} ${fa['of'].precio if fa else 0:>10,.2f} s={sa}")
                print(f"        después: {pb!r:<55} ${fb['of'].precio if fb else 0:>10,.2f} s={sb}")
        if not alguno:
            print("      ningún cambio de representante en esta transición")


def piso_brecha(ctxs):
    P.barra("§7 — PISO POR CADENA Y BRECHA, EN CADA ESCENARIO, CON EL CONTEO DE FALTANTES AL LADO",
            "filas ok % = representantes NO excluidos del total (case 4 o sin candidato elegible) / "
            "representantes encontrados. faltante = la cadena no tiene el producto (ni siquiera "
            "un candidato por debajo del umbral).")
    for n, _ in ESCENARIOS:
        clas = ctxs[n]["clas"]
        print(f"\n    {n}:")
        print(f"    {'':<11}{'n_rep':>7}{'faltantes':>11}{'excluidas':>11}{'filas ok %':>12}")
        metricas = {}
        for c in CADENAS:
            cl = [clas[(c, p["nombre"])] for p in CANASTA]
            n_falt = sum(1 for x in cl if x["cajon"] == "faltante")
            n_rep = len(CANASTA) - n_falt
            n_excl = sum(1 for x in cl if x["cajon"] != "faltante" and x["excluida"])
            ok_pct = (n_rep - n_excl) / n_rep * 100 if n_rep else 0.0
            metricas[c] = ok_pct
            print(f"    {c:<11}{n_rep:>7}{n_falt:>11}{n_excl:>11}{ok_pct:>11.1f}%")
        peor = min(metricas, key=metricas.get)
        mejor = max(metricas, key=metricas.get)
        print(f"    piso (peor) = {peor} {metricas[peor]:.1f}% · mejor = {mejor} {metricas[mejor]:.1f}% "
              f"· brecha = {metricas[mejor] - metricas[peor]:.1f} puntos")


def atribucion(ctxs):
    P.barra("§8 — QUÉ EXPLICA CADA MOVIMIENTO GRANDE (mismas filas de §4, por transición)")
    fi_hoy = ctxs["hoy"]["fichas"]
    for c in CADENAS:
        for prod in CANASTA:
            nombre = prod["nombre"]
            d_hoy = U.fila_ficha(fi_hoy[c], nombre)
            sub_hoy = d_hoy["subtotal"] if d_hoy else 0.0
            movida = False
            for dest in (SIN_X07, "los tres"):
                d_dest = U.fila_ficha(ctxs[dest]["fichas"][c], nombre)
                sub_dest = d_dest["subtotal"] if d_dest else 0.0
                movida |= bool(sub_hoy) and (sub_dest / sub_hoy > 2 or sub_dest / sub_hoy < 0.5)
            if not movida:
                continue
            d_tres = U.fila_ficha(ctxs["los tres"]["fichas"][c], nombre)
            sub_tres = d_tres["subtotal"] if d_tres else 0.0
            print(f"\n    [{nombre}] {c}: ${sub_hoy:,.2f} → ${sub_tres:,.2f}")
            anterior = sub_hoy
            for n, _ in ESCENARIOS[1:]:
                d = U.fila_ficha(ctxs[n]["fichas"][c], nombre)
                sub = d["subtotal"] if d else 0.0
                causa = {"solo S-escala": "S-escala", "S-escala+S-filtro": "S-filtro",
                         SIN_X07: "×0,7", "los tres": "+0,20"}[n]
                if sub != anterior:
                    print(f"      {causa:<10} ${anterior:,.2f} → ${sub:,.2f} "
                          f"(×{sub / anterior if anterior else float('inf'):.2f})")
                else:
                    print(f"      {causa:<10} sin cambio (${sub:,.2f})")
                anterior = sub


YERBA_CHANGO = ("Chango Más", "Yerba mate", "Yerba Mate Buen Dia 1 Kg", 2799.0)


def regla_b(ctxs, filas):
    P.barra(f"§9 — {SIN_X07}: LA YERBA DE CHANGO MÁS Y QUÉ HACE EL ×0,7 SOLO",
            "El +0,20 satura en 1,0: entre conmensurables empata los scores y decide el precio. "
            "Si la yerba de Chango Más se mueve con el +0,20 puesto, el mecanismo está mal leído.")
    c, q, titulo, precio = YERBA_CHANGO
    for n in (IMPLEMENTADA, SIN_X07):
        f = ctxs[n]["elegido"].get((c, q))
        ok = bool(f) and f["of"].producto == titulo and float(f["of"].precio) == precio
        real = f"{f['of'].producto!r} ${f['of'].precio:,.2f}" if f else "sin representante"
        print(f"\n    {c}/{q} en {n}: {real} — "
              + ("OK, no se mueve" if ok else f"⚠ SE MOVIÓ (esperado {titulo!r} ${precio:,.2f})"))
    prod = next(p for p in CANASTA if p["nombre"] == q)
    print("    Candidatos con score ≥ umbral, con y sin el +0,20 (siempre sin el ×0,7):")
    for g in U.candidatos(filas, c, q):
        s_con = puntuar_variante(prod, g["of"], main.normalizar, main._extraer_cantidades_desc,
                                 con_bonus=True, con_castigo=False)
        s_sin = puntuar_variante(prod, g["of"], main.normalizar, main._extraer_cantidades_desc,
                                 con_bonus=False, con_castigo=False)
        if max(s_con, s_sin) >= UMBRAL_MATCH:
            print(f"      {g['of'].producto!r:<48} ${g['of'].precio:>10,.2f}  con +0,20 {s_con:.3f} · "
                  f"sin {s_sin:.3f}")

    print(f"\n    El ×0,7 solo (S-escala+S-filtro → {SIN_X07}): representantes que cambian, con el score "
          "de los dos candidatos con y sin el ×0,7:")
    alguno = False
    for prod in CANASTA:
        nombre = prod["nombre"]
        for c in CADENAS:
            fa = ctxs["S-escala+S-filtro"]["elegido"].get((c, nombre))
            fb = ctxs[SIN_X07]["elegido"].get((c, nombre))
            if (fa and fa["of"].producto) == (fb and fb["of"].producto):
                continue
            alguno = True
            print(f"      [{nombre}] {c}")
            for rot, g in (("antes", fa), ("después", fb)):
                if not g:
                    print(f"        {rot:<8} —")
                    continue
                s1 = puntuar_variante(prod, g["of"], main.normalizar, main._extraer_cantidades_desc)
                s2 = puntuar_variante(prod, g["of"], main.normalizar, main._extraer_cantidades_desc,
                                      con_bonus=True, con_castigo=False)
                pu = g["pu"]
                cont = f"{pu['cantidad_base']:g} {pu['unidad_base']}" if pu else "sin métrica"
                print(f"        {rot:<8} {g['of'].producto!r:<48} ${g['of'].precio:>10,.2f} ({cont})  "
                      f"con ×0,7 {s1:.3f} · sin {s2:.3f}")
    if not alguno:
        print("      ninguno: el ×0,7 solo no cambia ningún representante en esta captura")


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
        val = validar(filas, dia)
    except InstrumentoInvalido as e:
        print(f"ERROR (InstrumentoInvalido):\n  {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Captura: {cap['__fecha']} · {cap.get('__origen')} · día {dia} (0=lunes)")
    print(f"Canasta: CANASTA_DEFAULT, {len(CANASTA)} ítems")
    print("Instrumento validado:")
    print(f"  (1) puntuar_variante(con +0,20 y con ×0,7) == puntuar en los {val['n_candidatos_comparados']} candidatos")
    print(f"  (2) seleccionar(S-filtro) == producción en los {val['n_representantes']} representantes")
    print(f"  (2) con S-filtro, mismos sin_elegible y mismos envases que producción")
    print(f"  (3) OBLIGATORIA: '{IMPLEMENTADA}' simulado == código real, ficha por ficha")
    print("  (4) 'hoy' simulado == la columna 'hoy' documentada antes de implementar")

    ctxs = {n: correr_escenario(CANASTA, filas, dia, **kw) for n, kw in ESCENARIOS}

    resumen_por_cadena(ctxs)
    orden_cadenas(ctxs)
    mediana_por_producto(ctxs)
    movimientos_grandes(ctxs)
    sin_candidato_elegible(ctxs)
    cambios_de_representante(ctxs)
    piso_brecha(ctxs)
    atribucion(ctxs)
    regla_b(ctxs, filas)


if __name__ == "__main__":
    main_cli()
