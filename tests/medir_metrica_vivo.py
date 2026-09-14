"""
Tarea 19, continuación (13/09/2026) — ¿la API de las cadenas corrige la métrica que el
título parsea mal, o el bug de "30 Mts x 4 Un" está en producción?

Instrumento de MEDICIÓN con RED REAL. No es test ni guard: fuera del CI a propósito.

La pregunta que las fixtures no pueden contestar: `precios_vtex._precio_por_100u`
(precios_vtex.py:197-203) usa primero `measurementUnit` + `unitMultiplier` y recién después
el título, pero las fixtures del 09/09 no traen esos campos. La Tarea 19 midió 18 EANs de
papel con métrica distinta según la cadena sin poder saber si en VTEX la API lo tapa.

Qué hace, sin tocar el código de producción:
  1. Captura en vivo las 5 cadenas: `FuenteVTEX._pedir` (el mismo request que producción)
     y el endpoint de Coto con los mismos parámetros que `FuenteCoto.buscar`. Guarda una
     copia recortada con --guardar y la re-analiza sin red con --desde.
  2. Parsea con `FuenteVTEX._a_oferta` / `FuenteCoto._parsear` y calcula la métrica con
     `_precio_por_100u` tal cual (`fuente_contenido` = api | nombre), y además la del título
     solo: el mismo código con los campos de la API en None.
  3. Referencia del título INDEPENDIENTE del parser del backend (`leer_titulo`): regex
     propias, estrictas. Donde el título es ambiguo no adivina, dice "ambiguo". Supone que
     en "N u. x M" la M es POR UNIDAD: es la convención del papel, pero no de todo rubro
     ("Pack x 2 190 Gr." puede ser el total). Por eso la comparación contra el backend dice
     "coincide con N×M", no "está bien", y hay una verdad de terreno aparte:
  4. Verdad de terreno que no sale de ningún título: en Coto, activePrice / referencePrice
     es el contenido en la unidad de referencia de Coto (m² en papel, L en bebidas, kg en
     alimentos). Se valida primero contra los títulos de la propia Coto y recién si valida
     se cruza por EAN con las VTEX.
  5. Representantes: `buscar_precios_online` sobre la captura, el pipeline de producción.

Todo sale POR CADENA (CLAUDE.md, regla de las constantes derivadas de datos).

Salida con código:
  0  medición completa
  1  el instrumento no reproduce el código que dice medir (no se informa nada)
  2  medición incompleta: alguna cadena o consulta falló (se informa, marcado)

Desde sepa_backend/:
    venv\\Scripts\\python.exe -m tests.medir_metrica_vivo --guardar captura.json      # red real
    venv\\Scripts\\python.exe -m tests.medir_metrica_vivo --desde tests/capturas/metrica_vivo_2026-09-13.json

La captura del 13/09 19:02 ART es la que respalda el resultado escrito en la Tarea 19
(continuación). Desde el fix de la Tarea 19 (14/09) es además INSUMO DEL CI: `test_metrica_titulos.py`
la lee con `parsear` y usa `leer_titulo` como referencia. El script sigue fuera del CI; la captura y
esas dos funciones no. Si tocás cualquiera de las tres, corré ese test.
"""
import argparse
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime

import requests

from fuentes import UA, FuenteCoto, FuenteVTEX, Resultado
from precios_vtex import _UNIDAD_SIN_CONTENIDO, _precio_por_100u, buscar_precios_online
from main import _extraer_cantidades_desc, normalizar

VTEX = ["Carrefour", "Día", "Vea", "Chango Más"]
CADENAS = VTEX + ["Coto"]

# Los tres primeros son de CANASTA_DEFAULT (main.py). El resto se eligió porque sus títulos
# declaran "N u. x M unidad" en rubros que no son papel: un producto solo no alcanza.
ITEMS = [
    {"nombre": "Papel higienico", "cantidad": 1, "unidad": "pack", "categoria": "Higiene"},
    {"nombre": "Pollo entero", "cantidad": 2, "unidad": "kg", "categoria": "Carnes"},
    {"nombre": "Carne picada", "cantidad": 1, "unidad": "kg", "categoria": "Carnes"},
    {"nombre": "Cerveza lata", "cantidad": 1, "unidad": "pack"},
    {"nombre": "Agua mineral pack", "cantidad": 1, "unidad": "pack"},
    {"nombre": "Gaseosa pack", "cantidad": 1, "unidad": "pack"},
    {"nombre": "Postre Danette", "cantidad": 1, "unidad": "pack"},
]
CONSULTAS = [i["nombre"] for i in ITEMS]

SPEC_VTEX = ("PrecioPorUnd", "UnidaddeMedida", "pricePerUnit", "Precio x unidad")
ATTRS_COTO = ("product.description", "sku.displayName", "sku.activePrice", "sku.referencePrice",
              "product.eanPrincipal", "product.brand", "product.dtoDescuentos",
              "product.dtoDescuentosMediosPago", "product.CANTIDAD", "sku.quantity",
              "product.unidades.descUnidad", "product.unidades.esPesable",
              "product.dtoCaracteristicas")

TOL = 0.02          # dos cantidades base coinciden si difieren menos de 2%
TOL_COTO = 0.05     # contra el cociente de Coto: referencePrice viene redondeado a centavos

# Unidad de referencia de Coto → a qué base del backend se convierte el cociente.
# longitud: Coto usa m²; con rollos de 10 cm de ancho, 1 m² = 10 m. Es un SUPUESTO y por
# eso se valida contra los títulos de Coto antes de usarlo.
COTO_A_BASE = {"longitud": 10.0, "volumen": 1000.0, "peso": 1000.0}


class InstrumentoInvalido(Exception):
    pass


# ─────────────────────────────────────────────────────────────────
#  CAPTURA
# ─────────────────────────────────────────────────────────────────

def recortar_vtex(p):
    it = (p.get("items") or [{}])[0]
    co = ((it.get("sellers") or [{}])[0]).get("commertialOffer") or {}
    oferta = {k: co.get(k) for k in ("Price", "ListPrice", "PriceWithoutDiscount",
                                     "FullSellingPrice", "IsAvailable", "AvailableQuantity")}
    oferta["Teasers"] = []
    return {"productName": p.get("productName"), "brand": p.get("brand"),
            "spec": {k: p[k] for k in SPEC_VTEX if k in p},
            "items": [{"ean": it.get("ean"), "measurementUnit": it.get("measurementUnit"),
                       "unitMultiplier": it.get("unitMultiplier"),
                       "sellers": [{"commertialOffer": oferta}]}]}


def recortar_coto(rec):
    a = ((rec.get("records") or [{}])[0]).get("attributes") or {}
    return {"records": [{"attributes": {k: a[k] for k in ATTRS_COTO if k in a}}]}


def pedir_coto(q):
    """Mismo request que `FuenteCoto.buscar`, pero devuelve los records crudos."""
    r = requests.get(FuenteCoto.BASE, params={"Ntt": q, "format": "json"},
                     headers={"User-Agent": UA, "Accept": "application/json"},
                     timeout=(8, 15), allow_redirects=True)
    r.raise_for_status()
    return FuenteCoto._hallar_records(r.json())


def capturar():
    cap = {"__fecha": datetime.now().isoformat(timespec="seconds"),
           "__origen": "red real desde la máquina que corrió el script",
           "datos": {c: {} for c in CADENAS}, "fallas": {}}
    vtex = FuenteVTEX()
    for q in CONSULTAS:
        with ThreadPoolExecutor(max_workers=len(CADENAS)) as pool:
            futs = {c: pool.submit(vtex._pedir, c, q) for c in VTEX}
            futs["Coto"] = pool.submit(pedir_coto, q)
            for c, fut in futs.items():
                try:
                    crudos = fut.result()
                except Exception as e:                   # noqa: BLE001 — se reporta
                    cap["fallas"][f"{c} / {q}"] = f"{type(e).__name__}: {str(e)[:120]}"
                    continue
                recortar = recortar_coto if c == "Coto" else recortar_vtex
                cap["datos"][c][q] = [recortar(r) for r in crudos]
    return cap


# ─────────────────────────────────────────────────────────────────
#  REFERENCIA DEL TÍTULO — independiente de _extraer_cantidades_desc
# ─────────────────────────────────────────────────────────────────

_MEDIDA = {}
for _u in ("kg", "kgs", "kilo", "kilos"):
    _MEDIDA[_u] = ("peso", 1000.0)
for _u in ("g", "gr", "grs", "grm", "grms", "gramo", "gramos"):
    _MEDIDA[_u] = ("peso", 1.0)
for _u in ("ml", "cc", "cmq"):
    _MEDIDA[_u] = ("volumen", 1.0)
for _u in ("l", "lt", "lts", "ltr", "ltrs", "litro", "litros"):
    _MEDIDA[_u] = ("volumen", 1000.0)
for _u in ("m", "mt", "mts", "mtr", "mtrs", "metro", "metros"):
    _MEDIDA[_u] = ("longitud", 1.0)
_CONTABLE = {"u", "un", "uni", "unid", "unids", "unidad", "unidades", "ud", "uds",
             "rollo", "rollos", "lata", "latas", "botella", "botellas"}
_ANCHO = {"cm"}
_PACK_PALABRA = {"two": 2, "four": 4, "six": 6, "eight": 8, "twelve": 12}

_NUM = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)(?!\d)")
# Palabra pegada o separada después del número. Corta en "x"+dígito ("473mlx6"), en punto
# ("80 m.") o en fin de palabra ("30 mts 4"). Perezosa: "mts" no se lee como "m".
_SUFIJO = re.compile(r"\s*([a-z]+?)(?:x(?=\d)|\.|(?![a-z0-9]))")


def _norm_ref(t):
    t = unicodedata.normalize("NFD", t or "")
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Mn").lower()
    t = t.replace("\r", " ").replace("\n", " ")
    return re.sub(r"(\d),(\d)", r"\1.\2", t)


def leer_titulo(titulo):
    """
    {"tipo", "base", "n", "m", "motivo"} o {"tipo": None, "motivo"}.

    `base` = contenido total en g / ml / m / u, suponiendo que en "N u. x M" la M es por
    unidad. `motivo` explica cuando no se da número: nunca se adivina.
    """
    t = _norm_ref(titulo)
    medidas, contables, sueltos, area = [], [], [], []
    for mt in _NUM.finditer(t):
        val = float(mt.group(1))
        antes, despues = t[:mt.start()], t[mt.end():]
        if re.match(r"\s*%", despues):
            continue
        if re.match(r"\s*m2(?![a-z0-9])", despues):
            area.append(val)
            continue
        suf = _SUFIJO.match(despues)
        palabra = suf.group(1) if suf else None
        if palabra in _MEDIDA:
            tipo, f = _MEDIDA[palabra]
            medidas.append((tipo, val * f))
        elif palabra in _CONTABLE:
            contables.append(val)
        elif palabra in _ANCHO:
            continue
        elif re.search(r"(?:\bx|[a-z]x|pack|pck)\s*$", antes):
            contables.append(val)
        elif re.match(r"\s*x\s*\d", despues):
            contables.append(val)
        else:
            sueltos.append(val)
    for palabra, n in _PACK_PALABRA.items():
        if re.search(rf"\b{palabra}\s*pack\b", t):
            contables.append(float(n))

    if area:
        return {"tipo": None, "motivo": f"declara m² ({area})", "area": area}
    ms = sorted(set(medidas))
    ns = sorted(set(n for n in contables if n != 1))
    if sueltos:
        return {"tipo": None, "motivo": f"número suelto {sueltos}"}
    if len(ms) > 1:
        return {"tipo": None, "motivo": f"ambiguo: {len(ms)} medidas {ms}"}
    if len(ns) > 1:
        return {"tipo": None, "motivo": f"ambiguo: contables {ns}"}
    n = ns[0] if ns else 1.0
    if ms:
        tipo, m = ms[0]
        return {"tipo": tipo, "base": m * n, "n": n, "m": m,
                "motivo": "N×M" if n > 1 else "solo medida"}
    if ns:
        return {"tipo": "count", "base": n, "n": n, "m": None, "motivo": "solo contable"}
    return {"tipo": None, "motivo": "sin cantidad"}


def comparar(pu, ref):
    """Clase de coincidencia entre la métrica del backend y la referencia del título."""
    if not ref or ref.get("tipo") is None:
        return "sin referencia"
    if not pu:
        return "backend sin métrica"
    if pu["tipo"] != ref["tipo"]:
        return f"tipo distinto ({pu['tipo']})"
    b = pu["cantidad_base"]
    if abs(b - ref["base"]) <= TOL * ref["base"]:
        return "coincide"
    if ref.get("n", 1) > 1 and ref.get("m") and abs(b - ref["m"]) <= TOL * ref["m"] + 0.05:
        return "tomó UNA unidad"
    return "cantidad distinta"


# ─────────────────────────────────────────────────────────────────
#  ANÁLISIS
# ─────────────────────────────────────────────────────────────────

def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _caracteristicas(crudo):
    try:
        return {d["nombre"]: d["descripcion"] for d in json.loads(crudo or "[]")
                if d.get("nombre") in ("CONTENIDO", "TAMAÑO DEL ROLLO", "CANTIDAD DE ROLLOS",
                                       "CANTIDAD")}
    except (ValueError, TypeError, KeyError, AttributeError):
        return {}


def fila_de(cadena, of, raw):
    pu = _precio_por_100u(of, _extraer_cantidades_desc, normalizar)
    pu_tit = _precio_por_100u(replace(of, unidad_medida=None, contenido=None),
                              _extraer_cantidades_desc, normalizar)
    f = {"c": cadena, "of": of, "pu": pu, "pu_tit": pu_tit, "ref": leer_titulo(of.producto)}
    f["clase"] = comparar(pu, f["ref"])
    if cadena == "Coto":
        a = raw["records"][0]["attributes"]
        g = lambda k: (a.get(k) or [None])[0]                       # noqa: E731
        act, ref = _num(g("sku.activePrice")), _num(g("sku.referencePrice"))
        f["coto"] = {"cociente": act / ref if act and ref else None,
                     "CANTIDAD": g("product.CANTIDAD"), "sku.quantity": g("sku.quantity"),
                     "descUnidad": g("product.unidades.descUnidad"),
                     "esPesable": g("product.unidades.esPesable"),
                     "car": _caracteristicas(g("product.dtoCaracteristicas"))}
    else:
        it = raw["items"][0]
        f["api"] = (it.get("measurementUnit"), it.get("unitMultiplier"))
        f["spec"] = raw.get("spec") or {}
        f["full"] = it["sellers"][0]["commertialOffer"].get("FullSellingPrice")
    return f


def parsear(cap):
    """{cadena: {consulta: [fila]}} con el parser de producción, sin tocarlo."""
    out = {c: {} for c in CADENAS}
    for c in CADENAS:
        for q, crudos in cap["datos"][c].items():
            filas = []
            for raw in crudos:
                if c == "Coto":
                    ofs = FuenteCoto._parsear(
                        {"contents": [{"Main": [{"contents": [{"records": [raw]}]}]}]})
                    of = ofs[0] if ofs else None
                else:
                    of = FuenteVTEX._a_oferta(c, raw)
                if of:
                    filas.append(fila_de(c, of, raw))
            out[c][q] = filas
    return out


class FuenteCaptura:
    nombre = "captura"

    def __init__(self, filas):
        self.filas = filas

    def cadenas_soportadas(self):
        return list(CADENAS)

    def buscar(self, query, cadenas=None):
        objetivo = [c for c in (cadenas or CADENAS) if query in self.filas.get(c, {})]
        res = Resultado(consultadas=objetivo)
        for c in objetivo:
            res.ofertas.extend(f["of"] for f in self.filas[c][query])
        return res


def validar(filas):
    """
    El instrumento tiene que medir el código que dice medir.
    (a) Con measurementUnit ausente o "sin contenido", producción == título solo: si no,
        la lectura de la precedencia (precios_vtex.py:197-203) está mal.
    (b) Cada representante de buscar_precios_online aparece en la captura.
    """
    errores = []
    for c in CADENAS:
        for q, fs in filas[c].items():
            for f in fs:
                # ✏️ Tarea 25: se lee de la Oferta, no del crudo VTEX. Desde el fix, los pesables de
                # Coto (esPesable=1, descUnidad=KGS) también declaran unidad_medida="kg" y contenido 1.
                mu = (f["of"].unidad_medida or "").strip().lower()
                sin_api = not mu or mu in _UNIDAD_SIN_CONTENIDO or not f["of"].contenido
                if sin_api and f["pu"] != f["pu_tit"]:
                    errores.append(f"(a) {c}/{q}: {f['of'].producto!r} api={f.get('api')} "
                                   f"prod={f['pu']} título={f['pu_tit']}")
    precios, _ = buscar_precios_online(ITEMS, CADENAS, FuenteCaptura(filas),
                                       normalizar, _extraer_cantidades_desc)
    reps = {}
    for (c, q), d in precios.items():
        cand = [f for f in filas[c][q]
                if f["of"].disponible and f["of"].precio == d["precio_min"]
                and f["of"].ean == d["ean"] and f["pu"] == d["precio_por_100u"]]
        if not cand:
            errores.append(f"(b) {c}/{q}: representante ${d['precio_min']} ean={d['ean']} "
                           f"no está en la captura")
            continue
        reps[(c, q)] = (cand[0], d["match_score"])
    if errores:
        raise InstrumentoInvalido("\n  ".join(errores))
    return reps


# ─────────────────────────────────────────────────────────────────
#  INFORME
# ─────────────────────────────────────────────────────────────────

def txt_pu(pu):
    if not pu:
        return "SIN MÉTRICA"
    return (f"{pu['tipo']} {pu['cantidad_base']:g}{pu['unidad_base']} → "
            f"{pu['valor']:,.2f} {pu['label'].split(' (')[0]} [{pu.get('fuente_contenido')}]")


def txt_ref(ref):
    if ref.get("tipo") is None:
        return f"— ({ref['motivo']})"
    extra = f" = {ref['n']:g}×{ref['m']:g}" if ref.get("n", 1) > 1 and ref.get("m") else ""
    return f"{ref['tipo']} {ref['base']:g}{extra}"


def cerca(a, b, tol):
    return a is not None and b is not None and abs(a - b) <= tol * max(abs(b), 1e-9)


def indice_coto(filas):
    """{ean: [filas de Coto distintas]}. Una misma fila puede aparecer en dos consultas."""
    idx = defaultdict(dict)
    for fs in filas["Coto"].values():
        for f in fs:
            if f["of"].ean:
                idx[f["of"].ean.strip()][(f["of"].producto, f["of"].precio)] = f
    return {e: list(v.values()) for e, v in idx.items()}


def gemela_coto(idx, ean):
    """(fila de Coto, None), (None, motivo) si el EAN es ambiguo en Coto, o (None, None)."""
    fs = idx.get((ean or "").strip(), [])
    if len(fs) > 1:
        return None, (f"EAN en {len(fs)} filas de Coto ("
                      + " | ".join(g["of"].producto[:38] for g in fs) + ")")
    return (fs[0], None) if fs else (None, None)


def verdad(f, g):
    """
    Contenido de la fila `f` según DOS fuentes independientes: su propio título (referencia)
    y el cociente de su gemela de Coto `g`. Solo hay verdad si coinciden. Si no, no se usa:
    no se elige cuál de las dos tiene razón.
    """
    tipo, k = f["ref"].get("tipo"), g["coto"]["cociente"]
    if tipo not in COTO_A_BASE or not k:
        return None, "sin verdad: el título no da medida o Coto no da cociente"
    base = k * COTO_A_BASE[tipo]
    if not cerca(f["ref"]["base"], base, TOL_COTO):
        return None, (f"sin verdad: título {f['ref']['base']:g} y cociente de Coto {base:g} "
                      f"no coinciden")
    return (tipo, base), None


def informe_api(filas):
    print("\n" + "=" * 100)
    print("§1 — ¿QUÉ MANDA LA API? measurementUnit/unitMultiplier crudos, todas las filas")
    print("    'gana api' = filas DISPONIBLES donde _precio_por_100u usó los campos de la API")
    print("=" * 100)
    for c in VTEX:
        print(f"\n  {c}")
        for q in CONSULTAS:
            fs = filas[c].get(q)
            if fs is None:
                print(f"    {q:<18} NO MEDIDO (falló la consulta)")
                continue
            cnt = Counter(f["api"] for f in fs)
            disp = [f for f in fs if f["of"].disponible]
            api = sum(1 for f in disp if f["pu"] and f["pu"].get("fuente_contenido") == "api")
            dist = ", ".join(f"{mu}/{um}×{n}" for (mu, um), n in cnt.most_common(4))
            print(f"    {q:<18} filas={len(fs):<3} disp={len(disp):<3} gana api={api:<3} {dist}")
    print("\n  Coto: el parser llena unidad_medida/contenido solo en los pesables (esPesable=1, KGS → 1 kg, "
          "Tarea 25); el resto, título.")


def informe_representantes(filas, reps):
    print("\n" + "=" * 100)
    print("§2 — REPRESENTANTES de buscar_precios_online sobre la captura")
    print("=" * 100)
    idx_coto = indice_coto(filas)
    for q in CONSULTAS:
        print(f"\n  ■ {q}")
        for c in CADENAS:
            if (c, q) not in reps:
                estado = "NO MEDIDO" if q not in filas[c] else "sin representante"
                print(f"    {c:<11} {estado}")
                continue
            f, s = reps[(c, q)]
            of = f["of"]
            print(f"    {c:<11} s={s:.3f} ${of.precio:>10,.2f}  {of.producto}")
            print(f"    {'':11} backend : {txt_pu(f['pu'])}")
            if c != "Coto":
                spec = " ".join(f"{k}={v}" for k, v in f["spec"].items())
                print(f"    {'':11} api     : measurementUnit={f['api'][0]!r} "
                      f"unitMultiplier={f['api'][1]!r} FullSellingPrice={f['full']} {spec}")
            else:
                co = f["coto"]
                print(f"    {'':11} coto    : cociente act/ref={co['cociente'] and round(co['cociente'], 3)} "
                      f"CANTIDAD={co['CANTIDAD']!r} {co['car'] or ''}")
            print(f"    {'':11} título  : {txt_ref(f['ref'])}  → {f['clase']}")
            g, ambiguo = (f, None) if c == "Coto" else gemela_coto(idx_coto, of.ean)
            if ambiguo:
                print(f"    {'':11} Coto EAN: {ambiguo} → no se usa")
            elif g:
                v, motivo = verdad(f, g)
                if v:
                    ok = bool(f["pu"]) and f["pu"]["tipo"] == v[0] \
                        and cerca(f["pu"]["cantidad_base"], v[1], TOL_COTO)
                    print(f"    {'':11} verdad  : {v[0]} {v[1]:g} (título y cociente de Coto "
                          f"coinciden) → backend {'COINCIDE' if ok else 'NO coincide'}")
                else:
                    print(f"    {'':11} Coto EAN: {motivo}")


def informe_nxm(filas):
    print("\n" + "=" * 100)
    print("§3 — TÍTULOS 'N u. x M unidad' (referencia N×M con N>1), filas DISPONIBLES")
    print("    'tomó UNA unidad' = el backend dio M (un rollo, una lata) en vez de N×M.")
    print("    OJO: N×M supone M por unidad. Es la convención del papel (§4 lo verifica); en otros")
    print("    rubros M a veces es el TOTAL ('Pack x 2 190 Gr.') y ahí 'tomó UNA unidad' es correcto.")
    print("    Fuera del papel esta tabla no es un veredicto: el detalle se lee a mano.")
    print("=" * 100)
    clases = ["coincide", "tomó UNA unidad", "tipo distinto (count)", "backend sin métrica",
              "cantidad distinta"]
    for q in CONSULTAS:
        print(f"\n  ■ {q}")
        print(f"    {'cadena':<11} {'N×M':>4} " + " ".join(f"{k[:18]:>18}" for k in clases)
              + "   gana api/nombre")
        for c in CADENAS:
            fs = [f for f in filas[c].get(q, []) if f["of"].disponible
                  and f["ref"].get("n", 1) > 1 and f["ref"].get("m")]
            if q not in filas[c]:
                print(f"    {c:<11} NO MEDIDO")
                continue
            cnt = Counter(f["clase"] for f in fs)
            otros = sum(v for k, v in cnt.items() if k not in clases)
            fuente = Counter((f["pu"] or {}).get("fuente_contenido") for f in fs)
            print(f"    {c:<11} {len(fs):>4} " + " ".join(f"{cnt[k]:>18}" for k in clases)
                  + f"   {fuente['api']}/{fuente['nombre']}" + (f"  (+{otros} otras)" if otros else ""))
    print("\n  TOTAL por cadena, todas las consultas:")
    for c in CADENAS:
        fs = [f for q in CONSULTAS for f in filas[c].get(q, []) if f["of"].disponible
              and f["ref"].get("n", 1) > 1 and f["ref"].get("m")]
        cnt = Counter(f["clase"] for f in fs)
        ok = cnt["coincide"]
        print(f"    {c:<11} N×M={len(fs):<4} coincide={ok:<4} ({ok / len(fs) * 100 if fs else 0:5.1f}%)  "
              + " · ".join(f"{k}={v}" for k, v in cnt.items() if k != "coincide"))

    print("\n  DETALLE — todas las filas N×M disponibles (para leer a mano):")
    for q in CONSULTAS:
        for c in CADENAS:
            for f in filas[c].get(q, []):
                if not (f["of"].disponible and f["ref"].get("n", 1) > 1 and f["ref"].get("m")):
                    continue
                marca = "  " if f["clase"] == "coincide" else "✗ "
                api = f"api={f['api'][0]}/{f['api'][1]}" if c != "Coto" else "coto"
                print(f"    {marca}{c:<10} {q[:14]:<14} ${f['of'].precio:>9,.0f} "
                      f"back={(f['pu'] or {}).get('tipo', '-')} {(f['pu'] or {}).get('cantidad_base', '-')!s:<7} "
                      f"ref={txt_ref(f['ref']):<22} {api:<11} {f['of'].producto[:66]}")


def informe_coto(filas):
    print("\n" + "=" * 100)
    print("§4 — VERDAD DE TERRENO: cociente activePrice/referencePrice de Coto")
    print("    Coto no declara la unidad de referencia: el factor (m²×10 → m, L×1000, kg×1000) es un")
    print("    supuesto. (a) lo mide contra los títulos de la propia Coto, separado por forma de título.")
    print("    (b) hay 'verdad' solo donde el título de la fila y el cociente coinciden: dos fuentes.")
    print("=" * 100)
    unicas = {}
    for fs in filas["Coto"].values():
        for f in fs:
            unicas.setdefault((f["of"].producto, f["of"].precio), f)

    print("\n  (a) Cociente contra los títulos de la PROPIA Coto (filas distintas, todas las consultas):")
    for tipo, factor in COTO_A_BASE.items():
        for forma in ("solo medida", "N×M", "m² declarado"):
            if forma == "m² declarado" and tipo != "longitud":
                continue
            casos = []
            for f in unicas.values():
                k, ref = f["coto"]["cociente"], f["ref"]
                if not k:
                    continue
                if forma == "m² declarado":
                    if len(ref.get("area") or []) == 1:
                        casos.append((f, cerca(k, ref["area"][0], TOL_COTO), f"{ref['area'][0]:g} m²"))
                elif ref.get("tipo") == tipo and ref.get("motivo") == forma:
                    casos.append((f, cerca(k * factor, ref["base"], TOL_COTO), txt_ref(ref)))
            if not casos:
                continue
            ok = sum(1 for _, bien, _ in casos if bien)
            print(f"    {tipo:<9} {forma:<13} {ok}/{len(casos)} coinciden (factor ×{factor:g})")
            for f, bien, txt in casos:
                if not bien:
                    print(f"       ✗ cociente={f['coto']['cociente']:.3f} título={txt:<22} "
                          f"{f['of'].producto[:70]}")

    print("\n  (b) Mismo EAN en otra cadena y en Coto (gemela única). Coto contra sí misma: la fila")
    print("      es su propia gemela, así que mide el backend donde título y cociente coinciden.")
    idx = indice_coto(filas)
    for c in CADENAS:
        cnt, lineas, vistos = Counter(), [], set()
        for q in CONSULTAS:
            for f in filas[c].get(q, []):
                e = (f["of"].ean or "").strip()
                if not e or e in vistos:
                    continue
                g, ambiguo = (f, None) if c == "Coto" else gemela_coto(idx, e)
                if not (g or ambiguo):
                    continue
                vistos.add(e)
                if ambiguo:
                    cnt["EAN ambiguo en Coto"] += 1
                    continue
                v, motivo = verdad(f, g)
                if not v:
                    cnt["sin verdad"] += 1
                    if "no coinciden" in motivo:
                        lineas.append(f"       ? {motivo} | {f['of'].producto[:48]}  ⟷ Coto: "
                                      f"{g['of'].producto[:44]}")
                    continue
                ok = bool(f["pu"]) and f["pu"]["tipo"] == v[0] \
                    and cerca(f["pu"]["cantidad_base"], v[1], TOL_COTO)
                cnt[f"con verdad {v[0]}"] += 1
                cnt[f"backend ok {v[0]}"] += ok
                lineas.append(f"       {'  ' if ok else '✗ '}{v[0]} verdad={v[1]:g} "
                              f"back={(f['pu'] or {}).get('cantidad_base', '-')} "
                              f"disp={str(f['of'].disponible)[0]} | {f['of'].producto[:50]}  ⟷ Coto: "
                              f"{g['of'].producto[:44]}")
        print(f"    {c:<11} " + " · ".join(f"{k}={v}" for k, v in sorted(cnt.items())))
        if lineas:
            print("\n".join(lineas))


def informe_pesables(filas):
    print("\n" + "=" * 100)
    print("§5 — DONDE LA API GANA: filas con fuente_contenido='api' (todas, marcando disponibilidad)")
    print("    ¿Price es por el artículo (unitMultiplier × unidad) o por UNA unidad de medida?")
    print("=" * 100)
    for c in VTEX:
        fs = [f for q in CONSULTAS for f in filas[c].get(q, [])
              if f["pu"] and f["pu"].get("fuente_contenido") == "api"]
        print(f"\n  {c}: {len(fs)} filas ({sum(1 for f in fs if f['of'].disponible)} disponibles)")
        for f in fs:
            of, pu = f["of"], f["pu"]
            tit = f["ref"]
            choca = (tit.get("tipo") == pu["tipo"] and not cerca(pu["cantidad_base"], tit["base"], TOL))
            spec = " ".join(f"{k}={v}" for k, v in f["spec"].items())
            full = f["full"]
            rel = f" Full/Price={full / of.precio:.2f}" if full and of.precio else ""
            print(f"    {'✗ ' if choca else '  '}disp={str(of.disponible)[0]} {f['api'][0]}/{f['api'][1]:<5} "
                  f"${of.precio:>10,.2f}{rel:<16} back={pu['cantidad_base']:g}g título={txt_ref(tit):<18} "
                  f"{spec[:60]:<60} {of.producto[:50]}")


def informe_coto_papel(filas):
    print("\n" + "=" * 100)
    print("§6 — COTO, papel higiénico: qué dice el título, qué dicen los campos que el parser no lee")
    print("=" * 100)
    for f in filas["Coto"].get("Papel higienico", []):
        co = f["coto"]
        k = co["cociente"]
        v = f"{k:.1f} m² ≈ {k * 10:g} m" if k else "-"
        print(f"  disp={str(f['of'].disponible)[0]} ${f['of'].precio:>9,.0f} cociente={v:<18} "
              f"back={txt_pu(f['pu'])[:44]:<44} ref={txt_ref(f['ref'])[:30]:<30} "
              f"CANT={co['CANTIDAD']!s:<12} {co['car'] or ''}\n        {f['of'].producto}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--guardar", help="guarda la captura recortada en este archivo")
    ap.add_argument("--desde", help="re-analiza una captura guardada, sin red")
    args = ap.parse_args()

    if args.desde:
        with open(args.desde, encoding="utf-8") as fh:
            cap = json.load(fh)
    else:
        cap = capturar()
        if args.guardar:
            with open(args.guardar, "w", encoding="utf-8") as fh:
                json.dump(cap, fh, ensure_ascii=False)

    filas = parsear(cap)
    try:
        reps = validar(filas)
    except InstrumentoInvalido as e:
        print(f"ERROR (InstrumentoInvalido):\n  {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Captura: {cap['__fecha']} · {cap['__origen']}")
    print("Instrumento validado: (a) sin campos de API, producción == título solo en todas las "
          "filas · (b) los representantes de buscar_precios_online están en la captura")
    for c in CADENAS:
        print(f"  {c:<11} " + " · ".join(f"{q.split()[0]} {len(filas[c][q])}" if q in filas[c]
                                         else f"{q.split()[0]} FALLÓ" for q in CONSULTAS))
    if cap["fallas"]:
        print(f"  FALLAS: {cap['fallas']}")

    informe_api(filas)
    informe_representantes(filas, reps)
    informe_nxm(filas)
    informe_coto(filas)
    informe_pesables(filas)
    informe_coto_papel(filas)

    if cap["fallas"]:
        print(f"\nMEDICIÓN INCOMPLETA: {len(cap['fallas'])} consultas fallaron", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
