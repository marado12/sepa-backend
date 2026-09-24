"""
Tarea 22, Abierto 2 — SONDA de `FuenteCoto`: ¿qué es un "200 con cero registros"?

**La hipótesis a confirmar o descartar:** *toda consulta de la canasta tiene palabras conocidas, así
que un 0 en la canasta es anómalo sea cual sea el catálogo.*

De dónde sale. La medición del 22/09 vio que `FuenteCoto` devuelve 0 filas sin error en algunos ítems
y que no hay parámetro que explique el patrón. La primera versión de esta sonda quiso contestarlo con
un brazo de "productos que Coto no vende", y los datos lo tumbaron: `"zzzzzzzz producto inexistente
12345"` devolvió **24 registros** y `"xkcd flargle bimbat"`, **0**. O sea que el buscador parece
contestar cuando reconoce **alguna** palabra, y entonces un ausente escrito en español —"Aceite de
trufa"— trae aceites y no mide el catálogo: mide el tokenizador. El brazo de ausentes se sacó por eso.

Lo que queda es más simple y más fuerte: si "palabra conocida + basura" trae resultados y "solo
basura" trae 0, entonces **0 registros significa "no reconocí nada"**, y como toda consulta de la
canasta tiene palabras conocidas, un 0 ahí no lo puede explicar el catálogo.

Y hay un tercer caso que no es ninguno de los dos: que vengan registros y `_parsear` los descarte
enteros (precio 0, sin nombre). Es la forma del bug del 09/09 que advierte el docstring de
`FuenteCoto`, y por eso se guardan `registros` y `ofertas` por separado.

`totalNumRecs` es el total que declara Endeca, no la página de 24: distingue "hay 300 y te doy 24" de
"hay 0". Se guarda en cada request.

**Esta sonda solo mide.** No toca `fuentes.py`, ni el matcher, ni producción, y no propone umbral: el
diseño de la alarma y su test se escriben DESPUÉS, con estos datos, en un plan nuevo.

⚠️ **Un punto de medición solo** (la máquina que la corre). Si los 0 fueran por origen —como el 403 del
SEPA, que desde Argentina baja y desde Render da 403— esta sonda no los puede ver. Eso no se arregla
midiendo más rondas desde acá.

Los tres brazos:
  `canasta`    los 20 ítems de producción: es donde se vio el problema.
  `positivo`   productos que Coto seguro tiene: control de que la sonda y el parser funcionan. Si un
               `positivo` da 0, el 0 es de la sonda o del sitio, no del catálogo.
  `semantica`  el brazo que contesta la hipótesis. Por cada palabra de la canasta: la palabra sola,
               la palabra + una inventada, y la inventada + la palabra (por si pesa el orden). Más dos
               consultas de solo inventadas, que son el piso.

Desde sepa_backend/ (en Windows, con `$env:PYTHONIOENCODING='utf-8'`):
    venv\\Scripts\\python.exe -m tests.sonda_coto --rondas 1                  # prueba
    venv\\Scripts\\python.exe -m tests.sonda_coto --rondas 12 --intervalo 30  # la corrida real
"""
import argparse
import hashlib
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

from fuentes import UA, FuenteCoto
from tests import medir_unidad_pedida as U

RAIZ = Path(__file__).resolve().parents[1]
CANASTA = U.CANASTA

# Tope de seguridad de bodies crudos completos; la política de abajo ya los acota mucho más.
MAX_BODIES = 16

# Menos rondas que esto es una prueba, y la captura sale marcada como tal en el nombre y adentro.
RONDAS_REALES = 12

# ── El brazo de semántica ────────────────────────────────────────────────────────────────────────
#
# Las 4 palabras salen de los ítems donde el 22/09 hubo 0 (Detergente, Atún natural) y de dos que
# nunca fallaron (Leche entera, Arroz largo fino). ⚠️ Son las PALABRAS, no las consultas de
# producción: acá se pide `Atún`, y la consulta de producción es `Atún natural` — son dos búsquedas
# distintas y declaran totales distintos (54 contra 17 el 24/09). `Detergente` coincide de casualidad,
# porque el ítem de la canasta se llama así. Este brazo mide cómo TOKENIZA Endeca; si un ítem de
# producción devuelve 0 o no, lo mide el brazo `canasta`.
# Si la hipótesis es cierta, las 12 consultas con palabra conocida traen registros SIEMPRE —incluso
# con basura pegada— y las 2 de solo inventadas dan 0 SIEMPRE.
PALABRAS = ["Detergente", "Atún", "Leche", "Arroz"]
INVENTADA = "zzqxw"
SOLO_INVENTADAS = ["zzqxw", "frbnt zzqxw"]

# Marcas y productos masivos de Coto: si alguno de estos da 0, sospechá de la sonda, no del catálogo.
POSITIVO = [
    "Coca Cola",
    "Arroz Gallo",
    "Leche La Serenisima",
    "Fideos Matarazzo",
    "Yerba Playadito",
    "Aceite Natura",
]


def consultas_semantica():
    out = []
    for w in PALABRAS:
        out += [w, f"{w} {INVENTADA}", f"{INVENTADA} {w}"]
    return out + SOLO_INVENTADAS


def brazos():
    return [("canasta", [p["nombre"] for p in CANASTA]),
            ("positivo", POSITIVO),
            ("semantica", consultas_semantica())]


def total_endeca(data):
    """`totalNumRecs`: el total que declara Endeca, no la página de 24. None si no está."""
    encontrado = []

    def recorrer(nodo):
        if encontrado:
            return
        if isinstance(nodo, dict):
            if isinstance(nodo.get("totalNumRecs"), int):
                encontrado.append(nodo["totalNumRecs"])
                return
            for v in nodo.values():
                recorrer(v)
        elif isinstance(nodo, list):
            for v in nodo:
                recorrer(v)

    recorrer(data)
    return encontrado[0] if encontrado else None


def pedir(q):
    """
    El request EXACTO de `FuenteCoto.buscar` (sin `Nrpp`: es el de producción), instrumentado.

    Devuelve la medición y el body crudo. Reusa `FuenteCoto.BASE`, `_hallar_records` y `_parsear` a
    propósito: mide el parser real, no una copia que puede derivar.
    """
    t0 = time.monotonic()
    fila = {"ts": datetime.now().isoformat(timespec="seconds"), "consulta": q}
    try:
        r = requests.get(FuenteCoto.BASE,
                         params={"Ntt": q, "format": "json"},
                         headers={"User-Agent": UA, "Accept": "application/json"},
                         timeout=(8, 15), allow_redirects=True)
    except Exception as e:                                   # noqa: BLE001 — se reporta, no se tira
        fila |= {"ms": round((time.monotonic() - t0) * 1000), "error": f"{type(e).__name__}: {str(e)[:120]}"}
        return fila, None
    fila |= {"ms": round((time.monotonic() - t0) * 1000),
             "status": r.status_code,
             "bytes": len(r.content),
             "redirects": [x.status_code for x in r.history] or None,
             "url_final": r.url if r.history else None}
    if r.status_code != 200:
        fila["error"] = f"HTTP {r.status_code}"
        return fila, r.text
    try:
        data = r.json()
    except ValueError as e:
        fila["error"] = f"no es JSON: {str(e)[:80]}"
        return fila, r.text
    # `registros` y `ofertas` separados: es lo que distingue "no vino nada" de "vino y el parser lo
    # descartó entero", que son diagnósticos distintos y hoy se ven iguales desde afuera.
    fila["registros"] = len(FuenteCoto._hallar_records(data))
    fila["ofertas"] = len(FuenteCoto._parsear(data))
    fila["total"] = total_endeca(data)                       # lo que Endeca dice que hay, no la página
    return fila, r.text


def guardar_body(brazo, ronda, registros):
    """
    Qué body crudo se guarda entero. Uno pesa ~169 KB (la página de Endeca sin productos), así que
    guardarlos todos son 8 MB para contestar una pregunta que es de FORMA, no de volumen.

    Solo los 0 —un 200 con registros no tiene nada que diagnosticar— y, de esos:
      · `semantica`: solo en la ronda 1. Sus 0 son el resultado esperado del brazo, no una anomalía;
        con una muestra de la forma alcanza.
      · `canasta` y `positivo`: en TODAS las rondas. Ahí un 0 es justo lo que se está cazando.
    """
    if registros:
        return False
    return ronda == 1 if brazo == "semantica" else True


def una_ronda(n, total, cap, pausa):
    for brazo, consultas in brazos():
        for q in consultas:
            fila, crudo = pedir(q)
            fila["ronda"], fila["brazo"] = n, brazo
            if crudo is not None:
                # El hash va SIEMPRE que haya body: sirve para comparar si el vacío de una misma
                # consulta es idéntico entre rondas, que es la pregunta de la intermitencia.
                h = hashlib.sha1(crudo.encode("utf-8", "replace")).hexdigest()[:12]
                fila["body_sha1"], fila["body_bytes"] = h, len(crudo)
                if guardar_body(brazo, n, fila.get("registros")):
                    cap["__formas"][h] = cap["__formas"].get(h, 0) + 1
                    if len(cap["bodies"]) < MAX_BODIES and h not in cap["__guardados"]:
                        cap["__guardados"].append(h)
                        cap["bodies"].append({"ronda": n, "brazo": brazo, "consulta": q,
                                              "sha1": h, "body": crudo})
            cap["mediciones"].append(fila)
            if "error" in fila:
                marca = f"ERROR {fila['error'][:40]}"
            else:
                marca = (f"reg={fila['registros']:>3} of={fila['ofertas']:>3} "
                         f"total={fila['total'] if fila['total'] is not None else '?':>5}")
            print(f"  [r{n}/{total}] {brazo:<10} {q[:32]:<32} {marca}  {fila['ms']:>5} ms", flush=True)
            time.sleep(pausa)


def rango(xs):
    """
    Rango de los valores que NO son None, como texto. `—` si no hay ninguno.

    Existe porque un request fallido no deja `registros` en la fila, y `sorted({None, 24})` tira
    TypeError: el resumen se caía justo en la corrida que tuvo un error de red, que es la que más
    interesa mirar.
    """
    vs = sorted(x for x in {x for x in xs} if x is not None)
    if not vs:
        return "—"
    return str(vs[0]) if len(vs) == 1 else f"{vs[0]}–{vs[-1]}"


def guardar(cap, ruta):
    """
    Escribe la captura entera a `ruta`, atómicamente: temporal + rename en el mismo directorio.

    Se llama al final de CADA ronda. Antes se escribía una sola vez al final de todo, así que un
    error de red en la hora 5 de una corrida de 6 se llevaba las cinco anteriores.
    """
    tmp = ruta.with_name(ruta.name + ".tmp")
    tmp.write_text(json.dumps(cap, ensure_ascii=False), encoding="utf-8")
    tmp.replace(ruta)


def resumen(cap):
    """Lo que la sonda contesta hoy; el diseño de la alarma NO sale de acá, sale de la corrida entera."""
    ms = cap["mediciones"]
    print("\n" + "=" * 96)
    print("RESUMEN")
    print("=" * 96)
    print(f"  {'brazo':<11} {'consultas':>9} {'0 registros':>12} {'reg>0 of=0':>11} {'total=0':>8} {'errores':>8}")
    for brazo, _ in brazos():
        fs = [f for f in ms if f["brazo"] == brazo]
        if not fs:
            continue
        print(f"  {brazo:<11} {len(fs):>9} "
              f"{sum(1 for f in fs if f.get('registros') == 0):>12} "
              f"{sum(1 for f in fs if f.get('registros', 0) > 0 and f.get('ofertas') == 0):>11} "
              f"{sum(1 for f in fs if f.get('total') == 0):>8} "
              f"{sum(1 for f in ms if f['brazo'] == brazo and 'error' in f):>8}")

    print("\n  §SEMÁNTICA — la hipótesis: palabra conocida ⇒ registros; solo inventadas ⇒ 0")
    print(f"    {'consulta':<28} {'registros':>9} {'total':>7} {'rondas':>7}  lectura")
    for q in consultas_semantica():
        fs = [f for f in ms if f["brazo"] == "semantica" and f["consulta"] == q]
        if not fs:
            continue
        regs = [f.get("registros") for f in fs]
        conocida = q not in SOLO_INVENTADAS
        # ⚠️ El criterio NO puede ser "todas las rondas dieron 0": una palabra conocida que da
        # [0, 24, 24] es exactamente la intermitencia que esta sonda busca, y con `regs == [0]`
        # salía "a favor". Se cuenta CUÁNTAS rondas dieron 0; una sola alcanza para marcarla.
        n_cero = sum(1 for r in regs if r == 0)
        n_con = sum(1 for r in regs if r is not None and r > 0)
        n_err = sum(1 for r in regs if r is None)
        if conocida and n_cero:
            lectura = f"⚠️ CONTRA la hipótesis: palabra conocida y 0 en {n_cero}/{len(fs)} rondas"
        elif not conocida and n_con:
            lectura = f"⚠️ CONTRA el piso: solo inventadas y trae filas en {n_con}/{len(fs)} rondas"
        elif n_err == len(fs):
            lectura = "sin lectura: todas las rondas fallaron"
        else:
            lectura = "a favor"
        if n_err:
            lectura += f" · {n_err} con error"
        print(f"    {q:<28} {rango(regs):>9} {rango([f.get('total') for f in fs]):>7} "
              f"{len(fs):>7}  {lectura}")

    ceros = sorted({f["consulta"] for f in ms
                    if f["brazo"] in ("canasta", "positivo") and f.get("registros") == 0})
    print(f"\n  canasta/positivo con 0 registros: {ceros or '—'}")
    if cap["__rondas"] < RONDAS_REALES:
        print("  ⚠️ Una ronda no dice si un 0 es intermitente: para eso hacen falta las 12.")


def main_cli():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Sonda de FuenteCoto: qué es un 200 con cero registros.")
    ap.add_argument("--rondas", type=int, default=1)
    ap.add_argument("--intervalo", type=int, default=30, help="minutos entre rondas")
    ap.add_argument("--pausa", type=float, default=3.0, help="segundos entre requests dentro de una ronda")
    ap.add_argument("--salida", default=None)
    args = ap.parse_args()

    n_consultas = sum(len(c) for _b, c in brazos())
    cap = {"__fecha": datetime.now().isoformat(timespec="seconds"),
           "__origen": "red real desde la máquina que corrió tests/sonda_coto.py — UN SOLO punto",
           "__request": "el de FuenteCoto.buscar: Ntt=<q>&format=json, sin Nrpp (el de producción)",
           "__hipotesis": ("toda consulta de la canasta tiene palabras conocidas, así que un 0 en la "
                           "canasta es anómalo sea cual sea el catálogo"),
           "__rondas": args.rondas, "__intervalo_min": args.intervalo, "__pausa_s": args.pausa,
           "__brazos": {b: c for b, c in brazos()},
           "__prueba": args.rondas < RONDAS_REALES,
           "__formas": {}, "__guardados": [], "mediciones": [], "bodies": []}

    # Siempre a `tests/capturas/`, nunca a un temporal: una corrida real son 6 horas de red y
    # perderla por escribir en un directorio efímero es el error caro. Las de prueba llevan
    # `_prueba` en el nombre para que se distingan de un vistazo y las borre quien corresponda —
    # la sonda NO borra capturas, ni las suyas.
    marca = "_prueba" if cap["__prueba"] else ""
    ruta = Path(args.salida) if args.salida else (
        RAIZ / "tests" / "capturas" / f"sonda_coto{marca}_{datetime.now():%Y-%m-%d_%H%M}.json")
    ruta.parent.mkdir(parents=True, exist_ok=True)

    print(f"{n_consultas} consultas × {args.rondas} ronda(s) = {n_consultas * args.rondas} requests, "
          f"1 cada {args.pausa} s, rondas cada {args.intervalo} min")
    print(f"escribiendo a {ruta.name} al final de cada ronda\n")
    for n in range(1, args.rondas + 1):
        una_ronda(n, args.rondas, cap, args.pausa)
        # Al final de CADA ronda, no al final de todo: si la corrida se cae en la hora 5, lo
        # medido hasta ahí queda en disco.
        guardar(cap, ruta)
        print(f"  ✔ ronda {n}/{args.rondas} guardada · {ruta.stat().st_size / 1024:,.0f} KB", flush=True)
        if n < args.rondas:
            print(f"\n  … esperando {args.intervalo} min hasta la ronda {n + 1}\n", flush=True)
            time.sleep(args.intervalo * 60)

    resumen(cap)
    guardar(cap, ruta)
    kb = ruta.stat().st_size / 1024
    bytes_bodies = sum(len(b["body"]) for b in cap["bodies"])
    donde = ruta.relative_to(RAIZ) if RAIZ in ruta.resolve().parents else ruta
    print(f"\n  {donde}  ·  {kb:,.0f} KB")
    print(f"  {len(cap['mediciones'])} mediciones · {len(cap['bodies'])} bodies crudos completos "
          f"({bytes_bodies / 1024:,.0f} KB) · formas distintas de vacío: {len(cap['__formas'])}")
    if args.rondas == 1:
        # Los bodies de `semantica` son solo de la ronda 1; los de canasta/positivo crecerían solo si
        # aparecen 0 nuevos, que es justo lo que se está cazando y hay que guardar.
        meta_kb = kb - bytes_bodies / 1024
        print(f"  proyección a 12 rondas: ~{(meta_kb * 12 + bytes_bodies / 1024) / 1024:,.1f} MB "
              f"({meta_kb:,.0f} KB de mediciones × 12 + los bodies de la ronda 1, que no se repiten) "
              f"+ ~165 KB por cada 0 nuevo de canasta/positivo")


if __name__ == "__main__":
    main_cli()
