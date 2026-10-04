"""
Tarea 22, Abierto 2, Etapa 2 — la ALARMA de "Coto respondió 200 sin ofertas". Sin red.

El problema: si `FuenteCoto` recibe un 200 con cero registros, devolvía `ofertas=[]` y `fallidas={}`, y "Coto
no contestó" quedaba igual que "Coto no lo vende". La captura del 24/09 trae 5 de esos ceros con una forma
anómala (`contents: []`, sin `totalNumRecs`), distinta del vacío legítimo (`records: []`, `totalNumRecs: 0`).

La alarma no cambia comportamiento: `Resultado.vacias` (aparte de `fallidas`) y una línea WARNING
`[coto-vacio] … total=<n|?> …`. Por eso la mitad de estos tests son de NO regresión: `precios` y `meta`
iguales al snapshot sacado con `2e8e79e`, y el `Resultado` combinado sin cambios.

Todos corren con la red bloqueada a nivel socket (`tests/arnes_coto_vacio.py`), y si hubo un intento de red
ESE es el error del test. Los que pasan por las cadenas mockean las 5 con respuestas reales; el `[]` de las
VTEX para fideos es sintético (no hay fixture VTEX de fideos). Las mutaciones que demuestran que cada
aserción muerde están en `tests/coto_vacio_mutaciones.py`, fuera de la CI.

Desde sepa_backend/ (en Windows, con `$env:PYTHONIOENCODING='utf-8'`):
    venv\\Scripts\\python.exe test_coto_vacio.py
"""
import functools
import hashlib
import inspect
import json
import logging
import re
import sys
import traceback
from pathlib import Path

from tests import arnes_coto_vacio as A          # primero: importa main con el cache en un temporal

from fuentes import FuenteCoto, FuenteVTEX
from test_fuentes import COTO_REAL

LINEA = re.compile(r"^\[coto-vacio\] q=(?P<q>.+) status=(?P<status>\d+) bytes=(?P<bytes>\d+) "
                   r"records=(?P<records>\d+|\?) ofertas=(?P<ofertas>\d+) total=(?P<total>\d+|\?) "
                   r"ms=(?P<ms>\d+)$")


# ─────────────────────────────────────────────────────────────
#  Armado: red bloqueada y log capturado a mano
# ─────────────────────────────────────────────────────────────

def sin_red(fn):
    """La red bloqueada en todo el test. Si hubo un intento, ESE es el error, aunque el cuerpo haya
    fallado por otra cosa después: un mock que falta se tiene que ver como lo que es."""
    @functools.wraps(fn)
    def envuelta():
        with A.red_bloqueada() as intentos:
            try:
                fn()
            finally:
                assert not intentos, f"intento de red con la red bloqueada: {intentos[:3]}"
    return envuelta


class _Capturador(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.mensajes = []

    def emit(self, record):
        self.mensajes.append(record.getMessage())


class log_de_fuentes:
    """Captura lo que loguea `fuentes` (el logger de `fuentes.py:28`). `.coto_vacio` son solo las líneas
    de la alarma."""

    def __enter__(self):
        self._h = _Capturador()
        logging.getLogger("fuentes").addHandler(self._h)
        return self

    def __exit__(self, *exc):
        logging.getLogger("fuentes").removeHandler(self._h)

    @property
    def coto_vacio(self):
        return [m for m in self._h.mensajes if m.startswith("[coto-vacio]")]


def _coto(consulta, cuerpo: bytes):
    """`FuenteCoto().buscar(consulta)` contra un body, con el log capturado."""
    with log_de_fuentes() as log, A.enrutado(coto={consulta: cuerpo}):
        r = FuenteCoto().buscar(consulta)
    return r, log.coto_vacio


def _escenario():
    """El camino de producción con la canasta del snapshot, como lo arma el arnés."""
    with log_de_fuentes() as log, A.enrutado(*A.rutas_escenario()):
        precios, meta, resultados = A.correr()
    return precios, meta, resultados, log.coto_vacio


# ─────────────────────────────────────────────────────────────
#  Las fixtures son lo que dicen ser
# ─────────────────────────────────────────────────────────────

@sin_red
def test_las_fixtures_son_los_bodies_de_las_capturas():
    """Cada body de `tests/fixtures/` es el de la captura, con su medición: un test con una fixture
    inventada no es un test."""
    for ruta in (A.BODY_ANOMALO, A.BODY_ZZQXW, A.BODY_HUEVOS):
        fx = A.cargar_body(ruta)
        cap = json.loads((A.CAPTURAS / fx["__captura"]).read_text(encoding="utf-8"))
        clave = (fx["__ronda"], fx["__brazo"], fx["__consulta"])
        bodies = [b for b in cap["bodies"] if (b["ronda"], b["brazo"], b["consulta"]) == clave]
        meds = [m for m in cap["mediciones"] if (m["ronda"], m["brazo"], m["consulta"]) == clave]
        assert len(bodies) == 1 and len(meds) == 1, f"{ruta.name}: {len(bodies)} bodies, {len(meds)} mediciones"
        m = meds[0]
        assert fx["body"] == bodies[0]["body"], f"{ruta.name}: el body no es el de la captura"
        sha1 = hashlib.sha1(fx["body"].encode("utf-8", "replace")).hexdigest()[:12]
        assert sha1 == fx["__sha1"] == m["body_sha1"], f"{ruta.name}: sha1 {sha1}"
        assert len(fx["body"].encode("utf-8")) == fx["__bytes"] == m["bytes"], f"{ruta.name}: bytes"
        assert (fx["__status"], fx["__total_registrado"], fx["__registros"], fx["__ofertas"]) == \
               (m["status"], m["total"], m["registros"], m["ofertas"]), f"{ruta.name}: medición"


# ─────────────────────────────────────────────────────────────
#  La alarma
# ─────────────────────────────────────────────────────────────

@sin_red
def test_fideos_anomalo_avisa_y_no_cambia_precios_ni_meta():
    """El caso del 24/09 por el camino de producción: una línea, con total=?, y nada más cambia."""
    snap = A.cargar_snapshot()
    precios, meta, _resultados, lineas = _escenario()

    assert len(lineas) == 1, f"esperaba UNA línea [coto-vacio] (Coto, Fideos spaghetti), hubo {lineas}"
    m = LINEA.match(lineas[0])
    assert m, f"la línea no tiene el formato: {lineas[0]!r}"
    fx = A.cargar_body(A.BODY_ANOMALO)
    assert (m["q"], m["status"], m["bytes"], m["records"], m["ofertas"], m["total"]) == \
           ("Fideos spaghetti", "200", str(fx["__bytes"]), "0", "0", "?"), lineas[0]

    assert A.precios_a_json(precios) == snap["precios"], "precios distintos del snapshot de 2e8e79e"
    assert A.a_json(meta) == snap["meta"], "meta distinta del snapshot de 2e8e79e"


@sin_red
def test_zzqxw_da_total_0_nunca_signo():
    """El vacío legítimo declara totalNumRecs 0: la línea dice total=0. Es lo que lo separa del anómalo."""
    fx = A.cargar_body(A.BODY_ZZQXW)
    r, lineas = _coto("zzqxw", fx["body"].encode("utf-8"))
    assert len(lineas) == 1, lineas
    m = LINEA.match(lineas[0])
    assert m, f"la línea no tiene el formato: {lineas[0]!r}"
    assert m["total"] == "0" and m["total"] != "?", lineas[0]
    assert (m["q"], m["status"], m["bytes"], m["records"], m["ofertas"]) == \
           ("zzqxw", "200", str(fx["__bytes"]), "0", "0"), lineas[0]


@sin_red
def test_coto_real_no_genera_linea():
    """Una respuesta con productos no es un vacío: ninguna línea, y `vacias` vacío."""
    r, lineas = _coto("leche entera", json.dumps(COTO_REAL).encode("utf-8"))
    assert lineas == [], f"COTO_REAL trajo productos y aun así hubo línea: {lineas}"
    assert len(r.ofertas) == 2
    assert r.vacias == {} and r.fallidas == {}


@sin_red
def test_coto_vacio_llena_vacias():
    """`FuenteCoto` anota el vacío en `vacias` y NO en `fallidas`: es una alarma, no una caída."""
    fx = A.cargar_body(A.BODY_ANOMALO)
    r, _lineas = _coto("Fideos spaghetti", fx["body"].encode("utf-8"))
    assert r.ofertas == []
    assert r.vacias == {"Coto": "HTTP 200 sin ofertas (records=0, total=?)"}, r.vacias
    assert r.fallidas == {}, r.fallidas
    assert r.consultadas == ["Coto"]


@sin_red
def test_compuesta_propaga_vacias():
    """`FuenteCompuesta` (la de producción, `main._get_fuente_online()`) propaga `vacias`, y las 4 VTEX
    —que contestan `[]`— quedan con `vacias` vacío: la alarma es solo de Coto."""
    with log_de_fuentes(), A.enrutado(*A.rutas_escenario()):
        vtex = FuenteVTEX().buscar("Fideos spaghetti", A.CADENAS)
        total = A.main._get_fuente_online().buscar("Fideos spaghetti", A.CADENAS)
    assert sorted(vtex.consultadas) == A.VTEX and vtex.ofertas == []
    assert vtex.vacias == {}, f"las VTEX no tendrían que anotar vacías: {vtex.vacias}"
    assert total.vacias == {"Coto": "HTTP 200 sin ofertas (records=0, total=?)"}, total.vacias


# ─────────────────────────────────────────────────────────────
#  No regresión contra 2e8e79e — dos tests separados
# ─────────────────────────────────────────────────────────────

@sin_red
def test_no_regresion_resultado():
    """`parcial`, `cobertura_pct` y `aviso` del `Resultado` combinado, por consulta, iguales al snapshot.
    `parcial` no viaja a la respuesta de la API: este test es el que la cuida."""
    snap = A.cargar_snapshot()["resultados"]
    _p, _m, resultados, _l = _escenario()
    assert sorted(resultados) == sorted(snap), sorted(resultados)
    for q, r in resultados.items():
        assert (r.parcial, r.cobertura_pct, r.aviso) == \
               (snap[q]["parcial"], snap[q]["cobertura_pct"], snap[q]["aviso"]), \
               f"{q}: (parcial, cobertura_pct, aviso) = {(r.parcial, r.cobertura_pct, r.aviso)}"


@sin_red
def test_no_regresion_meta():
    """`cadenas_fallidas`, `cobertura_cadenas_pct` y `aviso` de la meta —lo que SÍ llega a /api/comparar—
    iguales al snapshot."""
    snap = A.cargar_snapshot()["meta"]
    _p, meta, _r, _l = _escenario()
    meta = A.a_json(meta)
    claves = ("cadenas_fallidas", "cobertura_cadenas_pct", "aviso")
    assert {k: meta[k] for k in claves} == {k: snap[k] for k in claves}, {k: meta[k] for k in claves}


# ─────────────────────────────────────────────────────────────
#  _total_endeca contra lo que registró la sonda
# ─────────────────────────────────────────────────────────────

@sin_red
def test_total_endeca_contra_lo_registrado():
    """Los 7 bodies de la captura del 24/09 y el de Huevos del 03/10, contra el `total` que registró la
    sonda en esa medición: None donde la sonda puso "?"."""
    casos = []
    cap = json.loads((A.CAPTURAS / "sonda_coto_2026-09-24_0343.json").read_text(encoding="utf-8"))
    for b in cap["bodies"]:
        med = [m for m in cap["mediciones"]
               if (m["ronda"], m["brazo"], m["consulta"]) == (b["ronda"], b["brazo"], b["consulta"])]
        assert len(med) == 1, b["consulta"]
        casos.append((f"0343 r{b['ronda']} {b['consulta']}", b["body"], med[0]["total"]))
    hu = A.cargar_body(A.BODY_HUEVOS)
    casos.append((f"0828 r{hu['__ronda']} {hu['__consulta']}", hu["body"], hu["__total_registrado"]))

    assert len(casos) == 8, len(casos)
    assert sum(1 for *_x, t in casos if t is None) == 5, "esperaba 5 anómalos (total=?)"
    malos = [f"{et}: registrado {reg!r}, _total_endeca {FuenteCoto._total_endeca(json.loads(cuerpo))!r}"
             for et, cuerpo, reg in casos
             if FuenteCoto._total_endeca(json.loads(cuerpo)) != reg]
    assert not malos, " | ".join(malos)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    pruebas = [(n, f) for n, f in list(globals().items())
               if n.startswith("test_") and inspect.isfunction(f) and f.__module__ == __name__]
    fallas = []
    for nombre, fn in pruebas:
        try:
            fn()
            print(f"  ok     {nombre}")
        except Exception as e:                          # noqa: BLE001 — se reporta y se sigue
            donde = traceback.extract_tb(e.__traceback__)[-1]
            print(f"  FALLA  {nombre}  →  {Path(donde.filename).name}:{donde.lineno}  {donde.line}")
            print(f"           {type(e).__name__}: {(str(e).splitlines() or [''])[0][:400]}")
            fallas.append(nombre)
    print(f"\n{len(pruebas)} tests corridos: {len(pruebas) - len(fallas)} ok, {len(fallas)} con falla")
    sys.exit(1 if fallas else 0)
