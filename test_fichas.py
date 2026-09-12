"""
Fichas por supermercado: el reemplazo del ranking (Bloque A, paso 3, tanda 1).

POR QUÉ EXISTE. El ranking ordenaba canastas que no son comparables y no lo decía.
Medido en producción: Día encabezaba como "la más barata" con 3 de 6 productos
($11.189) y Carrefour quedaba última con 4 de 6 ($18.121) — el orden premiaba a la
cadena que MENOS tenía, porque un producto no encontrado suma 0 al total.

El reemplazo no es otro orden: es un RATIO contra el promedio de mercado, que por
construcción no crece con la cobertura. Los tests de abajo existen sobre todo para
impedir que alguien lo "simplifique" a una suma y reinstale el mismo bug.

Sin red. Fixtures construidas a mano — ningún número sale de una corrida en vivo.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import main


def _pu(precio, cantidad_base, tipo, unidad_base, desc="x"):
    """Un `precio_por_100u` como el que arma precios_vtex.precio_unitario."""
    factor = 100 if tipo in ("peso", "volumen") else 1
    return {"valor": round(precio / cantidad_base * factor, 2),
            "tipo": tipo, "label": "", "desc_ganadora": desc,
            "cantidad_base": float(cantidad_base),
            "precio_base": round(precio / cantidad_base, 6),
            "unidad_base": unidad_base}


# ── Fixture principal ────────────────────────────────────────────
# Diseñada para que los TRES órdenes difieran. Si coincidieran, los tests de orden
# no probarían nada.
#
#            cobertura      total      % vs mercado
#  Carrefour    3/3        $13.000        +2,4%
#  Día          2/3         $5.800       -10,8%
#  Vea          2/3         $4.200        +6,7%   <- total más bajo, PEOR porcentaje
#
# El papel higiénico reproduce el ejemplo del ítem 1.1: Carrefour vende 320 m a
# $6.000 ($18,75/m) y Vea 100 m a $2.000 ($20/m). El envase de Carrefour cuesta el
# triple y es más barato por metro.

CANASTA = [
    {"nombre": "leche entera", "cantidad": 1},
    {"nombre": "yerba", "cantidad": 1},
    {"nombre": "papel higienico", "cantidad": 1},
    {"nombre": "harissa", "cantidad": 1},        # no la tiene NADIE
]

PRECIOS = {
    # leche: las tres, en ml. promedio = 2.0 $/ml
    ("Carrefour", "leche entera"): {"precio_min": 2000.0,
                                    "precio_por_100u": _pu(2000, 1000, "volumen", "ml")},
    ("Día", "leche entera"): {"precio_min": 1800.0,
                              "precio_por_100u": _pu(1800, 1000, "volumen", "ml")},
    ("Vea", "leche entera"): {"precio_min": 2200.0,
                              "precio_por_100u": _pu(2200, 1000, "volumen", "ml")},
    # yerba: Carrefour y Día, en g. promedio = 4.5 $/g
    ("Carrefour", "yerba"): {"precio_min": 5000.0,
                             "precio_por_100u": _pu(5000, 1000, "peso", "g")},
    ("Día", "yerba"): {"precio_min": 4000.0,
                       "precio_por_100u": _pu(4000, 1000, "peso", "g")},
    # papel: Carrefour y Vea, en m. promedio = 19.375 $/m
    ("Carrefour", "papel higienico"): {"precio_min": 6000.0,
                                       "precio_por_100u": _pu(6000, 320, "longitud", "m")},
    ("Vea", "papel higienico"): {"precio_min": 2000.0,
                                 "precio_por_100u": _pu(2000, 100, "longitud", "m")},
}

CADENAS = ["Carrefour", "Día", "Vea"]


def _fichas(canasta=None, precios=None, cadenas=None, promos=None):
    canasta = CANASTA if canasta is None else canasta
    precios = PRECIOS if precios is None else precios
    cadenas = CADENAS if cadenas is None else cadenas
    resultado = main._analizar(canasta, precios, promos or [], 0, None)
    proms = main._promedios_por_producto(canasta, precios)
    return main._fichas(canasta, precios, resultado, proms, cadenas)


def _por_cadena(fichas):
    return {f["cadena"]: f for f in fichas}


def _fila(fichas, cadena, producto):
    return next(d for d in _por_cadena(fichas)[cadena]["detalle"]
                if d["producto"] == producto)


# ── El promedio ──────────────────────────────────────────────────
def test_el_promedio_va_sobre_precio_base_no_sobre_el_envase():
    """
    Si se promediaran envases, el papel higiénico daría (6000+2000)/2 = $4.000 y
    Carrefour —que vende el pack grande— aparecería carísimo. Por metro es el más
    barato. Es el ejemplo del ítem 1.1: promediar envases invierte la respuesta.
    """
    p = main._promedios_por_producto(CANASTA, PRECIOS)["papel higienico"]
    assert p["precio_base"] == 19.375, p       # (18.75 + 20.0) / 2
    assert p["unidad_base"] == "m"
    assert p["n_cadenas"] == 2


def test_el_n_del_promedio_viaja_siempre():
    """Un promedio de dos no es "el mercado". El n tiene que poder mostrarse."""
    proms = main._promedios_por_producto(CANASTA, PRECIOS)
    assert proms["leche entera"]["n_cadenas"] == 3
    assert proms["yerba"]["n_cadenas"] == 2


def test_un_producto_que_nadie_tiene_no_entra_al_promedio():
    assert "harissa" not in main._promedios_por_producto(CANASTA, PRECIOS)


# ── El porcentaje NO es una suma ─────────────────────────────────
def test_duplicar_la_canasta_no_mueve_el_porcentaje():
    """
    EL test del spec (sección 8.2). Pedir el doble de cada producto duplica todos
    los subtotales y todos los esperados: el ratio queda idéntico. Una suma se
    duplicaría. Si alguien reemplaza el % por algo que crece con el tamaño de la
    canasta, esto se pone en rojo.
    """
    doble = [{**p, "cantidad": p["cantidad"] * 2} for p in CANASTA]
    a = _por_cadena(_fichas())
    b = _por_cadena(_fichas(canasta=doble))

    for cadena in CADENAS:
        assert a[cadena]["delta_pct_sin_promo"] == b[cadena]["delta_pct_sin_promo"], cadena
        assert b[cadena]["total_envase"] == a[cadena]["total_envase"] * 2, cadena


def _escenario_diez_por_ciento_arriba(n_productos):
    """
    Una cadena que paga exactamente 10% sobre el promedio en CADA fila.

    Con dos cadenas, el promedio es (900 + 1100) / 2 = 1000 y la cara queda en
    1100/1000 = +10%, fila por fila. Los dos escenarios se corren por separado a
    propósito: si las dos cadenas de prueba convivieran, cada una entraría en el
    promedio de la otra y los ratios por fila dejarían de ser iguales.
    """
    canasta = [{"nombre": "p%d" % i, "cantidad": 1} for i in range(n_productos)]
    precios = {}
    for i in range(n_productos):
        precios[("Barata", "p%d" % i)] = {"precio_min": 900.0,
                                          "precio_por_100u": _pu(900, 1000, "peso", "g")}
        precios[("Cara", "p%d" % i)] = {"precio_min": 1100.0,
                                        "precio_por_100u": _pu(1100, 1000, "peso", "g")}
    return _por_cadena(_fichas(canasta, precios, ["Barata", "Cara"]))


def test_el_porcentaje_no_depende_de_cuantas_filas_tenga_la_ficha():
    """
    El test que impide convertir el porcentaje en una suma. Una cadena con UNA
    fila y otra con CINCO, las dos 10% sobre el mercado en cada fila, muestran las
    dos +10,0%. Con una suma, la de cinco filas daría cinco veces más.
    """
    una = _escenario_diez_por_ciento_arriba(1)
    cinco = _escenario_diez_por_ciento_arriba(5)

    assert una["Cara"]["delta_pct_sin_promo"] == 10.0, una["Cara"]["delta_pct_sin_promo"]
    assert cinco["Cara"]["delta_pct_sin_promo"] == 10.0, cinco["Cara"]["delta_pct_sin_promo"]
    # y la barata, simétrica, en las dos
    assert una["Barata"]["delta_pct_sin_promo"] == -10.0
    assert cinco["Barata"]["delta_pct_sin_promo"] == -10.0
    # el total sí crece con las filas; el porcentaje no
    assert cinco["Cara"]["total_envase"] == una["Cara"]["total_envase"] * 5


def test_los_tres_ordenes_difieren():
    """
    Si el orden por cobertura coincidiera con el orden por total, los tests de orden
    no probarían nada. La fixture está construida para que los tres discrepen.
    """
    fichas = _fichas()
    por_cobertura = [f["cadena"] for f in fichas]           # como las devuelve
    por_total = [f["cadena"] for f in sorted(fichas, key=lambda f: f["total_final"])]
    por_pct = [f["cadena"] for f in sorted(fichas, key=lambda f: f["delta_pct_final"])]

    assert por_cobertura == ["Carrefour", "Día", "Vea"], por_cobertura
    assert por_total == ["Vea", "Día", "Carrefour"], por_total
    assert por_pct == ["Día", "Carrefour", "Vea"], por_pct
    assert por_cobertura != por_total
    assert por_total != por_pct


def test_el_total_mas_bajo_puede_ser_el_peor_porcentaje():
    """Vea es la más barata en pesos y la más cara por unidad de contenido."""
    f = _por_cadena(_fichas())
    assert f["Vea"]["total_final"] < f["Carrefour"]["total_final"]
    assert f["Vea"]["delta_pct_final"] > f["Carrefour"]["delta_pct_final"]


# ── Cobertura y faltantes ────────────────────────────────────────
def test_lo_que_nadie_tiene_no_penaliza_a_nadie():
    """
    La harissa no la tiene ninguna cadena: sale del divisor. Las fichas dicen
    "3 de 3", no "3 de 4". El aviso de arriba explica que se pidieron 4.
    """
    f = _por_cadena(_fichas())
    assert f["Carrefour"]["n_comparables"] == 3
    assert f["Carrefour"]["n_disponibles"] == 3
    assert "harissa" not in f["Día"]["faltantes"]
    assert f["Día"]["faltantes"] == ["papel higienico"]


def test_el_faltante_se_nombra_no_se_cuenta():
    f = _por_cadena(_fichas())
    assert f["Vea"]["faltantes"] == ["yerba"]


def test_una_cadena_sin_ningun_producto_tiene_ficha():
    """
    Hoy `_analizar` la filtra con `total > 0` y desaparece: "no tiene nada" y "esa
    cadena no está cerca tuyo" se ven igual.
    """
    f = _por_cadena(_fichas(cadenas=CADENAS + ["Jumbo"]))
    assert "Jumbo" in f
    assert f["Jumbo"]["n_disponibles"] == 0
    assert f["Jumbo"]["delta_pct_final"] is None
    assert sorted(f["Jumbo"]["faltantes"]) == ["leche entera", "papel higienico", "yerba"]


# ── Estados borde ────────────────────────────────────────────────
def test_estado_faltante():
    assert _fila(_fichas(), "Vea", "yerba")["estado"] == "faltante"


def test_estado_manual():
    """Precio cargado a mano: sin $/unidad y sin %, pero con precio."""
    precios = dict(PRECIOS)
    precios[("Vea", "yerba")] = {"precio_min": 4500.0, "precio_por_100u": None,
                                 "fuente": "manual", "nota": "cargado a mano"}
    assert _fila(_fichas(precios=precios), "Vea", "yerba")["estado"] == "manual"


def test_estado_sin_metrica():
    """El título no declara cantidad — el 7,2% que quedó del paso 1."""
    precios = dict(PRECIOS)
    precios[("Vea", "yerba")] = {"precio_min": 4500.0, "precio_por_100u": None}
    assert _fila(_fichas(precios=precios), "Vea", "yerba")["estado"] == "sin_metrica"


def test_estado_unico():
    """
    Una sola cadena tiene el producto: el promedio sería ella misma y el delta
    daría 0,0% por construcción — un cero que se lee como "está en el promedio".
    """
    precios = {k: v for k, v in PRECIOS.items() if k != ("Día", "yerba")}
    fichas = _fichas(precios=precios)
    fila = _fila(fichas, "Carrefour", "yerba")
    assert fila["estado"] == "unico"
    assert fila["delta_pct"] is None, "un solo dato no es un promedio"


def test_estado_unidad_distinta():
    """
    El representante de una cadena parseó en otra unidad_base que el resto. No se
    promedia con los demás y no muestra delta — pero sí su $/unidad.
    """
    precios = dict(PRECIOS)
    # Vea representa el papel con un pack de 4 unidades en vez de metros
    precios[("Vea", "papel higienico")] = {"precio_min": 2000.0,
                                           "precio_por_100u": _pu(2000, 4, "count", "u")}
    fichas = _fichas(precios=precios)
    assert _fila(fichas, "Vea", "papel higienico")["estado"] == "unidad_distinta"
    # y el promedio de los otros no se contamina: queda solo Carrefour, o sea n=1
    p = main._promedios_por_producto(CANASTA, precios)["papel higienico"]
    assert p["unidad_base"] == "m"
    assert p["n_cadenas"] == 1


def test_unidades_distintas_no_se_promedian_entre_si():
    """Dos en metros y una en unidades: la de unidades no arrastra a las otras."""
    precios = dict(PRECIOS)
    precios[("Día", "papel higienico")] = {"precio_min": 3000.0,
                                           "precio_por_100u": _pu(3000, 150, "longitud", "m")}
    precios[("Vea", "papel higienico")] = {"precio_min": 9999.0,
                                           "precio_por_100u": _pu(9999, 1, "count", "u")}
    p = main._promedios_por_producto(CANASTA, precios)["papel higienico"]
    assert p["unidad_base"] == "m"
    assert p["n_cadenas"] == 2                  # Carrefour y Día, NO Vea
    assert p["precio_base"] == round((18.75 + 20.0) / 2, 6)


def test_sin_filas_comparables_el_delta_es_none_no_cero():
    """Un cero se lee como "exactamente en el promedio". Es lo contrario de "no sé"."""
    precios = {("Solo", "leche entera"): {"precio_min": 1000.0, "precio_por_100u": None}}
    f = _por_cadena(_fichas([{"nombre": "leche entera", "cantidad": 1}], precios, ["Solo"]))
    assert f["Solo"]["delta_pct_final"] is None
    assert f["Solo"]["delta_pct_sin_promo"] is None


# ── El contrato que consume promos_sitio ─────────────────────────
def test_las_claves_que_lee_reintegro_para_siguen_estando():
    """
    `promos_sitio.reintegro_para` indexa `detalle` por producto/subtotal/ok
    (promos_sitio.py:208-209) y si cambian degrada a reintegro 0 SIN error. La
    ficha agrega claves pero no puede sacar ninguna de esas tres.
    """
    for fila in _por_cadena(_fichas())["Carrefour"]["detalle"]:
        assert "producto" in fila
        assert "subtotal" in fila
        assert "ok" in fila


if __name__ == "__main__":
    fallos = 0
    for nombre, fn in sorted(globals().items()):
        if nombre.startswith("test_") and callable(fn):
            try:
                fn(); print("  PASS  " + nombre)
            except Exception as e:
                fallos += 1
                print("  FAIL  %s: %s: %s" % (nombre, type(e).__name__, e))
    print("\n" + ("TODO OK" if not fallos else "%d FALLOS" % fallos))
    raise SystemExit(1 if fallos else 0)
