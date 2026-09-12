"""
Tests del matching online. Sin red.

El riesgo de esta capa no es que falle: es que acierte mal. Un precio de KitKat
cuando el usuario pidió leche entra al optimizador sin que nadie lo note.
Por eso la mayoría de estos tests verifican que NO matchee.
"""
import re

from fuentes import Oferta, Resultado
from precios_vtex import UMBRAL_MATCH, buscar_precios_online, puntuar


# Stubs de los helpers de main.py
def normalizar(t: str) -> str:
    t = (t or "").lower()
    for a, b in (("á","a"),("é","e"),("í","i"),("ó","o"),("ú","u")):
        t = t.replace(a, b)
    return re.sub(r"\s+", " ", t).strip()


_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(kg|g|gr|l|ml|cc)\b", re.I)
_ML = {"l": 1000, "ml": 1, "cc": 1}
_G  = {"kg": 1000, "g": 1, "gr": 1}

def extraer_cantidades(desc: str) -> list:
    out = []
    for val, u in _RE.findall(desc or ""):
        v = float(val.replace(",", "."))
        u = u.lower()
        if u in _G:  out.append((v * _G[u], "peso"))
        elif u in _ML: out.append((v * _ML[u], "volumen"))
    return out


def of(cadena, producto, precio, **kw):
    return Oferta(cadena=cadena, producto=producto, precio=precio, origen="vtex", **kw)


class FuenteFalsa:
    """Devuelve ofertas fijas, sin red."""
    nombre = "falsa"
    def __init__(self, ofertas, fallidas=None, consultadas=None):
        self.ofertas, self._fallidas = ofertas, fallidas or {}
        self._consultadas = consultadas or ["Carrefour", "Día", "Vea"]
    def cadenas_soportadas(self): return list(self._consultadas)
    def buscar(self, query, cadenas=None):
        return Resultado(ofertas=list(self.ofertas), fallidas=dict(self._fallidas),
                         consultadas=list(cadenas or self._consultadas))


ITEM_ACEITE = {"nombre": "aceite girasol", "cantidad": 1.5, "unidad": "l"}


# ── Scoring ──────────────────────────────────────────────────────
def test_match_bueno_puntua_alto():
    s = puntuar(ITEM_ACEITE, of("Día", "Aceite de girasol Cocinero 1.5 L", 4890),
                normalizar, extraer_cantidades)
    assert s >= 0.9, s


def test_el_kitkat_no_matchea_leche():
    """El caso real: buscar 'leche' en Jumbo devolvía un KitKat primero."""
    s = puntuar({"nombre": "leche entera"},
                of("Jumbo", "Oblea Leche 4 Fingers 41.5 Grs Kitkat®", 2800),
                normalizar, extraer_cantidades)
    assert s < UMBRAL_MATCH, f"score {s} — el KitKat entraría como leche"


def test_producto_no_relacionado_no_matchea():
    s = puntuar(ITEM_ACEITE, of("Día", "Papas fritas Lays clásicas 85 g", 3844),
                normalizar, extraer_cantidades)
    assert s < UMBRAL_MATCH, s


def test_marca_pedida_y_presente_sube_el_score():
    base = puntuar({"nombre": "aceite girasol"},
                   of("Día", "Aceite girasol Natura 900 ml", 3990), normalizar)
    con  = puntuar({"nombre": "aceite girasol", "marca": "Natura"},
                   of("Día", "Aceite girasol Natura 900 ml", 3990), normalizar)
    assert con > base


def test_marca_pedida_y_ausente_penaliza():
    con_otra = puntuar({"nombre": "aceite girasol", "marca": "Natura"},
                       of("Día", "Aceite girasol Cocinero 900 ml", 3990), normalizar)
    sin_marca = puntuar({"nombre": "aceite girasol"},
                        of("Día", "Aceite girasol Cocinero 900 ml", 3990), normalizar)
    assert con_otra < sin_marca


def test_cantidad_muy_distinta_penaliza():
    igual = puntuar(ITEM_ACEITE, of("Día", "Aceite de girasol Cocinero 1.5 L", 4890),
                    normalizar, extraer_cantidades)
    chico = puntuar(ITEM_ACEITE, of("Día", "Aceite de girasol Cocinero 500 ml", 1990),
                    normalizar, extraer_cantidades)
    assert chico < igual


def test_marcas_aceptadas_cuentan_como_marca():
    s = puntuar({"nombre": "aceite girasol", "marcas_aceptadas": ["Natura", "Cocinero"]},
                of("Día", "Aceite girasol Cocinero 1.5 L", 4890), normalizar)
    assert s >= 0.9


def test_oferta_vacia_o_item_vacio_no_explota():
    assert puntuar({}, of("Día", "x", 1), normalizar) == 0.0
    assert puntuar({"nombre": "aceite"}, of("Día", "", 1), normalizar) == 0.0


# ── Estructura de salida ─────────────────────────────────────────
def test_forma_identica_a_buscar_precios():
    """El optimizador consume esto sin cambios: la forma tiene que coincidir."""
    fuente = FuenteFalsa([of("Día", "Aceite de girasol Cocinero 1.5 L", 4890),
                          of("Carrefour", "Aceite girasol Cocinero 1.5 L", 5250)])
    precios, meta = buscar_precios_online([ITEM_ACEITE], ["Día", "Carrefour"],
                                          fuente, normalizar, extraer_cantidades)
    assert set(precios) == {("Día", "aceite girasol"), ("Carrefour", "aceite girasol")}
    for v in precios.values():
        assert "precio_min" in v and isinstance(v["precio_min"], float)
        assert "precio_por_100u" in v
    assert precios[("Día", "aceite girasol")]["precio_min"] == 4890.0


def test_precio_por_100u_se_calcula():
    fuente = FuenteFalsa([of("Día", "Aceite de girasol Cocinero 1.5 L", 4890)])
    precios, _ = buscar_precios_online([ITEM_ACEITE], ["Día"], fuente,
                                       normalizar, extraer_cantidades)
    pu = precios[("Día", "aceite girasol")]["precio_por_100u"]
    assert pu["tipo"] == "volumen" and pu["label"] == "$/100ml"
    assert pu["valor"] == round(4890 / 1500 * 100, 2)


def test_se_queda_con_el_mejor_match_no_el_mas_barato():
    """Un producto irrelevante y barato NO debe ganarle a uno correcto y caro."""
    fuente = FuenteFalsa([of("Día", "Papas fritas Lays 85 g", 500),
                          of("Día", "Aceite de girasol Cocinero 1.5 L", 4890)])
    precios, _ = buscar_precios_online([ITEM_ACEITE], ["Día"], fuente,
                                       normalizar, extraer_cantidades)
    assert precios[("Día", "aceite girasol")]["precio_min"] == 4890.0


def test_a_igual_match_gana_el_mas_barato():
    fuente = FuenteFalsa([of("Día", "Aceite de girasol Cocinero 1.5 L", 5500),
                          of("Día", "Aceite de girasol Cocinero 1.5 L", 4890)])
    precios, _ = buscar_precios_online([ITEM_ACEITE], ["Día"], fuente,
                                       normalizar, extraer_cantidades)
    assert precios[("Día", "aceite girasol")]["precio_min"] == 4890.0


def test_conserva_promos_y_ean():
    fuente = FuenteFalsa([of("Carrefour", "Aceite girasol Cocinero 1.5 L", 4890,
                             precio_lista=5990.0, ean="779007", promos=["Tarjeta Carrefour 15%"])])
    precios, _ = buscar_precios_online([ITEM_ACEITE], ["Carrefour"], fuente,
                                       normalizar, extraer_cantidades)
    e = precios[("Carrefour", "aceite girasol")]
    assert e["promos"] == ["Tarjeta Carrefour 15%"]
    assert e["ean"] == "779007"
    assert e["descuento_pct"] == 18.4


# ── Degradación ──────────────────────────────────────────────────
def test_producto_sin_match_se_reporta_y_no_inventa_precio():
    fuente = FuenteFalsa([of("Día", "Papas fritas Lays 85 g", 500)])
    precios, meta = buscar_precios_online([ITEM_ACEITE], ["Día"], fuente,
                                          normalizar, extraer_cantidades)
    assert precios == {}
    assert meta["productos_sin_match"] == ["aceite girasol"]
    assert "aceite girasol" in meta["aviso"]


def test_cadena_caida_se_refleja_en_cobertura():
    fuente = FuenteFalsa([of("Día", "Aceite de girasol Cocinero 1.5 L", 4890)],
                         fallidas={"Carrefour": "TimeoutError"},
                         consultadas=["Día", "Carrefour"])
    precios, meta = buscar_precios_online([ITEM_ACEITE], ["Día", "Carrefour"], fuente,
                                          normalizar, extraer_cantidades)
    assert meta["cobertura_cadenas_pct"] == 50.0
    assert "Carrefour" in meta["cadenas_fallidas"]
    assert "Carrefour" in meta["aviso"]


def test_fuente_que_explota_no_tumba_la_comparacion():
    class Explota:
        nombre = "boom"
        def cadenas_soportadas(self): return ["Día"]
        def buscar(self, q, c=None): raise RuntimeError("bug")
    precios, meta = buscar_precios_online([ITEM_ACEITE], ["Día"], Explota(),
                                          normalizar, extraer_cantidades)
    assert precios == {}
    assert meta["productos_sin_match"] == ["aceite girasol"]


def test_producto_sin_stock_se_ignora():
    fuente = FuenteFalsa([of("Día", "Aceite de girasol Cocinero 1.5 L", 4890, disponible=False)])
    precios, _ = buscar_precios_online([ITEM_ACEITE], ["Día"], fuente,
                                       normalizar, extraer_cantidades)
    assert precios == {}


def test_canasta_vacia_devuelve_vacio():
    precios, meta = buscar_precios_online([], ["Día"], FuenteFalsa([]), normalizar)
    assert precios == {} and meta["productos_pedidos"] == 0


# ── 'pack' no es una restricción de contenido (Tarea 20) ─────────
#
# "1 pack" quiere decir "un paquete, el tamaño me da igual", no "un ítem de
# contenido". `_cantidad_objetivo` excluía "unidad" y "u" pero NO "pack", así que
# deducía (1.0, 'count') y el paso 3 de puntuar() castigaba todo candidato que
# declarara otra medida. Medido sobre las 5 fixtures: los candidatos aceptados de
# papel higiénico cayeron de 103 a 55 al habilitar metros en el paso 1, y el
# representante de Carrefour pasó a ser un PORTARROLLOS de plástico.
#
# `CANASTA_DEFAULT` usa unidad 'pack' en papel higiénico y en huevos, así que
# esto se estaba sirviendo a todo usuario que no armara su propia canasta.

def _fixtures_papel(cadena):
    """Ofertas reales de papel higiénico de una cadena, del parser de producción."""
    from tests.evaluar_matching import cargar
    from tests.medir_regresion import FIXTURES
    datos = cargar({cadena: FIXTURES[cadena]})
    return [o for o in datos[cadena]["Papel higienico"] if o.disponible and o.precio > 0]


def _elegido(item, ofertas, extraer):
    """Mismo desempate que buscar_precios_online: mejor score, a igual score el más barato."""
    arriba = [(puntuar(item, o, normalizar, extraer), o) for o in ofertas]
    arriba = [(s, o) for s, o in arriba if s >= UMBRAL_MATCH]
    if not arriba:
        return None
    return max(arriba, key=lambda f: (f[0], -f[1].precio))[1]


def test_pack_no_fija_un_objetivo_de_cantidad():
    """
    De las seis unidades que ofrece el frontend (unidad · kg · gramos · litro ·
    ml · pack), 'pack' es la única donde el número NO describe contenido.
    """
    from precios_vtex import _cantidad_objetivo
    import main
    assert _cantidad_objetivo({"nombre": "Papel higienico", "cantidad": 1,
                               "unidad": "pack"},
                              main._extraer_cantidades_desc) is None
    # las que sí describen contenido siguen fijando objetivo
    assert _cantidad_objetivo({"nombre": "Leche", "cantidad": 1, "unidad": "litro"},
                              main._extraer_cantidades_desc) == (1000.0, "volumen")


def test_papel_higienico_no_elige_un_portarrollos():
    """
    El caso exacto que estaba en producción. Contra la fixture real de Carrefour,
    "papel higiénico, 1 pack" tiene que elegir papel higiénico — el pack de 320 m
    a $5.899 — y no el "Palito porta rollo" de $3.500, que ganaba porque no
    declara cantidad y así esquivaba la penalización.
    """
    import main
    item = {"nombre": "Papel higienico", "cantidad": 1, "unidad": "pack"}
    elegido = _elegido(item, _fixtures_papel("Carrefour"), main._extraer_cantidades_desc)

    assert elegido is not None
    assert "porta rollo" not in elegido.producto.lower(), (
        "el representante de papel higiénico es un accesorio: %r" % elegido.producto)
    assert "80 m" in elegido.producto, elegido.producto


def test_huevos_por_pack_no_castiga_al_maple_de_12():
    """
    El otro ítem de CANASTA_DEFAULT con unidad 'pack'. Pedir "2 pack" deducía
    (2, count) y castigaba un maple de 12 con ratio 2/12 < 0.5 -> score x0.7.
    Las fixtures no traen huevos, así que este caso va con ofertas armadas.
    """
    import main
    item = {"nombre": "Huevos", "cantidad": 2, "unidad": "pack"}
    maple = Oferta(cadena="Día", producto="Huevos blancos x 12 unidades",
                   precio=3600.0, origen="vtex")
    s = puntuar(item, maple, normalizar, main._extraer_cantidades_desc)
    sin_unidad = puntuar({"nombre": "Huevos", "cantidad": 2, "unidad": "unidad"},
                         maple, normalizar, main._extraer_cantidades_desc)
    assert s == sin_unidad, (
        "'pack' no puede puntuar distinto que 'unidad': %s vs %s" % (s, sin_unidad))


if __name__ == "__main__":
    fallos = 0
    for nombre, fn in sorted(globals().items()):
        if nombre.startswith("test_") and callable(fn):
            try:
                fn(); print(f"  PASS  {nombre}")
            except Exception as e:
                fallos += 1; print(f"  FAIL  {nombre}: {type(e).__name__}: {e}")
    print(f"\n{'TODO OK' if not fallos else str(fallos)+' FALLOS'}")
    raise SystemExit(1 if fallos else 0)
