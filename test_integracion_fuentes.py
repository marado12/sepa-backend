"""
Tests de integración de /api/comparar con la capa de fuentes. SIN RED.

Verifican la propiedad que motiva todo el cambio: que la app siga respondiendo
cuando el SEPA no está. Y la contraria, igual de importante: que con
FUENTE_PRECIOS=sepa el comportamiento sea el de antes.
"""
import importlib
import os
import sys
from unittest.mock import patch

from fastapi.testclient import TestClient

from fuentes import Oferta, Resultado

JUNIN = {"lat": -34.58731, "lon": -60.93391, "radio_km": 5.0}
CANASTA = [{"nombre": "aceite girasol", "cantidad": 1.5, "unidad": "l"}]


def cargar_main(modo: str):
    """Recarga main.py con FUENTE_PRECIOS en el modo pedido."""
    os.environ["FUENTE_PRECIOS"] = modo
    for m in ("main",):
        if m in sys.modules:
            del sys.modules[m]
    import main
    return importlib.reload(main) if "main" in sys.modules else main


def ofertas_falsas(cadenas):
    return [Oferta(cadena=c, producto="Aceite de girasol Cocinero 1.5 L",
                   precio=4890.0 + i * 100, precio_lista=5990.0, origen="vtex",
                   promos=["Tarjeta 15%"], ean="779007")
            for i, c in enumerate(cadenas)]


class FuenteFalsa:
    nombre = "falsa"
    def __init__(self, caidas=None): self.caidas = caidas or {}
    def cadenas_soportadas(self):
        return ["Carrefour", "Chango Más", "Coto", "Día", "Disco", "Jumbo", "Vea"]
    def buscar(self, query, cadenas=None):
        cs = [c for c in (cadenas or self.cadenas_soportadas()) if c not in self.caidas]
        return Resultado(ofertas=ofertas_falsas(cs), fallidas=dict(self.caidas),
                         consultadas=list(cadenas or self.cadenas_soportadas()))


def _pedir(main, **extra):
    cuerpo = {**JUNIN, "canasta": CANASTA, **extra}
    return TestClient(main.app).post("/api/comparar", json=cuerpo)


# ── El caso que motivó todo ──────────────────────────────────────
def test_sepa_caido_la_app_sigue_respondiendo():
    """SEPA inalcanzable + modo auto -> responde con precios online."""
    main = cargar_main("auto")
    with patch.object(main, "_obtener_datos",
                      side_effect=ConnectionError("TCP timeout a datos.produccion.gob.ar")), \
         patch.object(main, "_get_fuente_online", return_value=FuenteFalsa()):
        r = _pedir(main)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["origen_precios"] == "online"
    assert "sepa_error" in d["fuente"]
    assert d["ranking"], "sin ranking"
    assert d["optimo"]["total_optimo"] > 0
    assert d["optimo"]["items"][0]["ok"] is True
    assert d["optimo"]["items"][0]["cadena"] in ("Día", "Vea", "Chango Más")


def test_solo_consulta_las_cadenas_cercanas():
    """En Junín el catálogo da Día, Vea y Chango Más: no se pregunta a las otras."""
    main = cargar_main("online")
    fuente = FuenteFalsa()
    vistas = []
    orig = fuente.buscar
    fuente.buscar = lambda q, c=None: (vistas.append(list(c or [])), orig(q, c))[1]
    with patch.object(main, "_get_fuente_online", return_value=fuente):
        r = _pedir(main)
    assert r.status_code == 200, r.text
    assert vistas and set(vistas[0]) == {"Día", "Vea", "Chango Más"}
    assert "Carrefour" not in vistas[0]      # el más cercano está lejos de Junín


def test_jumbo_y_disco_quedan_fuera_pero_se_reportan():
    """No están en el catálogo: no se recomiendan a ciegas, pero se hacen visibles."""
    main = cargar_main("online")
    with patch.object(main, "_get_fuente_online", return_value=FuenteFalsa()):
        r = _pedir(main)
    geo = r.json()["fuente"]["geo"]
    assert set(geo["cadenas_sin_geo"]) == {"Jumbo", "Disco"}
    assert geo["cadenas_sin_geo_incluidas"] is False
    assert geo["filtro_geografico"] is True


def test_cadena_caida_da_resultado_parcial_no_error():
    main = cargar_main("online")
    with patch.object(main, "_get_fuente_online",
                      return_value=FuenteFalsa(caidas={"Día": "TimeoutError"})):
        r = _pedir(main)
    assert r.status_code == 200
    d = r.json()
    assert "Día" in d["fuente"]["cadenas_fallidas"]
    assert d["fuente"]["cobertura_cadenas_pct"] < 100
    assert "Día" in d["aviso_datos"]
    assert d["ranking"], "las otras cadenas tienen que seguir apareciendo"


def test_promos_y_descuento_llegan_a_la_respuesta():
    """Lo que el SEPA no podía dar: la oferta web y la promo bancaria."""
    main = cargar_main("online")
    with patch.object(main, "_get_fuente_online", return_value=FuenteFalsa()):
        r = _pedir(main)
    assert r.status_code == 200
    assert r.json()["fuente"]["detalle_match"]["aceite girasol"]["cadenas_con_precio"]


# ── No romper lo que andaba ──────────────────────────────────────
def test_modo_sepa_no_toca_la_fuente_online():
    """Con FUENTE_PRECIOS=sepa el fallback no existe: falla como antes."""
    main = cargar_main("sepa")
    llamada = {"online": False}
    def marcar():
        llamada["online"] = True
        return FuenteFalsa()
    with patch.object(main, "_obtener_datos", side_effect=ConnectionError("SEPA caído")), \
         patch.object(main, "_get_fuente_online", side_effect=marcar):
        r = _pedir(main)
    assert r.status_code == 503
    assert llamada["online"] is False, "modo sepa NO debe consultar online"


def test_modo_sepa_usa_buscar_precios_de_siempre():
    main = cargar_main("sepa")
    precios_sepa = {("Día", "aceite girasol"): {"precio_min": 5250.0, "precio_por_100u": None}}
    with patch.object(main, "_obtener_datos", return_value=(None, "/fake.parquet")), \
         patch.object(main, "_buscar_precios", return_value=precios_sepa):
        r = _pedir(main)
    assert r.status_code == 200
    d = r.json()
    assert d["origen_precios"] == "sepa"
    assert d["ranking"][0]["cadena"] == "Día"


def test_auto_prefiere_sepa_cuando_hay_datos():
    main = cargar_main("auto")
    precios_sepa = {("Día", "aceite girasol"): {"precio_min": 5250.0, "precio_por_100u": None}}
    with patch.object(main, "_obtener_datos", return_value=(None, "/fake.parquet")), \
         patch.object(main, "_buscar_precios", return_value=precios_sepa), \
         patch.object(main, "_get_fuente_online", side_effect=AssertionError("no debía llamarse")):
        r = _pedir(main)
    assert r.status_code == 200
    assert r.json()["origen_precios"] == "sepa"


def test_status_expone_la_configuracion():
    main = cargar_main("auto")
    d = TestClient(main.app).get("/api/status").json()
    assert d["fuente_precios"] == "auto"
    assert "Jumbo" in d["cadenas_online"]
    assert d["catalogo_sucursales"]["total"] > 1000
    # claves viejas intactas: el banner del frontend las usa
    for k in ("cache", "listo", "en_progreso", "aviso_datos"):
        assert k in d, f"se perdió la clave {k}"


def test_sin_precios_devuelve_404_con_motivo():
    main = cargar_main("online")
    class Vacia:
        nombre = "vacia"
        def cadenas_soportadas(self): return ["Día", "Vea", "Chango Más"]
        def buscar(self, q, c=None): return Resultado(consultadas=list(c or []))
    with patch.object(main, "_get_fuente_online", return_value=Vacia()):
        r = _pedir(main)
    assert r.status_code == 404
    assert "online" in r.json()["detail"]


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
