"""
Tarea 22, Abierto 2, Etapa 2 — el ARNÉS de mocks de la alarma "Coto respondió 200 sin ofertas".

Lo usan dos cosas, y que sea el mismo para las dos es el punto: el snapshot previo (`--generar`, sacado una
vez con `2e8e79e`, antes de tocar `fuentes.py`) y `test_coto_vacio.py`, que compara contra ese snapshot. Un
snapshot sacado con otros mocks no probaría nada.

Qué arma:

- **La red, bloqueada a nivel socket, y cada intento anotado.** `FuenteVTEX` y `FuenteCoto` se tragan las
  excepciones —es su contrato: una cadena caída no tumba a las demás—, así que un request que se escapa no
  tira el test por sí solo. Hay que preguntar después si hubo intentos.
- **Un router de `fuentes.requests.get` para las 5 cadenas.** Contesta solo los pares (cadena, consulta) que
  conoce; todo lo demás va al `requests.get` real, que choca con el bloqueo. Así un mock que falta se ve como
  lo que es —un intento de red— y no como otro error.
- **Las cadenas, las mismas que consulta producción** con filtro geográfico, que es el caso normal (el
  frontend manda lat/lon): las que soporta la fuente online (`main.py:2770`, `_get_fuente_online` en
  `main.py:2754-2759`) y tienen sucursales en el catálogo (`main.py:2772` y `:2785`). Las "sin geo" no
  entran porque `INCLUIR_CADENAS_SIN_GEO` vale `"0"` por defecto (`main.py:89`, `:2786`). Hoy son 5:
  Carrefour, Chango Más, Coto, Día y Vea. Si el catálogo cambia, esto falla a propósito.
- **La canasta:** Leche entera + Fideos spaghetti, tal cual en `CANASTA_DEFAULT` (`main.py:126` y `:129`).
  - Leche entera: las fixtures reales del 09/09 de las 5 cadenas, rearmadas con los helpers que ya existen
    (`a_producto_vtex`, `a_producto_coto`).
  - Fideos spaghetti: Coto devuelve el body ANÓMALO real de la captura del 24/09 (r8). ⚠️ **Las 4 VTEX
    devuelven `[]`, y eso es SINTÉTICO:** no hay fixture VTEX de fideos. `[]` es la forma de "sin resultados"
    de VTEX; no se capturó para esta consulta.
- **El camino de producción:** `buscar_precios_online` con la fuente de `_get_fuente_online()`, `normalizar`
  y `_extraer_cantidades_desc`, como lo llama `main.py:2875-2878`.

`main` se importa con `CACHE_DIR` y `PRECIOS_MANUALES_PATH` en un temporal: `main.py:39` hace `makedirs` al
importarse y el default es `/tmp/sepa_cache` (en Windows, `C:\\tmp\\sepa_cache`). El entorno se restaura
después del import.

Desde sepa_backend/ (en Windows, con `$env:PYTHONIOENCODING='utf-8'`):
    venv\\Scripts\\python.exe -m tests.arnes_coto_vacio --generar
Escribe `tests/fixtures/snapshot_coto_vacio_2e8e79e.json` y se niega si `fuentes.py`, `precios_vtex.py` o
`main.py` difieren de `2e8e79e`: el snapshot es la foto de ANTES del cambio.
"""
import contextlib
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path

import requests

RAIZ = Path(__file__).resolve().parents[1]
FIX = RAIZ / "tests" / "fixtures"
CAPTURAS = RAIZ / "tests" / "capturas"
SNAPSHOT = FIX / "snapshot_coto_vacio_2e8e79e.json"
COMMIT_SNAPSHOT = "2e8e79e67825ed3594ad8aafbaa4c021222aab2e"

BODY_ANOMALO = FIX / "coto_body_anomalo_fideos_2026-09-24.json"
BODY_ZZQXW = FIX / "coto_body_vacio_zzqxw_2026-09-24.json"
BODY_HUEVOS = FIX / "coto_body_huevos_2026-10-03.json"


# ─────────────────────────────────────────────────────────────────
#  RED BLOQUEADA
# ─────────────────────────────────────────────────────────────────

class RedBloqueada(RuntimeError):
    """
    Un intento de red con la red bloqueada. Hereda de RuntimeError y NO de OSError a propósito: urllib3
    convierte los OSError en errores de conexión, y esto tiene que llegar tal cual a quien lo atrape.
    """


@contextlib.contextmanager
def red_bloqueada():
    """Bloquea resolución y conexión de sockets en todo el proceso (los hilos de `FuenteVTEX` incluidos)
    y entrega la lista de intentos."""
    intentos: list[str] = []
    candado = threading.Lock()
    # Resolución (incluidas las gethostby*, que usa el chequeo de proxy de Windows) y conexión.
    puntos = [(socket, n, False) for n in ("getaddrinfo", "gethostbyname", "gethostbyname_ex",
                                           "gethostbyaddr", "create_connection")]
    puntos += [(socket.socket, n, True) for n in ("connect", "connect_ex")]
    originales = [(obj, n, getattr(obj, n)) for obj, n, _m in puntos]

    def bloqueo(nombre, es_metodo):
        def f(*a, **_k):
            destino = a[1] if es_metodo and len(a) > 1 else (a[:2] if a else "?")
            with candado:
                intentos.append(f"{nombre}{destino!r}")
            raise RedBloqueada(f"RED BLOQUEADA: {nombre}{destino!r}")
        return f

    for obj, n, es_metodo in puntos:
        setattr(obj, n, bloqueo(n, es_metodo))
    try:
        yield intentos
    finally:
        for obj, n, f in originales:
            setattr(obj, n, f)


# ─────────────────────────────────────────────────────────────────
#  MAIN, CON EL CACHE EN UN TEMPORAL
# ─────────────────────────────────────────────────────────────────

def _importar_main():
    if "main" in sys.modules:
        raise RuntimeError("`main` ya estaba importado: su CACHE_DIR quedó fijado antes que el arnés "
                           "pudiera apuntarlo a un temporal")
    previo = {k: os.environ.get(k) for k in ("CACHE_DIR", "PRECIOS_MANUALES_PATH")}
    tmp = tempfile.mkdtemp(prefix="coto_vacio_cache_")
    os.environ["CACHE_DIR"] = tmp
    os.environ["PRECIOS_MANUALES_PATH"] = os.path.join(tmp, "precios_manuales.json")
    try:
        with red_bloqueada() as intentos:
            import main as m
        if intentos:
            raise RuntimeError(f"importar main intentó red: {intentos[:3]}")
    finally:
        for k, v in previo.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    if os.path.realpath(m.CACHE_DIR) != os.path.realpath(tmp):
        raise RuntimeError(f"main.CACHE_DIR quedó en {m.CACHE_DIR}, no en el temporal {tmp}")
    return m


main = _importar_main()

# Después de `main`: los dos importan main a nivel de módulo.
import sucursales                                                    # noqa: E402
from fuentes import VTEX_BASES, FuenteCoto                           # noqa: E402
from precios_vtex import buscar_precios_online                       # noqa: E402
from tests.evaluar_matching import a_producto_coto, a_producto_vtex  # noqa: E402
from tests.medir_regresion import FIXTURES                           # noqa: E402


# ─────────────────────────────────────────────────────────────────
#  CADENAS Y CANASTA — las de producción
# ─────────────────────────────────────────────────────────────────

CADENAS = sorted(set(main._get_fuente_online().cadenas_soportadas())
                 & set(sucursales.resumen()["por_cadena"]))
if main.INCLUIR_CADENAS_SIN_GEO or CADENAS != ["Carrefour", "Chango Más", "Coto", "Día", "Vea"]:
    raise RuntimeError(f"las cadenas de producción con filtro geográfico ya no son las 5 del snapshot: "
                       f"{CADENAS} (INCLUIR_CADENAS_SIN_GEO={main.INCLUIR_CADENAS_SIN_GEO})")
VTEX = [c for c in CADENAS if c != "Coto"]

CONSULTAS = ("Leche entera", "Fideos spaghetti")
CANASTA = [dict(it) for it in main.CANASTA_DEFAULT if it["nombre"] in CONSULTAS]
assert [it["nombre"] for it in CANASTA] == list(CONSULTAS), CANASTA


# ─────────────────────────────────────────────────────────────────
#  RESPUESTAS
# ─────────────────────────────────────────────────────────────────

def cargar_body(ruta: Path) -> dict:
    """Una fixture de body de Coto: el `r.text` de la sonda y la medición que lo acompaña."""
    return json.loads(ruta.read_text(encoding="utf-8"))


def respuesta(cuerpo: bytes, url: str, status: int = 200) -> requests.Response:
    """Un `requests.Response` de verdad, no un objeto con forma parecida: `.content`, `.json()`,
    `.status_code` y `raise_for_status()` se comportan como contra el sitio."""
    r = requests.Response()
    r.status_code = status
    r._content = cuerpo
    r.encoding = "utf-8"
    r.url = url
    r.headers["Content-Type"] = "application/json;charset=UTF-8"
    return r


def _filas(cadena: str, consulta: str) -> list:
    data = json.loads((FIX / FIXTURES[cadena]).read_text(encoding="utf-8"))
    return data[consulta]


def _vtex_leche(cadena: str) -> bytes:
    return json.dumps([a_producto_vtex(f) for f in _filas(cadena, "Leche entera")]).encode("utf-8")


def _coto_leche() -> bytes:
    filas = _filas("Coto", "Leche entera")
    raw = {"contents": [{"Main": [{"contents": [{"records": [a_producto_coto(f) for f in filas]}]}]}]}
    return json.dumps(raw).encode("utf-8")


def rutas_escenario() -> tuple[dict, dict]:
    """(vtex, coto) del escenario del snapshot. vtex = {cadena: {consulta: bytes}}; coto = {consulta: bytes}."""
    vtex = {c: {"Leche entera": _vtex_leche(c),
                "Fideos spaghetti": b"[]"}            # SINTÉTICO: no hay fixture VTEX de fideos
            for c in CADENAS if c != "Coto"}
    coto = {"Leche entera": _coto_leche(),
            "Fideos spaghetti": cargar_body(BODY_ANOMALO)["body"].encode("utf-8")}
    return vtex, coto


@contextlib.contextmanager
def enrutado(vtex: dict | None = None, coto: dict | None = None):
    """Reemplaza `fuentes.requests.get`. Lo que no está en las tablas va al `requests.get` real."""
    real = requests.get
    vtex, coto = vtex or {}, coto or {}

    def get(url, params=None, **kw):
        if url == FuenteCoto.BASE and params and params.get("Ntt") in coto:
            return respuesta(coto[params["Ntt"]], url)
        for cadena, tabla in vtex.items():
            prefijo = VTEX_BASES[cadena] + "/api/catalog_system/pub/products/search/?ft="
            if url.startswith(prefijo):
                consulta = requests.utils.unquote(url[len(prefijo):].split("&", 1)[0])
                if consulta in tabla:
                    return respuesta(tabla[consulta], url)
        return real(url, params=params, **kw)                   # no ruteado: a la red, que está bloqueada

    import fuentes
    original = fuentes.requests.get
    fuentes.requests.get = get
    try:
        yield
    finally:
        fuentes.requests.get = original


class Espia:
    """Pasa las llamadas a la fuente de producción y se queda con cada `Resultado`, que
    `buscar_precios_online` no devuelve y la no regresión del `Resultado` combinado necesita."""

    def __init__(self, fuente):
        self.fuente = fuente
        self.nombre = fuente.nombre
        self.resultados: dict = {}

    def cadenas_soportadas(self):
        return self.fuente.cadenas_soportadas()

    def buscar(self, query, cadenas=None):
        r = self.fuente.buscar(query, cadenas)
        self.resultados[query] = r
        return r


def correr() -> tuple[dict, dict, dict]:
    """(precios, meta, {consulta: Resultado}) por el camino de producción. Llamar dentro de
    `red_bloqueada()` y `enrutado(*rutas_escenario())`."""
    espia = Espia(main._get_fuente_online())
    precios, meta = buscar_precios_online(CANASTA, CADENAS, espia, main.normalizar,
                                          main._extraer_cantidades_desc)
    return precios, meta, espia.resultados


# ─────────────────────────────────────────────────────────────────
#  FORMA COMPARABLE (JSON)
# ─────────────────────────────────────────────────────────────────

def a_json(x):
    """Lo que sobrevive a un ida y vuelta por JSON: tuplas → listas. Así se compara contra el snapshot."""
    return json.loads(json.dumps(x, ensure_ascii=False))


def precios_a_json(precios: dict) -> list:
    return a_json([[c, n, precios[(c, n)]] for (c, n) in sorted(precios)])


def resultado_a_json(r) -> dict:
    return a_json({"parcial": r.parcial, "cobertura_pct": r.cobertura_pct, "aviso": r.aviso,
                   "fallidas": r.fallidas, "consultadas": r.consultadas, "n_ofertas": len(r.ofertas)})


def cargar_snapshot() -> dict:
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


# ─────────────────────────────────────────────────────────────────
#  --generar
# ─────────────────────────────────────────────────────────────────

def _git(*args) -> str:
    return subprocess.run(["git", *args], cwd=RAIZ, capture_output=True, text=True,
                          check=True).stdout.strip()


def generar() -> int:
    distintos = subprocess.run(["git", "diff", "--quiet", COMMIT_SNAPSHOT, "--",
                                "fuentes.py", "precios_vtex.py", "main.py"], cwd=RAIZ).returncode
    if distintos:
        print(f"fuentes.py, precios_vtex.py o main.py difieren de {COMMIT_SNAPSHOT[:7]}: el snapshot es "
              f"la foto de ANTES del cambio y no se puede sacar con este código.")
        return 1
    with red_bloqueada() as intentos, enrutado(*rutas_escenario()):
        precios, meta, resultados = correr()
    if intentos:
        print(f"hubo intentos de red: {intentos}")
        return 1
    snap = {
        "__que": ("Snapshot PREVIO de la alarma de Coto vacío (Tarea 22, Abierto 2, Etapa 2): lo que devuelve "
                  "el código de antes del cambio para la canasta de abajo, con Coto contestando el body "
                  "anómalo del 24/09 para Fideos spaghetti. test_coto_vacio.py exige que después del cambio "
                  "precios y meta sigan iguales, y que el Resultado combinado no cambie."),
        "__generado": datetime.now().isoformat(timespec="seconds"),
        "__commit": _git("rev-parse", "HEAD"),
        "__fuentes_py_sha1": hashlib.sha1((RAIZ / "fuentes.py").read_bytes()).hexdigest(),
        "__comando": "venv\\Scripts\\python.exe -m tests.arnes_coto_vacio --generar",
        "__cadenas": CADENAS,
        "__canasta": CANASTA,
        "__nota_vtex_fideos": ("SINTÉTICO: las 4 VTEX contestan [] para 'Fideos spaghetti'. No hay fixture "
                               "VTEX de fideos; [] es la forma de 'sin resultados' de VTEX y no se capturó "
                               "para esta consulta."),
        "__nota_coto": ("Coto: 'Leche entera' rearmada de coto_estructura_real.json (09/09) con "
                        "a_producto_coto; 'Fideos spaghetti' es el body anómalo REAL de la captura del 24/09, "
                        "r8 (coto_body_anomalo_fideos_2026-09-24.json)."),
        "__nota_vtex_leche": "Las 4 VTEX: 'Leche entera' de sus fixtures reales del 09/09, con a_producto_vtex.",
        "precios": precios_a_json(precios),
        "meta": a_json(meta),
        "resultados": {q: resultado_a_json(r) for q, r in resultados.items()},
    }
    with open(SNAPSHOT, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(snap, ensure_ascii=False, indent=1) + "\n")
    print(f"escrito {SNAPSHOT.relative_to(RAIZ)}: {len(snap['precios'])} precios, "
          f"consultadas {meta['cadenas_consultadas']}, fallidas {meta['cadenas_fallidas']}, "
          f"cobertura {meta['cobertura_cadenas_pct']}, sin match {meta['productos_sin_match']}")
    for q, r in snap["resultados"].items():
        print(f"  Resultado[{q!r}]: {r}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if sys.argv[1:] != ["--generar"]:
        print(__doc__)
        sys.exit(2)
    sys.exit(generar())
