"""
Tarea 25 (14/09/2026) — Pesables: ¿qué es `Price`, qué cobra el carrito y cuánto trae el artículo?

Instrumento de MEDICIÓN con RED REAL. No es test ni guard: fuera del CI a propósito.

Santiago verificó en el carrito de Día que $4.390 es el precio del KILO del pollo entero (`kg` /
3.0). El backend usa `Price` como precio del artículo (`fuentes.py:260`) y `unitMultiplier` como su
contenido (`precios_vtex.py:198-203`). Son dos preguntas distintas y se contestan por separado:
  (a) ¿cuál es el precio del ARTÍCULO?      (b) ¿cuál es su CONTENIDO?

Qué hace, sin tocar el código de producción:
  1. Captura las 5 cadenas con el mismo request que producción (`FuenteVTEX._pedir` y el de
     `FuenteCoto.buscar`) y, en las VTEX, otra vez con el canal de venta (`sc`) leído del
     `PriceToken`, como `scripts/snapshot_catalogo.sales_channel`. Producción no manda `sc`.
  2. Simulación de checkout VTEX (`/api/checkout/pub/orderForms/simulation`, cantidad 1) para toda
     fila con measurementUnit ≠ "un" o unitMultiplier ≠ 1, más 4 filas `un`/1.0 de control por
     cadena. Es la cuenta del carrito, sin sesión y sin crear nada. Se valida contra la
     verificación manual de Santiago (pollo de Día): si no la reproduce, el instrumento no sirve.
  3. Coto: los atributos que el parser no lee (esPesable, cFormato, saltoCantidad, cantidadMinima,
     dtoPrice) y, en los pesables y en 5 no pesables de contraste por consulta, el registro entero
     menos ruido (disponibilidad por sucursal, imágenes, urls).
  4. §7: la canasta por defecto con el código de hoy y con dos lecturas del fix simuladas EN MEMORIA
     sobre copias de las Oferta — (A) precio por unidad de medida, (B) precio del artículo — sin
     modificar fuentes.py ni precios_vtex.py.

Todo sale POR CADENA y con disponibles / no disponibles separados (CLAUDE.md).

Salida: 0 medición completa · 1 el instrumento no reproduce lo que dice medir · 2 incompleta.

Desde sepa_backend/:
    venv\\Scripts\\python.exe -m tests.medir_pesables --guardar tests/capturas/pesables_vivo_AAAA-MM-DD.json
    venv\\Scripts\\python.exe -m tests.medir_pesables --desde tests/capturas/pesables_vivo_AAAA-MM-DD.json
"""
import argparse
import base64
import json
import logging
import math
import re
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import requests

import main
from fuentes import UA, VTEX_BASES, FuenteCoto, FuenteVTEX, Resultado, _precio_lista
from precios_vtex import _UNIDAD_SIN_CONTENIDO, buscar_precios_online
from tests import medir_metrica_vivo as M

RAIZ = Path(__file__).resolve().parents[1]
CAPTURA_CANASTA = RAIZ / "tests" / "capturas" / "canasta_default_2026-09-14.json"
CAPTURA_13_09 = RAIZ / "tests" / "capturas" / "metrica_vivo_2026-09-13.json"
VTEX, CADENAS = M.VTEX, M.CADENAS
CANASTA = main.CANASTA_DEFAULT

_CAN = {p["nombre"]: p for p in CANASTA}
# Las cuatro primeras son de CANASTA_DEFAULT y en la captura del 14/09 traen pesables o
# multiplicadores ≠ 1 (el tomate fresco por kilo entra por "Tomate perita lata"; las medialunas
# `un`/6.0 por "Manteca"). Las otras tres amplían el rango: carne en corte, queso en horma, fiambre.
ITEMS = [_CAN["Pollo entero"], _CAN["Carne picada"], _CAN["Tomate perita lata"], _CAN["Manteca"],
         {"nombre": "Nalga", "cantidad": 1, "unidad": "kg", "categoria": "Carnes"},
         {"nombre": "Queso cremoso", "cantidad": 1, "unidad": "kg", "categoria": "Lácteos"},
         {"nombre": "Jamon cocido", "cantidad": 1, "unidad": "kg", "categoria": "Fiambres"}]
CONSULTAS = [i["nombre"] for i in ITEMS]

# Verificación manual de Santiago, 14/09: pollo entero de Día a $4.390 el kilo; el carrito cobra
# el artículo de 3 kg. La simulación tiene que dar lo mismo o el instrumento no mide el carrito.
DIA_POLLO = {"titulo": "Pollo Entero x Kg.", "price": 4390.0, "carrito": 13170.0}

COTO_EXTRA = ("product.cFormato", "product.cantForm", "product.cantidadMinima",
              "product.saltoCantidad", "sku.dtoPrice", "sku.unit_of_measure")
_COTO_RUIDO = re.compile(r"^(product\.sDisp_|DGraph\.|allAncestors\.repositoryId|product\.(large|medium)"
                         r"Image|product\.url|sku\.url|product\.baseUrl|sku\.baseUrl|product\.keywords|"
                         r"product\.auxiliaryMedia|product\.RELEVANCIA|product\.TOTALDEVENTAS)")
# Especificaciones VTEX que pueden declarar peso, contenido o precio por unidad. Los nombres cambian
# por cadena (Carrefour y Chango Más: "Gramaje ..."; Día: PrecioPorUnd): se busca por patrón.
_SPEC_PESO = re.compile(r"gramaje|fraccionable|balanza|cantidad|gross|peso|contenido|precio ?x|"
                        r"priceperunit|precioporund|unidadde", re.I)
TOL_PESO = 0.02


class InstrumentoInvalido(Exception):
    pass


# ─────────────────────────────────────────────────────────────────
#  CAPTURA
# ─────────────────────────────────────────────────────────────────

def _es_raro(item):
    """measurementUnit ≠ "un" o unitMultiplier ≠ 1: donde `Price` puede no ser el artículo."""
    return (item.get("measurementUnit") or "").strip().lower() != "un" or item.get("unitMultiplier") != 1.0


def recortar_vtex(p):
    r = M.recortar_vtex(p)
    it = (p.get("items") or [{}])[0]
    s = (it.get("sellers") or [{}])[0]
    co = s.get("commertialOffer") or {}
    r["items"][0]["itemId"] = it.get("itemId")
    r["items"][0]["sellerId"] = s.get("sellerId")
    if _es_raro(it):
        inst = (co.get("Installments") or [None])[0] or {}
        r["extra"] = {
            "spec_peso": {k: v for k, v in p.items() if isinstance(k, str) and _SPEC_PESO.search(k)},
            "description": (p.get("description") or "")[:400],
            "complementName": it.get("complementName"),
            "nameComplete": it.get("nameComplete"),
            "installment": {k: inst.get(k) for k in ("Value", "NumberOfInstallments",
                                                     "TotalValuePlusInterestRate")} if inst else None,
        }
    return r


def recortar_coto(rec, completo=False):
    r = M.recortar_coto(rec)
    a = ((rec.get("records") or [{}])[0]).get("attributes") or {}
    at = r["records"][0]["attributes"]
    for k in COTO_EXTRA:
        if k in a:
            at[k] = a[k]
    if completo or str((a.get("product.unidades.esPesable") or [None])[0]) == "1":
        r["completo"] = {k: v for k, v in a.items() if not _COTO_RUIDO.match(k)}
    return r


def _sc_del_token(tok):
    if not tok or tok.count(".") != 2:
        return None
    cuerpo = tok.split(".")[1]
    cuerpo += "=" * (-len(cuerpo) % 4)
    try:
        return (json.loads(base64.urlsafe_b64decode(cuerpo)).get("data") or {}).get("salesChannel")
    except Exception:                                        # noqa: BLE001 — dato, no credencial
        return None


def sales_channel(productos):
    for p in productos:
        for it in p.get("items") or []:
            for s in it.get("sellers") or []:
                sc = _sc_del_token((s.get("commertialOffer") or {}).get("PriceToken"))
                if sc:
                    return str(sc)
    return None


def pedir_vtex_sc(cadena, q, sc):
    """El request de `FuenteVTEX._pedir`, con el canal de venta explícito."""
    url = (f"{VTEX_BASES[cadena]}/api/catalog_system/pub/products/search/"
           f"?ft={requests.utils.quote(q)}&_from=0&_to={FuenteVTEX().pagina - 1}&sc={sc}")
    r = requests.get(url, headers={"User-Agent": UA, "Accept": "application/json"}, timeout=(8, 20))
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        raise ValueError(f"esperaba lista, vino {type(data).__name__}")
    return data


def simular(cadena, item_id, seller, sc):
    """El carrito de VTEX para 1 unidad. Los montos vienen en centavos."""
    url = f"{VTEX_BASES[cadena]}/api/checkout/pub/orderForms/simulation" + (f"?sc={sc}" if sc else "")
    body = {"items": [{"id": str(item_id), "quantity": 1, "seller": str(seller)}], "country": "ARG"}
    r = requests.post(url, json=body, headers={"User-Agent": UA, "Accept": "application/json"},
                      timeout=(8, 20))
    if not r.ok:
        return {"status": r.status_code, "error": r.text[:200]}
    j = r.json()
    x = (j.get("items") or [{}])[0]
    cent = lambda v: None if v is None else v / 100                          # noqa: E731
    pdef = x.get("priceDefinition") or {}
    items_total = next((t.get("value") for t in (j.get("totals") or []) if t.get("id") == "Items"), None)
    return {"status": r.status_code, "price": cent(x.get("price")), "listPrice": cent(x.get("listPrice")),
            "sellingPrice": cent(x.get("sellingPrice")), "total": cent(pdef.get("total")),
            "totals_items": cent(items_total), "unitMultiplier": x.get("unitMultiplier"),
            "measurementUnit": x.get("measurementUnit"), "availability": x.get("availability")}


def capturar():
    cap = {"__fecha": datetime.now().isoformat(timespec="seconds"),
           "__origen": "red real desde la máquina que corrió el script",
           "__consultas": CONSULTAS,
           "datos": {c: {} for c in CADENAS}, "datos_sc": {c: {} for c in VTEX},
           "sc": {}, "sim": {c: {} for c in VTEX}, "fallas": {}}
    vtex = FuenteVTEX()
    for q in CONSULTAS:
        with ThreadPoolExecutor(max_workers=len(CADENAS)) as pool:
            futs = {c: pool.submit(vtex._pedir, c, q) for c in VTEX}
            futs["Coto"] = pool.submit(M.pedir_coto, q)
            for c, fut in futs.items():
                try:
                    crudos = fut.result()
                except Exception as e:                       # noqa: BLE001 — se reporta
                    cap["fallas"][f"{c} / {q}"] = f"{type(e).__name__}: {str(e)[:120]}"
                    continue
                if c == "Coto":
                    no_pes = 0
                    filas = []
                    for rec in crudos:
                        a = ((rec.get("records") or [{}])[0]).get("attributes") or {}
                        pes = str((a.get("product.unidades.esPesable") or [None])[0]) == "1"
                        filas.append(recortar_coto(rec, completo=not pes and no_pes < 5))
                        no_pes += not pes
                    cap["datos"][c][q] = filas
                else:
                    cap["datos"][c][q] = [recortar_vtex(p) for p in crudos]
                    if c not in cap["sc"] and sales_channel(crudos):
                        cap["sc"][c] = sales_channel(crudos)

    for c in VTEX:
        if c not in cap["sc"]:
            cap["fallas"][f"{c} / sales channel"] = "no se pudo leer del PriceToken"
    for q in CONSULTAS:
        with ThreadPoolExecutor(max_workers=len(VTEX)) as pool:
            futs = {c: pool.submit(pedir_vtex_sc, c, q, cap["sc"][c]) for c in VTEX if c in cap["sc"]}
            for c, fut in futs.items():
                try:
                    cap["datos_sc"][c][q] = [recortar_vtex(p) for p in fut.result()]
                except Exception as e:                       # noqa: BLE001
                    cap["fallas"][f"{c} (sc={cap['sc'][c]}) / {q}"] = f"{type(e).__name__}: {str(e)[:120]}"

    pedidos = {}
    for c in VTEX:
        control = 0
        for datos in (cap["datos_sc"][c], cap["datos"][c]):
            for filas in datos.values():
                for p in filas:
                    it = p["items"][0]
                    iid = it.get("itemId")
                    if not iid or (c, iid) in pedidos:
                        continue
                    raro = _es_raro(it)
                    con_precio = bool(it["sellers"][0]["commertialOffer"].get("Price"))
                    if raro or (control < 4 and con_precio):
                        pedidos[(c, iid)] = it.get("sellerId") or "1"
                        control += not raro
    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = {k: pool.submit(simular, k[0], k[1], s, cap["sc"].get(k[0])) for k, s in pedidos.items()}
        for (c, iid), fut in futs.items():
            try:
                cap["sim"][c][str(iid)] = fut.result()
            except Exception as e:                           # noqa: BLE001
                cap["fallas"][f"{c} / simulación {iid}"] = f"{type(e).__name__}: {str(e)[:120]}"
    return cap


# ─────────────────────────────────────────────────────────────────
#  PARSEO Y LECTURAS SIMULADAS DEL FIX
# ─────────────────────────────────────────────────────────────────

def parsear(datos):
    """Como `M.parsear`, pero la fila guarda el crudo: hace falta para Full, List y el itemId."""
    out = {c: {} for c in CADENAS}
    for c in CADENAS:
        for q, crudos in (datos.get(c) or {}).items():
            filas = []
            for raw in crudos:
                if c == "Coto":
                    ofs = FuenteCoto._parsear({"contents": [{"Main": [{"contents": [{"records": [raw]}]}]}]})
                    of = ofs[0] if ofs else None
                else:
                    of = FuenteVTEX._a_oferta(c, raw)
                if of:
                    f = M.fila_de(c, of, raw)
                    f["raw"] = raw
                    filas.append(f)
            out[c][q] = filas
    return out


def vt(f):
    it = f["raw"]["items"][0]
    return it, it["sellers"][0]["commertialOffer"]


def mu_um(f):
    it = f["raw"]["items"][0]
    return (it.get("measurementUnit") or "").strip().lower(), it.get("unitMultiplier")


def es_contenido(mu):
    return bool(mu) and mu not in _UNIDAD_SIN_CONTENIDO


def es_raro(f):
    return f["c"] != "Coto" and _es_raro(f["raw"]["items"][0])


def trunc2(x):
    return math.floor(x * 100 + 1e-6) / 100


def coto_attr(f, k):
    return ((f["raw"]["records"][0]["attributes"].get(k)) or [None])[0]


def lista_en_escala_price(co, precio, um):
    """Los candidatos a precio tachado en la escala de `Price`: FullSellingPrice ÷ unitMultiplier."""
    full = co.get("FullSellingPrice")
    return _precio_lista({"ListPrice": co.get("ListPrice"),
                          "PriceWithoutDiscount": co.get("PriceWithoutDiscount"),
                          "FullSellingPrice": (full / um) if full and um else None}, precio)


def variante(f, cual, coto_regla=False):
    """
    La Oferta de la fila según una lectura del fix. En memoria: producción no se toca.
      hoy  la de producción, tal cual.
      A    precio por unidad de medida: `Price` con contenido = 1 measurementUnit.
      B    precio del artículo: `FullSellingPrice` con contenido = unitMultiplier.
      Con measurementUnit "un" y multiplicador ≠ 1 no hay unidad de medida con contenido: A y B
      toman el artículo del click (Full) y el contenido sale del título, como hoy.
      +Coto (A): esPesable=1 y descUnidad=KGS → contenido 1 kg. (B): el click = saltoCantidad
      gramos. Es un SUPUESTO: nadie verificó qué cobra el carrito de Coto.
    Las filas `un`/1.0 salen idénticas en todas.
    """
    of = f["of"]
    if cual == "hoy":
        return of
    if f["c"] == "Coto":
        if coto_regla and str(coto_attr(f, "product.unidades.esPesable")) == "1" \
                and (coto_attr(f, "product.unidades.descUnidad") or "").strip().upper() == "KGS":
            if cual == "A":
                return replace(of, unidad_medida="kg", contenido=1.0)
            salto = M._num(coto_attr(f, "product.saltoCantidad"))
            if salto:
                k = salto / 1000
                return replace(of, precio=round(of.precio * k, 2), unidad_medida="kg", contenido=k)
        return of
    _it, co = vt(f)
    mu, um = mu_um(f)
    if not es_contenido(mu) and um in (None, 1.0):
        return of
    price = float(co["Price"])
    full = co.get("FullSellingPrice") or trunc2(price * (um or 1.0))
    lista = lista_en_escala_price(co, price, um or 1.0)
    if cual == "A" and es_contenido(mu):
        return replace(of, precio=price, precio_lista=lista, contenido=1.0)
    return replace(of, precio=float(full), precio_lista=(round(lista * (um or 1.0), 2) if lista else None),
                   contenido=um)


VARIANTES = [("hoy", False), ("A", False), ("A", True), ("B", False), ("B", True)]


def nombre_var(cual, coto):
    return cual if cual == "hoy" else f"{cual}{'+Coto' if coto else ''}"


class FuenteVariante:
    nombre = "variante"

    def __init__(self, filas, cual, coto_regla):
        self.filas, self.cual, self.coto = filas, cual, coto_regla

    def cadenas_soportadas(self):
        return list(CADENAS)

    def buscar(self, query, cadenas=None):
        objetivo = [c for c in (cadenas or CADENAS) if query in self.filas.get(c, {})]
        res = Resultado(consultadas=objetivo)
        for c in objetivo:
            res.ofertas.extend(variante(f, self.cual, self.coto) for f in self.filas[c][query])
        return res


def precios_de(filas, items, cual="hoy", coto=False):
    precios, _ = buscar_precios_online(items, CADENAS, FuenteVariante(filas, cual, coto),
                                       main.normalizar, main._extraer_cantidades_desc)
    return precios


def fichas(filas, canasta, cual, coto, dia):
    precios = precios_de(filas, canasta, cual, coto)
    res = main._analizar(canasta, precios, main.PROMOS_DEFAULT, dia, None)
    prom = main._promedios_por_producto(canasta, precios)
    return precios, prom, {fi["cadena"]: fi for fi in main._fichas(canasta, precios, res, prom, list(CADENAS))}


# ─────────────────────────────────────────────────────────────────
#  VALIDACIÓN — el instrumento mide lo que dice medir
# ─────────────────────────────────────────────────────────────────

def validar(cap, prod, con_sc):
    """
    (a) Los representantes de `buscar_precios_online` están en la captura, y la lectura "hoy" da
        exactamente lo mismo que producción.
    (b) `lista_en_escala_price` con unitMultiplier = 1 reproduce el precio tachado de producción en
        todas las filas `un`/1.0: si no, las lecturas A/B cambian algo que no dicen cambiar.
    (c) La simulación reproduce el carrito de Santiago en el pollo de Día (si el precio no cambió).
    """
    errores = []
    for vista, filas in (("sin sc", prod), ("con sc", con_sc)):
        p_prod, _ = buscar_precios_online(ITEMS, CADENAS, M.FuenteCaptura(filas),
                                          main.normalizar, main._extraer_cantidades_desc)
        if p_prod != precios_de(filas, ITEMS):
            errores.append(f"(a) {vista}: la lectura 'hoy' no da lo mismo que producción")
        for (c, q), d in p_prod.items():
            if not any(f["of"].precio == d["precio_min"] and f["of"].ean == d["ean"]
                       and f["pu"] == d["precio_por_100u"] for f in filas[c][q]):
                errores.append(f"(a) {vista} {c}/{q}: representante ${d['precio_min']} no está en la captura")
    for filas in (prod, con_sc):
        for c in VTEX:
            for fs in filas[c].values():
                for f in fs:
                    mu, um = mu_um(f)
                    if mu == "un" and um == 1.0:
                        _it, co = vt(f)
                        if lista_en_escala_price(co, f["of"].precio, 1.0) != f["of"].precio_lista:
                            errores.append(f"(b) {c}: {f['of'].producto!r} lista distinta de producción")
    estado = "sin fila"
    for f in (f for fs in con_sc["Día"].values() for f in fs):
        if f["of"].producto.strip() != DIA_POLLO["titulo"]:
            continue
        s = cap["sim"]["Día"].get(str(vt(f)[0].get("itemId")))
        if f["of"].precio != DIA_POLLO["price"]:
            estado = f"no re-validable: el Price cambió a {f['of'].precio}"
        elif not s or s.get("total") is None:
            estado = "no re-validable: sin simulación"
        elif abs(s["total"] - DIA_POLLO["carrito"]) > 0.01:
            errores.append(f"(c) simulación del pollo de Día = {s['total']}, el carrito de Santiago dio "
                           f"{DIA_POLLO['carrito']}")
        else:
            estado = f"OK: la simulación da ${s['total']:,.2f}, lo mismo que el carrito de Santiago"
        break
    if errores:
        raise InstrumentoInvalido("\n  ".join(errores[:15]))
    return estado


# ─────────────────────────────────────────────────────────────────
#  INFORME
# ─────────────────────────────────────────────────────────────────

def barra(titulo, *lineas):
    print("\n" + "=" * 110)
    print(titulo)
    for li in lineas:
        print("    " + li)
    print("=" * 110)


def por_item(filas, c):
    out = {}
    for q, fs in filas[c].items():
        for f in fs:
            iid = vt(f)[0].get("itemId") if c != "Coto" else None
            out.setdefault(iid or (f["of"].producto, f["of"].precio), (q, f))
    return out


def clase_articulo(price, full, s):
    if not s:
        return "sin simulación"
    if s.get("status") != 200:
        return f"simulación HTTP {s.get('status')}"
    if s.get("total") is None:
        return f"carrito sin precio ({s.get('availability')})"
    t = s["total"]
    if full is not None and abs(t - full) <= 0.02:
        return "carrito = Full"
    if abs(t - price) <= 0.02:
        return "carrito = Price"
    return "carrito ≠ Full y ≠ Price"


def contenido_api(mu, um):
    if not es_contenido(mu) or not um:
        return None
    for val, tipo in main._extraer_cantidades_desc(f"{um} {mu}") or []:
        return tipo, val
    return None


def clase_contenido(f):
    mu, um = mu_um(f)
    api, ref = contenido_api(mu, um), f["ref"]
    if ref.get("tipo") is None:
        return "título sin medida"
    if not api:
        return f"API sin contenido (título {ref['tipo']} {ref['base']:g})"
    if api[0] != ref["tipo"]:
        return f"tipos distintos (API {api[0]}, título {ref['tipo']})"
    if M.cerca(api[1], ref["base"], TOL_PESO):
        return "API = título"
    return f"API {api[1]:g} ≠ título {ref['base']:g}"


def txt_spec(spec, largo=230):
    s = " · ".join(f"{k}={v[0] if isinstance(v, list) and len(v) == 1 else v}" for k, v in (spec or {}).items())
    return s[:largo]


def informe_crudo(cap, prod, con_sc):
    barra("§1 — FILAS CON measurementUnit ≠ un O unitMultiplier ≠ 1: crudo de la API, carrito y lo que calcula hoy el backend",
          "disp = producción (sin sc) / con sc. (a) = qué cobra el carrito por 1 unidad. (b) = contenido según API "
          "(unitMultiplier × measurementUnit) contra el título.",
          "Backend hoy: precio_min y precio_por_100u que producción calcula para esa fila (código 5ecda2b).")
    resumen = {}
    for c in VTEX:
        p_idx, s_idx = por_item(prod, c), por_item(con_sc, c)
        ids = [i for i in dict.fromkeys(list(p_idx) + list(s_idx))
               if es_raro((p_idx.get(i) or s_idx.get(i))[1])]
        sin_precio = Counter()
        for vista, datos in (("sin sc", cap["datos"][c]), ("con sc", cap["datos_sc"][c])):
            vistos = set()
            for crudos in datos.values():
                for p in crudos:
                    it = p["items"][0]
                    if _es_raro(it) and not it["sellers"][0]["commertialOffer"].get("Price") \
                            and it.get("itemId") not in vistos:
                        vistos.add(it.get("itemId"))
                        sin_precio[vista] += 1
        print(f"\n  ■ {c}  (sc={cap['sc'].get(c)}) — {len(ids)} filas con precio; con Price 0 (el backend "
              f"las descarta): sin sc {sin_precio['sin sc']}, con sc {sin_precio['con sc']}")
        cnt_a, cnt_b = defaultdict(Counter), defaultdict(Counter)
        for grupo, disp in (("DISPONIBLES en producción", True), ("NO DISPONIBLES en producción", False)):
            filas = [i for i in ids if bool(p_idx.get(i) and p_idx[i][1]["of"].disponible) == disp]
            print(f"\n    — {grupo}: {len(filas)}")
            for i in sorted(filas, key=lambda i: ((p_idx.get(i) or s_idx.get(i))[0], str(i))):
                q, f = p_idx.get(i) or s_idx.get(i)
                fs = s_idx.get(i, (None, None))[1]
                _it, co = vt(f)
                mu, um = mu_um(f)
                price, full = co.get("Price"), co.get("FullSellingPrice")
                regla = "✓" if full is not None and abs(full - price * um) < 0.011 else "✗"
                s = cap["sim"][c].get(str(i))
                ca, cb = clase_articulo(price, full, s), clase_contenido(f)
                cnt_a[grupo][ca] += 1
                cnt_b[grupo][cb] += 1
                disp_sc = "-" if not fs else ("S" if fs["of"].disponible else "N")
                sim = (f"carrito ${s['total']:,.2f} ({s.get('availability')})" if s and s.get("total") is not None
                       else (f"carrito sin precio ({s.get('availability')})" if s else "sin simulación"))
                precio_sc = "" if not fs or fs["of"].precio == price else f" [con sc Price={fs['of'].precio:g}]"
                print(f"      [{q[:12]}] {f['of'].producto[:70]}  (itemId {i})")
                print(f"          {mu}/{um:g}  Price={price:,.2f}{precio_sc}  Full={full} {regla}  "
                      f"List={co.get('ListPrice')}  disp={'S' if f['of'].disponible else 'N'}/{disp_sc}  {sim}")
                print(f"          (a) {ca} · (b) {cb} · backend hoy: precio_min=${f['of'].precio:,.2f} "
                      f"{M.txt_pu(f['pu'])}")
                extra = f["raw"].get("extra") or {}
                if extra.get("spec_peso"):
                    print(f"          spec: {txt_spec(extra['spec_peso'])}")
        resumen[c] = (cnt_a, cnt_b)
    print("\n  RESUMEN (a) y (b) por cadena:")
    for c, (cnt_a, cnt_b) in resumen.items():
        for grupo in cnt_a:
            print(f"    {c:<11} {grupo:<30} (a) {dict(cnt_a[grupo])}")
            print(f"    {'':11} {'':30} (b) {dict(cnt_b[grupo])}")


def informe_regla(cap, vistas):
    barra("§2 — ¿FullSellingPrice = Price × unitMultiplier? TODAS las filas con Price > 0, por cadena",
          "Cada captura cuenta aparte (son observaciones distintas); sin sc y con sc de la misma captura se deduplican.",
          "✓ si |Full − Price × um| < 0,011 (cubre truncado y redondeo al centavo).")
    cnt = defaultdict(lambda: [0, 0])
    fallos, vistos = [], set()
    solo_trunc = Counter()
    for nombre, filas in vistas:
        for c in VTEX:
            for fs in filas[c].values():
                for f in fs:
                    it, co = vt(f)
                    mu, um = mu_um(f)
                    price, full = co.get("Price"), co.get("FullSellingPrice")
                    clave = (nombre, c, it.get("itemId") or f["of"].producto, price)
                    if clave in vistos:
                        continue
                    vistos.add(clave)
                    kind = mu if mu in ("un", "kg") else (mu or "None")
                    g = (nombre, c, kind, "um=1" if um == 1.0 else "um≠1", "disp" if f["of"].disponible else "no disp")
                    ok = full is not None and um is not None and abs(full - price * um) < 0.011
                    cnt[g][0] += ok
                    cnt[g][1] += 1
                    if ok and um != 1.0 and abs(full - trunc2(price * um)) < 1e-6 \
                            and abs(full - round(price * um, 2)) > 1e-6:
                        solo_trunc[(nombre, c)] += 1
                    if not ok:
                        fallos.append(f"{nombre} {c} {kind}/{um} Price={price} Full={full} "
                                      f"disp={f['of'].disponible} {f['of'].producto[:60]}")
    for g in sorted(cnt):
        ok, tot = cnt[g]
        print(f"    {g[0]:<14} {g[1]:<11} {g[2]:<4} {g[3]:<5} {g[4]:<8} {ok:>5}/{tot:<5}{'' if ok == tot else '  ✗'}")
    print(f"\n    Filas que solo cierran truncando al centavo (no redondeando): {dict(solo_trunc) or 'ninguna'}")
    print(f"    Filas que NO cumplen la regla: {len(fallos)}")
    for li in fallos:
        print("      ✗ " + li)

    print("\n  Carrito (simulación, con sc) contra FullSellingPrice de la misma fila con sc, por cadena:")
    for c in VTEX:
        s_idx = por_item(vistas[1][1], c)
        cnt_s = defaultdict(Counter)
        raros = []
        for iid, s in cap["sim"][c].items():
            if iid not in s_idx:
                continue
            f = s_idx[iid][1]
            _it, co = vt(f)
            tipo = "raro" if es_raro(f) else "un/1.0 (control)"
            clase = clase_articulo(co.get("Price"), co.get("FullSellingPrice"), s)
            cnt_s[(tipo, "disp" if f["of"].disponible else "no disp")][clase] += 1
            if clase == "carrito ≠ Full y ≠ Price" or (clase == "carrito = Price" and es_raro(f)
                                                       and mu_um(f)[1] != 1.0):
                raros.append(f"{f['of'].producto[:60]} Price={co.get('Price')} Full={co.get('FullSellingPrice')} "
                             f"carrito={s.get('total')}")
        for g in sorted(cnt_s):
            print(f"    {c:<11} {g[0]:<17} {g[1]:<8} {dict(cnt_s[g])}")
        for li in raros:
            print(f"      ✗ {li}")


def informe_canal(cap, prod, con_sc):
    barra("§3 — CANAL DE VENTA: producción consulta SIN sc. ¿Qué cambia con el sc del PriceToken?",
          "Se compara por itemId, solo filas con Price > 0 en alguna de las dos vistas.")
    for c in VTEX:
        p_idx, s_idx = por_item(prod, c), por_item(con_sc, c)
        ambos = set(p_idx) & set(s_idx)
        disp_p = sum(1 for _q, f in p_idx.values() if f["of"].disponible)
        disp_s = sum(1 for _q, f in s_idx.values() if f["of"].disponible)
        cambia_disp = [i for i in ambos if p_idx[i][1]["of"].disponible != s_idx[i][1]["of"].disponible]
        cambia_precio = [i for i in ambos if p_idx[i][1]["of"].precio != s_idx[i][1]["of"].precio]
        print(f"    {c:<11} sc={cap['sc'].get(c)!s:<4} filas sin sc={len(p_idx):<4} (disp {disp_p:<4}) · con sc="
              f"{len(s_idx):<4} (disp {disp_s:<4}) · solo sin sc={len(set(p_idx) - set(s_idx)):<3} solo con sc="
              f"{len(set(s_idx) - set(p_idx)):<3} · cambia disp={len(cambia_disp):<3} cambia Price={len(cambia_precio)}")

    print("\n  Vea, filas con multiplicador ≠ 1, lado a lado:")
    p_idx, s_idx = por_item(prod, "Vea"), por_item(con_sc, "Vea")
    ids = [i for i in dict.fromkeys(list(p_idx) + list(s_idx)) if mu_um((p_idx.get(i) or s_idx.get(i))[1])[1] != 1.0]
    for um in sorted({mu_um((p_idx.get(i) or s_idx.get(i))[1])[1] for i in ids}):
        grupo = [i for i in ids if mu_um((p_idx.get(i) or s_idx.get(i))[1])[1] == um]
        print(f"\n    unitMultiplier={um}: {len(grupo)} filas")
        for i in sorted(grupo, key=lambda i: (p_idx.get(i) or s_idx.get(i))[1]["of"].producto):
            q, f = p_idx.get(i) or s_idx.get(i)
            fp, fs = p_idx.get(i, (0, None))[1], s_idx.get(i, (0, None))[1]
            s = cap["sim"]["Vea"].get(str(i)) or {}
            tp = f"${fp['of'].precio:>10,.2f} {'S' if fp['of'].disponible else 'N'}" if fp else f"{'-':>12}  "
            ts = f"${fs['of'].precio:>10,.2f} {'S' if fs['of'].disponible else 'N'}" if fs else f"{'-':>12}  "
            print(f"      {mu_um(f)[0]:<3} sin sc {tp} · con sc {ts} · carrito {s.get('total')!s:<9} "
                  f"({s.get('availability')}) · título {M.txt_ref(f['ref'])[:22]:<22} {f['of'].producto[:55]}")

    print("\n  Representantes de producción SIN sc contra CON sc (mismo pipeline, las 7 consultas):")
    p_sin, p_con = precios_de(prod, ITEMS), precios_de(con_sc, ITEMS)
    for q in CONSULTAS:
        for c in VTEX:
            a, b = p_sin.get((c, q)), p_con.get((c, q))
            ta = f"${a['precio_min']:,.0f} {(a['precio_por_100u'] or {}).get('desc_ganadora', '')[:34]}" if a else "—"
            tb = f"${b['precio_min']:,.0f} {(b['precio_por_100u'] or {}).get('desc_ganadora', '')[:34]}" if b else "—"
            if (a or {}).get("ean") != (b or {}).get("ean") or (a or {}).get("precio_min") != (b or {}).get("precio_min"):
                print(f"    ≠ {q[:14]:<14} {c:<11} sin sc: {ta:<50} con sc: {tb}")


_REF = re.compile(r"\$?\s*([\d.,]+)\s*x\s*([\d.,]+)\s*([A-Za-z²0-9]+)", re.I)
_UNI_REF = {"k": ("peso", 1000.0), "kg": ("peso", 1000.0), "kgs": ("peso", 1000.0), "g": ("peso", 1.0),
            "gr": ("peso", 1.0), "grs": ("peso", 1.0), "l": ("volumen", 1000.0), "lt": ("volumen", 1000.0),
            "lts": ("volumen", 1000.0), "ml": ("volumen", 1.0), "cc": ("volumen", 1.0)}


def _num_ar(txt):
    """ "1,700.00" → 1700.0 (Carrefour usa coma de miles y punto decimal)."""
    try:
        return float(str(txt).replace(",", ""))
    except ValueError:
        return None


def ref_spec(c, spec):
    """(valor del spec, tipo, cantidad base de la referencia) o None."""
    uno = lambda k: (spec.get(k) or [None])[0] if isinstance(spec.get(k), list) else spec.get(k)  # noqa: E731
    if c == "Carrefour" and uno("pricePerUnit"):
        m = _REF.search(uno("Precio x unidad") or uno("Precio x Unidad") or "")
        val = _num_ar(uno("pricePerUnit"))
        if m and m.group(3).lower().rstrip(".") in _UNI_REF:
            tipo, f = _UNI_REF[m.group(3).lower().rstrip(".")]
            return val, tipo, (_num_ar(m.group(2)) or 1) * f, m.group(0)
        return val, None, None, (m.group(0) if m else uno("Precio x unidad"))
    if c == "Día" and uno("PrecioPorUnd"):
        val = _num_ar(uno("PrecioPorUnd"))
        m = re.match(r"\s*([\d.,]+)\s*([A-Za-z]+)", uno("UnidaddeMedida") or "")
        if m and m.group(2).lower() in _UNI_REF:
            tipo, f = _UNI_REF[m.group(2).lower()]
            return val, tipo, (_num_ar(m.group(1)) or 1) * f, uno("UnidaddeMedida")
        return val, None, None, uno("UnidaddeMedida")
    return None


def informe_spec(vistas):
    barra("§4 — pricePerUnit (Carrefour) y PrecioPorUnd (Día): ¿contra qué cierran?",
          "Clases, en orden: = Price ÷ contenido del título · = Price · = ListPrice ÷ contenido · = ListPrice · otro.",
          "Filas distintas por (cadena, producto, Price) en cada captura. Tolerancia 2%.")
    for c in ("Carrefour", "Día"):
        cnt = defaultdict(Counter)
        otros, pesables = [], []
        for nombre, filas in vistas:
            vistos = set()
            for fs in filas[c].values():
                for f in fs:
                    clave = (f["of"].producto, f["of"].precio)
                    if clave in vistos:
                        continue
                    vistos.add(clave)
                    r = ref_spec(c, f["spec"])
                    if not r or not r[0]:
                        continue
                    val, tipo, base_ref, txt = r
                    _it, co = vt(f)
                    price, lista = co.get("Price"), co.get("ListPrice")
                    ref = f["ref"]
                    por_titulo = (ref.get("tipo") == tipo and ref.get("base") and base_ref)
                    clase = "otro"
                    if por_titulo and M.cerca(val, price / ref["base"] * base_ref, TOL_PESO):
                        clase = "= Price ÷ contenido del título"
                    elif M.cerca(val, price, TOL_PESO):
                        clase = "= Price"
                    elif por_titulo and lista and M.cerca(val, lista / ref["base"] * base_ref, TOL_PESO):
                        clase = "= ListPrice ÷ contenido"
                    elif lista and M.cerca(val, lista, TOL_PESO):
                        clase = "= ListPrice"
                    disp = "disp" if f["of"].disponible else "no disp"
                    cnt[(nombre, disp)][clase] += 1
                    linea = (f"{nombre:<10} {disp:<7} spec={val:>10,.2f} ({txt}) Price={price:>10,.2f} "
                             f"spec/Price={val / price:5.2f} título={M.txt_ref(ref)[:20]:<20} {f['of'].producto[:48]}")
                    if es_raro(f):
                        pesables.append(f"{clase:<30} {mu_um(f)[0]}/{mu_um(f)[1]} " + linea)
                    elif clase == "otro":
                        otros.append(linea)
        print(f"\n  ■ {c}")
        for g in sorted(cnt):
            print(f"    {g[0]:<12} {g[1]:<8} {dict(cnt[g])}")
        print(f"    Filas con multiplicador ≠ 1 ({len(pesables)}):")
        for li in pesables:
            print("      " + li)
        print(f"    'otro' en filas un/1.0 ({len(otros)}; primeras 25):")
        for li in otros[:25]:
            print("      " + li)


def informe_coto(prod):
    barra("§5 — COTO: ¿qué publica en los pesables?",
          "Coto no tiene measurementUnit/unitMultiplier. Se leen los atributos que el parser no lee.")
    unicas = {}
    for q, fs in prod["Coto"].items():
        for f in fs:
            unicas.setdefault((f["of"].producto, f["of"].precio), (q, f))
    pes = [(q, f) for q, f in unicas.values() if str(coto_attr(f, "product.unidades.esPesable")) == "1"]
    no_pes = [(q, f) for q, f in unicas.values() if str(coto_attr(f, "product.unidades.esPesable")) != "1"]
    print(f"    filas distintas: {len(unicas)} · pesables (esPesable=1): {len(pes)}")
    cnt = Counter()
    for q, f in sorted(pes, key=lambda x: (x[0], x[1]["of"].producto)):
        g = lambda k: coto_attr(f, k)                                             # noqa: E731
        act, ref = M._num(g("sku.activePrice")), M._num(g("sku.referencePrice"))
        try:
            dto = json.loads(g("sku.dtoPrice") or "{}")
        except ValueError:
            dto = {}
        salto = M._num(g("product.saltoCantidad"))
        cnt[(g("product.unidades.descUnidad"), (g("product.cFormato") or "").strip(),
             "act=ref" if act and ref and abs(act - ref) < 0.01 else "act≠ref")] += 1
        print(f"      [{q[:12]:<12}] act={act!s:<9} ref={ref!s:<9} descUnidad={g('product.unidades.descUnidad')} "
              f"cFormato={(g('product.cFormato') or '').strip()!r} cantForm={g('product.cantForm')} "
              f"mín={g('product.cantidadMinima')} salto={g('product.saltoCantidad')} "
              f"dtoPrice precio/lista={dto.get('precio')}/{dto.get('precioLista')} qty={g('sku.quantity')} "
              f"→ click≈${act * salto / 1000 if act and salto else 0:,.2f} | {f['of'].producto[:50]}")
    print(f"\n    Resumen pesables (descUnidad, cFormato, act vs ref): {dict(cnt)}")
    cnt_np = Counter((coto_attr(f, "product.unidades.descUnidad"), (coto_attr(f, "product.cFormato") or "").strip(),
                      coto_attr(f, "product.cantidadMinima"), coto_attr(f, "product.saltoCantidad"))
                     for _q, f in no_pes)
    print(f"    No pesables (descUnidad, cFormato, cantidadMinima, saltoCantidad): {dict(cnt_np.most_common(8))}")

    claves_p = Counter(k for _q, f in pes for k in (f["raw"].get("completo") or {}))
    claves_np = Counter(k for _q, f in no_pes for k in (f["raw"].get("completo") or {}))
    n_np = sum(1 for _q, f in no_pes if f["raw"].get("completo"))
    solo_p = sorted(k for k in claves_p if k not in claves_np)
    print(f"\n    Claves del registro completo presentes en pesables y en NINGUNO de los {n_np} no pesables "
          f"de contraste: {solo_p or 'ninguna'}")
    for k in solo_p:
        vals = Counter(str((f["raw"]["completo"].get(k) or [None])[0])[:40] for _q, f in pes if f["raw"].get("completo"))
        print(f"      {k}: {dict(vals.most_common(6))}")
    patron = re.compile(r"peso|gram|kg|salto|minim|form|unid|medida|cant", re.I)
    print("    Claves con nombre de peso/cantidad en los pesables y sus valores:")
    for k in sorted(k for k in claves_p if patron.search(k)):
        vals = Counter(str((f["raw"]["completo"].get(k) or [None])[0])[:30] for _q, f in pes if f["raw"].get("completo"))
        print(f"      {k}: {dict(vals.most_common(8))}")


def informe_carne_chango(cap, prod, con_sc):
    barra("§6 — CARNE PICADA DE CHANGO MÁS: API, título, especificaciones, descripción y carrito, lado a lado")
    p_idx, s_idx = por_item(prod, "Chango Más"), por_item(con_sc, "Chango Más")
    for i in dict.fromkeys(list(p_idx) + list(s_idx)):
        q, f = p_idx.get(i) or s_idx.get(i)
        if q != "Carne picada" or not es_raro(f):
            continue
        _it, co = vt(f)
        mu, um = mu_um(f)
        s = cap["sim"]["Chango Más"].get(str(i)) or {}
        extra = f["raw"].get("extra") or {}
        print(f"\n    {f['of'].producto}  (itemId {i}, disp={f['of'].disponible})")
        print(f"      API     : {mu}/{um}  Price={co.get('Price')}  Full={co.get('FullSellingPrice')}  "
              f"List={co.get('ListPrice')}")
        print(f"      carrito : {s}")
        print(f"      título  : {M.txt_ref(f['ref'])}   nameComplete={extra.get('nameComplete')!r} "
              f"complementName={extra.get('complementName')!r}")
        print(f"      (b)     : {clase_contenido(f)}")
        for k, v in (extra.get("spec_peso") or {}).items():
            print(f"      spec    : {k} = {v}")
        print(f"      descr.  : {extra.get('description')!r}")
        print(f"      backend : precio_min=${f['of'].precio:,.2f} {M.txt_pu(f['pu'])}")


def _orden(fi_por_cadena):
    con = [(fi["delta_pct_sin_promo"], c) for c, fi in fi_por_cadena.items() if fi["delta_pct_sin_promo"] is not None]
    return [c for _d, c in sorted(con)]


def informe_fichas(titulo, filas, canasta, dia):
    barra(f"§7 — FICHAS: {titulo}",
          f"Día {dia} (0=lunes), PROMOS_DEFAULT, sin promos del sitio (la captura no guarda Teasers).",
          "hoy = producción · A = precio por unidad de medida · B = precio del artículo (click) · "
          "+Coto = misma regla en los pesables de Coto.")
    corr = {}
    for cual, coto in VARIANTES:
        corr[nombre_var(cual, coto)] = fichas(filas, canasta, cual, coto, dia)
    nombres = list(corr)
    for campo in ("total_envase", "total_final", "delta_pct_sin_promo", "delta_pct_final", "n_con_promedio"):
        print(f"\n    {campo:<20}" + "".join(f"{n:>13}" for n in nombres))
        for c in CADENAS:
            vals = [corr[n][2].get(c, {}).get(campo) for n in nombres]
            print(f"    {c:<20}" + "".join(f"{('—' if v is None else (f'{v:,.2f}' if isinstance(v, float) else v)):>13}"
                                           for v in vals))
    base = _orden(corr["hoy"][2])
    print(f"\n    Orden por delta_pct_sin_promo (menor primero):")
    for n in nombres:
        o = _orden(corr[n][2])
        marca = "" if o == base else "   ⚠ SE DA VUELTA respecto de hoy"
        print(f"      {n:<8} " + " < ".join(f"{c} {corr[n][2][c]['delta_pct_sin_promo']:+.1f}" for c in o) + marca)

    print("\n    Filas que cambian en alguna lectura (estado · cantidad × precio_unit = subtotal · Δ% · métrica):")
    for c in CADENAS:
        for p in canasta:
            filas_v = {}
            for n in nombres:
                fi = corr[n][2].get(c)
                filas_v[n] = next((d for d in fi["detalle"] if d["producto"] == p["nombre"]), None) if fi else None
            clave = lambda d: None if d is None else (d["estado"], d["precio_unit"], d["delta_pct"],   # noqa: E731
                                                      (d["precio_por_100u"] or {}).get("precio_base"))
            if len({clave(d) for d in filas_v.values()}) <= 1:
                continue
            d0 = filas_v["hoy"] or {}
            print(f"      {c:<10} {p['nombre']:<18} ({p['cantidad']} {p.get('unidad')}) "
                  f"{((d0.get('precio_por_100u') or {}).get('desc_ganadora') or '')[:50]}")
            for n in nombres:
                d = filas_v[n]
                if not d:
                    continue
                pu = d["precio_por_100u"] or {}
                pu_txt = f"{pu.get('valor', 0):,.2f} {pu.get('label', '').split(' (')[0]}" if pu else "sin métrica"
                pu_txt += f" [{pu.get('fuente_contenido')}]" if pu.get("fuente_contenido") else ""
                pr = d["precio_unit"]
                print(f"          {n:<7} {d['estado']:<15} {d['cantidad']} × {pr if pr is None else f'{pr:,.2f}'} "
                      f"= {d['subtotal']:,.2f}  Δ {d['delta_pct']}  {pu_txt}")
    print("\n    Promedio de mercado (mediana $/unidad base) donde cambia:")
    for p in canasta:
        vs = [corr[n][1].get(p["nombre"]) or {} for n in nombres]
        if len({(v.get("unidad_base"), v.get("n_cadenas"), round(v.get("precio_base") or 0, 4)) for v in vs}) > 1:
            print(f"      {p['nombre']:<20} " + " · ".join(
                f"{n}={v.get('precio_base')}{v.get('unidad_base') or ''}×{v.get('n_cadenas')}" for n, v in zip(nombres, vs)))
    print("\n    Precio tachado (descuento_pct) de los representantes, donde cambia:")
    for (c, q), d in sorted(corr["hoy"][0].items()):
        vals = [corr[n][0].get((c, q), {}).get("descuento_pct") for n in nombres]
        if len(set(vals)) > 1:
            print(f"      {c:<10} {q:<18} " + " · ".join(f"{n}={v}" for n, v in zip(nombres, vals)))


def informe_candidatos(cap, con_sc, prod):
    barra("§8 — CANDIDATOS PARA EL CARRITO DE SANTIAGO: filas raras disponibles con carrito simulado, más baratas primero")
    for c in VTEX:
        rows = []
        for i, (q, f) in por_item(con_sc, c).items():
            s = cap["sim"][c].get(str(i))
            if es_raro(f) and s and s.get("total") is not None and s.get("availability") == "available":
                rows.append((s["total"], q, f, s, i))
        print(f"\n    {c}:")
        for total, q, f, s, i in sorted(rows, key=lambda r: r[0])[:5]:
            _it, co = vt(f)
            mu, um = mu_um(f)
            print(f"      ${total:>10,.2f} por 1 unidad · {mu}/{um} Price={co.get('Price'):,.2f} "
                  f"Full={co.get('FullSellingPrice')} · [{q}] {f['of'].producto[:60]} (itemId {i})")
    print("\n    Coto (pesables, sin simulación posible; click ≈ activePrice × saltoCantidad):")
    unicas = {}
    for q, fs in prod["Coto"].items():
        for f in fs:
            if str(coto_attr(f, "product.unidades.esPesable")) == "1":
                unicas.setdefault(f["of"].producto, (q, f))
    for q, f in sorted(unicas.values(), key=lambda x: x[1]["of"].precio)[:6]:
        salto = M._num(coto_attr(f, "product.saltoCantidad"))
        print(f"      ${f['of'].precio:>10,.2f}/kg · salto={salto} g → ${f['of'].precio * (salto or 0) / 1000:,.2f} · "
              f"[{q}] {f['of'].producto[:60]}")


def main_cli():
    ap = argparse.ArgumentParser()
    ap.add_argument("--guardar", help="guarda la captura recortada en este archivo")
    ap.add_argument("--desde", help="re-analiza una captura guardada, sin red")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    logging.disable(logging.INFO)

    if args.desde:
        with open(args.desde, encoding="utf-8") as fh:
            cap = json.load(fh)
    else:
        cap = capturar()
        if args.guardar:
            with open(args.guardar, "w", encoding="utf-8") as fh:
                json.dump(cap, fh, ensure_ascii=False)

    prod = parsear(cap["datos"])
    con_sc = parsear({**cap["datos_sc"], "Coto": cap["datos"]["Coto"]})
    try:
        estado_dia = validar(cap, prod, con_sc)
    except InstrumentoInvalido as e:
        print(f"ERROR (InstrumentoInvalido):\n  {e}", file=sys.stderr)
        sys.exit(1)

    canasta_cap = json.loads(CAPTURA_CANASTA.read_text(encoding="utf-8"))
    filas_canasta = parsear(canasta_cap["datos"])
    filas_13 = parsear(json.loads(CAPTURA_13_09.read_text(encoding="utf-8"))["datos"])

    print(f"Captura: {cap['__fecha']} · {cap['__origen']} · sc por cadena: {cap['sc']}")
    print("Instrumento validado: (a) representantes en la captura y 'hoy' == producción · (b) precio tachado "
          "reimplementado == producción en las un/1.0")
    print(f"  (c) carrito de Santiago, pollo de Día: {estado_dia}")
    for c in CADENAS:
        print(f"  {c:<11} " + " · ".join(f"{q.split()[0]} {len(prod[c][q])}" if q in prod[c] else f"{q.split()[0]} FALLÓ"
                                         for q in CONSULTAS))
    n_sim = sum(len(v) for v in cap["sim"].values())
    print(f"  simulaciones de carrito: {n_sim}")
    if cap["fallas"]:
        print(f"  FALLAS: {cap['fallas']}")

    informe_crudo(cap, prod, con_sc)
    informe_regla(cap, [("nueva", prod), ("nueva", con_sc), ("13/09", filas_13), ("14/09 canasta", filas_canasta)])
    informe_canal(cap, prod, con_sc)
    informe_spec([("nueva", prod), ("nueva sc", con_sc), ("13/09", filas_13), ("14/09", filas_canasta)])
    informe_coto(prod)
    informe_carne_chango(cap, prod, con_sc)
    dia_canasta = datetime.fromisoformat(canasta_cap["__fecha"]).weekday()
    informe_fichas(f"canasta por defecto COMPLETA, captura del {canasta_cap['__fecha']} (producción, sin sc)",
                   filas_canasta, CANASTA, dia_canasta)
    parcial = [p for p in CANASTA if p["nombre"] in CONSULTAS]
    dia = datetime.fromisoformat(cap["__fecha"]).weekday()
    informe_fichas(f"canasta PARCIAL ({', '.join(p['nombre'] for p in parcial)}), captura nueva, SIN sc",
                   prod, parcial, dia)
    informe_candidatos(cap, con_sc, prod)

    if cap["fallas"]:
        print(f"\nMEDICIÓN INCOMPLETA: {len(cap['fallas'])} consultas fallaron", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main_cli()
