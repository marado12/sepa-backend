"""
Tarea 26, paso (b): la fila cobra los ENVASES ENTEROS que cubren lo pedido. Con filas REALES. Sin red.

POR QUÉ EXISTE. El subtotal era `precio_min × cantidad` sin mirar qué compra un `precio_min`:
"Fideos spaghetti, 2 kg" con un paquete de 500 g cobraba 2 paquetes (1 kg), y "Gaseosa cola, 3 litro"
con una botella de 3 L cobraba 3 botellas (9 L). Medido en la captura de la canasta del 14/09
(tests/medir_unidad_pedida.py, tests/medir_regla_26b.py). Decisiones de la Tarea 26:
  1. "2 kg" son 2 kg, cubiertos con envases enteros; el subtotal es lo que pagarías por esos envases.
  3. Un candidato que no puede contestar lo pedido no puede ser representante (filtro, no puntaje).
  6. Si ninguno puede: la fila queda fuera del total y del %, y sigue votando en la mediana.
  4 (modificada el 18/09): el +0,20 y el ×0,7 de tamaño de `puntuar` se CONSERVAN hasta la Tarea 22.

Captura: tests/capturas/canasta_default_2026-09-14.json (Argentina, un solo punto de medición).
Una fixture inventada no es un test (CLAUDE.md): cada oferta de acá sale de esa captura. Lo único
armado a mano son PEDIDOS (lo que escribe el usuario) y, en `sin_elegible`, un recorte de los
candidatos reales de una cadena.
"""
import copy
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import main
import precios_vtex
from precios_vtex import buscar_precios_online, envases_a_comprar, puntuar
from tests import medir_metrica_vivo as M
from tests import medir_pesables as P

RAIZ = Path(__file__).resolve().parent
CAPTURA = RAIZ / "tests" / "capturas" / "canasta_default_2026-09-14.json"
CADENAS = P.CADENAS
CANASTA = main.CANASTA_DEFAULT
_ITEM = {p["nombre"]: p for p in CANASTA}

# Faltantes por cadena en esta captura: la cadena no tiene NINGÚN candidato sobre el umbral.
# Van al lado del piso: sin ellos el piso premia a la cadena que tiene menos datos (Tarea 26, paso 1).
FALTANTES = {"Carrefour": 0, "Día": 0, "Vea": 1, "Chango Más": 0, "Coto": 3}
# "% de filas que cubren lo pedido con los envases mínimos", por cadena. La captura es fija: el piso
# es lo medido. Con el código de antes del 18/09 (mutación en memoria: sin escala ni filtro) daba
# Carrefour 80,0 · Día 80,0 · Vea 78,9 · Chango Más 80,0 · Coto 82,4 (brecha 3,5), con los mismos
# faltantes. La brecha sola casi no lo ve: lo detecta el piso.
PISO_CUBRE = 100.0
BRECHA_MAX = 0.0

_CACHE = {}


def _filas():
    """{cadena: {consulta: [fila]}}, parseado con el código de producción. Es lo único que se cachea:
    todo lo que pasa por `buscar_precios_online`, `_analizar` o `_fichas` se recalcula en cada test,
    para que una mutación en memoria no quede escondida detrás de un resultado viejo."""
    if "filas" not in _CACHE:
        _CACHE["filas"] = P.parsear(json.loads(CAPTURA.read_text(encoding="utf-8"))["datos"])
    return _CACHE["filas"]


def _oferta(cadena, consulta, titulo, precio=None):
    """La oferta de la captura con ese título. Un buscador puede listarla dos veces; si las copias
    difieren en algo que cambie el precio o la métrica, el test no elige por su cuenta: hay que
    darle el precio (Carrefour tiene dos "Tomate perita x kg.", a $4.799 y a $6.999)."""
    ofs = [f["of"] for f in _filas()[cadena][consulta]
           if f["of"].producto == titulo and (precio is None or f["of"].precio == precio)]
    distintas = {(o.precio, o.ean, o.unidad_medida, o.contenido) for o in ofs}
    assert len(distintas) == 1, f"{cadena}/{consulta}: {titulo!r} → {len(ofs)} filas, {distintas}"
    return ofs[0]


def _pu(of):
    return precios_vtex._precio_por_100u(of, main._extraer_cantidades_desc, main.normalizar)


def _envases(item, of):
    return envases_a_comprar(item, of, _pu(of), main._extraer_cantidades_desc)


class _Fuente:
    """Las ofertas reales de la captura, opcionalmente recortadas: {(cadena, consulta): [títulos]}."""
    nombre = "captura"

    def __init__(self, recorte=None):
        self.recorte = recorte or {}

    def cadenas_soportadas(self):
        return list(CADENAS)

    def buscar(self, query, cadenas=None):
        res = M.FuenteCaptura(_filas()).buscar(query, cadenas)
        for (c, q), titulos in self.recorte.items():
            if q == query:
                res.ofertas = [o for o in res.ofertas if o.cadena != c or o.producto in titulos]
        return res


def _correr(canasta=CANASTA, recorte=None, dia=0):
    """El camino real: buscar_precios_online → _analizar → _promedios_por_producto → _fichas."""
    precios, _ = buscar_precios_online(canasta, CADENAS, _Fuente(recorte),
                                       main.normalizar, main._extraer_cantidades_desc)
    res = main._analizar(canasta, precios, main.PROMOS_DEFAULT, dia, None)
    prom = main._promedios_por_producto(canasta, precios)
    fichas = {fi["cadena"]: fi for fi in main._fichas(canasta, precios, res, prom, list(CADENAS))}
    return precios, prom, fichas


def _fila(fichas, cadena, producto):
    return next(d for d in fichas[cadena]["detalle"] if d["producto"] == producto)


# ── (a) Los cuatro casos de la escala, con ofertas reales ────────────

def test_caso1_precio_por_kilo_cobra_exacto_lo_pedido():
    """Pedido en kg + precio por unidad de medida: pedido ÷ lo que cubre el precio, sin redondear."""
    pollo = _oferta("Carrefour", "Pollo entero", "Pollo entero congelado x kg")
    assert precios_vtex._cotiza_um(pollo) and pollo.precio == 3979.0
    assert _envases(_ITEM["Pollo entero"], pollo) == 2.0                       # 2 kg × $3.979
    # "Exacto" es pedido ÷ contenido del precio, no `cantidad × precio`: 1500 gramos contra el
    # precio del kilo son 1,5 kilos, no 1500.
    assert _envases({"nombre": "Pollo entero", "cantidad": 1500.0, "unidad": "gramos"}, pollo) == 1.5
    assert _envases({"nombre": "Pollo entero", "cantidad": 0.5, "unidad": "kg"}, pollo) == 0.5


def test_caso2_articulo_se_cubre_con_envases_enteros():
    """Pedido en kg/litro + artículo con métrica del mismo tipo: ceil(pedido ÷ envase), sin tolerancia."""
    casos = [
        # (cadena, consulta, título, envases, subtotal) — números de la decisión 1 y la 8.
        ("Carrefour", "Fideos spaghetti", "Fideos spaghetti N3 Terrabusi 500 g.", 4.0, 5200.0),
        ("Carrefour", "Gaseosa cola", "Gaseosa cola Pepsi Black pet 3 lts", 1.0, 6050.0),
        ("Vea", "Gaseosa cola", "Gaseosa Secco Cola 2,25 Lt", 2.0, 4180.0),
        ("Día", "Aceite girasol", "Aceite de Girasol Dia 1,5 Lt.", 2.0, 9200.0),
        # Producto equivocado (Tarea 22): la regla no lo arregla, lo agranda — y queda visible.
        ("Coto", "Azúcar", "Mermelada Sin Azucar Sabor Durazno Cormillot 390g", 6.0, 22644.0),
    ]
    for c, q, titulo, n, sub in casos:
        of = _oferta(c, q, titulo)
        assert not precios_vtex._cotiza_um(of), titulo
        assert _envases(_ITEM[q], of) == n, f"{c}/{q}: {_envases(_ITEM[q], of)} envases, esperaba {n}"
        assert of.precio * n == sub, f"{c}/{q}: ${of.precio * n}, esperaba ${sub}"
    # Sin tolerancia hacia abajo: 0,9 L no cubre 1 L.
    natural = _oferta("Día", "Aceite girasol", "Aceite Girasol Natural 0,9 Lt.")
    assert _envases({"nombre": "Aceite girasol", "cantidad": 1, "unidad": "litro"}, natural) == 2.0


def test_caso3_unidad_se_cobra_por_click():
    """Pedido en unidad/pack + artículo: ceil(cantidad ÷ click). El click es 1, salvo "un" con multiplicador."""
    medialunas = _oferta("Chango Más", "Manteca", "Medialunas De Manteca\xa06u")
    assert precios_vtex._click(medialunas) == 6.0 and medialunas.precio == 3654.0
    assert _envases(_ITEM["Manteca"], medialunas) == 1.0          # "2 unidad": un click trae 6
    atun = _oferta("Carrefour", "Atún natural", "Atún desmenuzado Bulnez al natural 170 g.")
    assert _envases(_ITEM["Atún natural"], atun) == 3.0
    papel = _oferta("Carrefour", "Papel higienico", "Papel higiénico Elegante 4 u. x 80 m.")
    assert _envases(_ITEM["Papel higienico"], papel) == 1.0      # "1 pack"
    assert _envases({"nombre": "Atún natural", "cantidad": 1.5, "unidad": "unidad"}, atun) == 2.0


def test_caso4_sin_metrica_del_tipo_pedido_no_se_escala():
    """Lo que no se puede escalar da None: no se inventa un número de envases."""
    # Pedido "unidad" contra el precio del kilo: el kilo no dice cuántas latas.
    tomate_kg = _oferta("Carrefour", "Tomate perita lata", "Tomate perita x kg.", precio=6999.0)
    assert precios_vtex._cotiza_um(tomate_kg)
    assert _envases(_ITEM["Tomate perita lata"], tomate_kg) is None
    # Pedido en litros contra un título que no dice cuánto trae ("cmq" no es una unidad conocida).
    cunnington = _oferta("Coto", "Gaseosa cola", "Gaseosa Cola Cunnington 500cmq")
    assert _pu(cunnington) is None
    assert _envases(_ITEM["Gaseosa cola"], cunnington) is None
    # Pedido en kilos contra métrica de otro tipo (volumen): tampoco.
    odex = _oferta("Chango Más", "Detergente", "Detergente Odex Limón 500ml")
    assert _envases({"nombre": "Detergente", "cantidad": 1, "unidad": "kg"}, odex) is None
    # Y todo lo que da None es inelegible: los dos lados de la misma regla.
    for item, of in ((_ITEM["Tomate perita lata"], tomate_kg), (_ITEM["Gaseosa cola"], cunnington)):
        assert not precios_vtex.elegible(item, of, _pu(of), main._extraer_cantidades_desc)


# ── (b) El filtro, el estado residual y el invariante del Δ% ─────────

def test_el_filtro_elige_un_candidato_que_contesta_lo_pedido():
    """Decisión 3. Con el puntaje de siempre ganaba el tomate fresco "x kg" (0,667 > 0,533)."""
    precios, _prom, fichas = _correr()
    for c, titulo, sub in (("Carrefour", "Tomate perita Arcor 400 g.", 3158.0),
                           ("Día", "Tomate Perita Alco 400 Gr.", 3070.0),
                           ("Vea", "Tomate Perita Cubeteado Arcor 400 Gr", 2980.0),
                           ("Chango Más", "Tomate Perita Check 400 G", 2358.0)):
        e = precios[(c, "Tomate perita lata")]
        assert e["precio_por_100u"]["desc_ganadora"] == titulo, (c, e["precio_por_100u"]["desc_ganadora"])
        assert not e["sin_elegible"]
        assert _fila(fichas, c, "Tomate perita lata")["subtotal"] == sub
    gaseosa_coto = precios[("Coto", "Gaseosa cola")]
    assert gaseosa_coto["precio_por_100u"]["desc_ganadora"] == "Gaseosa Cola Manaos 2.25l"
    assert gaseosa_coto["match_score"] == 0.8                    # mismo score que el "500cmq"


def test_sin_candidato_elegible_sale_del_total_y_sigue_votando():
    """
    Decisión 6. En la canasta por defecto no hay ocupantes; se ejercita recortando los candidatos
    REALES de Carrefour a los que cotizan por kilo: ninguno dice cuántas latas.
    """
    filas_c = _filas()["Carrefour"]["Tomate perita lata"]
    solo_kg = [f["of"].producto for f in filas_c if precios_vtex._cotiza_um(f["of"])]
    assert "Tomate perita x kg." in solo_kg
    recorte = {("Carrefour", "Tomate perita lata"): solo_kg}
    precios_r, prom_r, fichas_r = _correr(recorte=recorte)
    _precios, prom, fichas = _correr()

    e = precios_r[("Carrefour", "Tomate perita lata")]
    assert e["sin_elegible"] and e["envases"] is None
    assert e["precio_min"] == 6999.0                            # el ganador de siempre, marcado
    fila = _fila(fichas_r, "Carrefour", "Tomate perita lata")
    assert fila["estado"] == "faltante" and fila["sin_elegible"] and fila["subtotal"] == 0
    assert fila["descartado"]["precio_unit"] == 6999.0
    assert "Carrefour" not in fila["tambien_en"]
    fi_r, fi = fichas_r["Carrefour"], fichas["Carrefour"]
    assert "Tomate perita lata" in fi_r["faltantes"]
    assert fi_r["n_disponibles"] == fi["n_disponibles"] - 1
    assert abs(fi_r["total_envase"] - (fi["total_envase"] - 3158.0)) < 0.005
    assert fi_r["n_con_promedio"] == fi["n_con_promedio"] - 1
    # Sigue votando: la mediana del tomate se arma con las 5 cadenas, no con 4.
    assert prom_r["Tomate perita lata"]["n_cadenas"] == prom["Tomate perita lata"]["n_cadenas"] == 5
    # Las otras cadenas no se enteran.
    for c in CADENAS[1:]:
        assert fichas_r[c]["total_envase"] == fichas[c]["total_envase"], c


def test_el_delta_de_una_fila_no_depende_de_cuantos_envases():
    """
    Decisión 6/8: `esp` y `subtotal` multiplican por los mismos envases, así que el Δ% es el cociente
    de precios por unidad. Fideos en Carrefour: +26,3% con 4 envases y con 2 (el número documentado).
    """
    precios, prom, fichas = _correr()
    assert _fila(fichas, "Carrefour", "Fideos spaghetti")["delta_pct"] == 26.3
    medio = copy.deepcopy(precios)
    medio[("Carrefour", "Fideos spaghetti")]["envases"] = 2.0
    res = main._analizar(CANASTA, medio, main.PROMOS_DEFAULT, 0, None)
    fichas2 = {fi["cadena"]: fi for fi in main._fichas(CANASTA, medio, res, prom, list(CADENAS))}
    fila2 = _fila(fichas2, "Carrefour", "Fideos spaghetti")
    assert fila2["subtotal"] == 2600.0 and fila2["delta_pct"] == 26.3


def test_la_cantidad_pedida_no_se_pisa():
    """Lo que se muestra es lo pedido; lo que multiplica al precio, los envases."""
    _precios, _prom, fichas = _correr()
    fila = _fila(fichas, "Carrefour", "Gaseosa cola")
    assert (fila["cantidad"], fila["envases"], fila["subtotal"]) == (3, 1.0, 6050.0)
    fila = _fila(fichas, "Carrefour", "Fideos spaghetti")
    assert (fila["cantidad"], fila["envases"], fila["subtotal"]) == (2, 4.0, 5200.0)


def test_una_entrada_sin_envases_se_cobra_como_siempre():
    """Ruta SEPA y precios manuales no traen `envases`: precio × cantidad, igual que antes del 18/09."""
    precios = {("Día", "x"): {"precio_min": 100.0, "precio_por_100u": None, "fuente": "manual"}}
    canasta = [{"nombre": "x", "cantidad": 3, "unidad": "kg"}]
    res = main._analizar(canasta, precios, [], 0, None)
    assert res["Día"]["total_base"] == 300.0
    fi = main._fichas(canasta, precios, res, {}, ["Día"])[0]
    assert fi["detalle"][0]["subtotal"] == 300.0 and fi["detalle"][0]["estado"] == "manual"


# ── (c) La señal de tamaño se conserva: lo que cuesta sacarla ────────

def test_el_bonus_de_tamano_empata_y_decide_el_precio():
    """
    El +0,20 satura en 1,0: entre dos yerbas de 1 kg empata los scores y gana la más barata. Si
    alguien lo saca, este test se pone en rojo en vez de cobrar $3.060 más en silencio.
    """
    item = _ITEM["Yerba mate"]
    buen_dia = _oferta("Chango Más", "Yerba mate", "Yerba Mate Buen Dia 1 Kg")
    mananita = _oferta("Chango Más", "Yerba mate", "Yerba Mate Mañanita 1 Kg")
    assert puntuar(item, buen_dia, main.normalizar) == 0.8            # sin la señal de tamaño
    assert puntuar(item, mananita, main.normalizar) == 0.867
    assert puntuar(item, buen_dia, main.normalizar, main._extraer_cantidades_desc) == 1.0
    assert puntuar(item, mananita, main.normalizar, main._extraer_cantidades_desc) == 1.0
    recorte = {("Chango Más", "Yerba mate"): [buen_dia.producto, mananita.producto]}
    precios, _ = buscar_precios_online([item], ["Chango Más"], _Fuente(recorte),
                                       main.normalizar, main._extraer_cantidades_desc)
    e = precios[("Chango Más", "Yerba mate")]
    assert (e["precio_min"], e["precio_por_100u"]["desc_ganadora"]) == (2799.0, buen_dia.producto), e


def test_el_castigo_de_tamano_evita_el_envase_chico():
    """
    El ×0,7 separa al envase lejos de lo pedido. Sin él, aceite en Día empata en 0,867 y el desempate
    por precio del envase elige el de 0,9 L: 3 × $4.369 = $13.107 en vez de 2 × $4.600 = $9.200.
    """
    item = _ITEM["Aceite girasol"]
    dia15 = _oferta("Día", "Aceite girasol", "Aceite de Girasol Dia 1,5 Lt.")
    natural = _oferta("Día", "Aceite girasol", "Aceite Girasol Natural 0,9 Lt.")
    assert puntuar(item, dia15, main.normalizar, main._extraer_cantidades_desc) == 0.867
    assert puntuar(item, natural, main.normalizar, main._extraer_cantidades_desc) == 0.607
    recorte = {("Día", "Aceite girasol"): [dia15.producto, natural.producto]}
    precios, _ = buscar_precios_online([item], ["Día"], _Fuente(recorte),
                                       main.normalizar, main._extraer_cantidades_desc)
    e = precios[("Día", "Aceite girasol")]
    assert (e["precio_min"], e["envases"]) == (4600.0, 2.0), e


# ── (d) Guard por cadena: piso, brecha y faltantes ───────────────────

_BASE_PEDIDO = {"kg": ("peso", 1000.0), "litro": ("volumen", 1000.0)}   # leído a mano, no con el parser


def _cubre(prod, fila, precios_oferta):
    """¿La plata de la fila compra lo pedido con los envases mínimos? Independiente de `envases_a_comprar`:
    lee los envases de la plata (subtotal ÷ precio) y el pedido de una tabla escrita a mano."""
    n = round(fila["subtotal"] / fila["precio_unit"], 6)
    pu = fila["precio_por_100u"]
    unidad = prod["unidad"]
    if unidad in _BASE_PEDIDO:
        tipo, base = _BASE_PEDIDO[unidad]
        pedido = prod["cantidad"] * base
        if not pu or pu["tipo"] != tipo:
            return False
        if pu.get("fuente_contenido") == "api":                       # precio por unidad de medida
            return abs(n * pu["cantidad_base"] - pedido) < 1e-6
        return n == int(n) and n * pu["cantidad_base"] >= pedido > (n - 1) * pu["cantidad_base"]
    if pu and pu.get("fuente_contenido") == "api":
        return False                                                   # el kilo no dice cuántos
    k = precios_oferta.contenido or 1.0
    return n == int(n) and n * k >= prod["cantidad"] > (n - 1) * k


def medir_por_cadena():
    """{cadena: (filas en el total, que cubren, faltantes, sin_elegible, % cubre)} por el camino real."""
    precios, _prom, fichas = _correr()
    out = {}
    for c in CADENAS:
        fi = fichas[c]
        en_total = cubre = 0
        for d in fi["detalle"]:
            if not d["ok"]:
                continue
            e = precios[(c, d["producto"])]
            of = next(f["of"] for f in _filas()[c][d["producto"]]
                      if f["of"].precio == e["precio_min"] and f["of"].ean == e["ean"])
            en_total += 1
            cubre += _cubre(_ITEM[d["producto"]], d, of)
        suma = sum(d["subtotal"] for d in fi["detalle"] if d["ok"])
        assert abs(suma - fi["total_envase"]) < 0.005, f"{c}: Σ filas {suma} ≠ total_envase {fi['total_envase']}"
        n_sin = sum(1 for d in fi["detalle"] if d.get("sin_elegible"))
        out[c] = (en_total, cubre, len(fi["faltantes"]), n_sin, cubre / en_total * 100 if en_total else 0.0)
    return out


def test_piso_y_brecha_por_cadena_con_faltantes():
    """
    Piso por cadena y brecha máxima (CLAUDE.md), con los faltantes al lado. También exige que la suma
    de las filas sea `total_envase`: el subtotal se calcula en dos lugares (`_analizar` y `_fichas`,
    decisión 7) y tienen que decir lo mismo.
    """
    m = medir_por_cadena()
    for c, (en_total, cubre, falt, n_sin, pct) in m.items():
        assert falt == FALTANTES[c], f"{c}: {falt} faltantes, la captura tiene {FALTANTES[c]}"
        assert n_sin == 0, f"{c}: {n_sin} filas sin candidato elegible (en esta captura son 0)"
        assert pct >= PISO_CUBRE, f"{c}: {cubre}/{en_total} filas cubren lo pedido ({pct:.1f}%)"
    pcts = [x[4] for x in m.values()]
    assert max(pcts) - min(pcts) <= BRECHA_MAX, f"brecha {max(pcts) - min(pcts):.1f} puntos"


if __name__ == "__main__":
    import logging
    logging.disable(logging.INFO)
    fallos = 0
    for nombre, fn in sorted(globals().items()):
        if nombre.startswith("test_") and callable(fn):
            try:
                fn(); print(f"  PASS  {nombre}")
            except Exception as e:
                fallos += 1; print(f"  FAIL  {nombre}: {type(e).__name__}: {e}")
    if "-v" in sys.argv:
        for c, (en_total, cubre, falt, n_sin, pct) in medir_por_cadena().items():
            print(f"    {c:<11} cubren {cubre}/{en_total} = {pct:.1f}% · faltantes {falt} · sin_elegible {n_sin}")
    print(f"\n{'TODO OK' if not fallos else str(fallos)+' FALLOS'}")
    raise SystemExit(1 if fallos else 0)
