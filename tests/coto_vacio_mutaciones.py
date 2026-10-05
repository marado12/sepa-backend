"""
Tarea 22, Abierto 2, Etapa 2 — MUTACIONES de la alarma de Coto vacío (`fuentes.py` y su arnés).

Un guard que nunca se vio fallar no vale nada. Cada mutación reintroduce, en memoria, una forma concreta de
romper la alarma —o el arnés— y exige que `test_coto_vacio.py` caiga **por SU aserción**: el test y la línea
que se nombran abajo, no cualquier otro error. Algunas piden además algo más fino: que M-B deje en verde el
llenado (cae la propagación, no el llenado), que M-D deje en verde la meta (`parcial` no llega a la API), que
M-E caiga solo por Huevos y que M-R caiga solo por el bloqueo de red.

Cómo muta: lee el archivo, exige que el texto a reemplazar aparezca **una sola vez**, compila la copia con el
nombre del archivo real (así el traceback muestra la línea mutada) y la instala en `sys.modules` antes de que
nadie la importe. Cada mutación corre en un subproceso propio, así que nada queda importado de una a otra. El
repo no se toca. El control sin mutar tiene que dar todo verde.

Fuera de la CI. Desde sepa_backend/ (en Windows, con `$env:PYTHONIOENCODING='utf-8'`):
    venv\\Scripts\\python.exe -m tests.coto_vacio_mutaciones
"""
import linecache
import re
import runpy
import socket
import subprocess
import sys
import types
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
TESTS = RAIZ / "test_coto_vacio.py"

# Cada una: qué rompe, dónde, el reemplazo exacto, y por qué aserción tiene que caer.
MUTACIONES = [
    {"id": "M-A", "que": "sin el llenado de `vacias` en FuenteCoto",
     "archivo": "fuentes.py", "modulo": "fuentes",
     "viejo": 'res.vacias["Coto"] = self._avisar_vacio(',
     "nuevo": "self._avisar_vacio(",
     "cae": "test_coto_vacio_llena_vacias", "linea": 'assert r.vacias == {"Coto"'},
    {"id": "M-B", "que": "sin la propagación de `vacias` en FuenteCompuesta",
     "archivo": "fuentes.py", "modulo": "fuentes",
     "viejo": "            total.vacias.update(r.vacias)\n",
     "nuevo": "",
     "cae": "test_compuesta_propaga_vacias", "linea": "assert total.vacias ==",
     "verdes": ["test_coto_vacio_llena_vacias"]},
    {"id": "M-C", "que": "`_total_endeca` devuelve 0 en vez de None",
     "archivo": "fuentes.py", "modulo": "fuentes",
     "viejo": "return encontrado[0] if encontrado else None",
     "nuevo": "return encontrado[0] if encontrado else 0",
     "cae": "test_total_endeca_contra_lo_registrado", "linea": "assert not malos",
     "mensaje_cuenta": ("registrado None, _total_endeca 0", 5)},
    {"id": "M-D", "que": "parcial = bool(fallidas or vacias)",
     "archivo": "fuentes.py", "modulo": "fuentes",
     "viejo": "return bool(self.fallidas)",
     "nuevo": "return bool(self.fallidas or self.vacias)",
     "cae": "test_no_regresion_resultado", "linea": "assert (r.parcial, r.cobertura_pct, r.aviso) ==",
     "verdes": ["test_no_regresion_meta"]},
    {"id": "M-E", "que": "`_total_endeca` devuelve 0 cuando encuentra el campo, sin mirar el valor",
     "archivo": "fuentes.py", "modulo": "fuentes",
     "viejo": 'encontrado.append(nodo["totalNumRecs"])',
     "nuevo": "encontrado.append(0)",
     "cae": "test_total_endeca_contra_lo_registrado", "linea": "assert not malos",
     "mensaje_cuenta": ("_total_endeca", 1), "mensaje_tiene": "0828 r1 Huevos"},
    {"id": "M-F", "que": "la línea [coto-vacio] sale en cualquier 200",
     "archivo": "fuentes.py", "modulo": "fuentes",
     "viejo": ('            if not res.ofertas:\n'
               '                res.vacias["Coto"] = self._avisar_vacio(query, r, data, len(res.ofertas), ms)\n'),
     "nuevo": ('            detalle = self._avisar_vacio(query, r, data, len(res.ofertas), ms)\n'
               '            if not res.ofertas:\n'
               '                res.vacias["Coto"] = detalle\n'),
     "cae": "test_coto_real_no_genera_linea", "linea": "assert lineas == []"},
    {"id": "M-G", "que": "FuenteCoto mete la vacía también en `fallidas`",
     "archivo": "fuentes.py", "modulo": "fuentes",
     "viejo": 'res.vacias["Coto"] = self._avisar_vacio(query, r, data, len(res.ofertas), ms)\n',
     "nuevo": ('res.vacias["Coto"] = self._avisar_vacio(query, r, data, len(res.ofertas), ms)\n'
               '                res.fallidas["Coto"] = res.vacias["Coto"]\n'),
     "cae": "test_no_regresion_meta", "linea": "assert {k: meta[k] for k in claves} =="},
    {"id": "M-H", "que": "`_total_endeca` mira si `totalNumRecs` es verdadero, no si es int: el 0 se pierde",
     "archivo": "fuentes.py", "modulo": "fuentes",
     "viejo": 'if isinstance(nodo.get("totalNumRecs"), int):',
     "nuevo": 'if nodo.get("totalNumRecs"):',
     "cae": "test_zzqxw_da_total_0_nunca_signo", "linea": 'assert m["total"] == "0"'},
    {"id": "M-I", "que": "FuenteVTEX anota en `vacias` la cadena que contesta 200 con []",
     "archivo": "fuentes.py", "modulo": "fuentes",
     "viejo": ("                for p in productos:\n"
               "                    of = self._a_oferta(cadena, p)\n"),
     "nuevo": ("                if not productos:\n"
               '                    res.vacias[cadena] = "HTTP 200 sin ofertas"\n'
               "                for p in productos:\n"
               "                    of = self._a_oferta(cadena, p)\n"),
     "cae": "test_compuesta_propaga_vacias", "linea": "assert vtex.vacias == {}"},
    {"id": "M-J", "que": "el arnés entrega alterado en memoria el body de Huevos (un espacio al final)",
     "archivo": "tests/arnes_coto_vacio.py", "modulo": "tests.arnes_coto_vacio",
     "viejo": '    return json.loads(ruta.read_text(encoding="utf-8"))\n',
     "nuevo": ('    fx = json.loads(ruta.read_text(encoding="utf-8"))\n'
               '    if ruta == BODY_HUEVOS:\n'
               '        fx["body"] += " "\n'
               '    return fx\n'),
     "cae": "test_las_fixtures_son_los_bodies_de_las_capturas", "linea": 'assert fx["body"] == bodies[0]["body"]'},
    {"id": "M-R", "que": "el arnés sin el mock de una VTEX (Vea)",
     "archivo": "tests/arnes_coto_vacio.py", "modulo": "tests.arnes_coto_vacio",
     "viejo": 'for c in CADENAS if c != "Coto"}',
     "nuevo": 'for c in CADENAS if c not in ("Coto", "Vea")}',
     "cae": None, "linea": "assert not intentos", "solo_red": "www.vea.com.ar"},
]


# ─────────────────────────────────────────────────────────────────
#  HIJO: aplica una mutación y corre los tests
# ─────────────────────────────────────────────────────────────────

def _cinturon_de_red():
    """Además del bloqueo de cada test: ningún socket sale de este proceso hacia afuera."""
    def no(*a, **_k):
        raise RuntimeError(f"RED BLOQUEADA (coto_vacio_mutaciones): {a[:2]!r}")
    for n in ("getaddrinfo", "gethostbyname", "gethostbyname_ex", "gethostbyaddr", "create_connection"):
        setattr(socket, n, no)
    socket.socket.connect = socket.socket.connect_ex = no


def aplicar(m):
    """Instala en sys.modules la copia mutada del módulo. Exige una sola coincidencia."""
    ruta = RAIZ / m["archivo"]
    fuente = ruta.read_text(encoding="utf-8")
    n = fuente.count(m["viejo"])
    if n != 1:
        raise SystemExit(f"{m['id']}: el texto a reemplazar aparece {n} veces en {m['archivo']}, no 1")
    mutado = fuente.replace(m["viejo"], m["nuevo"])
    mod = types.ModuleType(m["modulo"])
    mod.__file__ = str(ruta)
    if "." in m["modulo"]:
        paquete, _, hoja = m["modulo"].rpartition(".")
        mod.__package__ = paquete
        __import__(paquete)
        setattr(sys.modules[paquete], hoja, mod)
    sys.modules[m["modulo"]] = mod
    # Para que el traceback muestre la línea MUTADA y no la del disco.
    linecache.cache[str(ruta)] = (len(mutado), None, mutado.splitlines(True), str(ruta))
    exec(compile(mutado, str(ruta), "exec"), mod.__dict__)


def hijo(mid):
    _cinturon_de_red()
    sys.path.insert(0, str(RAIZ))
    if mid != "control":
        aplicar(next(m for m in MUTACIONES if m["id"] == mid))
    sys.argv = [str(TESTS)]
    runpy.run_path(str(TESTS), run_name="__main__")


# ─────────────────────────────────────────────────────────────────
#  PADRE: corre cada una y mira qué cayó
# ─────────────────────────────────────────────────────────────────

_OK = re.compile(r"^  ok     (\S+)$")
_FALLA = re.compile(r"^  FALLA  (\S+)  →  (\S+)  (.*)$")


def correr(mid):
    p = subprocess.run([sys.executable, "-m", "tests.coto_vacio_mutaciones", "--hijo", mid], cwd=RAIZ,
                       capture_output=True, text=True, encoding="utf-8",
                       env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
    oks, fallas, ultima = [], {}, None
    for ln in p.stdout.splitlines():
        if (a := _OK.match(ln)):
            oks.append(a[1])
        elif (b := _FALLA.match(ln)):
            ultima = b[1]
            fallas[ultima] = {"donde": b[2], "linea": b[3].strip(), "mensaje": ""}
        elif ultima and ln.startswith("           "):
            fallas[ultima]["mensaje"] = ln.strip()
            ultima = None
    return p, oks, fallas


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    problemas = []

    print("=" * 100)
    print("CONTROL sin mutar")
    print("=" * 100)
    p, oks, fallas = correr("control")
    print(f"  {len(oks)} ok, {len(fallas)} con falla (exit {p.returncode})")
    if fallas or p.returncode != 0 or not oks:
        problemas.append("control: no da todo verde")
        print(p.stdout[-2000:], p.stderr[-2000:])
    else:
        print("  ✔ todo verde")

    for m in MUTACIONES:
        print()
        print("=" * 100)
        print(f"{m['id']} — {m['que']}   ({m['archivo']})")
        print("=" * 100)
        p, oks, fallas = correr(m["id"])
        if not oks and not fallas:
            problemas.append(f"{m['id']}: el hijo no corrió los tests")
            print(f"  ✖ el hijo no corrió los tests (exit {p.returncode})\n{p.stdout[-1500:]}\n{p.stderr[-1500:]}")
            continue
        print(f"  {len(oks)} ok, {len(fallas)} con falla")
        for t, f in fallas.items():
            print(f"    ✖ {t}\n        {f['donde']}  {f['linea']}\n        {f['mensaje'][:300]}")

        ok = True
        if m.get("solo_red"):
            if not fallas:
                ok = False
                print("  ✖ NO se detectó: ningún test cayó")
            for t, f in fallas.items():
                if (m["linea"] not in f["linea"] or "intento de red" not in f["mensaje"]
                        or m["solo_red"] not in f["mensaje"]):
                    ok = False
                    print(f"  ✖ {t} cayó por OTRA cosa, no por el bloqueo de red hacia {m['solo_red']}")
            if ok:
                print(f"  ✔ ROJO por el bloqueo de red, y solo por eso: {len(fallas)} tests, todos en "
                      f"`{m['linea']}` con un intento hacia {m['solo_red']}")
        else:
            f = fallas.get(m["cae"])
            if not f:
                ok = False
                print(f"  ✖ NO se detectó: {m['cae']} quedó en verde")
            elif m["linea"] not in f["linea"]:
                ok = False
                print(f"  ✖ {m['cae']} cayó, pero por otra aserción: {f['linea']}")
            else:
                if "mensaje_cuenta" in m:
                    texto, n = m["mensaje_cuenta"]
                    if f["mensaje"].count(texto) != n:
                        ok = False
                        print(f"  ✖ el mensaje tendría que nombrar {n} caso(s) con «{texto}»")
                if "mensaje_tiene" in m and m["mensaje_tiene"] not in f["mensaje"]:
                    ok = False
                    print(f"  ✖ el mensaje tendría que nombrar «{m['mensaje_tiene']}»")
                if ok:
                    print(f"  ✔ ROJO por su propia aserción: {m['cae']}\n        {f['donde']}  {f['linea']}")
            for t in m.get("verdes", []):
                if t in fallas:
                    ok = False
                    print(f"  ✖ {t} tendría que seguir en verde y cayó")
                else:
                    print(f"  ✔ {t} sigue en verde")
        if not ok:
            problemas.append(m["id"])

    print()
    print("=" * 100)
    if problemas:
        print(f"FALLARON {len(problemas)}:")
        for x in problemas:
            print(f"  ✖ {x}")
        return 1
    print(f"TODAS OK — el control da todo verde y las {len(MUTACIONES)} mutaciones caen por su aserción.")
    return 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["--hijo"]:
        hijo(sys.argv[2])
    else:
        sys.exit(main())
