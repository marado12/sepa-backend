"""
Pesables: `Price` cotiza UNA unidad de medida (el kilo), no el artículo. Con filas REALES. Sin red.

POR QUÉ EXISTE (Tarea 25). El backend usaba `Price` como precio del artículo y `unitMultiplier` como
su contenido. Santiago verificó en el carrito de Día que $4.390 es el precio del KILO del pollo
(`kg` / 3.0): la métrica salía 3× abaratada, y en el tomate de Vea (`kg` / 0.1) 10× encarecida
(+684% contra el mercado). De paso `_precio_lista` tomaba `FullSellingPrice` —otra escala— como precio
tachado: 66,7% de descuento falso en los tres pollos.

Medido el 14/09 (tests/capturas/pesables_vivo_2026-09-14.json, instrumento tests/medir_pesables.py),
con la simulación de checkout de VTEX, que reproduce el carrito de Santiago ($13.170):
  - el carrito cobra `FullSellingPrice` = Price × unitMultiplier en 284 de 284 filas simuladas;
  - `unitMultiplier` es el PASO DE VENTA (lo que agrega un click), no el paquete: Vea vende el
    "Pollo Entero Sin Menudos 2 Kg" de a 0,5 kg y el jamón de a 100 g;
  - Coto publica `activePrice` por kilo en sus pesables (esPesable=1, descUnidad=KGS).

Decisiones de Santiago (14/09):
  - lectura A: en un pesable `precio_min` es el precio por unidad de medida, y la cantidad de la
    canasta son unidades de medida ("Pollo entero, 2 kg" = 2 × $/kg). Es la única lectura que se
    aplica igual en las 5 cadenas: el paso de venta ni siquiera es comparable entre las VTEX.
  - Coto entra con la misma regla.
  - (delegada) `measurementUnit` "un" con multiplicador ≠ 1: el precio es lo que cobra el click
    (`FullSellingPrice`) y el contenido sale del título, como hoy.

Alcance honesto: una captura, un solo punto de medición (Argentina); la simulación es sin sesión.
"""
import json
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import main
from precios_vtex import _UNIDAD_SIN_CONTENIDO, _precio_por_100u, buscar_precios_online
from tests import medir_metrica_vivo as M
from tests import medir_pesables as P

RAIZ = Path(__file__).resolve().parent
CAPTURA = RAIZ / "tests" / "capturas" / "pesables_vivo_2026-09-14.json"
CAPTURAS_VIEJAS = [RAIZ / "tests" / "capturas" / "metrica_vivo_2026-09-13.json",
                   RAIZ / "tests" / "capturas" / "canasta_default_2026-09-14.json"]
VTEX = P.VTEX

_CACHE = {}


def _cap():
    if "cap" not in _CACHE:
        _CACHE["cap"] = json.loads(CAPTURA.read_text(encoding="utf-8"))
    return _CACHE["cap"]


def _filas(ruta=None):
    """{cadena: {clave: fila}}, filas distintas, parseadas con el código de producción."""
    clave_cache = str(ruta or CAPTURA)
    if clave_cache not in _CACHE:
        datos = _cap()["datos"] if ruta is None else json.loads(ruta.read_text(encoding="utf-8"))["datos"]
        out = {c: {} for c in P.CADENAS}
        for c, por_q in P.parsear(datos).items():
            for fs in por_q.values():
                for f in fs:
                    iid = P.vt(f)[0].get("itemId") if c != "Coto" else None
                    out[c].setdefault(iid or (f["of"].producto, f["of"].precio), f)
        _CACHE[clave_cache] = out
    return _CACHE[clave_cache]


def _metrica(of):
    return _precio_por_100u(of, main._extraer_cantidades_desc, main.normalizar)


def _crudo(f):
    it, co = P.vt(f)
    return ((it.get("measurementUnit") or "").strip().lower(), it.get("unitMultiplier"),
            float(co["Price"]), co.get("FullSellingPrice"))


def _de_contenido(mu):
    return bool(mu) and mu not in _UNIDAD_SIN_CONTENIDO


def _es_pesable_coto(f):
    return (str(P.coto_attr(f, "product.unidades.esPesable")) == "1"
            and (P.coto_attr(f, "product.unidades.descUnidad") or "").strip().upper() == "KGS")


def _cerca(a, b, tol=0.01):
    return a is not None and b is not None and abs(a - b) <= tol * max(abs(b), 1e-9)


# ── (a) Filas medidas, escritas a mano desde la captura ──────────
# (cadena, itemId o título en Coto) → (precio_min, $/100g esperado o None si no se exige,
# descuento_pct esperado). Los números salen de los campos crudos y del carrito, no del código.
FILAS_MEDIDAS = {
    # kg / 3.0 — el caso de Santiago. Carrito 11.937 = 3.979 × 3.
    ("Carrefour", "105063"): (3979.0, 397.90, None),    # "Pollo entero congelado x kg" (hoy 132,63 · 66,7% OFF)
    ("Día", "90164"): (4390.0, 439.00, None),           # "Pollo Entero x Kg." — carrito 13.170
    # paso de venta < 1 kg: la métrica salía encarecida
    ("Carrefour", "8939"): (6999.0, 699.90, None),      # "Tomate perita x kg." kg/0.25 (hoy 2.799,60)
    ("Vea", "167686"): (6899.0, 689.90, None),          # "Tomate Perita x Kg" kg/0.1 (hoy 6.899)
    ("Chango Más", "196455"): (4399.0, 439.90, None),   # "Tomate Perita 500 G" kg/0.5 (hoy 879,80)
    ("Chango Más", "38299"): (15399.0, 1539.90, None),  # "Carne Picada Magra 800 G" kg/0.65 (hoy 2.369,08)
    # "un" con multiplicador ≠ 1: lo que cobra el click, contenido del título
    ("Vea", "331899"): (1995.0, 399.00, None),          # "Carne Picada de Cerdo 500 Grs" un/0.5 — carrito 1.995 (hoy $3.990)
    ("Chango Más", "221947"): (3654.0, None, None),     # "Medialunas De Manteca 6u" un/6 — carrito 3.654 (hoy $609 · 83,3% OFF)
    ("Chango Más", "238114"): (29994.0, None, None),    # "Medialuna De Manteca 6 U" un/6 — carrito 29.994
    # Coto: activePrice es el kilo (hoy sin métrica)
    ("Coto", ("Picada Desgrasada Estancias Coto X KG", 15699.0)): (15699.0, 1569.90, None),
    ("Coto", ("Pata Con Piel X Kg Congelados", 4999.0)): (4999.0, 499.90, None),
}


def test_las_filas_medidas_dan_el_precio_y_la_metrica_del_carrito():
    fallos = []
    for (c, clave), (precio, valor, desc) in FILAS_MEDIDAS.items():
        f = _filas()[c].get(clave)
        assert f, f"{c} {clave} no está en la captura — la lista del test está mal escrita"
        of, pu = f["of"], _metrica(f["of"])
        if abs(of.precio - precio) > 0.01:
            fallos.append(f"{c} {of.producto!r}: precio_min {of.precio} (esperado {precio})")
        if valor is not None and not (pu and abs(pu["valor"] - valor) <= 0.01):
            fallos.append(f"{c} {of.producto!r}: métrica {pu and pu['valor']} (esperado {valor})")
        if of.descuento_pct != desc:
            fallos.append(f"{c} {of.producto!r}: descuento_pct {of.descuento_pct} (esperado {desc})")
    assert not fallos, "filas medidas que no dan lo del carrito:\n   " + "\n   ".join(fallos)


# ── (b) El supuesto de datos: la regla de la API ─────────────────
def test_full_selling_price_es_price_por_unit_multiplier_en_todas_las_capturas():
    """
    Todo el fix se apoya en esto. Si una captura nueva lo rompe, el fix hay que revisarlo, no el test.
    Medido: 4.274 filas con Price > 0 en tres capturas, 0 fallas. En Vea cierra truncando al centavo.
    """
    fallos, total = [], Counter()
    for ruta in [CAPTURA] + CAPTURAS_VIEJAS:
        for c in VTEX:
            for f in _filas(None if ruta == CAPTURA else ruta)[c].values():
                mu, um, price, full = _crudo(f)
                total[c] += 1
                if full is None or um is None or abs(full - price * um) >= 0.011:
                    fallos.append(f"{ruta.name} {c} {mu}/{um} Price={price} Full={full} {f['of'].producto[:50]}")
    assert all(total[c] > 0 for c in VTEX), f"alguna cadena sin filas: {dict(total)}"
    assert not fallos, f"{len(fallos)} filas donde Full ≠ Price × um:\n   " + "\n   ".join(fallos[:15])


def test_el_carrito_simulado_cobra_full_selling_price():
    fallos, total = [], Counter()
    for c in VTEX:
        for f in _filas()[c].values():
            s = _cap()["sim"][c].get(str(P.vt(f)[0].get("itemId")))
            if not s or s.get("total") is None:
                continue
            total[c] += 1
            if abs(s["total"] - _crudo(f)[3]) > 0.01:
                fallos.append(f"{c} {f['of'].producto[:50]}: carrito {s['total']} ≠ Full {_crudo(f)[3]}")
    assert all(total[c] > 0 for c in VTEX), f"alguna cadena sin simulación: {dict(total)}"
    assert not fallos, "\n   ".join(fallos)


# ── (c) Las un/1.0 no se mueven ──────────────────────────────────
def _lista_original(co, precio):
    """La regla de `_precio_lista` antes de la Tarea 25, escrita acá como referencia."""
    candidatos = []
    for campo in ("ListPrice", "PriceWithoutDiscount", "FullSellingPrice"):
        v = float(co.get(campo) or 0)
        if v > precio and (1 - precio / v) * 100 <= 90.0:
            candidatos.append(v)
    return max(candidatos) if candidatos else None


def test_las_filas_un_1_no_cambian_ni_precio_ni_tachado_ni_metrica():
    fallos, total = [], Counter()
    for ruta in [None] + CAPTURAS_VIEJAS:
        for c in VTEX:
            for f in _filas(ruta)[c].values():
                mu, um, price, _full = _crudo(f)
                if not (mu == "un" and um == 1.0):
                    continue
                total[c] += 1
                of = f["of"]
                titulo = _metrica(replace(of, unidad_medida=None, contenido=None))
                if of.precio != price or of.precio_lista != _lista_original(P.vt(f)[1], price) \
                        or _metrica(of) != titulo:
                    fallos.append(f"{c} {of.producto[:50]}: precio {of.precio}/{price} "
                                  f"lista {of.precio_lista} métrica {_metrica(of)} vs título {titulo}")
    assert all(total[c] > 100 for c in VTEX), f"muy pocas un/1.0 por cadena: {dict(total)}"
    assert not fallos, f"{len(fallos)} filas un/1.0 cambiaron:\n   " + "\n   ".join(fallos[:15])


# ── (d) Guards POR CADENA: piso y brecha, como test_unidades.py ──
# Sobre una captura fija las dos reglas son exactas, no estadísticas: el piso es 100 y la brecha 0.
# La brecha no es redundante: dice QUÉ cadena se cayó, que es lo que un promedio esconde.
# ⚠️ Lección de la Tarea 20: si una brecha se pone en rojo, antes de tocar la constante hay que mirar
# si alguna cadena BAJÓ. Una brecha también se abre cuando el fix se aplica en unas cadenas y no en otras.
PISO_CARRITO = 100.0
BRECHA_CARRITO_MAX = 0.0
# Filas mínimas por cadena para que el guard mida algo (lo medido el 14/09, redondeado hacia abajo).
MIN_FILAS_CARRITO = {"Carrefour": 65, "Día": 12, "Vea": 150, "Chango Más": 20}


def _acierto_carrito():
    """
    % de filas con carrito simulado donde `precio_min` reconstruye lo que cobra el click:
      measurementUnit con contenido → precio_min × unitMultiplier == carrito, y contenido == 1;
      "un" (con o sin multiplicador) → precio_min == carrito.
    """
    ok, tot = Counter(), Counter()
    for c in VTEX:
        for f in _filas()[c].values():
            s = _cap()["sim"][c].get(str(P.vt(f)[0].get("itemId")))
            if not s or s.get("total") is None:
                continue
            mu, um, _price, _full = _crudo(f)
            of = f["of"]
            if _de_contenido(mu):
                bien = abs(of.precio * um - s["total"]) < 0.011 and of.contenido == 1.0
            else:
                bien = abs(of.precio - s["total"]) < 0.011
            ok[c] += bien
            tot[c] += 1
    return {c: round(ok[c] / tot[c] * 100, 1) for c in VTEX if tot[c]}, tot


def test_ninguna_cadena_queda_abajo_del_piso_del_carrito():
    acierto, tot = _acierto_carrito()
    cortas = {c: tot[c] for c, n in MIN_FILAS_CARRITO.items() if tot[c] < n}
    assert not cortas, f"el guard mide muy pocas filas: {cortas} (mínimos {MIN_FILAS_CARRITO})"
    bajas = {c: p for c, p in acierto.items() if p < PISO_CARRITO}
    assert not bajas, f"precio_min no reconstruye el carrito: {bajas} (todas: {acierto})"


def test_la_brecha_del_carrito_entre_cadenas_no_se_abre():
    acierto, _ = _acierto_carrito()
    assert set(acierto) == set(VTEX), f"faltan cadenas: {acierto}"
    brecha = max(acierto.values()) - min(acierto.values())
    assert brecha <= BRECHA_CARRITO_MAX, (
        f"{brecha:.1f} puntos entre la mejor y la peor cadena (máx {BRECHA_CARRITO_MAX}): {acierto}. "
        f"Mirá si alguna cadena BAJÓ antes de tocar esto.")


PISO_METRICA_PESABLES = 100.0
BRECHA_METRICA_PESABLES_MAX = 0.0
MIN_FILAS_PESABLES = {"Carrefour": 60, "Día": 10, "Vea": 150, "Chango Más": 15, "Coto": 50}


def _acierto_metrica_pesables():
    """
    % de pesables cuyo $/unidad de medida es el precio publicado por unidad de medida.
    VTEX: measurementUnit con contenido (kg, g, lt…) y Price > 0 → precio_base × 1 unidad == Price.
    Coto: esPesable=1 y descUnidad=KGS → precio_base × 1000 g == activePrice.
    """
    ok, tot = Counter(), Counter()
    for c in P.CADENAS:
        for f in _filas()[c].values():
            if c == "Coto":
                if not _es_pesable_coto(f):
                    continue
                referencia, base = f["of"].precio, 1000.0
            else:
                mu, _um, price, _full = _crudo(f)
                if not _de_contenido(mu):
                    continue
                una = P.contenido_api(mu, 1.0)
                if not una:
                    continue
                referencia, base = price, una[1]
            pu = _metrica(f["of"])
            ok[c] += bool(pu and _cerca(pu["precio_base"] * base, referencia))
            tot[c] += 1
    return {c: round(ok[c] / tot[c] * 100, 1) for c in P.CADENAS if tot[c]}, tot


def test_ninguna_cadena_queda_abajo_del_piso_de_metrica_de_pesables():
    acierto, tot = _acierto_metrica_pesables()
    cortas = {c: tot[c] for c, n in MIN_FILAS_PESABLES.items() if tot[c] < n}
    assert not cortas, f"el guard mide muy pocas filas: {cortas} (mínimos {MIN_FILAS_PESABLES})"
    bajas = {c: p for c, p in acierto.items() if p < PISO_METRICA_PESABLES}
    assert not bajas, f"$/kg de pesables distinto del precio por kilo: {bajas} (todas: {acierto})"


def test_la_brecha_de_metrica_de_pesables_no_se_abre():
    acierto, _ = _acierto_metrica_pesables()
    assert set(acierto) == set(P.CADENAS), f"faltan cadenas (¿Coto sin la regla?): {acierto}"
    brecha = max(acierto.values()) - min(acierto.values())
    assert brecha <= BRECHA_METRICA_PESABLES_MAX, (
        f"{brecha:.1f} puntos entre la mejor y la peor cadena (máx {BRECHA_METRICA_PESABLES_MAX}): "
        f"{acierto}. Mirá si alguna cadena BAJÓ antes de tocar esto.")


# ── (e) Ningún precio tachado en otra escala ─────────────────────
def test_ningun_descuento_sale_de_mezclar_escalas():
    """
    Con multiplicador ≠ 1, el tachado solo puede ser ListPrice o PriceWithoutDiscount (que cotizan
    lo mismo que Price), llevados a la escala de precio_min. Medido antes del fix: 66,7% en los
    pollos kg/3.0 y 83,3% en las medialunas un/6, los dos con FullSellingPrice como "precio de lista".
    """
    fallos, total = [], Counter()
    for ruta in [None] + CAPTURAS_VIEJAS:
        for c in VTEX:
            for f in _filas(ruta)[c].values():
                mu, um, price, _full = _crudo(f)
                if um in (None, 1.0):
                    continue
                total[c] += 1
                of, co = f["of"], P.vt(f)[1]
                if of.precio_lista is None:
                    continue
                escala = of.precio / price
                validos = [float(co.get(k) or 0) * escala for k in ("ListPrice", "PriceWithoutDiscount")]
                if not any(abs(of.precio_lista - v) < 0.02 for v in validos):
                    fallos.append(f"{c} {mu}/{um} {of.producto[:45]}: tachado {of.precio_lista} "
                                  f"({of.descuento_pct}% OFF) con precio {of.precio}")
    assert all(total[c] > 0 for c in VTEX), f"alguna cadena sin filas con multiplicador: {dict(total)}"
    assert not fallos, f"{len(fallos)} descuentos en otra escala:\n   " + "\n   ".join(fallos[:15])


# ── (f) La canasta por defecto, filas que el fix toca ────────────
def test_la_canasta_por_defecto_compara_pesables_por_kilo():
    """
    Captura de la canasta del 14/09, pipeline de producción. El total de la fila sigue siendo
    cantidad × precio por kilo (lectura A); lo que cambia es la métrica y, con ella, el %.
    """
    filas = P.parsear(json.loads(CAPTURAS_VIEJAS[1].read_text(encoding="utf-8"))["datos"])
    canasta = main.CANASTA_DEFAULT
    precios, _ = buscar_precios_online(canasta, P.CADENAS, M.FuenteCaptura(filas),
                                       main.normalizar, main._extraer_cantidades_desc)
    res = main._analizar(canasta, precios, [], 0, None)
    prom = main._promedios_por_producto(canasta, precios)
    fichas = {fi["cadena"]: {d["producto"]: d for d in fi["detalle"]}
              for fi in main._fichas(canasta, precios, res, prom, list(P.CADENAS))}
    pollo = fichas["Carrefour"]["Pollo entero"]
    assert pollo["subtotal"] == 2 * 3979.0, f"2 kg de pollo a $3.979 el kilo: {pollo['subtotal']}"
    assert abs(pollo["precio_por_100u"]["valor"] - 397.90) < 0.01, pollo["precio_por_100u"]
    tomate_vea = fichas["Vea"]["Tomate perita lata"]
    assert tomate_vea["delta_pct"] is not None and abs(tomate_vea["delta_pct"]) < 50, (
        f"el tomate de Vea volvió a salir en otra escala: Δ {tomate_vea['delta_pct']}% (antes del fix +684,2%)")
    for (c, q), d in precios.items():
        assert d["descuento_pct"] is None or d["descuento_pct"] < 60, f"{c}/{q}: {d['descuento_pct']}% OFF"


if __name__ == "__main__":
    fallidos = 0
    for nombre, fn in list(globals().items()):
        if nombre.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"OK    {nombre}")
            except AssertionError as e:
                fallidos += 1
                print(f"FALLA {nombre}\n      {str(e)[:900]}")
    print(f"\n{'TODO OK' if not fallidos else f'{fallidos} FALLARON'}")
    sys.exit(1 if fallidos else 0)
