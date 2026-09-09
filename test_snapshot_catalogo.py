"""
Tests de `scripts/snapshot_catalogo.py`. Sin red, como el resto de la suite.

Cubren lo que se puede romper en silencio: que se recorra el nivel más profundo del
árbol (para no chocar contra el techo de 2500 por consulta), que la paginación
respete la ventana de 50 y corte con el total del header `resources`, y que no se
dupliquen productos que aparecen en más de una categoría.
"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
import snapshot_catalogo as S  # noqa: E402

ARBOL = [
    {"id": 161, "name": "Almacén", "children": [
        {"id": 176, "name": "Harinas", "children": [
            {"id": 177, "name": "Harina de trigo", "children": []}]},
        {"id": 168, "name": "Pastas secas", "children": []}]},
    {"id": 3, "name": "Electro y tecnología", "children": [
        {"id": 4, "name": "Televisores", "children": []}]},
]


def _producto(i: int) -> dict:
    return {"productName": f"Producto {i}", "brand": "Marca",
            "items": [{"ean": f"779{i:010d}", "sellers": [{"commertialOffer": {
                "Price": 100 + i, "ListPrice": 100 + i, "IsAvailable": True,
                "AvailableQuantity": 10, "Teasers": []}}]}]}


class _Resp:
    def __init__(self, data, total=0):
        self.status_code = 200
        self._data = data
        self.headers = {"resources": f"0-49/{total}"}

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def _params(params):
    """`params` pasa de dict a lista de tuplas: `fq` se repite para el sales channel."""
    if isinstance(params, dict):
        return dict(params), []
    d, fqs = {}, []
    for k, v in params:
        if k == "fq":
            fqs.append(v)
        d[k] = v
    return d, fqs


def _falso(total: int, registro: list):
    def get(url, params=None, timeout=None, **kw):
        if "category/tree" in url:
            return _Resp(ARBOL)
        d, fqs = _params(params)
        desde = d["_from"]
        registro.append((fqs[0], desde, d["_to"], fqs[1:]))
        return _Resp([_producto(i) for i in range(desde, min(desde + 50, total))], total)
    return get


def test_recorre_la_hoja_mas_profunda_del_arbol():
    """
    Consultar "/161/" traería 6373 productos y el techo de paginación es 2500.
    Por eso se baja por la categoría más específica disponible.
    """
    hojas = [h for c in ARBOL for h in S._hojas(c)]
    assert ("/161/176/177", "Harina de trigo") in hojas
    assert ("/161/168", "Pastas secas") in hojas
    assert ("/161", "Almacén") not in hojas       # nunca el padre si tiene hijos


def test_saltea_las_categorias_que_no_son_de_supermercado():
    quedan = [c["name"] for c in ARBOL if S.es_supermercado(c["name"])]
    assert quedan == ["Almacén"]


def test_el_filtro_de_categorias_compara_por_subcadena_no_por_nombre_exacto():
    """
    La primera corrida real de Día trajo heladeras de $2.469.999 y microondas: la
    lista exacta estaba hecha con los nombres de Carrefour y ninguna cadena los
    repite igual. Ahora se compara por subcadena y sin acentos.
    """
    for fuera in ("Heladeras", "Microondas", "Electro y tecnología",
                  "Electrodomésticos", "TV y Audio", "Jugueteria",
                  "Muebles de jardín", "Aire Libre y Ocio"):
        assert not S.es_supermercado(fuera), fuera
    for dentro in ("Almacén", "Lácteos y productos frescos", "Bebidas",
                   "Harinas", "Carnes y pescados", "Limpieza",
                   "Bolsas de residuos", "Panadería"):
        assert S.es_supermercado(dentro), dentro


def test_pagina_de_a_50_y_corta_con_el_total_del_header():
    registro = []
    with patch.object(S, "PAUSA", 0), \
         patch.object(S.requests.Session, "get",
                      lambda self, *a, **k: _falso(120, registro)(*a, **k)):
        filas = S.bajar_vtex("Carrefour", "https://x", S.Contador(), todo=False)

    ventanas = [(d, h) for _, d, h, _ in registro]
    assert (0, 49) in ventanas and (50, 99) in ventanas and (100, 149) in ventanas
    assert all(h - d + 1 <= S.VENTANA for d, h in ventanas)
    assert max(d for d, _ in ventanas) < S.TOPE_CONSULTA
    assert len(filas) == 120


def test_no_duplica_productos_que_estan_en_dos_categorias():
    """El mismo EAN en Harinas y en Pastas secas entra una sola vez."""
    registro = []
    with patch.object(S, "PAUSA", 0), \
         patch.object(S.requests.Session, "get",
                      lambda self, *a, **k: _falso(30, registro)(*a, **k)):
        filas = S.bajar_vtex("Carrefour", "https://x", S.Contador(), todo=False)

    categorias_visitadas = {fq for fq, _, _, _ in registro}
    assert len(categorias_visitadas) == 2          # se consultaron las dos
    assert len(filas) == 30                        # pero los productos no se duplican
    assert len({f.ean for f in filas}) == 30


def test_una_categoria_que_falla_no_tumba_la_cadena():
    llamadas = {"n": 0}

    def get(url, params=None, timeout=None, **kw):
        if "category/tree" in url:
            return _Resp(ARBOL)
        llamadas["n"] += 1
        # la llamada 1 la consume la detección del sales channel
        if llamadas["n"] == 2:
            raise TimeoutError("categoría lenta")
        return _Resp([_producto(1)], 1)

    with patch.object(S, "PAUSA", 0), \
         patch.object(S.requests.Session, "get", lambda self, *a, **k: get(*a, **k)):
        cont = S.Contador()
        filas = S.bajar_vtex("Carrefour", "https://x", cont, todo=False)

    assert cont.errores == 1
    assert len(filas) == 1        # la segunda categoría se bajó igual


def _producto_sin_stock(i: int) -> dict:
    p = _producto(i)
    oferta = p["items"][0]["sellers"][0]["commertialOffer"]
    oferta["IsAvailable"] = False
    oferta["AvailableQuantity"] = 0
    return p


def _mixto(total: int, registro: list = None):
    """Mitad con stock, mitad sin: como viene el catálogo navegado por categoría."""
    def get(url, params=None, timeout=None, **kw):
        if "category/tree" in url:
            return _Resp(ARBOL)
        d, fqs = _params(params)
        if registro is not None:
            registro.append(fqs[1:])
        desde = d["_from"]
        lote = [(_producto(i) if i % 2 == 0 else _producto_sin_stock(i))
                for i in range(desde, min(desde + 50, total))]
        return _Resp(lote, total)
    return get


def test_por_defecto_no_guarda_los_productos_sin_stock():
    """
    Navegar por categoría devuelve producto discontinuado que la búsqueda no muestra:
    en la primera corrida real de Vea, 822 de 888 filas eran sin stock, con precios
    viejos (mediana $400 contra $12.499 de los disponibles).
    """
    with patch.object(S, "PAUSA", 0), \
         patch.object(S.requests.Session, "get",
                      lambda self, *a, **k: _mixto(40)(*a, **k)):
        filas = S.bajar_vtex("Carrefour", "https://x", S.Contador(), todo=False)
    assert len(filas) == 20
    assert all(f.disponible for f in filas)


def test_con_sin_stock_se_guardan_todos():
    with patch.object(S, "PAUSA", 0), \
         patch.object(S.requests.Session, "get",
                      lambda self, *a, **k: _mixto(40)(*a, **k)):
        filas = S.bajar_vtex("Carrefour", "https://x", S.Contador(), todo=False,
                             sin_stock=True)
    assert len(filas) == 40
    assert sum(1 for f in filas if not f.disponible) == 20


# ── sales channel ────────────────────────────────────────────────

def _con_token(sc: str):
    """Un producto cuyo PriceToken declara el sales channel, como lo manda VTEX."""
    import base64, json
    payload = json.dumps({"data": {"price": 249000, "seller": "1",
                                   "accountName": "veaargentina", "salesChannel": sc}})
    b64 = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    p = _producto(1)
    p["items"][0]["sellers"][0]["commertialOffer"]["PriceToken"] = f"aaa.{b64}.bbb"
    return p


def test_detecta_el_sales_channel_desde_el_pricetoken():
    """
    Sin canal explícito la API devuelve el catálogo por defecto: la primera corrida
    real de Vea trajo 66 productos con stock de 888. El canal viaja en el PriceToken.
    """
    def get(url, params=None, timeout=None, **kw):
        return _Resp([_con_token("34")], 1)

    with patch.object(S.requests.Session, "get", lambda self, *a, **k: get(*a, **k)):
        assert S.sales_channel(S._sesion(), "https://x", "/1", S.Contador()) == "34"


def test_sin_pricetoken_no_inventa_canal():
    def get(url, params=None, timeout=None, **kw):
        return _Resp([_producto(1)], 1)

    with patch.object(S.requests.Session, "get", lambda self, *a, **k: get(*a, **k)):
        assert S.sales_channel(S._sesion(), "https://x", "/1", S.Contador()) is None


def test_el_canal_detectado_filtra_en_el_origen():
    """El fq extra `isAvailablePerSalesChannel_N:1` tiene que viajar en cada página."""
    registro = []

    def get(url, params=None, timeout=None, **kw):
        if "category/tree" in url:
            return _Resp(ARBOL)
        d, fqs = _params(params)
        registro.append(fqs[1:])
        return _Resp([_con_token("34")], 1)

    with patch.object(S, "PAUSA", 0), \
         patch.object(S.requests.Session, "get", lambda self, *a, **k: get(*a, **k)):
        S.bajar_vtex("Vea", "https://x", S.Contador(), todo=False)

    conf = [r for r in registro if r]
    assert conf, "ninguna página llevó el filtro de canal"
    assert all(r == ["isAvailablePerSalesChannel_34:1"] for r in conf)


def test_con_sin_stock_no_se_filtra_por_canal():
    """Pedir explícitamente los sin stock y filtrar por disponibilidad se contradicen."""
    registro = []
    with patch.object(S, "PAUSA", 0), \
         patch.object(S.requests.Session, "get",
                      lambda self, *a, **k: _mixto(20, registro)(*a, **k)):
        S.bajar_vtex("Vea", "https://x", S.Contador(), todo=False, sin_stock=True)
    assert all(r == [] for r in registro)
