"""
Tarea 26 paso (e) = Tarea 22 como medición propia (21/09/2026) — ¿qué separa, POR CADENA, un buen
representante de uno malo?

Instrumento de MEDICIÓN, fuera del CI. SIN RED sobre la captura del 14/09
(`tests/capturas/canasta_default_2026-09-14.json`); con red SOLO en `--capturar`, que corre Santiago.
No toca main.py, fuentes.py ni precios_vtex.py.

Pasan el umbral productos equivocados que la regla de envases de (b) escala sin arreglar: mermelada por
azúcar (Coto, 0,46), medialunas por manteca, picada de cerdo, Finish en tabletas por detergente, huevos
de pascua, pan de pancho por pan lactal. Este instrumento mide, contra ETIQUETAS escritas a ciegas
(`tests/capturas/etiquetas_2026-09-14.json`: el etiquetador vio el criterio del ítem, el título y la
marca; no la cadena, el score, el precio ni el EAN), qué señal los separa y a qué costo, por cadena.

Cómo corre cada variante (sin una tercera copia del lazo de selección que no se valide):
  · `seleccionar` es el lazo de `precios_vtex.buscar_precios_online` (clave (elegible, s, −precio),
    estricto `>`, orden de la captura) parametrizado por puntaje, umbral, filtro y clave.
  · Toda variante que producción puede correr —un filtro (una Fuente que descarta ofertas), un umbral
    (`umbral=`) o un puntaje (`precios_vtex.puntuar` parcheado en memoria)— se corre TAMBIÉN por el
    `buscar_precios_online` real y las entradas tienen que dar idénticas, campo por campo.
  · Solo las claves por precio (paso 4) no tienen equivalente en producción: esas se validan porque el
    mismo lazo, con la clave de hoy, reproduce producción (entradas, fichas y el objeto elegido).

Salida: 0 medición completa · 1 el instrumento no reproduce lo que dice medir · 2 captura incompleta.

Desde sepa_backend/ (en Windows, con `$env:PYTHONIOENCODING='utf-8'`):
    venv\\Scripts\\python.exe -m tests.medir_representantes                       # medición, sin red
    venv\\Scripts\\python.exe -m tests.medir_representantes --a-etiquetar SALIDA.json  # qué etiquetar
    venv\\Scripts\\python.exe -m tests.medir_representantes --capturar tests/capturas/representantes_AAAA-MM-DD.json
"""
import argparse
import json
import logging
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import requests

import main
import precios_vtex
from fuentes import UA, VTEX_BASES, FuenteCoto, FuenteVTEX, Resultado
from precios_vtex import UMBRAL_MATCH, buscar_precios_online, elegible, envases_a_comprar
from tests import evaluar_matching as EM
from tests import medir_metrica_vivo as M
from tests import medir_pesables as P
from tests import medir_regla_26b as R
from tests import medir_unidad_pedida as U
from tests.medir_regresion import FIXTURES

RAIZ = Path(__file__).resolve().parents[1]
CAPTURA = U.CAPTURA
FECHA_CAPTURA = "2026-09-14T00:26:24"
ETIQUETAS = RAIZ / "tests" / "capturas" / "etiquetas_2026-09-14.json"
CADENAS, VTEX, CANASTA = U.CADENAS, U.VTEX, U.CANASTA
ITEM = {p["nombre"]: p for p in CANASTA}
NORM, EXTRAER = main.normalizar, main._extraer_cantidades_desc
# Los 4 ítems donde Coto no tenía un representante correcto en la captura del 14/09 (3 faltantes y la
# mermelada; Tarea 26, "Paso (c)" y "Fallas que encontró el paso 1"). **Queda congelada**: es la lista con la
# que se armó el pool etiquetado del 14/09, así que cambiarla cambiaría qué filas tienen etiqueta.
# ✏️ Revisión de Santiago (22/09): para los informes la lista se calcula POR FECHA con `items_sin_correcto`,
# porque el 22/09 a Coto le faltan Huevos, Pollo, Carne picada, Detergente y Atún, y Azúcar sí tiene
# representante (dudoso).
COTO_FALLA_14SEP = ["Azúcar", "Huevos", "Pollo entero", "Carne picada"]
COTO_FALLA = COTO_FALLA_14SEP


def items_sin_correcto(elegido, et_fn, cadena="Coto"):
    """Los ítems donde esa cadena no tiene representante, o el que tiene no está etiquetado correcto."""
    out = []
    for prod in CANASTA:
        par = elegido.get((cadena, prod["nombre"]))
        if par is None or et_fn(par[0]["cid"]) != "correcto":
            out.append(prod["nombre"])
    return out
UMBRALES = [0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80]

# Criterios por ítem: qué cuenta como el producto. Aprobados por Santiago con el plan de (e), 21/09, ANTES
# de etiquetar. Regla general: una variante del mismo producto (marca, tamaño, sin sal, light) es correcta;
# el pack va en una nota aparte (no es identidad).
CRITERIOS = {
    "Leche entera":       ("leche de vaca entera fluida, también sin lactosa",
                           "descremada, en polvo, chocolatada, vegetal", ""),
    "Pan lactal":         ("pan de molde lactal: blanco, integral o salvado",
                           "pan de pancho, pan de hamburguesa, pan rallado, tostadas", ""),
    "Arroz largo fino":   ("arroz blanco largo fino",
                           "integral, yamaní, doble carolina, preparados", "parboil largo fino"),
    "Fideos spaghetti":   ("spaghetti o tallarín seco",
                           "otras formas, frescos, sopas instantáneas", "integrales o de verdura"),
    "Aceite girasol":     ("girasol, también alto oleico", "mezcla, maíz, oliva, aerosol", ""),
    "Azúcar":             ("azúcar común blanca", "edulcorante, mermelada, golosinas",
                           "rubia, mascabo, orgánica, impalpable"),
    "Yerba mate":         ("yerba mate, con o sin palo", "mate cocido en saquitos, té",
                           "compuesta o con hierbas"),
    "Café molido":        ("café tostado o torrado molido", "instantáneo, cápsulas, en grano",
                           "molido en saquitos"),
    "Harina 000":         ("harina de trigo 000", "0000, leudante, premezclas, harina de maíz", ""),
    "Manteca":            ("manteca de leche",
                           "margarina, medialunas, galletitas \"sabor manteca\", cosmética", ""),
    "Huevos":             ("huevos frescos de gallina",
                           "de pascua o de chocolate, pastas al huevo, incubadora", "de codorniz, en polvo"),
    "Pollo entero":       ("pollo entero, fresco o congelado",
                           "presas, elaborados, alimento para mascotas", ""),
    "Carne picada":       ("carne vacuna picada", "de cerdo, de pollo, hamburguesas",
                           "picada sin especie en el título, hasta tener la categoría"),
    "Detergente":         ("lavavajillas líquido, para lavar a mano",
                           "pastillas o polvo para lavavajillas de máquina, detergente para ropa", ""),
    "Jabón polvo":        ("jabón en polvo para ropa",
                           "jabón líquido para ropa, lavavajillas en polvo, jabón de tocador", ""),
    "Papel higienico":    ("papel higiénico en rollos", "portarrollos, servilletas, rollo de cocina", ""),
    "Shampoo":            ("shampoo para el cabello", "acondicionador, shampoo para mascotas o autos",
                           "2 en 1, kits"),
    "Tomate perita lata": ("tomate perita en conserva: entero, cubeteado o triturado",
                           "tomate fresco, salsa, ketchup", "puré o pulpa en caja"),
    "Atún natural":       ("atún al natural en lata", "al aceite, otros pescados", ""),
    "Gaseosa cola":       ("gaseosa cola: común, light o zero", "otros sabores, agua saborizada", ""),
}


class InstrumentoInvalido(Exception):
    pass


# ─────────────────────────────────────────────────────────────────
#  TEXTO — lo mismo que mira `puntuar`, sin vocabulario nuevo
# ─────────────────────────────────────────────────────────────────

def lista_tokens(texto):
    """Los tokens de `precios_vtex._tokens` (normalizados, > 2 letras), EN ORDEN y con repetidos."""
    return [t for t in re.split(r"\W+", NORM(texto or "")) if len(t) > 2]


def raiz(t):
    """Singular mínimo, sin vocabulario: "medialunas" → "medialuna". Solo para comparar palabras."""
    return t[:-1] if len(t) > 3 and t.endswith("s") else t


def sin_tildes(s):
    return "".join(ch for ch in unicodedata.normalize("NFD", s) if unicodedata.category(ch) != "Mn")


def contigua(frase, titulo):
    """Los tokens de `frase` aparecen seguidos y en orden entre los tokens del título."""
    f, t = lista_tokens(frase), lista_tokens(titulo)
    return bool(f) and any(t[i:i + len(f)] == f for i in range(len(t) - len(f) + 1))


def negativas(item):
    """
    N-criterio: palabras de la columna "otro producto" de CRITERIOS, MECÁNICAMENTE — menos las que aparecen
    en la columna "correcto" y las del nombre del ítem. Escritas antes de etiquetar, pero conociendo los 6
    casos documentados: para esos 6 es in-sample (se dice en el informe).
    """
    ok, otro, _dudoso = CRITERIOS[item]
    fuera = {raiz(t) for t in lista_tokens(ok) + lista_tokens(item)}
    genericas = {"otra", "otro", "forma", "para", "con", "sin", "sabor"}
    return sorted({raiz(t) for t in lista_tokens(otro)} - fuera - genericas)


def sobrantes(prod, of, sin_marca=False, sin_numeros=False):
    """Las palabras del título que no son del ítem: S0 (la precisión de hoy), S1 (sin la marca), S2 (y sin números)."""
    t_item = precios_vtex._tokens(prod["nombre"], NORM)
    s = precios_vtex._tokens(of.producto, NORM) - t_item
    if sin_marca and of.marca:
        s -= precios_vtex._tokens(of.marca, NORM)
    if sin_numeros:
        s = {t for t in s if not any(ch.isdigit() for ch in t)}
    return s


def manda(prod, of):
    """S3: la primera palabra del título que no es de la marca ni tiene números, ¿es una palabra del ítem?"""
    t_item = {raiz(t) for t in lista_tokens(prod["nombre"])}
    marca = set(lista_tokens(of.marca or ""))
    for t in lista_tokens(of.producto):
        if t in marca or any(ch.isdigit() for ch in t):
            continue
        return raiz(t) in t_item
    return False


# ─────────────────────────────────────────────────────────────────
#  EAN
# ─────────────────────────────────────────────────────────────────

def ean_cruzable(ean):
    """EAN normalizado (sin ceros a la izquierda) si se puede cruzar entre cadenas; None si no (código de
    tienda, no numérico, o de largo no GTIN). Código de tienda: EAN-13 con prefijo 2 o 02, y UPC-A (12
    dígitos) con prefijo 2 — el mismo código de peso variable escrito como GTIN-12. En la captura del 14/09
    no hay ninguno de 12/02; se cubren por la captura nueva."""
    e = str(ean or "").strip()
    if not e.isdigit() or len(e) not in (8, 12, 13, 14):
        return None
    if (len(e) == 13 and (e.startswith("2") or e.startswith("02"))) or (len(e) == 12 and e.startswith("2")):
        return None
    return e.lstrip("0") or None


# ─────────────────────────────────────────────────────────────────
#  PUNTAJES, CLAVES Y EL LAZO DE SELECCIÓN
# ─────────────────────────────────────────────────────────────────

def s_hoy(prod, of):
    return U.score(prod, of)


def s_sin_tamano(prod, of):
    """`puntuar` sin el +0,20 ni el ×0,7 (conserva el ×0,5 de tipo distinto). Validado == puntuar con las dos llaves."""
    return R.puntuar_variante(prod, of, NORM, EXTRAER, con_bonus=False, con_castigo=False)


def clave_hoy(prod, f, s, ok):
    return (ok, s, -f["of"].precio)


def costo(prod, f):
    """Lo que se paga por lo pedido con ESTE candidato: precio × envases_a_comprar (None si no puede contestar)."""
    env = envases_a_comprar(prod, f["of"], f["pu"], EXTRAER)
    return None if env is None else f["of"].precio * env


def clave_precio(solo_kg_l):
    """
    P1: entre elegibles, gana el que menos cuesta por lo pedido (decisión 2 de la Tarea 26); a igual costo,
    el mejor score. `solo_kg_l`: en unidad/pack el costo es el precio del envase —sin leer el pack del
    título no hay "precio por lo pedido" (decisión 5)— y queda el orden de hoy.
    """
    def k(prod, f, s, ok):
        if solo_kg_l and not precios_vtex._pide_contenido(prod):
            return clave_hoy(prod, f, s, ok)
        c = costo(prod, f) if ok else None
        return (ok, -c if c is not None else 0.0, s, -f["of"].precio)
    return k


def seleccionar(filas, canasta=CANASTA, puntaje=s_hoy, umbral=UMBRAL_MATCH, filtro=None, clave=clave_hoy):
    """{(cadena, ítem): (fila, score)} — el lazo de `buscar_precios_online`, parametrizado."""
    elegido = {}
    for prod in canasta:
        q = prod["nombre"]
        for c in CADENAS:
            mejor = None
            for f in filas[c].get(q, []):
                of = f["of"]
                if not of.disponible or of.precio <= 0:
                    continue
                if filtro is not None and not filtro(prod, f):
                    continue
                s = puntaje(prod, of)
                if s < umbral:
                    continue
                ok = elegible(prod, of, f["pu"], EXTRAER)
                k = clave(prod, f, s, ok)
                if mejor is None or k > mejor[0]:
                    mejor = (k, s, f)
            if mejor:
                elegido[(c, q)] = (mejor[2], mejor[1])
    return elegido


def entrada(prod, f, s):
    """La entrada de `precios` que arma `buscar_precios_online` para el candidato elegido."""
    of, pu = f["of"], f["pu"]
    env = envases_a_comprar(prod, of, pu, EXTRAER)
    return {"precio_min": float(of.precio), "precio_por_100u": pu, "precio_lista": of.precio_lista,
            "descuento_pct": of.descuento_pct, "promos": of.promos, "ean": of.ean, "match_score": s,
            "origen": of.origen, "envases": env, "sin_elegible": env is None}


def precios_de(elegido):
    return {(c, q): entrada(ITEM[q], f, s) for (c, q), (f, s) in elegido.items()}


class FuenteFiltrada:
    """Una Fuente sobre la captura que descarta las ofertas que no pasan `filtro(prod, fila)`."""
    nombre = "filtrada"

    def __init__(self, filas, filtro):
        self.filas, self.filtro = filas, filtro

    def cadenas_soportadas(self):
        return list(CADENAS)

    def buscar(self, query, cadenas=None):
        prod = ITEM[query]
        objetivo = [c for c in (cadenas or CADENAS) if query in self.filas.get(c, {})]
        res = Resultado(consultadas=objetivo)
        for c in objetivo:
            res.ofertas.extend(f["of"] for f in self.filas[c][query]
                               if self.filtro is None or self.filtro(prod, f))
        return res


def produccion(filas, puntaje=None, umbral=UMBRAL_MATCH, filtro=None):
    """La misma variante corrida por el `buscar_precios_online` REAL."""
    original = precios_vtex.puntuar
    if puntaje is not None:
        precios_vtex.puntuar = lambda item, of, normalizar, extraer=None: puntaje(item, of)
    try:
        precios, _ = buscar_precios_online(CANASTA, CADENAS, FuenteFiltrada(filas, filtro), NORM, EXTRAER,
                                           umbral=umbral)
    finally:
        precios_vtex.puntuar = original
    return precios


def fichas_de(precios, dia):
    res = main._analizar(CANASTA, precios, main.PROMOS_DEFAULT, dia, None)
    prom = main._promedios_por_producto(CANASTA, precios)
    return prom, {fi["cadena"]: fi for fi in main._fichas(CANASTA, precios, res, prom, list(CADENAS))}


# ─────────────────────────────────────────────────────────────────
#  VARIANTES
# ─────────────────────────────────────────────────────────────────

def cruzables_aceptados(filas):
    """{(ítem, ean normalizado): {cadenas donde un candidato con ese EAN pasa el umbral de hoy}}."""
    out = defaultdict(set)
    for c in CADENAS:
        for prod in CANASTA:
            for f in U.candidatos(filas, c, prod["nombre"]):
                e = ean_cruzable(f["of"].ean)
                if e and s_hoy(prod, f["of"]) >= UMBRAL_MATCH:
                    out[(prod["nombre"], e)].add(c)
    return out


def variantes(filas):
    """
    Nombre → kwargs de `seleccionar`, y si producción la puede correr (`prod`: kwargs de `produccion`).
    Las por ítem (N, K) usan datos que solo tiene CANASTA_DEFAULT: el frontend manda `categoria: 'Otro'` y
    `palabras_clave: [nombre]` para lo que arma el usuario (BasketScreen.jsx:13,230,261).
    """
    cruz = cruzables_aceptados(filas)

    def ean_otra(prod, f):
        e = ean_cruzable(f["of"].ean)
        return bool(e) and bool(cruz.get((prod["nombre"], e), set()) - {f["c"]})

    filtros = {
        "S3 manda":        ("genérica", lambda p, f: manda(p, f["of"])),
        "S2 ≤ 1":          ("genérica", lambda p, f: len(sobrantes(p, f["of"], True, True)) <= 1),
        "S2 ≤ 2":          ("genérica", lambda p, f: len(sobrantes(p, f["of"], True, True)) <= 2),
        "S2 ≤ 3":          ("genérica", lambda p, f: len(sobrantes(p, f["of"], True, True)) <= 3),
        "K0 nombre frase": ("genérica", lambda p, f: contigua(p["nombre"], f["of"].producto)),
        "N criterio":      ("por ítem", lambda p, f: not ({raiz(t) for t in lista_tokens(f["of"].producto)}
                                                          & set(negativas(p["nombre"])))),
        "K1 clave tokens": ("por ítem", lambda p, f: any(set(lista_tokens(k)) <= set(lista_tokens(f["of"].producto))
                                                         for k in (p.get("palabras_clave") or [p["nombre"]]))),
        "K2 clave frase":  ("por ítem", lambda p, f: U.proxy_kw(p, f["of"].producto)),
    }
    v = {"hoy": ("—", {}, {})}
    for n, (tipo, fil) in filtros.items():
        v[n] = (tipo, {"filtro": fil}, {"filtro": fil})
    v["sin tamaño"] = ("genérica", {"puntaje": s_sin_tamano}, {"puntaje": s_sin_tamano})
    v["E desempate"] = ("genérica", {"clave": lambda p, f, s, ok: (ok, s, ean_otra(p, f), -f["of"].precio)}, None)
    v["P1 kg/l"] = ("genérica", {"clave": clave_precio(True)}, None)
    v["P1 todo"] = ("genérica", {"clave": clave_precio(False)}, None)
    v["P1 kg/l sin tamaño"] = ("genérica", {"clave": clave_precio(True), "puntaje": s_sin_tamano}, None)
    for u in UMBRALES[1:]:
        v[f"umbral {u:.2f}"] = ("genérica", {"umbral": u}, {"umbral": u})
        v[f"sin tamaño, umbral {u:.2f}"] = ("genérica", {"puntaje": s_sin_tamano, "umbral": u},
                                           {"puntaje": s_sin_tamano, "umbral": u})
        v[f"P1 kg/l, umbral {u:.2f}"] = ("genérica", {"clave": clave_precio(True), "umbral": u}, None)
    return v


# ─────────────────────────────────────────────────────────────────
#  CARGA Y VALIDACIÓN
# ─────────────────────────────────────────────────────────────────

def cargar(ruta=CAPTURA):
    with open(ruta, encoding="utf-8") as fh:
        cap = json.load(fh)
    filas = P.parsear(cap["datos"])
    for c in CADENAS:
        for q, fs in filas[c].items():
            indice = {id(raw): i for i, raw in enumerate(cap["datos"][c][q])}
            for f in fs:
                f["cid"] = f"{c}|{q}|{indice[id(f['raw'])]}"
    return cap, filas


def validar(cap, filas, dia):
    """(1) captura y código ya validados · (2) el lazo == producción, en cada variante que producción corre."""
    if cap.get("__fecha") != FECHA_CAPTURA:
        raise InstrumentoInvalido(f"(1) la captura es {cap.get('__fecha')}; este instrumento mide {FECHA_CAPTURA}")
    try:
        ctx = U.validar(cap, filas, dia)
    except U.InstrumentoInvalido as e:
        raise InstrumentoInvalido(f"(1) medir_unidad_pedida.validar: {e}")

    errores = []
    # (2a) El lazo con la clave de hoy == producción: entradas completas, fichas y el objeto elegido.
    hoy = seleccionar(filas)
    p_hoy = precios_de(hoy)
    if p_hoy != ctx["precios"]:
        dif = [k for k in set(p_hoy) | set(ctx["precios"]) if p_hoy.get(k) != ctx["precios"].get(k)]
        errores.append(f"(2a) el lazo no reproduce producción en {sorted(dif)[:6]}")
    _prom, fi = fichas_de(p_hoy, dia)
    if fi != ctx["hoy"]:
        errores.append("(2a) las fichas del lazo no son las de producción")
    for k, (f, _s) in hoy.items():
        if f is not ctx["reps"].get(k):
            errores.append(f"(2a) {k}: el lazo elige otra fila que la que encontró medir_unidad_pedida")
    # (2b) puntuar_variante con las dos llaves == puntuar, en todos los candidatos.
    for c in CADENAS:
        for prod in CANASTA:
            for f in U.candidatos(filas, c, prod["nombre"]):
                a = R.puntuar_variante(prod, f["of"], NORM, EXTRAER, True, True)
                if a != s_hoy(prod, f["of"]):
                    errores.append(f"(2b) {c}/{prod['nombre']}: puntuar_variante {a} ≠ puntuar")
    # (2c) Cada variante que producción puede correr, corrida por las dos vías, da lo mismo.
    elegidos = {}
    for nombre, (_tipo, kw, kw_prod) in variantes(filas).items():
        elegidos[nombre] = seleccionar(filas, **kw)
        if kw_prod is not None:
            if precios_de(elegidos[nombre]) != produccion(filas, **kw_prod):
                errores.append(f"(2c) '{nombre}': el lazo y buscar_precios_online no eligen lo mismo")
    if errores:
        raise InstrumentoInvalido("\n  ".join(errores[:20]))
    ctx["elegidos"] = elegidos
    return ctx


# ─────────────────────────────────────────────────────────────────
#  ETIQUETAS — se cargan, se validan y no se tocan
# ─────────────────────────────────────────────────────────────────

ETQ = ("correcto", "otro producto", "dudoso")


def cargar_etiquetas(filas, elegidos, reps, ruta=ETIQUETAS):
    """
    {cid: fila etiquetada}. Validaciones (3) etiquetas frescas, (4) cierre y (5) consistencia por EAN.
    Las etiquetas son el patrón de oro de esta medición: si no describen a ESTOS candidatos, todo lo que
    sigue mide otra cosa.
    """
    if not ruta.exists():
        raise InstrumentoInvalido(f"faltan las etiquetas: {ruta}")
    doc = json.load(open(ruta, encoding="utf-8"))
    if doc.get("__fecha_captura") != FECHA_CAPTURA:
        raise InstrumentoInvalido(f"las etiquetas son de {doc.get('__fecha_captura')}, la captura de {FECHA_CAPTURA}")
    etq = {x["cid"]: x for x in doc["candidatos"]}
    errores = []

    # (3) Frescas: título, EAN y precio de la etiqueta == los del candidato de la captura.
    porcid = {f["cid"]: f for c in CADENAS for fs in filas[c].values() for f in fs}
    for cid, x in etq.items():
        if cid.split("|")[0] not in CADENAS:   # fix0909, profundo, coto_no24, alternativa: ver (3b)
            continue
        f = porcid.get(cid)
        if f is None:
            errores.append(f"(3) {cid}: la etiqueta no corresponde a ningún candidato de la captura")
            continue
        of = f["of"]
        if (of.producto, str(of.ean or ""), float(of.precio)) != (x["titulo"], str(x["ean"] or ""), float(x["precio"])):
            errores.append(f"(3) {cid}: la etiqueta describe otro candidato ({x['titulo'][:40]!r} "
                           f"${x['precio']} vs {of.producto[:40]!r} ${of.precio})")
        if x["etiqueta"] not in ETQ:
            errores.append(f"(3) {cid}: etiqueta {x['etiqueta']!r} fuera de {ETQ}")

    # (4) Cierre: todo representante de hoy y todo elegido por cualquier variante tiene etiqueta.
    falta_rep = [f["cid"] for f in reps.values() if f["cid"] not in etq]
    if falta_rep:
        errores.append(f"(4) representantes sin etiqueta: {sorted(falta_rep)[:6]}")
    falta_el = {f["cid"] for el in elegidos.values() for f, _s in el.values()} - set(etq)
    if falta_el:
        errores.append(f"(4) elegidos por alguna variante sin etiqueta: {sorted(falta_el)[:6]}")
    # El oráculo elige entre los elegibles que pasan el umbral: todos están en el pool etiquetado.
    falta_pool = []
    for c in CADENAS:
        for prod in CANASTA:
            for f in U.candidatos(filas, c, prod["nombre"]):
                if max(s_hoy(prod, f["of"]), s_sin_tamano(prod, f["of"])) >= UMBRAL_MATCH and f["cid"] not in etq:
                    falta_pool.append(f["cid"])
    if falta_pool:
        errores.append(f"(4) candidatos del pool sin etiqueta: {sorted(falta_pool)[:6]}")

    # (5) Consistencia por EAN: mismo ítem + mismo EAN cruzable → misma etiqueta. Las excepciones son
    # las que el archivo documenta (títulos de cadenas que se contradicen), ni una más ni una menos.
    por_ean = defaultdict(set)
    for x in etq.values():
        e = ean_cruzable(x["ean"])
        if e:
            por_ean[f"{x['item']}|{e}"].add(x["etiqueta"])
    vistas = {k for k, v in por_ean.items() if len(v) > 1}
    documentadas = set(doc.get("__inconsistencias_ean") or {})
    if vistas != documentadas:
        errores.append(f"(5) inconsistencias por EAN no documentadas: {sorted(vistas - documentadas)} "
                       f"· documentadas que ya no están: {sorted(documentadas - vistas)}")
    if errores:
        raise InstrumentoInvalido("\n  ".join(errores[:20]))
    return etq, doc


def et(etq, cid):
    x = etq.get(cid)
    return x["etiqueta"] if x else None


# (7) Lo que esta captura da, fijado después de la primera corrida: la base de toda la medición. Si cambia
# el matcher, las etiquetas o la captura, esto sale en rojo y hay que mirarlo A MANO, no actualizarlo solo.
ESPERADO_E = {
    "2026-09-14T00:26:24": {
        # cadena: correctos, otro producto, dudoso, sin representante (sobre los 20 ítems)
        # ✏️ 22/09: con las correcciones de Santiago la (8) salió EN ROJO —Carrefour 17 correctos contra 16 y
        # Chango Más 19 contra 18, porque La Cumbrecita y Buen Día pasaron a dudoso— y se actualizó a mano
        # después de mirar cada fila. Eso es lo que esta validación busca: fricción, no un número que se
        # ajusta solo.
        "reps": {"Carrefour": (16, 3, 1, 0), "Día": (18, 2, 0, 0), "Vea": (15, 3, 1, 1),
                 "Chango Más": (18, 1, 1, 0), "Coto": (13, 3, 1, 3)},
        # cadena: aceptados, disponibles (captura del 14/09, 20 consultas)
        "aceptacion": {"Carrefour": (494, 591), "Día": (311, 393), "Vea": (374, 424),
                       "Chango Más": (524, 578), "Coto": (217, 396)},
    }
}


def validar_respuestas_conocidas(filas, etq, hoy):
    """(7) Dos respuestas que ya se conocen, sobre filas reales."""
    errores = []
    if ("Coto", "Azúcar") in seleccionar(filas, umbral=0.47):
        errores.append("(7) con umbral 0,47 Coto/Azúcar sigue teniendo representante (la mermelada saca 0,46)")
    cid_med = hoy[("Carrefour", "Manteca")][0]["cid"]
    par = seleccionar(filas, filtro=lambda p, f: f["cid"] != cid_med).get(("Carrefour", "Manteca"))
    if par is None:
        errores.append("(7) sin las medialunas, Carrefour/Manteca queda sin representante")
    elif et(etq, par[0]["cid"]) is None:
        errores.append("(7) el candidato que sigue a las medialunas de Carrefour no tiene etiqueta")
    sigue = None if par is None else (par[0]["of"].producto, et(etq, par[0]["cid"]), par[0]["of"].precio)
    return errores, sigue


def validar_esperado_e(cap, etq, hoy, bases):
    """(8) Los números de esta captura son los documentados en la Tarea 22."""
    esp = ESPERADO_E.get(cap.get("__fecha"))
    if not esp:
        return [], f"NO APLICA: no hay números de referencia para la captura {cap.get('__fecha')}"
    errores = []
    for c, doc in esp["reps"].items():
        n = Counter(et(etq, f["cid"]) or "sin etiqueta" for (cc, q), (f, _s) in hoy.items() if cc == c)
        falt = len(CANASTA) - sum(n.values())
        got = (n["correcto"], n["otro producto"], n["dudoso"], falt)
        if got != tuple(doc):
            errores.append(f"(8) {c}: representantes {got}, documentado {tuple(doc)}")
    for c, (a, d) in esp["aceptacion"].items():
        x = bases[1][1][c]
        if (x["aceptados"], x["disponibles"]) != (a, d):
            errores.append(f"(8) {c}: aceptación {x['aceptados']}/{x['disponibles']}, documentada {a}/{d}")
    return errores, ("OK: los representantes etiquetados y la aceptación por cadena son los documentados "
                     "en la Tarea 22")


# ─────────────────────────────────────────────────────────────────
#  §A — ACEPTACIÓN POR CADENA (paso 3; NO usa etiquetas)
# ─────────────────────────────────────────────────────────────────

# Lo que documenta `test_unidades.py:159` (medido el 12/09, después del hotfix de 'pack'), sobre las
# fixtures del 09/09 y las 6 consultas de `evaluar_matching.CANASTA`. La Tarea 22 arranca de acá: el
# 43,2% de Coto contra 78,7–85,3% de las VTEX.
ACEPTACION_0909 = {"Carrefour": 78.7, "Chango Más": 85.3, "Coto": 43.2, "Día": 83.3, "Vea": 83.1}
COTO_0909 = (48, 111)
CANASTA_6 = EM.CANASTA
Q6 = [p["nombre"] for p in CANASTA_6]
# Las 6 son las mismas filas de `CANASTA_DEFAULT` (mismo `cantidad` y `unidad`; `puntuar` no mira
# `palabras_clave` ni `categoria`), así que entre la base 1 y la base 3 lo único que cambia son los datos.


def aceptacion(ofertas, canasta):
    """
    Por cadena, con TRES denominadores: `crudos` (todo lo que devolvió el parser), `con_precio`
    (precio > 0) y `disponibles` (lo que mira el matcher: disponible y precio > 0). `aceptados` son los
    que llegan a `UMBRAL_MATCH` con el score de hoy, contados sobre los disponibles — la misma cuenta
    que `test_unidades._estado_matching`. `sin_disponible` es dato de la cadena y no del matcher: el
    parser de Coto nunca pone `disponible` en falso, así que los denominadores no son comparables sin
    decirlo.
    """
    out = {}
    for c, porq in ofertas.items():
        d = dict(crudos=0, con_precio=0, disponibles=0, sin_disponible=0, sin_precio=0, aceptados=0,
                 items=0, items_con_aceptado=0, por_item={})
        for prod in canasta:
            ofs = porq.get(prod["nombre"], [])
            vivas = [o for o in ofs if o.disponible and o.precio > 0]
            acept = [o for o in vivas if s_hoy(prod, o) >= UMBRAL_MATCH]
            d["crudos"] += len(ofs)
            d["con_precio"] += sum(1 for o in ofs if o.precio > 0)
            d["sin_precio"] += sum(1 for o in ofs if o.precio <= 0)
            d["sin_disponible"] += sum(1 for o in ofs if not o.disponible)
            d["disponibles"] += len(vivas)
            d["aceptados"] += len(acept)
            d["items"] += 1
            d["items_con_aceptado"] += bool(acept)
            d["por_item"][prod["nombre"]] = (len(acept), len(vivas), len(ofs))
        out[c] = d
    return out


def ofertas_captura(filas, canasta):
    """{cadena: {consulta: [Oferta]}} desde la captura, con el parser de producción."""
    return {c: {p["nombre"]: [f["of"] for f in filas[c].get(p["nombre"], [])] for p in canasta}
            for c in CADENAS}


def bases_aceptacion(filas):
    """Las tres bases del paso 3, para separar el efecto de la fecha del de las consultas."""
    return [("fixtures 09/09 · 6 consultas", aceptacion(EM.cargar(FIXTURES), CANASTA_6)),
            ("captura 14/09 · 20 consultas", aceptacion(ofertas_captura(filas, CANASTA), CANASTA)),
            ("captura 14/09 · las mismas 6", aceptacion(ofertas_captura(filas, CANASTA_6), CANASTA_6))]


def validar_aceptacion(bases):
    """(6) Respuesta conocida: la base de las fixtures tiene que reproducir lo que documenta el guard."""
    errores = []
    _nombre, a = bases[0]
    for c, esp in ACEPTACION_0909.items():
        t = round(100.0 * a[c]["aceptados"] / a[c]["disponibles"], 1) if a[c]["disponibles"] else 0.0
        if t != esp:
            errores.append(f"(6) fixtures 09/09, {c}: {t}% ≠ {esp}% documentado en test_unidades.py")
    if (a["Coto"]["aceptados"], a["Coto"]["disponibles"]) != COTO_0909:
        errores.append(f"(6) fixtures 09/09, Coto: {a['Coto']['aceptados']}/{a['Coto']['disponibles']} "
                       f"≠ {COTO_0909[0]}/{COTO_0909[1]} (el 43,2% de la Tarea 22)")
    return errores


def informe_aceptacion(bases):
    print("\n" + "=" * 100)
    print("§A · ACEPTACIÓN POR CADENA — cuántos candidatos llegan al umbral (sin etiquetas)")
    print("=" * 100)
    for nombre, a in bases:
        print(f"\n{nombre}")
        print(f"  {'Cadena':<11} {'acept/dispon':>14} {'%':>6} {'/con precio':>12} {'%':>6} "
              f"{'/crudos':>9} {'%':>6} {'no dispon.':>11} {'ítems con':>10}")
        for c in CADENAS:
            d = a.get(c)
            if not d:
                print(f"  {c:<11} sin datos")
                continue
            def pct(n, t):
                return f"{100.0 * n / t:.1f}" if t else "—"
            print(f"  {c:<11} {d['aceptados']:>6}/{d['disponibles']:<7} {pct(d['aceptados'], d['disponibles']):>6} "
                  f"{'/' + str(d['con_precio']):>12} {pct(d['aceptados'], d['con_precio']):>6} "
                  f"{'/' + str(d['crudos']):>9} {pct(d['aceptados'], d['crudos']):>6} "
                  f"{d['sin_disponible']:>11} {str(d['items_con_aceptado']) + '/' + str(d['items']):>10}")
    print("\n  'no dispon.' es cuántas filas trajo el parser con `disponible` en falso. En Coto es 0 porque su")
    print("  parser nunca lo pone en falso (Tarea 22): sobre esa cadena, 'disponibles' y 'con precio' son lo mismo.")
    print("  ⚠️ Por eso **el único denominador comparable entre cadenas es 'crudos'**, y ahí Coto (54,8%) no es")
    print("  la peor: Vea da 43,9% y Carrefour 64,6%. La captura nueva muestra por qué no se puede arreglar con")
    print("  los datos de Coto: su disponibilidad es POR SUCURSAL (`sDisp_*`), ver §G.")


# ─────────────────────────────────────────────────────────────────
#  §B — QUÉ SEPARA UN BUEN REPRESENTANTE DE UNO MALO, POR CADENA
# ─────────────────────────────────────────────────────────────────

def como_fn(etq_o_fn):
    """Una función cid -> etiqueta, venga un dict de etiquetas o ya una función (otra fecha)."""
    return etq_o_fn if callable(etq_o_fn) else (lambda cid: et(etq_o_fn, cid))


def efecto(elegido, etq, hoy):
    """Por cadena: cómo quedan las 20 filas con esta variante y qué cambió respecto de hoy."""
    etf = como_fn(etq)
    out = {}
    for c in CADENAS:
        d = {k: 0 for k in ETQ}
        d.update(faltante=0, sin_etiqueta=0, cambios=[], trans=Counter())
        for prod in CANASTA:
            q = prod["nombre"]
            a, b = hoy.get((c, q)), elegido.get((c, q))
            ea = etf(a[0]["cid"]) if a else "faltante"
            eb = etf(b[0]["cid"]) if b else "faltante"
            if b is None:
                d["faltante"] += 1
            elif eb in ETQ:
                d[eb] += 1
            else:
                d["sin_etiqueta"] += 1
            d["trans"][(ea or "sin etiqueta", eb or "sin etiqueta")] += 1
            if (a[0]["cid"] if a else None) != (b[0]["cid"] if b else None):
                d["cambios"].append({"item": q, "antes": a and a[0], "despues": b and b[0],
                                     "e_antes": ea, "e_despues": eb})
        d["con_rep"] = len(CANASTA) - d["faltante"]
        d["pct"] = 100.0 * d["correcto"] / d["con_rep"] if d["con_rep"] else 0.0
        # ✏️ Revisión de Santiago (22/09): piso y brecha sobre los ítems con representante NO cuentan los
        # faltantes, y una señal puede "mejorar" dejando filas vacías. Los dos denominadores, siempre.
        d["pct20"] = 100.0 * d["correcto"] / len(CANASTA)
        out[c] = d
    return out


def piso_brecha(ef, campo="pct"):
    pcts = [ef[c][campo] for c in CADENAS]
    return min(pcts), max(pcts) - min(pcts)


# La regla de "la señal sirve", FIJADA ANTES DE MEDIR (plan del 21/09, aprobado):
#   1. saca al menos la mitad de los representantes "otro producto";
#   2. ninguna cadena pasa un "correcto" a "otro producto" o a faltante;
#   3. la brecha entre la mejor y la peor cadena no crece.
# ✏️ 22/09: la (3) se evalúa con los DOS denominadores —ítems con representante y los 20 ítems— y "SIRVE"
# exige las dos. Con los 20, un faltante cuenta como fallo, que es lo que ve el usuario.
def sirve(ef, ef_hoy):
    malos_hoy = sum(ef_hoy[c]["otro producto"] for c in CADENAS)
    malos = sum(ef[c]["otro producto"] for c in CADENAS)
    rompe = sum(n for c in CADENAS for (a, b), n in ef[c]["trans"].items()
                if a == "correcto" and b in ("otro producto", "faltante"))
    _p0, b0 = piso_brecha(ef_hoy)
    _p1, b1 = piso_brecha(ef)
    _q0, g0 = piso_brecha(ef_hoy, "pct20")
    _q1, g1 = piso_brecha(ef, "pct20")
    return {"malos": malos, "saca": malos_hoy - malos, "rompe_correctos": rompe,
            "brecha": b1, "brecha_hoy": b0, "brecha20": g1, "brecha20_hoy": g0,
            "sirve": ((malos_hoy - malos) >= malos_hoy / 2 and rompe == 0
                      and b1 <= b0 + 1e-9 and g1 <= g0 + 1e-9)}


def informe_cobertura_correcta(filas, etq):
    """
    Una medida que NO depende del denominador: en cuántos ítems la cadena tiene al menos un candidato
    aceptado y etiquetado correcto. Es lo que el usuario puede llegar a ver bien.
    """
    print("\n  Ítems con al menos un candidato ACEPTADO y etiquetado correcto (captura del 14/09, 20 ítems):")
    print(f"  {'Cadena':<11} {'ítems':>7} {'ítems con algún candidato':>26} {'aceptados correctos':>21}")
    for c in CADENAS:
        con = ncand = acept_ok = 0
        for prod in CANASTA:
            cands = U.candidatos(filas, c, prod["nombre"])
            ncand += bool(cands)
            ok = [f for f in cands if s_hoy(prod, f["of"]) >= UMBRAL_MATCH and et(etq, f["cid"]) == "correcto"]
            acept_ok += len(ok)
            con += bool(ok)
        print(f"  {c:<11} {f'{con}/{len(CANASTA)}':>7} {f'{ncand}/{len(CANASTA)}':>26} {acept_ok:>21}")


# Los casos que ya estaban DOCUMENTADOS cuando se escribieron los criterios (Tarea 26, "Fallas que encontró
# el paso 1" y decisión 5): mermelada por azúcar, medialunas por manteca, picada de cerdo, Finish en
# tabletas, huevos de pascua y pan de pancho por pan lactal. Todo lo que no esté acá es fuera de muestra.
DOCUMENTADOS_ANTES = {
    ("Coto", "Azúcar"), ("Carrefour", "Manteca"), ("Día", "Manteca"), ("Chango Más", "Manteca"),
    ("Vea", "Carne picada"), ("Carrefour", "Detergente"), ("Carrefour", "Huevos"),
    ("Día", "Pan lactal"), ("Vea", "Pan lactal"), ("Coto", "Pan lactal"),
}


def informe_in_sample(ctx, etq, filas):
    """
    N criterio es un TECHO, no un hallazgo: las palabras negativas salen de las mismas columnas de criterio
    que vieron los etiquetadores, y los casos que arregla estaban documentados antes de escribirlas. Esta
    tabla separa in-sample de fuera de muestra, fila por fila, y muestra qué elige la señal en su lugar.
    """
    hoy = ctx["elegidos"]["hoy"]
    ncrit = ctx["elegidos"]["N criterio"]
    print("\n  N criterio, caso por caso: in-sample contra fuera de muestra")
    print(f"  {'Cadena':<11} {'Ítem':<19} {'documentado':>12} {'lo marca':>9}   qué queda en su lugar")
    for (c, q), (f, _s) in sorted(hoy.items(), key=lambda t: (t[0][1], t[0][0])):
        if et(etq, f["cid"]) != "otro producto":
            continue
        pasa = not ({raiz(t) for t in lista_tokens(f["of"].producto)} & set(negativas(q)))
        par = ncrit.get((c, q))
        nuevo = ("— (queda sin representante)" if par is None
                 else f"{et(etq, par[0]['cid']) or '—'}: {par[0]['of'].producto[:38]}")
        print(f"  {c:<11} {q:<19} {'sí' if (c, q) in DOCUMENTADOS_ANTES else 'NO':>12} "
              f"{'no' if pasa else 'sí':>9}   {nuevo}")
    print("  'documentado' = el caso estaba escrito en la Tarea 26 antes de que se redactaran los criterios.")


def informe_senales(ctx, etq, filas):
    hoy = ctx["elegidos"]["hoy"]
    ef_hoy = efecto(hoy, etq, hoy)
    tipos = {n: t for n, (t, _kw, _p) in variantes(filas).items()}
    print("\n" + "=" * 100)
    print("§B · SEÑALES — cada una como FILTRO antes de elegir, por cadena (k/n, no %)")
    print("=" * 100)
    print("\n  Hoy, para comparar:")
    fila_ef("hoy", ef_hoy, ef_hoy, "—")

    orden = ["S3 manda", "S2 ≤ 1", "S2 ≤ 2", "S2 ≤ 3", "K0 nombre frase", "N criterio",
             "K1 clave tokens", "K2 clave frase", "sin tamaño", "E desempate"]
    print("\n  Señales genéricas (las puede correr cualquier canasta):")
    for n in orden:
        if tipos.get(n) == "genérica":
            fila_ef(n, efecto(ctx["elegidos"][n], etq, hoy), ef_hoy, tipos[n])
    print("\n  Señales POR ÍTEM — solo llegan a la canasta por defecto: el frontend manda")
    print("  `categoria: 'Otro'` y `palabras_clave: [nombre]` para lo que arma el usuario:")
    for n in orden:
        if tipos.get(n) == "por ítem":
            fila_ef(n, efecto(ctx["elegidos"][n], etq, hoy), ef_hoy, tipos[n])

    print("\n  Detalle de los cambios de representante, señal por señal:")
    for n in orden:
        ef = efecto(ctx["elegidos"][n], etq, hoy)
        camb = [(c, x) for c in CADENAS for x in ef[c]["cambios"]]
        if not camb:
            continue
        print(f"\n  · {n}")
        for c, x in camb:
            ant = x["antes"]["of"].producto[:46] if x["antes"] else "—"
            des = x["despues"]["of"].producto[:46] if x["despues"] else "— (queda sin representante)"
            print(f"      {c:<11} {x['item']:<19} {x['e_antes'] or '—':<13} → {x['e_despues'] or '—':<13}")
            print(f"      {'':<11} {'':19} antes: {ant}")
            print(f"      {'':<11} {'':19} ahora: {des}")


def filtros_de(filas):
    return {n: kw["filtro"] for n, (_t, kw, _p) in variantes(filas).items() if "filtro" in kw}


def informe_clasificador(ctx, etq, filas):
    """La otra mitad de la pregunta: como CLASIFICADOR sobre los 96, ¿marca a los malos sin marcar correctos?"""
    hoy = ctx["elegidos"]["hoy"]
    fils = filtros_de(filas)
    tipos = {n: t for n, (t, _kw, _p) in variantes(filas).items()}
    reps = [(c, q, f) for (c, q), (f, _s) in hoy.items()]
    malos = [(c, q, f) for c, q, f in reps if et(etq, f["cid"]) == "otro producto"]
    oks = [(c, q, f) for c, q, f in reps if et(etq, f["cid"]) == "correcto"]
    print("\n" + "=" * 100)
    print("§B2 · LAS MISMAS SEÑALES COMO CLASIFICADOR — sobre los 96 representantes de hoy")
    print("=" * 100)
    print("  'marca' = la señal dice que ESTE representante no sirve. Por cadena: malos marcados / malos,")
    print("  y al lado los correctos que también marca (falsos positivos).\n")
    print(f"  {'Señal':<18} " + " ".join(f"{c:>15}" for c in CADENAS) + f"   {'total':>12}")
    for n, fil in fils.items():
        cel = []
        tm = tf = 0
        for c in CADENAS:
            m = sum(1 for cc, q, f in malos if cc == c and not fil(ITEM[q], f))
            fp = sum(1 for cc, q, f in oks if cc == c and not fil(ITEM[q], f))
            nm = sum(1 for cc, _q, _f in malos if cc == c)
            tm += m
            tf += fp
            cel.append(f"{m}/{nm} (+{fp})")
        print(f"  {n:<18} " + " ".join(f"{v:>15}" for v in cel) + f"   {f'{tm}/{len(malos)} (+{tf})':>12}"
              + ("" if tipos[n] == "genérica" else "  ← por ítem"))

    print("\n  Fila por fila, los 14 representantes que no son 'correcto':")
    print(f"  {'Cadena':<11} {'Ítem':<19} {'etiqueta':<13} " + " ".join(f"{n[:9]:>10}" for n in fils))
    for c, q, f in sorted(reps, key=lambda t: (t[1], t[0])):
        e = et(etq, f["cid"])
        if e == "correcto":
            continue
        marcas = ["marca" if not fil(ITEM[q], f) else "·" for fil in fils.values()]
        print(f"  {c:<11} {q:<19} {e:<13} " + " ".join(f"{v:>10}" for v in marcas))


def fila_ef(nombre, ef, ef_hoy, tipo):
    s = sirve(ef, ef_hoy)
    cel = []
    for c in CADENAS:
        d = ef[c]
        cel.append(f"{d['correcto']}/{d['con_rep']}" + (f"+{d['faltante']}f" if d["faltante"] else ""))
    piso, _b = piso_brecha(ef)
    piso20, _g = piso_brecha(ef, "pct20")
    marca = "SIRVE" if s["sirve"] else ""
    print(f"  {nombre:<18} " + " ".join(f"{v:>9}" for v in cel) +
          f"  | malos {s['malos']:>2} (saca {s['saca']:>2}) · rompe {s['rompe_correctos']:>2}"
          f" · piso {piso:5.1f}%/{piso20:5.1f}% · brecha {s['brecha']:5.1f}/{s['brecha20']:5.1f} {marca}")


# ─────────────────────────────────────────────────────────────────
#  §C — EL UMBRAL
# ─────────────────────────────────────────────────────────────────

def corte_sin_perder(sel, etq, umbrales=None):
    """
    (corte más alto que no pierde ni un correcto, malos que saca, peor correcto) y qué cuesta 0,50.
    `sel` es {(cadena, ítem): (fila, score)}; `etq` un dict de etiquetas o una función.
    """
    etf = como_fn(etq)
    reps = [(c, q, f) for (c, q), (f, _s) in sel.items()]
    ok = [(s_hoy(ITEM[q], f["of"]), c, q) for c, q, f in reps if etf(f["cid"]) == "correcto"]
    mal = [(s_hoy(ITEM[q], f["of"]), c, q) for c, q, f in reps if etf(f["cid"]) == "otro producto"]
    peor_ok = min(ok) if ok else None
    mejor = (UMBRAL_MATCH, [])
    for u in (umbrales or [x / 1000 for x in range(int(UMBRAL_MATCH * 1000), 801)]):
        if any(sc < u for sc, _c, _q in ok):
            break
        mejor = (u, [(c, q) for sc, c, q in mal if sc < u])
    en_050 = {"saca_malos": [(c, q) for sc, c, q in mal if sc < 0.50],
              "pierde_correctos": [(c, q, sc) for sc, c, q in ok if sc < 0.50]}
    return mejor, peor_ok, en_050


def informe_umbral(ctx, etq, filas):
    hoy = ctx["elegidos"]["hoy"]
    print("\n" + "=" * 100)
    print("§C · UMBRAL — barrido 0,45 → 0,80, con el score de hoy y sin la señal de tamaño")
    print("=" * 100)
    for puntaje, tit in ((None, "score de hoy"), (s_sin_tamano, "score sin tamaño")):
        print(f"\n  {tit}:")
        print(f"  {'umbral':<8} " + " ".join(f"{c:>18}" for c in CADENAS) + "   (correctos/con rep · malos)")
        for u in UMBRALES:
            nombre = ("hoy" if u == UMBRAL_MATCH else f"umbral {u:.2f}") if puntaje is None else (
                "sin tamaño" if u == UMBRAL_MATCH else f"sin tamaño, umbral {u:.2f}")
            ef = efecto(ctx["elegidos"][nombre], etq, hoy)
            cel = [f"{ef[c]['correcto']}/{ef[c]['con_rep']} · {ef[c]['otro producto']}" for c in CADENAS]
            print(f"  {u:<8.2f} " + " ".join(f"{v:>18}" for v in cel))

    print("\n  ¿Hay un corte que mate a los malos sin matar correctos? Por cadena, con el score de hoy:")
    print(f"  {'Cadena':<11} {'peor correcto':>14} {'mejor malo':>12}   veredicto")
    for c in CADENAS:
        ok = [(s_hoy(ITEM[q], f["of"]), q) for (cc, q), (f, _s) in hoy.items()
              if cc == c and et(etq, f["cid"]) == "correcto"]
        mal = [(s_hoy(ITEM[q], f["of"]), q) for (cc, q), (f, _s) in hoy.items()
               if cc == c and et(etq, f["cid"]) == "otro producto"]
        if not mal:
            print(f"  {c:<11} {min(ok)[0]:>14.3f} {'—':>12}   sin malos que cortar")
            continue
        pc, pm = min(ok), max(mal)
        v = (f"existe: entre {pm[0]:.3f} y {pc[0]:.3f}" if pm[0] < pc[0]
             else f"NO: el peor correcto ({pc[1]}) puntúa igual o menos que el mejor malo ({pm[1]})")
        print(f"  {c:<11} {pc[0]:>14.3f} {pm[0]:>12.3f}   {v}")

    # ✏️ Revisión de Santiago (22/09): que no exista un corte que saque a TODOS los malos no significa que
    # ningún corte sirva. El corte más alto que no pierde un solo correcto, con su margen:
    print("\n  El corte más alto que no pierde ni un correcto, mirando las 5 cadenas juntas:")
    (u, saca_mal), peor_ok, en_050 = corte_sin_perder(hoy, etq)
    malos_tot = sum(1 for (_c, q), (f, _s) in hoy.items() if et(etq, f["cid"]) == "otro producto")
    print(f"    umbral {u:.3f}: saca {len(saca_mal)} de {malos_tot} malos "
          f"({', '.join(f'{c}/{q}' for c, q in saca_mal) or '—'}) y ningún correcto. Margen "
          f"{peor_ok[0] - u:.3f} contra el peor correcto ({peor_ok[1]}/{peor_ok[2]}, {peor_ok[0]:.3f}).")
    print("    El resto puntúa por encima de cualquier correcto: el umbral no los alcanza.")
    # ✏️ 23/09, revisión: el veredicto del 0,50 NO es de la regla, es de esta captura. Ver §G.
    print(f"    Con 0,50 exacto, en ESTA captura: saca {len(en_050['saca_malos'])} malos y pierde "
          f"{len(en_050['pierde_correctos'])} correctos. En la captura del 22/09 no sale igual: ver §G.")


# ─────────────────────────────────────────────────────────────────
#  §D — EL EAN
# ─────────────────────────────────────────────────────────────────

def informe_ean(ctx, etq, filas):
    hoy = ctx["elegidos"]["hoy"]
    cruz = cruzables_aceptados(filas)
    print("\n" + "=" * 100)
    print("§D · EAN — ¿el EAN sabe algo que el título no dice?")
    print("=" * 100)
    print(f"  {'Cadena':<11} {'candidatos':>11} {'con EAN':>9} {'cruzable':>9} {'cód. tienda':>12} "
          f"{'reps con EAN en otra cadena':>29}")
    for c in CADENAS:
        tot = con = cruzables = tienda = 0
        for prod in CANASTA:
            for f in U.candidatos(filas, c, prod["nombre"]):
                tot += 1
                e = str(f["of"].ean or "").strip()
                con += bool(e)
                if ean_cruzable(e):
                    cruzables += 1
                elif e:
                    tienda += 1
        reps_c = [(q, f) for (cc, q), (f, _s) in hoy.items() if cc == c]
        comp = sum(1 for q, f in reps_c
                   if ean_cruzable(f["of"].ean) and cruz.get((q, ean_cruzable(f["of"].ean)), set()) - {c})
        print(f"  {c:<11} {tot:>11} {con:>9} {cruzables:>9} {tienda:>12} {f'{comp}/{len(reps_c)}':>29}")

    print("\n  Los representantes que comparten EAN con un candidato ACEPTADO de otra cadena, y su etiqueta:")
    for c in CADENAS:
        for (cc, q), (f, _s) in sorted(hoy.items()):
            if cc != c:
                continue
            e = ean_cruzable(f["of"].ean)
            otras = sorted(cruz.get((q, e), set()) - {c}) if e else []
            if otras:
                print(f"    {c:<11} {q:<19} {et(etq, f['cid']) or '—':<13} también en {', '.join(otras)}")


# ─────────────────────────────────────────────────────────────────
#  §E — SELECCIÓN POR PRECIO (medida, NO implementada)
# ─────────────────────────────────────────────────────────────────

def fichas_fijo(precios, prom_hoy, dia):
    """Las fichas de una variante con la MEDIANA DE HOY FIJA: si la mediana se mueve, el Δ% no compara."""
    res = main._analizar(CANASTA, precios, main.PROMOS_DEFAULT, dia, None)
    return {fi["cadena"]: fi for fi in main._fichas(CANASTA, precios, res, prom_hoy, list(CADENAS))}


def oraculo(filas, etq, solo_kg_l=True):
    """P-oráculo: P1 entre los candidatos CORRECTOS. Es la cota de una identidad perfecta, no una propuesta."""
    def filtro(prod, f):
        return et(etq, f["cid"]) == "correcto"
    return seleccionar(filas, filtro=filtro, clave=clave_precio(solo_kg_l))


def informe_precio(ctx, etq, filas, dia):
    hoy = ctx["elegidos"]["hoy"]
    prom_hoy = ctx["prom"]
    base = fichas_fijo(precios_de(hoy), prom_hoy, dia)
    print("\n" + "=" * 100)
    print("§E · SELECCIÓN POR PRECIO — qué cambiaría (sin implementarla). Mediana de hoy FIJA")
    print("=" * 100)
    vars_p = [("hoy", hoy), ("P1 kg/l", ctx["elegidos"]["P1 kg/l"]), ("P1 todo", ctx["elegidos"]["P1 todo"]),
              ("P1 kg/l sin tamaño", ctx["elegidos"]["P1 kg/l sin tamaño"]),
              ("P-oráculo kg/l", oraculo(filas, etq, True))]
    comunes = [p["nombre"] for p in CANASTA
               if all((c, p["nombre"]) in el for _n, el in vars_p for c in CADENAS)]
    print(f"  Ítems cubiertos por todas las variantes en las 5 cadenas: {len(comunes)} de {len(CANASTA)}")
    for nombre, el in vars_p:
        ef = efecto(el, etq, hoy)
        fi = fichas_fijo(precios_de(el), prom_hoy, dia)
        print(f"\n  {nombre}")
        print(f"    {'Cadena':<11} {'correctos':>10} {'malos':>6} {'total_envase':>14} {'Δ vs hoy':>12} "
              f"{'Δ% sin promo':>13} {'común':>12}")
        for c in CADENAS:
            d = ef[c]
            t, t0 = fi[c]["total_envase"], base[c]["total_envase"]
            com = sum(dd["subtotal"] for dd in fi[c]["detalle"] if dd["producto"] in comunes)
            ok = f"{d['correcto']}/{d['con_rep']}"
            dpct = fi[c]["delta_pct_sin_promo"]
            print(f"    {c:<11} {ok:>10} {d['otro producto']:>6} "
                  f"{t:>14,.0f} {t - t0:>12,.0f} {f_pct(dpct):>13} {com:>12,.0f}")
        orden = sorted(CADENAS, key=lambda c: sum(dd["subtotal"] for dd in fi[c]["detalle"]
                                                  if dd["producto"] in comunes))
        print(f"    orden por los {len(comunes)} ítems comunes: " + " < ".join(orden))
        camb = [(c, x) for c in CADENAS for x in ef[c]["cambios"]]
        for c, x in camb[:40]:
            ant = x["antes"]["of"] if x["antes"] else None
            des = x["despues"]["of"] if x["despues"] else None
            print(f"      {c:<11} {x['item']:<19} {x['e_antes'] or '—':<13} → {x['e_despues'] or '—':<13} "
                  f"${ant.precio if ant else 0:>9,.0f} → ${des.precio if des else 0:>9,.0f}  {des.producto[:40] if des else '—'}")


# ─────────────────────────────────────────────────────────────────
#  §F — COTO: FORMA DE TÍTULO, BUSCADOR O DUDOSO
# ─────────────────────────────────────────────────────────────────

def f_pct(v):
    return "—" if v is None else f"{v:+.1f}%"


def rechazos_etiquetados(por_cadena, etq):
    """{cadena: [(prod, cid, oferta)]} → los que NO llegan al umbral, partidos por etiqueta."""
    out = {}
    for c, trios in por_cadena.items():
        n, ejemplos = Counter(), defaultdict(list)
        for prod, cid, o in trios:
            if not o.disponible or o.precio <= 0 or s_hoy(prod, o) >= UMBRAL_MATCH:
                continue
            e = et(etq, cid) or "sin etiqueta"
            n[e] += 1
            if len(ejemplos[e]) < 4:
                ejemplos[e].append(f"{o.producto[:60]} (s={s_hoy(prod, o):.2f})")
        out[c] = (n, ejemplos)
    return out


def informe_coto(ctx, etq, filas, bases):
    print("\n" + "=" * 100)
    print("§F · COTO — sus rechazos, leídos con las etiquetas: forma de título o buscador")
    print("=" * 100)
    print("  'forma de título' = el candidato ERA el producto y el matcher lo rechazó (etiqueta 'correcto').")
    print("  'buscador' = lo que trajo la consulta es otra cosa (etiqueta 'otro producto'): no lo arregla el matcher.\n")

    # Base A: la captura del 14/09, las 20 consultas, las 5 cadenas. El cid sale de la fila, no del índice.
    r14 = rechazos_etiquetados(
        {c: [(prod, f["cid"], f["of"]) for prod in CANASTA for f in filas[c].get(prod["nombre"], [])]
         for c in CADENAS}, etq)
    print("  Captura del 14/09, 20 consultas:")
    print(f"  {'Cadena':<11} {'rechazos':>9} {'forma de título':>17} {'buscador':>10} {'dudoso':>8} "
          f"{'sin etiqueta':>13} {'aceptación hoy':>15} {'si entrara la forma':>20}")
    for c in CADENAS:
        n, _ej = r14[c]
        d = bases[1][1][c]
        tot = sum(n.values())
        hoy_pct = 100.0 * d["aceptados"] / d["disponibles"] if d["disponibles"] else 0
        con = 100.0 * (d["aceptados"] + n["correcto"]) / d["disponibles"] if d["disponibles"] else 0
        print(f"  {c:<11} {tot:>9} {n['correcto']:>17} {n['otro producto']:>10} {n['dudoso']:>8} "
              f"{n['sin etiqueta']:>13} {hoy_pct:>14.1f}% {con:>19.1f}%")
    if any(r14[c][0]["sin etiqueta"] for c in CADENAS):
        print("  ⚠️ 'si entrara la forma' cuenta solo lo etiquetado: con filas sin etiqueta es un PISO, y la")
        print("  comparación entre cadenas no es válida hasta que la columna 'sin etiqueta' esté en cero.")
    print("\n  Ejemplos de Coto, por etiqueta:")
    n, ej = r14["Coto"]
    for k in ETQ:
        for x in ej.get(k, []):
            print(f"    {k:<13} {x}")

    # Base B: los 63 rechazos de Coto del 09/09, contra el conteo a mano de la Tarea 19 §8.
    fix = EM.cargar({"Coto": FIXTURES["Coto"]})
    cid09 = {(prod["nombre"], id(o)): f"fix0909|Coto|{prod['nombre']}|{i}"
             for prod in CANASTA_6 for i, o in enumerate(fix["Coto"].get(prod["nombre"], []))}

    def et_of09(q, o):
        return et(etq, cid09.get((q, id(o)), ""))

    r09 = rechazos_etiquetados(
        {"Coto": [(prod, cid09[(prod["nombre"], id(o))], o) for prod in CANASTA_6
                  for o in fix["Coto"].get(prod["nombre"], [])]}, etq)["Coto"][0]
    print("\n  Calibración contra el conteo A MANO de la Tarea 19 §8 (fixtures del 09/09, 63 rechazos de Coto):")
    print(f"    a mano:     21 forma de título · 40 buscador · 2 dudosos")
    print(f"    etiquetas:  {r09['correcto']} forma de título · {r09['otro producto']} buscador · "
          f"{r09['dudoso']} dudosos" + (f" · {r09['sin etiqueta']} sin etiqueta" if r09.get("sin etiqueta") else ""))
    print("    ⚠️ Los totales pueden coincidir por compensación: lo que vale es la comparación fila por fila.")

    # ✏️ Revisión de Santiago (22/09): mostrar las filas que difieren. Las 4 familias de "forma de título" del
    # §8 se escriben acá tal como las describe ese texto, y la cuenta de cada una se verifica abajo.
    filas09 = [(prod["nombre"], o) for prod in CANASTA_6
               for o in fix["Coto"].get(prod["nombre"], [])
               if o.disponible and o.precio > 0
               and s_hoy(ITEM[prod["nombre"]], o) < UMBRAL_MATCH]
    familias = Counter()
    difieren = []
    for q, o in filas09:
        t = NORM(o.producto)
        if q == "Papel higienico" and t.startswith("p.higienico"):
            clase19, familia = "forma de título", "P.Higienico (§8 dice 10)"
        elif q == "Carne picada" and t.startswith("picada"):
            clase19, familia = "forma de título", "picada sin 'carne' (§8 dice 3)"
        elif t.startswith("pollo congelado x kg"):
            clase19, familia = "forma de título", "pollo sin 'entero' (§8 dice 1)"
        elif q == "Gaseosa cola":
            clase19, familia = "forma de título", "cola sin 'gaseosa' o sin 'cola' (§8 dice 7)"
        elif q == "Leche entera" and t.startswith("leche") and "entera" not in t:
            clase19, familia = "dudoso", "leche que no dice 'entera' (§8 dice 2)"
        else:
            clase19, familia = "buscador", "resto (§8 dice 40)"
        familias[familia] += 1
        nuestra = {"correcto": "forma de título", "otro producto": "buscador",
                   "dudoso": "dudoso"}.get(et_of09(q, o), "sin etiqueta")
        if nuestra != clase19:
            difieren.append((q, o.producto, clase19, nuestra))
    print("    Las familias del §8, contadas con su propia descripción:")
    for f, n in familias.most_common():
        print(f"      {n:>3}  {f}")
    print(f"    Filas donde la etiqueta y el §8 NO coinciden: {len(difieren)} de {len(filas09)}")
    for q, titulo, c19, nuestra in difieren:
        print(f"      {q:<16} {titulo[:52]:<54} §8: {c19:<16} etiqueta: {nuestra}")


# ─────────────────────────────────────────────────────────────────
#  §G — LA CAPTURA NUEVA: CATEGORÍA Y HOLDOUT CON FECHA POSTERIOR
# ─────────────────────────────────────────────────────────────────

def categoria_de(f):
    """La categoría que declara la cadena para ESTE candidato. None si no vino: no se inventa."""
    raw = f["raw"]
    if f["c"] == "Coto":
        comp = raw.get("completo") or {}
        for k in ("product.category", "product.LCLASE", "product.LDEPAR", "product.allAncestors"):
            v = comp.get(k)
            if v:
                return " / ".join(str(x) for x in (v if isinstance(v, list) else [v]))[:120]
        return None
    cats = raw.get("categories") or []
    return cats[0][:120] if cats else None


def cobertura_categoria(filas, canasta):
    out = {}
    for c in CADENAS:
        con = tot = 0
        for prod in canasta:
            for f in filas[c].get(prod["nombre"], []):
                tot += 1
                con += bool(categoria_de(f))
        out[c] = (con, tot)
    return out


def c1(prod, f):
    """C1: la categoría que declara la cadena contiene una palabra del ítem."""
    cat = categoria_de(f)
    if not cat:
        return None                                  # sin categoría no se decide: se reporta aparte
    tc = {raiz(t) for t in lista_tokens(cat)}
    return bool(tc & {raiz(t) for t in lista_tokens(prod["nombre"])})


def modal_categoria(filas, c, q):
    """C2: la categoría más frecuente entre los candidatos ACEPTADOS. En el azúcar de Coto es circular
    (casi todos los candidatos son mermeladas) y se muestra así, no como señal que funciona."""
    cats = [categoria_de(f) for f in U.candidatos(filas, c, q)
            if s_hoy(ITEM[q], f["of"]) >= UMBRAL_MATCH and categoria_de(f)]
    return Counter(cats).most_common(1)[0][0] if cats else None


def clave_union(c, of):
    """cadena + EAN sin ceros a la izquierda + título normalizado. Un EAN de tienda no une solo por EAN."""
    return (c, ean_cruzable(of.ean) or "", NORM(of.producto or ""))


def elegidos_de(filas):
    return {n: seleccionar(filas, **kw) for n, (_t, kw, _p) in variantes(filas).items()}


ETIQUETAS_NUEVA = RAIZ / "tests" / "capturas" / "etiquetas_2026-09-22.json"

# Consultas donde el request de producción devolvió 0 filas SIN error, documentadas por captura. Es un
# hallazgo, no un dato: con `Nrpp=48` el Detergente de Coto trae 48 filas, con lavavajillas líquido adentro.
# Un ítem vacío que no esté acá sale con exit 1: si la próxima captura trae otro, se mira a mano.
VACIOS_CONOCIDOS = {"2026-09-22T16:11:11": {"Coto": ["Detergente", "Atún natural"]}}


def validar_nueva(cap2, filas2):
    """
    (10) La captura nueva está completa y el lazo la corre igual que producción.
    ✏️ Revisión de Santiago (22/09): que la consulta ESTÉ no es que tenga filas. El 22/09 el request de
    producción de Coto devolvió 0 filas en Detergente y Atún natural **sin error**, y esta validación no lo
    veía. Ahora los ítems vacíos se listan y salen con exit 1: es una posible falla silenciosa de
    `FuenteCoto`, no un dato de la captura.
    """
    errores = []
    if cap2.get("fallas"):
        errores.append(f"(10) la captura nueva tiene consultas fallidas: {cap2['fallas']}")
    for c in CADENAS:
        falta = [p["nombre"] for p in CANASTA if p["nombre"] not in (cap2["datos"].get(c) or {})]
        if falta:
            errores.append(f"(10) {c}: la captura nueva no trae {falta}")
        vacios = [p["nombre"] for p in CANASTA if not (cap2["datos"].get(c) or {}).get(p["nombre"])]
        conocidos = (VACIOS_CONOCIDOS.get(cap2.get("__fecha")) or {}).get(c, [])
        nuevos = [q for q in vacios if q not in conocidos]
        if nuevos:
            errores.append(f"(10) {c}: la consulta de producción devolvió 0 filas, sin error, en {nuevos} "
                           f"(no está documentado en VACIOS_CONOCIDOS)")
    if not errores and precios_de(seleccionar(filas2)) != produccion(filas2):
        errores.append("(10) sobre la captura nueva, el lazo y buscar_precios_online no eligen lo mismo")
    return errores


def etiquetas_nueva(filas, filas2, etq, ruta=ETIQUETAS_NUEVA):
    """
    Las etiquetas de la fecha nueva: se HEREDAN por la unión (cadena + EAN cruzable + título normalizado) y
    lo que no une se etiqueta en una ronda chica, a ciegas, con los mismos criterios.
    """
    her, _sin = unir(filas, filas2, CANASTA)
    extra = {}
    if ruta.exists():
        extra = {x["cid"]: x for x in json.load(open(ruta, encoding="utf-8"))["candidatos"]}

    def et2(cid):
        if cid in extra:
            return extra[cid]["etiqueta"]
        a = her.get(cid)
        return et(etq, a) if a else None

    # Las secciones "profundo" y "alternativas" se parsean aparte y no tienen `cid`: para ellas la etiqueta
    # se busca por la misma clave de unión (ítem + cadena + EAN cruzable + título normalizado). El mapa se
    # arma con los CAMPOS GUARDADOS de las etiquetas, no con las capturas, así entran también las filas de
    # fuentes profundas, que no están en `datos` y por lo tanto no tienen `cid` de captura.
    mapa = {}
    for tabla in (etq, extra):
        for x in tabla.values():
            k = (x["item"], (x["cadena"], ean_cruzable(x["ean"]) or "", NORM(x["titulo"] or "")))
            mapa.setdefault(k, x["etiqueta"])

    def et_of(q, c, of):
        return mapa.get((q, clave_union(c, of)))
    return et2, et_of, her, extra


def unir(filas_a, filas_b, canasta):
    """{cid de b: cid de a} — para heredar la etiqueta del 14/09 en la captura nueva."""
    idx = {}
    for c in CADENAS:
        for prod in canasta:
            for f in filas_a[c].get(prod["nombre"], []):
                idx.setdefault((prod["nombre"], clave_union(c, f["of"])), f["cid"])
    out, sin = {}, []
    for c in CADENAS:
        for prod in canasta:
            for f in filas_b[c].get(prod["nombre"], []):
                a = idx.get((prod["nombre"], clave_union(c, f["of"])))
                if a:
                    out[f["cid"]] = a
                else:
                    sin.append(f["cid"])
    return out, sin


def informe_nueva(filas2, cap2, et2, herencia, etq_extra):
    """La captura con fecha posterior: categoría (que el 14/09 no trae) y holdout de las señales."""
    hoy2 = seleccionar(filas2)
    print("\n" + "=" * 100)
    print(f"§G · CAPTURA NUEVA ({cap2['__fecha']}) — categoría y holdout con fecha posterior")
    print("=" * 100)
    cob = cobertura_categoria(filas2, CANASTA)
    print(f"  {'Cadena':<11} {'candidatos':>11} {'con categoría':>14} {'reps':>6} {'etiqueta heredada':>18} "
          f"{'ronda chica':>12}")
    for c in CADENAS:
        con, tot = cob[c]
        reps_c = [f for (cc, _q), (f, _s) in hoy2.items() if cc == c]
        her = sum(1 for f in reps_c if f["cid"] in herencia)
        ext = sum(1 for f in reps_c if f["cid"] in etq_extra)
        print(f"  {c:<11} {tot:>11} {f'{con}/{tot}':>14} {len(reps_c):>6} {her:>18} {ext:>12}")

    print("\n  Los representantes de la fecha nueva, por etiqueta:")
    print(f"  {'Cadena':<11} {'correcto':>9} {'otro prod.':>11} {'dudoso':>7} {'sin etiqueta':>13} {'sin rep':>8}")
    for c in CADENAS:
        n = Counter(et2(f["cid"]) or "sin etiqueta" for (cc, _q), (f, _s) in hoy2.items() if cc == c)
        sin_rep = len(CANASTA) - sum(n.values())
        print(f"  {c:<11} {n['correcto']:>9} {n['otro producto']:>11} {n['dudoso']:>7} "
              f"{n['sin etiqueta']:>13} {sin_rep:>8}")

    # ✏️ 23/09, revisión: C2 se había descartado con un ejemplo falso. Acá van las señales de la fecha nueva
    # como CLASIFICADOR y como FILTRO, con la misma regla y los dos denominadores.
    modales = {(c, p["nombre"]): modal_categoria(filas2, c, p["nombre"])
               for c in CADENAS for p in CANASTA}
    validas = defaultdict(set)
    for c in CADENAS:
        for prod in CANASTA:
            for f in U.candidatos(filas2, c, prod["nombre"]):
                if et2(f["cid"]) == "correcto" and categoria_de(f):
                    validas[(c, prod["nombre"])].add(categoria_de(f))

    def f_c2(p, f):
        m = modales.get((f["c"], p["nombre"]))
        return m is None or categoria_de(f) == m

    def f_c4(p, f):
        v = validas[(f["c"], p["nombre"])]
        return not v or categoria_de(f) in v

    señales = {"N criterio": lambda p, f: not ({raiz(t) for t in lista_tokens(f["of"].producto)}
                                               & set(negativas(p["nombre"]))),
               "S3 manda": lambda p, f: manda(p, f["of"]),
               "C1 categoría": lambda p, f: c1(p, f) is not False,
               "C2 modal": f_c2,
               "C4 cota": f_c4}
    print("\n  Señales sobre la fecha nueva (holdout), como CLASIFICADOR de los representantes:")
    print(f"  {'Señal':<14} " + " ".join(f"{c:>15}" for c in CADENAS) + f"   {'total':>12}")
    for n, fil in señales.items():
        cel, tm, tf = [], 0, 0
        for c in CADENAS:
            malos = [(q, f) for (cc, q), (f, _s) in hoy2.items() if cc == c and et2(f["cid"]) == "otro producto"]
            oks = [(q, f) for (cc, q), (f, _s) in hoy2.items() if cc == c and et2(f["cid"]) == "correcto"]
            m = sum(1 for q, f in malos if not fil(ITEM[q], f))
            fp = sum(1 for q, f in oks if not fil(ITEM[q], f))
            tm, tf = tm + m, tf + fp
            cel.append(f"{m}/{len(malos)} (+{fp})")
        print(f"  {n:<14} " + " ".join(f"{v:>15}" for v in cel) + f"   {f'{tm} (+{tf})':>12}")
    print("  'C1 categoría' solo puede marcar donde la categoría vino; sin categoría no decide (cuenta como")
    print("  no marcar), así que su columna de falsos positivos es un piso, no el número final.")

    print("\n  Las mismas, como FILTRO antes de elegir, con la regla y los dos denominadores:")
    ef_hoy2 = efecto(hoy2, et2, hoy2)
    fila_ef("hoy", ef_hoy2, ef_hoy2, "—")
    for n, fil in señales.items():
        fila_ef(n, efecto(seleccionar(filas2, filtro=fil), et2, hoy2), ef_hoy2, "—")
    print("  ⚠️ **C2 no necesita etiquetas ni tabla curada**: la categoría modal sale de los candidatos que el")
    print("  matcher ya acepta. Por eso mismo hereda su sesgo: donde la mayoría de los aceptados es otro")
    print("  producto —el azúcar de Coto del 14/09, con 24 candidatos que son mermeladas y edulcorantes— la")
    print("  modal se equivoca con el matcher. Y se midió sobre UNA captura: el 14/09 no trae categoría.")

    # ✏️ 23/09, revisión: el umbral, sobre ESTA fecha. En el 14/09 el corte 0,50 no perdía ningún correcto;
    # acá sí, y por eso "0,50 sale gratis" no se puede afirmar con una sola captura.
    (u2, saca2), peor_ok2, en_050 = corte_sin_perder(hoy2, et2)
    print(f"\n  El umbral en esta fecha: el corte más alto que no pierde un correcto es {u2:.3f} "
          f"(saca {len(saca2)} malos).")
    print(f"    Con 0,50: saca {len(en_050['saca_malos'])} malos y **pierde "
          f"{len(en_050['pierde_correctos'])} correctos**"
          + ("".join(f"\n      pierde {c}/{q} (s={sc:.3f}), que queda faltante"
                     for c, q, sc in en_050["pierde_correctos"]) if en_050["pierde_correctos"] else "."))

    print("\n  Los cambios de representante que hace C2:")
    for c in CADENAS:
        for x in efecto(seleccionar(filas2, filtro=f_c2), et2, hoy2)[c]["cambios"]:
            ant = x["antes"]["of"].producto[:40] if x["antes"] else "—"
            des = x["despues"]["of"].producto[:40] if x["despues"] else "— (sin representante)"
            print(f"    {c:<11} {x['item']:<19} {x['e_antes'] or '—':<13} -> {x['e_despues'] or '—':<13}"
                  f" {ant:<42} -> {des}")

    print("\n  C4 · COTA con la categoría DADA. No es una señal que se pueda correr hoy: el conjunto de")
    print("  categorías válidas sale de las etiquetas (ítem × cadena). Mide qué compraría una tabla curada de")
    print("  20 ítems × 5 cadenas, porque C1 falla por vocabulario: la categoría de Coto para el pan lactal es")
    print("  'Molde' y la de la picada vacuna es 'Bovinos' — ninguna contiene una palabra del ítem.")
    validas = defaultdict(set)
    for c in CADENAS:
        for prod in CANASTA:
            for f in U.candidatos(filas2, c, prod["nombre"]):
                if et2(f["cid"]) == "correcto" and categoria_de(f):
                    validas[(c, prod["nombre"])].add(categoria_de(f))
    print(f"  {'Cadena':<11} {'malos marcados':>15} {'correctos marcados':>19} {'ítems sin categoría válida':>27}")
    for c in CADENAS:
        malos = [(q, f) for (cc, q), (f, _s) in hoy2.items() if cc == c and et2(f["cid"]) == "otro producto"]
        oks = [(q, f) for (cc, q), (f, _s) in hoy2.items() if cc == c and et2(f["cid"]) == "correcto"]
        def fuera(q, f):
            v = validas[(c, q)]
            return bool(v) and categoria_de(f) not in v
        m = sum(1 for q, f in malos if fuera(q, f))
        fp = sum(1 for q, f in oks if fuera(q, f))
        sin = sum(1 for prod in CANASTA if not validas[(c, prod["nombre"])])
        print(f"  {c:<11} {f'{m}/{len(malos)}':>15} {f'{fp}/{len(oks)}':>19} {sin:>27}")

    print("\n  La categoría modal de los aceptados (C2), en las filas que hoy NO son correctas. Se ve por qué")
    print("  funciona —la modal es la categoría del producto de verdad y el impostor está en otra— y dónde no:")
    print("  cuando la mayoría de los aceptados es otro producto, la modal se equivoca con el matcher:")
    for c in CADENAS:
        for prod in CANASTA:
            q = prod["nombre"]
            par = hoy2.get((c, q))
            if par is None or et2(par[0]["cid"]) == "correcto":
                continue
            print(f"    {c:<11} {q:<19} {et2(par[0]['cid']) or '—':<13} modal: {modal_categoria(filas2, c, q)}")


def fuentes_profundas(cap2):
    """Las secciones de la captura nueva que NO son el request de producción, parseadas con el parser real."""
    return {"profundo": P.parsear(cap2.get("profundo") or {c: {} for c in CADENAS}),
            "alternativas": P.parsear(cap2.get("alternativas") or {c: {} for c in CADENAS}),
            "coto_no24": P.parsear({"Coto": cap2.get("coto_no24") or {}})}


def validar_etiquetas_profundas(cap2, extra, secs=None):
    """(3b) Cada etiqueta de una fuente profunda describe una fila que está en esa fuente, por clave de unión."""
    secs = secs or fuentes_profundas(cap2)
    alt_de = {qa: q for q, qs in (cap2.get("__alternativas") or {}).items() for qa in qs}
    presentes = set()
    for c in CADENAS:
        for q, fs in secs["profundo"][c].items():
            presentes |= {(q, clave_union(c, f["of"])) for f in fs}
        for qa, fs in secs["alternativas"][c].items():
            presentes |= {(alt_de.get(qa, qa), clave_union(c, f["of"])) for f in fs}
    for q, fs in secs["coto_no24"]["Coto"].items():
        presentes |= {(q, clave_union("Coto", f["of"])) for f in fs}
    # ✏️ 22/09: con la captura recortada, las filas repetidas viven solo en `datos`.
    for c in CADENAS:
        for q, fs in P.parsear(cap2["datos"])[c].items():
            presentes |= {(q, clave_union(c, f["of"])) for f in fs}
    errores = []
    for cid, x in extra.items():
        origen = cid.split("|")[0]
        if origen in CADENAS:
            continue
        k = (x["item"], (x["cadena"], ean_cruzable(x["ean"]) or "", NORM(x["titulo"] or "")))
        if k not in presentes:
            errores.append(f"(3b) {cid}: la etiqueta no corresponde a ninguna fila de las fuentes profundas")
    return errores


def fuentes_coto(cap2, filas2, secs, q):
    """
    [(nombre, filas)] de las fuentes de Coto para un ítem: la consulta de producción, `Nrpp=48`, `No=24` y
    las alternativas. Con la captura RECORTADA cada fuente se completa con las filas repetidas que el
    recorte movió a `datos` (`__recorte.quitadas_claves` dice exactamente cuáles), así los conteos son los
    de la captura entera.
    """
    quit_claves = (cap2.get("__recorte") or {}).get("quitadas_claves") or {}
    por_clave = {clave_union("Coto", f["of"]): f for f in filas2["Coto"].get(q, [])}

    def completar(seccion, consulta, fs):
        faltan = quit_claves.get(f"{seccion}|Coto|{consulta}") or []
        return list(fs) + [por_clave[tuple(k)] for k in faltan if tuple(k) in por_clave]

    fuentes = [("consulta de hoy (24)", filas2["Coto"].get(q, [])),
               ("Nrpp=48", completar("profundo", q, secs["profundo"]["Coto"].get(q, []))),
               ("No=24 (2ª página)", completar("coto_no24", q, secs["coto_no24"]["Coto"].get(q, [])))]
    fuentes += [(f"alternativa '{qa}'", completar("alternativas", qa, secs["alternativas"]["Coto"].get(qa, [])))
                for qa in (cap2.get("__alternativas") or {}).get(q, [])]
    return fuentes


def validar_reconstruccion_coto(cap2, filas2, secs=None):
    """
    (11) §H1b cuenta sobre la captura ENTERA, no sobre la recortada.

    La captura del 22/09 entró al repo recortada: a `profundo/Coto` se le sacaron 305 filas que repetían
    la consulta de hoy, con sus claves anotadas en `__recorte.quitadas_claves` para reconstruirlas. Contar
    con `len()` sobre lo que quedó daba 258 filas de `Nrpp=48` en vez de 563 y mostraba 0 en ocho ítems que
    sí traen filas — el informe cambiaba con el recorte, que es justo lo que el recorte promete que no pasa.
    Con una captura entera el recorte no existe y la cuenta tiene que dar igual.
    """
    secs = secs or fuentes_profundas(cap2)
    total = sum(len(dict(fuentes_coto(cap2, filas2, secs, p["nombre"])).get("Nrpp=48", [])) for p in CANASTA)
    en_captura = sum(len(v) for v in ((cap2.get("profundo") or {}).get("Coto") or {}).values())
    quitadas = ((cap2.get("__recorte") or {}).get("quitadas_por_seccion") or {}).get("profundo/Coto", 0)
    if total != en_captura + quitadas:
        return [f"(11) §H1b cuenta {total} filas de `Nrpp=48` en Coto y la captura entera tiene "
                f"{en_captura + quitadas} ({en_captura} en el archivo + {quitadas} que se llevó el recorte): "
                f"el conteo no reconstruye el recorte"]
    return []


def informe_coto_profundo(cap2, filas2, et_of):
    """
    §H — ¿el producto que falta aparece si se pregunta distinto o se mira más hondo? "Catálogo" solo se
    afirma con evidencia positiva; si no la hay, queda "no separable" y se dice.
    `et_of(item, cadena, oferta)` devuelve la etiqueta por la clave de unión, o None: las secciones
    "profundo" y "alternativas" se parsean aparte y no tienen `cid`.
    """
    secs = fuentes_profundas(cap2)
    prof, alt, no24 = secs["profundo"], secs["alternativas"], secs["coto_no24"]
    print("\n" + "=" * 100)
    print("§H · BUSCADOR CONTRA CATÁLOGO — cuántas filas devuelve cada cadena y qué hay más allá")
    print("=" * 100)
    print(f"  El request de producción pide 50 filas en VTEX (`FuenteVTEX.pagina`) y **24 en Coto**")
    print("  (`FuenteCoto.buscar` no manda `Nrpp`). Con `Nrpp=48` y con `No=24` Coto devuelve más:")
    print("  ⚠️ Pero los 0 filas **no son del `Nrpp`**: con `Nrpp=48` hay ítems que devuelven 0 y con el")
    print("  request de producción devuelven 24. Es intermitente, y la tabla de abajo lo muestra por ítem.")
    # ✏️ Revisión de Santiago (22/09): `Nrpp=48` vuelve a traer la primera página y `No=24` se solapa con
    # ella, así que sumar las tres fuentes cuenta de más. Todo lo de abajo deduplica por clave de unión.
    print(f"  {'Cadena':<11} {'filas hoy':>10} {'filas nuevas':>13} {'repetidas':>10} {'aceptados hoy':>14} "
          f"{'aceptados nuevos':>17}")
    for c in CADENAS:
        hoy_f = sum(len(filas2[c].get(p["nombre"], [])) for p in CANASTA)
        nuevas = repetidas = extra = 0
        for p in CANASTA:
            q = p["nombre"]
            vistos = {clave_union(c, f["of"]) for f in filas2[c].get(q, [])}
            fuentes = list(prof[c].get(q, []))
            if c == "Coto":
                fuentes += no24["Coto"].get(q, [])
            for f in fuentes:
                k = clave_union(c, f["of"])
                if k in vistos:
                    repetidas += 1
                    continue
                vistos.add(k)
                nuevas += 1
                if f["of"].disponible and f["of"].precio > 0 and s_hoy(p, f["of"]) >= UMBRAL_MATCH:
                    extra += 1
        ac_hoy = sum(1 for p in CANASTA for f in U.candidatos(filas2, c, p["nombre"])
                     if s_hoy(p, f["of"]) >= UMBRAL_MATCH)
        repetidas += (cap2.get("__recorte", {}).get("repetidas_por_cadena", {}) or {}).get(c, 0)
        print(f"  {c:<11} {hoy_f:>10} {nuevas:>13} {repetidas:>10} {ac_hoy:>14} {extra:>17}")

    # ✏️ Revisión de Santiago (22/09): la lista de ítems se calcula con ESTA fecha, no con la del 14/09.
    hoy2 = seleccionar(filas2)
    faltan = [prod["nombre"] for prod in CANASTA
              if (hoy2.get(("Coto", prod["nombre"])) is None
                  or et_of(prod["nombre"], "Coto", hoy2[("Coto", prod["nombre"])][0]["of"]) != "correcto")]
    print(f"\n  Los {len(faltan)} ítems donde Coto no tiene representante correcto EN ESTA FECHA: qué trae")
    print("  cada fuente. Si el producto aparece bajo el umbral, es el MATCHER; si no aparece en ninguna, es")
    print("  el buscador o el catálogo — y eso solo se afirma sobre filas ETIQUETADAS:")
    for q in faltan:
        prod = ITEM[q]
        print(f"\n    ── {q}")
        fuentes = fuentes_coto(cap2, filas2, secs, q)
        for nombre, fs in fuentes:
            vivos = [(s_hoy(prod, f["of"]), f) for f in fs if f["of"].disponible and f["of"].precio > 0]
            ok = [x for x in vivos if x[0] >= UMBRAL_MATCH]
            correctos = [(s, f) for s, f in vivos if et_of(q, "Coto", f["of"]) == "correcto"]
            sin_et = sum(1 for _s, f in vivos if et_of(q, "Coto", f["of"]) is None)
            print(f"      {nombre:<24} {len(fs):>3} filas · {len(ok):>2} pasan el umbral · "
                  f"{len(correctos):>2} etiquetados correctos · {sin_et:>2} sin etiqueta")
            for s, f in sorted(correctos or vivos, key=lambda t: (-t[0], t[1]["of"].precio,
                                                                  t[1]["of"].producto))[:3]:
                e = et_of(q, "Coto", f["of"])
                print(f"          s={s:.3f} ${f['of'].precio:>9,.0f} {(e or '—'):<13} {f['of'].producto[:44]:<44}"
                      f" | {categoria_de(f)}")

    # ✏️ 23/09, revisión: los 0 filas por ítem y por request, y qué pasa si Coto trae 48 en vez de 24.
    # ✏️ 23/09, segunda revisión: los tres conteos salen de `fuentes_coto`, que RECONSTRUYE lo
    # que el recorte se llevó. Contarlos con `len()` sobre la captura recortada le sacaba a `Nrpp=48`
    # las 305 filas repetidas y mostraba 0 en ocho ítems que sí traen filas: la validación (11) lo
    # cuida, y la mutación M10 la pone en rojo.
    print("\n  §H1b · FILAS POR REQUEST Y POR ÍTEM EN COTO — los 0 son intermitentes, no del `Nrpp`:")
    print(f"    {'Ítem':<20} {'producción':>11} {'Nrpp=48':>8} {'No=24':>6}   nota")
    for prod in CANASTA:
        q = prod["nombre"]
        fu = dict(fuentes_coto(cap2, filas2, secs, q))
        n_hoy = len((cap2["datos"]["Coto"] or {}).get(q) or [])
        n_prof = len(fu.get("Nrpp=48", []))
        n_no24 = len(fu.get("No=24 (2ª página)", []))
        nota = ""
        if n_hoy == 0 and (n_prof or n_no24):
            nota = "producción devuelve 0 y los otros requests traen filas"
        elif n_hoy and n_prof == 0:
            nota = "Nrpp=48 devuelve 0 donde producción trae filas"
        elif not (n_hoy or n_prof or n_no24):
            nota = "0 en los tres: no separable"
        print(f"    {q:<20} {n_hoy:>11} {n_prof:>8} {n_no24:>6}   {nota}")

    print("\n  §H1c · SI COTO TRAJERA 48 FILAS (producción + `No=24`), ¿mejora? Medido, no supuesto:")
    filas48 = {c: filas2[c] for c in CADENAS}
    filas48["Coto"] = {}
    for prod in CANASTA:
        q = prod["nombre"]
        fu = dict(fuentes_coto(cap2, filas2, secs, q))
        vistos, juntas = set(), []
        for nombre in ("consulta de hoy (24)", "No=24 (2ª página)"):
            for f in fu.get(nombre, []):
                k = clave_union("Coto", f["of"])
                if k not in vistos:
                    vistos.add(k)
                    juntas.append(f)
        filas48["Coto"][q] = juntas
    e48 = seleccionar(filas48)
    n0 = Counter(et_of(q, "Coto", f["of"]) or "sin etiqueta" for (c, q), (f, _s) in hoy2.items() if c == "Coto")
    n1 = Counter(et_of(q, "Coto", f["of"]) or "sin etiqueta" for (c, q), (f, _s) in e48.items() if c == "Coto")
    print(f"    Coto con 24 filas: {dict(n0)}")
    print(f"    Coto con 48 filas: {dict(n1)}")
    for prod in CANASTA:
        q = prod["nombre"]
        a, b = hoy2.get(("Coto", q)), e48.get(("Coto", q))
        if (a[0]["of"].producto if a else None) != (b[0]["of"].producto if b else None):
            ea = (et_of(q, "Coto", a[0]["of"]) if a else "faltante") or "—"
            eb = (et_of(q, "Coto", b[0]["of"]) if b else "faltante") or "—"
            print(f"      {q:<20} {ea:<13} -> {eb:<13} {(a[0]['of'].producto[:32] if a else '—'):<34} -> "
                  f"{(b[0]['of'].producto[:36] if b else '—')}")
    print("    **Pedir más filas no está medido como arreglo:** gana el detergente y pierde la manteca, que se")
    print("    va a una 'Figazzita Manteca' que empata en 0,733 y gana por precio. Lo que sí se sostiene es que")
    print("    `FuenteCoto` puede devolver 0 filas sin error y la app no distingue 'no lo tiene' de 'no contestó'.")

    print("\n  §H2 · LA FORMA DE LA PALABRA — candidatos que el solapamiento EXACTO de tokens pierde y que")
    print("  un singular/plural mínimo recuperaría (`huevo` contra `huevos`). Por cadena, sobre la fecha nueva:")
    print(f"  {'Cadena':<11} {'pierde':>7} {'de esos, correctos':>19}   ejemplo")
    for c in CADENAS:
        perdidos = []
        for prod in CANASTA:
            t_item = precios_vtex._tokens(prod["nombre"], NORM)
            r_item = {raiz(t) for t in t_item}
            for f in U.candidatos(filas2, c, prod["nombre"]):
                if s_hoy(prod, f["of"]) >= UMBRAL_MATCH:
                    continue
                t_of = precios_vtex._tokens(f["of"].producto, NORM)
                if not (t_of & t_item) and ({raiz(t) for t in t_of} & r_item):
                    perdidos.append((prod["nombre"], f))
        ok = [x for x in perdidos if et_of(x[0], c, x[1]["of"]) == "correcto"]
        ej = (f"{ok[0][0]} → {ok[0][1]['of'].producto[:40]}" if ok else
              (f"{perdidos[0][0]} → {perdidos[0][1]['of'].producto[:40]}" if perdidos else "—"))
        print(f"  {c:<11} {len(perdidos):>7} {len(ok):>19}   {ej}")


# ─────────────────────────────────────────────────────────────────
#  QUÉ ETIQUETAR (--a-etiquetar)
# ─────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────
#  QUÉ ETIQUETAR (--a-etiquetar)
# ─────────────────────────────────────────────────────────────────

def a_etiquetar(filas):
    """
    Todo candidato que pueda terminar siendo representante en alguna variante, más lo que pide el paso 3:
      · pasa el umbral con el score de hoy O sin tamaño (el pool de toda variante: ninguna baja el umbral);
      · Coto: todos sus candidatos (aceptados y rechazados), para separar forma de título y buscador;
      · VTEX: los rechazados de los 4 ítems donde Coto falla, como control.
    """
    out = []
    for c in CADENAS:
        for prod in CANASTA:
            q = prod["nombre"]
            for f in U.candidatos(filas, c, q):
                s, s2 = s_hoy(prod, f["of"]), s_sin_tamano(prod, f["of"])
                motivo = ("pool" if max(s, s2) >= UMBRAL_MATCH else
                          "coto rechazo" if c == "Coto" else
                          "control rechazo" if q in COTO_FALLA else None)
                if motivo:
                    out.append({"cid": f["cid"], "cadena": c, "item": q, "titulo": f["of"].producto,
                                "marca": f["of"].marca, "ean": f["of"].ean, "precio": f["of"].precio,
                                "score": s, "score_sin_tamano": s2, "motivo": motivo})
    return out


# ─────────────────────────────────────────────────────────────────
#  CAPTURA (--capturar) — la única parte con red
# ─────────────────────────────────────────────────────────────────

CAMPOS_CAT_VTEX = ("productId", "linkText", "categoryId", "categories", "categoriesIds")


def recortar_vtex(p):
    r = P.recortar_vtex(p)
    for k in CAMPOS_CAT_VTEX:
        r[k] = p.get(k)
    return r


def recortar_coto(rec):
    """Como `P.recortar_coto(completo=True)` —el registro entero menos ruido: trae product.category,
    allAncestors, LDEPAR, LCLASE, DEPTO, CLASE— más la disponibilidad por sucursal (`sDisp_*`), que el
    parser de Coto no lee (nunca pone `disponible` en falso)."""
    r = P.recortar_coto(rec, completo=True)
    a = ((rec.get("records") or [{}])[0]).get("attributes") or {}
    r["sdisp"] = {k: v for k, v in a.items() if k.startswith("product.sDisp_")}
    return r


def pedir_vtex_pagina(cadena, q, desde, hasta):
    """El request de `FuenteVTEX._pedir`, con otra ventana."""
    url = (f"{VTEX_BASES[cadena]}/api/catalog_system/pub/products/search/"
           f"?ft={requests.utils.quote(q)}&_from={desde}&_to={hasta}")
    r = requests.get(url, headers={"User-Agent": UA, "Accept": "application/json"}, timeout=(8, 20))
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        raise ValueError(f"esperaba lista, vino {type(data).__name__}")
    return data


def pedir_coto_con(q, **extra):
    """El request de `FuenteCoto.buscar` con parámetros de Endeca agregados (Nrpp, No)."""
    r = requests.get(FuenteCoto.BASE, params={"Ntt": q, "format": "json", **extra},
                     headers={"User-Agent": UA, "Accept": "application/json"}, timeout=(8, 15),
                     allow_redirects=True)
    r.raise_for_status()
    return FuenteCoto._hallar_records(r.json())


def singular(s):
    return " ".join(w[:-1] if len(w) > 3 and w.lower().endswith("s") else w for w in s.split())


def alternativas(prod):
    """Regla fija: el nombre sin tildes, en singular, y cada frase de `palabras_clave`. Sin elegir a mano."""
    vistas, out = {prod["nombre"].lower()}, []
    for q in [sin_tildes(prod["nombre"]), singular(prod["nombre"]), *(prod.get("palabras_clave") or [])]:
        if q.lower() not in vistas:
            vistas.add(q.lower())
            out.append(q)
    return out


def capturar():
    cap = {"__fecha": datetime.now().isoformat(timespec="seconds"),
           "__origen": "red real desde la máquina que corrió el script (tests/medir_representantes.py)",
           "__consultas": [p["nombre"] for p in CANASTA],
           "__alternativas": {q: alternativas(ITEM[q]) for q in COTO_FALLA},
           "__profundo": {"VTEX": "_from=50&_to=99", "Coto Nrpp48": "Nrpp=48", "Coto No24": "No=24"},
           "datos": {c: {} for c in CADENAS}, "profundo": {c: {} for c in CADENAS},
           "coto_no24": {}, "alternativas": {c: {} for c in CADENAS}, "fallas": {}}
    vtex = FuenteVTEX()
    tandas = []
    for q in cap["__consultas"]:
        tandas.append(("datos", q, {**{c: (lambda c=c, q=q: vtex._pedir(c, q)) for c in VTEX},
                                    "Coto": lambda q=q: M.pedir_coto(q)}))
        tandas.append(("profundo", q, {**{c: (lambda c=c, q=q: pedir_vtex_pagina(c, q, 50, 99)) for c in VTEX},
                                       "Coto": lambda q=q: pedir_coto_con(q, Nrpp=48),
                                       "Coto No24": lambda q=q: pedir_coto_con(q, No=24)}))
    for item, alts in cap["__alternativas"].items():
        for qa in alts:
            tandas.append(("alternativas", qa, {**{c: (lambda c=c, q=qa: vtex._pedir(c, q)) for c in VTEX},
                                                "Coto": lambda q=qa: M.pedir_coto(q)}))
    for i, (seccion, q, pedidos) in enumerate(tandas, 1):
        print(f"  [{i}/{len(tandas)}] {seccion}: {q}", flush=True)
        with ThreadPoolExecutor(max_workers=len(pedidos)) as pool:
            futs = {c: pool.submit(fn) for c, fn in pedidos.items()}
            for c, fut in futs.items():
                try:
                    crudos = fut.result()
                except Exception as e:                       # noqa: BLE001 — se reporta
                    cap["fallas"][f"{seccion} / {c} / {q}"] = f"{type(e).__name__}: {str(e)[:120]}"
                    continue
                filas = [recortar_coto(r) if c.startswith("Coto") else recortar_vtex(r) for r in crudos]
                if c == "Coto No24":
                    cap["coto_no24"][q] = filas
                else:
                    cap[seccion][c][q] = filas

    # Presencia de la categoría, por cadena: si una cadena no la trae, se dice acá y no se asume.
    cob = {}
    for c in CADENAS:
        filas = [r for fs in cap["datos"][c].values() for r in fs]
        if c == "Coto":
            con = sum(1 for r in filas if (r.get("completo") or {}).get("product.category"))
        else:
            con = sum(1 for r in filas if r.get("categories"))
        cob[c] = [con, len(filas)]
    cap["__cobertura_categoria"] = cob
    return cap


# ─────────────────────────────────────────────────────────────────
#  RECORTE DE LA CAPTURA (--recortar) — para que entre al repo
# ─────────────────────────────────────────────────────────────────

# Lo único que el instrumento lee del registro completo de Coto. El resto son 58 claves por fila (1,4 KB)
# que nadie mira: duplicados del nombre, precios ya parseados, ranks.
COTO_COMPLETO_USADO = ("product.category", "product.allAncestors", "allAncestors.displayName",
                       "product.LDEPAR", "product.LCLASE", "product.DEPTO", "product.CLASE",
                       "product.dtoCaracteristicas", "product.description", "product.eanPrincipal",
                       "product.unidades.esPesable")


def recortar(cap):
    """
    La captura entera pesa 11,4 MB y no entra cómoda al repo. El recorte saca **solo lo que ninguna
    medición lee**, y deja registrado en `__recorte` lo que saca:
      · las filas de `profundo`, `coto_no24` y `alternativas` cuya clave de unión ya está en `datos` —
        §H las deduplica igual, y los conteos de repetidas salen de `__recorte`;
      · en Coto, las claves de `completo` que no están en `COTO_COMPLETO_USADO`;
      · `sdisp`, que se reemplaza por el histograma de códigos por sucursal, que es el dato del hallazgo
        (toda fila de Coto tiene sucursales con `1004` y con `1001`: la disponibilidad es por sucursal).
    """
    out = json.loads(json.dumps(cap))
    alt_de = {qa: q for q, qs in (cap.get("__alternativas") or {}).items() for qa in qs}
    filas = P.parsear(cap["datos"])
    claves = {c: {q: {clave_union(c, f["of"]) for f in fs} for q, fs in filas[c].items()} for c in CADENAS}
    quitadas, detalle = Counter(), defaultdict(list)

    def recortar_fila(c, r):
        if c != "Coto":
            return r
        comp = r.get("completo") or {}
        if comp:
            r["completo"] = {k: v for k, v in comp.items() if k in COTO_COMPLETO_USADO}
        sd = r.pop("sdisp", None)
        if sd:
            r["sdisp_codigos"] = dict(Counter(x[0] if isinstance(x, list) else x for x in sd.values()))
        return r

    for seccion in ("profundo", "alternativas"):
        for c in CADENAS:
            for q, crudos in (out[seccion].get(c) or {}).items():
                item = alt_de.get(q, q)
                ya = claves[c].get(item, set())
                nuevas = []
                for raw in crudos:
                    fs = P.parsear({c: {q: [raw]}})[c][q]
                    k = clave_union(c, fs[0]["of"]) if fs else None
                    if k is not None and k in ya:
                        quitadas[f"{seccion}/{c}"] += 1
                        detalle[f"{seccion}|{c}|{q}"].append(list(k))
                        continue
                    nuevas.append(recortar_fila(c, raw))
                out[seccion][c][q] = nuevas
    for q, crudos in (out.get("coto_no24") or {}).items():
        ya = claves["Coto"].get(q, set())
        nuevas = []
        for raw in crudos:
            fs = P.parsear({"Coto": {q: [raw]}})["Coto"][q]
            k = clave_union("Coto", fs[0]["of"]) if fs else None
            if k is not None and k in ya:
                quitadas["coto_no24/Coto"] += 1
                detalle[f"coto_no24|Coto|{q}"].append(list(k))
                continue
            nuevas.append(recortar_fila("Coto", raw))
        out["coto_no24"][q] = nuevas
    for c in CADENAS:
        for q, crudos in out["datos"][c].items():
            out["datos"][c][q] = [recortar_fila(c, r) for r in crudos]

    # Repetidas por cadena, como las cuenta §H: contra `datos`, dentro de las fuentes profundas.
    rep = Counter()
    for k, n in quitadas.items():
        # §H cuenta repetidas de `profundo` y `No=24` contra la consulta de hoy; `alternativas` va aparte,
        # así el informe da lo mismo con la captura entera y con la recortada.
        if not k.startswith("alternativas/"):
            rep[k.split("/")[1]] += n
    out["__recorte"] = {
        "que": "filas de profundo/coto_no24/alternativas cuya clave de unión ya está en datos; en Coto, las "
               "claves de `completo` fuera de COTO_COMPLETO_USADO y `sdisp` (queda su histograma)",
        "repetidas_por_cadena": dict(rep),
        "quitadas_por_seccion": dict(quitadas),
        # Las claves de unión de cada fila quitada, por sección|cadena|consulta: con esto §H reconstruye los
        # conteos exactos de la captura entera, así el informe no cambia por el recorte.
        "quitadas_claves": {k: v for k, v in detalle.items()},
    }
    return out


# ─────────────────────────────────────────────────────────────────
#  CLI
# ─────────────────────────────────────────────────────────────────

def main_cli():
    ap = argparse.ArgumentParser()
    ap.add_argument("--desde", default=str(CAPTURA), help="captura del 14/09 (sin red)")
    ap.add_argument("--a-etiquetar", metavar="SALIDA", help="escribe los candidatos a etiquetar y sale")
    ap.add_argument("--capturar", metavar="SALIDA", help="captura con RED REAL (la corre Santiago)")
    ap.add_argument("--recortar", metavar="SALIDA", help="escribe la captura de --nueva recortada para el repo")
    ap.add_argument("--nueva", default=str(RAIZ / "tests" / "capturas" / "representantes_2026-09-22.json"),
                    help="captura con fecha posterior (categoría y holdout); si no está, §G y §H no corren")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    logging.disable(logging.INFO)

    if args.capturar:
        cap = capturar()
        Path(args.capturar).write_text(json.dumps(cap, ensure_ascii=False), encoding="utf-8")
        tam = Path(args.capturar).stat().st_size
        print(f"\nCaptura {cap['__fecha']} → {args.capturar} ({tam / 1e6:.1f} MB)")
        for c, (con, tot) in cap["__cobertura_categoria"].items():
            print(f"  {c:<11} categoría en {con} de {tot} candidatos")
        prof = {c: sum(len(v) for v in cap["profundo"][c].values()) for c in CADENAS}
        print(f"  profundidad: {prof} · Coto No=24: {sum(len(v) for v in cap['coto_no24'].values())}")
        for k, v in cap["fallas"].items():
            print(f"  FALLA {k}: {v}")
        sys.exit(2 if cap["fallas"] else 0)

    if args.recortar:
        entrada = Path(args.nueva)
        cap2 = json.load(open(entrada, encoding="utf-8"))
        chico = recortar(cap2)
        Path(args.recortar).write_text(json.dumps(chico, ensure_ascii=False), encoding="utf-8")
        print(f"{entrada.name} ({entrada.stat().st_size / 1e6:.1f} MB) -> {args.recortar} "
              f"({Path(args.recortar).stat().st_size / 1e6:.1f} MB)")
        print(f"  repetidas quitadas: {chico['__recorte']['quitadas_por_seccion']}")
        return

    cap, filas = cargar(args.desde)
    dia = datetime.fromisoformat(cap["__fecha"]).weekday()
    try:
        ctx = validar(cap, filas, dia)
    except InstrumentoInvalido as e:
        print(f"ERROR (InstrumentoInvalido):\n  {e}", file=sys.stderr)
        sys.exit(1)

    if args.a_etiquetar:
        lista = a_etiquetar(filas)
        reps = {f["cid"] for f in ctx["reps"].values()}
        faltan = reps - {x["cid"] for x in lista}
        if faltan:
            print(f"ERROR: representantes fuera de la lista a etiquetar: {sorted(faltan)}", file=sys.stderr)
            sys.exit(1)
        elegidos = {f["cid"] for el in ctx["elegidos"].values() for f, _s in el.values()}
        fuera = elegidos - {x["cid"] for x in lista}
        if fuera:
            print(f"ERROR: elegidos por alguna variante fuera de la lista: {sorted(fuera)}", file=sys.stderr)
            sys.exit(1)
        for x in lista:
            x["representante"] = x["cid"] in reps
        Path(args.a_etiquetar).write_text(json.dumps(lista, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{len(lista)} candidatos a etiquetar → {args.a_etiquetar}: {dict(Counter(x['motivo'] for x in lista))}")
        print(f"  por cadena: {dict(Counter(x['cadena'] for x in lista))}")
        print(f"  elegidos por alguna variante: {len(elegidos)} · representantes de hoy: {len(reps)}")
        return

    bases = bases_aceptacion(filas)
    hoy = ctx["elegidos"]["hoy"]
    try:
        etq, doc_etq = cargar_etiquetas(filas, ctx["elegidos"], ctx["reps"])
    except InstrumentoInvalido as e:
        print(f"ERROR (InstrumentoInvalido):\n  {e}", file=sys.stderr)
        sys.exit(1)
    e6, sigue = validar_respuestas_conocidas(filas, etq, hoy)
    e8, estado_e = validar_esperado_e(cap, etq, hoy, bases)
    errores = validar_aceptacion(bases) + e6 + e8
    if errores:
        print("ERROR (InstrumentoInvalido):\n  " + "\n  ".join(errores), file=sys.stderr)
        sys.exit(1)

    print("=" * 100)
    print("VALIDACIÓN — el instrumento mide lo que dice medir")
    print("=" * 100)
    print(f"  (1) captura {cap['__fecha']} y código: {ctx['estado_t25']}")
    print(f"      cobertura de (c): {ctx['estado_c']}")
    print("  (2) el lazo reproduce `buscar_precios_online` en todas las variantes que producción puede correr")
    print(f"  (3–5) etiquetas frescas, cerradas y consistentes por EAN: {len(etq)} candidatos, "
          f"{len(doc_etq['__inconsistencias_ean'])} inconsistencias documentadas")
    print("  (6) fixtures del 09/09: reproduce la aceptación documentada en test_unidades.py, Coto 48/111")
    print("  (7) con umbral 0,47 Coto/Azúcar se queda sin representante; sin las medialunas, Carrefour/Manteca")
    print(f"      pasa a {sigue[0][:50]!r} (${sigue[2]:,.0f}), etiquetado {sigue[1]!r}")
    print(f"  (8) {estado_e}")
    print("  (9) las mutaciones las corre `python -m tests.medir_representantes_mutaciones`: las 10 en rojo")
    print(" (10) la captura nueva está completa, sin ítems vacíos fuera de VACIOS_CONOCIDOS, y el lazo la")
    print("      corre igual que producción · (3b) las etiquetas de fuentes profundas describen filas reales")
    print(" (11) §H1b cuenta las filas de la captura ENTERA: reconstruye las que se llevó el recorte")

    informe_aceptacion(bases)
    informe_cobertura_correcta(filas, etq)
    informe_senales(ctx, etq, filas)
    informe_clasificador(ctx, etq, filas)
    informe_in_sample(ctx, etq, filas)
    informe_umbral(ctx, etq, filas)
    informe_ean(ctx, etq, filas)
    informe_precio(ctx, etq, filas, dia)
    informe_coto(ctx, etq, filas, bases)

    ruta2 = Path(args.nueva)
    if not ruta2.exists():
        print(f"\n§G y §H no corren: falta la captura con fecha posterior ({ruta2.name}).")
        return
    cap2, filas2 = cargar(ruta2)
    et2, et_of, herencia, extra = etiquetas_nueva(filas, filas2, etq)
    secs2 = fuentes_profundas(cap2)
    err = (validar_nueva(cap2, filas2) + validar_etiquetas_profundas(cap2, extra, secs2)
           + validar_reconstruccion_coto(cap2, filas2, secs2))
    if err:
        print("ERROR (InstrumentoInvalido):\n  " + "\n  ".join(err), file=sys.stderr)
        sys.exit(1)
    informe_nueva(filas2, cap2, et2, herencia, extra)
    informe_coto_profundo(cap2, filas2, et_of)


if __name__ == "__main__":
    main_cli()
