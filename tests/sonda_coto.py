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

✏️ **24/09 — qué body se guarda, ampliado.** Hasta ahora solo los 0. La corrida de las 12 rondas
mostró dos formas que la regla vieja no guardaba y que son justo las que hacen falta:
  · `parcial`   Huevos: 12 registros contra `totalNumRecs` 171, en las 12 rondas. Sin el body no se
                puede separar "hay 12" de "`_parsear` toma `hijos[0]` de 12 agregados" ni de
                "`_hallar_records` cortó en el primero de dos contenedores".
  · `derrumbe`  Pollo entero en la r9: 2 contra 243 en la r8. Degradado que NO está vacío, así que
                ninguna regla de 0 lo ve.
Las reglas viven en `REGLAS` y se prueban sin red con `--replay` contra una captura ya hecha.

Desde sepa_backend/ (en Windows, con `$env:PYTHONIOENCODING='utf-8'`):
    venv\\Scripts\\python.exe -m tests.sonda_coto --rondas 1                  # prueba
    venv\\Scripts\\python.exe -m tests.sonda_coto --rondas 12 --intervalo 30  # la corrida real
    venv\\Scripts\\python.exe -m tests.sonda_coto --rondas 13 --intervalo 10 --inicio 06:30
    venv\\Scripts\\python.exe -m tests.sonda_coto --replay tests/capturas/sonda_coto_2026-09-24_0343.json
"""
import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

from fuentes import UA, FuenteCoto
from tests import medir_unidad_pedida as U

RAIZ = Path(__file__).resolve().parents[1]
CANASTA = U.CANASTA

# Tope de seguridad de bodies crudos completos; la política de abajo ya los acota mucho más.
# ✏️ 24/09: subido de 16 a 32. Las dos reglas nuevas (`parcial` y `derrumbe`) guardan formas que
# antes no se guardaban, y con 16 el tope se alcanzaba antes de haberlas visto todas.
MAX_BODIES = 32

# Menos rondas que esto es una prueba, y la captura sale marcada como tal en el nombre y adentro.
RONDAS_REALES = 12

# La página completa de Endeca son 24 registros. MEDIDO, no supuesto: la respuesta lo declara en
# `$.contents[0].Main[2].contents[0].recsPerPage` y en la captura del 24/09 vale 24.
NRPP_DEFECTO = 24

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

# ✏️ 24/09 — dos palabras CONOCIDAS las dos, de rubros sin productos en común. Contestan algo que
# las de arriba no pueden: si Endeca hace fallback parcial (OR) el total se parecerá al de una de
# las dos palabras; si hace AND estricto, dará 0. **Y eso cambiaría la base de la alarma**: hoy se
# apoya en que un 0 con palabras conocidas es anómalo, y un AND estricto lo vuelve la respuesta
# correcta para cualquier consulta de dos palabras que no coincidan en un producto.
# Van AL FINAL de la lista a propósito, para no correr las posiciones de las 40 consultas
# anteriores: las capturas viejas se siguen leyendo por índice sin desalinearse.
DOS_CONOCIDAS = ["Shampoo Yerba", "Detergente Leche"]

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
    return out + SOLO_INVENTADAS + DOS_CONOCIDAS


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


def _es_vacio(brazo, ronda, registros, total, total_previo):
    """
    La regla original: 0 registros. Un 200 con registros no tenía nada que diagnosticar.

    ✏️ **24/09 — sin la cláusula de `semantica`.** Hasta hoy los 0 de ese brazo se guardaban solo en
    la ronda 1, porque "son el resultado esperado del brazo, no una anomalía, y con una muestra de la
    forma alcanza". **Medido contra la captura de las 12 rondas, la segunda mitad de esa frase es
    falsa:** `zzqxw` dio TRES sha1 distintos (`2894441b044a` ×6, `997bf7410110` ×5, `6dd93b053273`
    ×1) y `frbnt zzqxw` otros tres, **todos con el mismo byte count exacto** (172.685 y 172.757 B).
    Adentro del vacío legítimo rota algo de largo fijo, y una sola ronda lo esconde. Guardarlos
    todos cuesta +674 KB sobre la corrida entera, que es barato para ver qué rota.
    """
    return registros == 0


def _es_parcial(brazo, ronda, registros, total, total_previo):
    """
    Endeca declara N y la página trae menos de los 24 que dice servir. **Es el caso de Huevos**:
    12 registros contra `totalNumRecs` 171, en las 12 rondas del 24/09, y el único de la captura.

    Sin el body no se puede separar "hay 12 y punto" de "hay 24 agregados y `_parsear` se queda con
    `hijos[0]` de cada uno" —o de "están repartidos en dos contenedores y `_hallar_records` corta en
    el primero—. El peso del body dice que no hay 24 registros escondidos, pero no alcanza para
    cerrar cuál de las tres es. Por eso hay que guardarlo.

    `min` y no `total` a secas: una consulta con 5 resultados trae 5 y eso es completo, no parcial.
    """
    if registros is None or total is None:
        return False
    return registros < min(NRPP_DEFECTO, total)


def _es_derrumbe(brazo, ronda, registros, total, total_previo):
    """
    El total se cae a menos de la mitad del de la ronda anterior de la MISMA consulta. Es el caso de
    Pollo entero en la r9 del 24/09: 2 registros y `totalNumRecs` 2, contra 243 en la r8 y 245 en la
    r10 — una respuesta degradada que NO está vacía, así que ninguna regla de 0 la ve.

    Con None de cualquiera de los dos lados no compara: "la ronda anterior no declaró total" no es
    una caída, es otra cosa, y tratarla como caída marcaría toda la ronda siguiente a un degradado.
    Es justo lo que pasa en la r9 del 24/09 detrás de los cuatro `total=?` de la r8.

    ⚠️ Esto guarda un body, no levanta una alarma. El catálogo se mueve solo: en la r9/r10 del 24/09
    ocho consultas cambiaron de total sin que pasara nada (la mayor, 350→349). El 50 % está elegido
    para no verlas y la única que lo cruzó fue Pollo entero.
    """
    if total is None or total_previo is None or total_previo <= 0:
        return False
    return total < 0.5 * total_previo


# Las reglas, en orden y con nombre. El motivo queda anotado en la captura porque son diagnósticos
# DISTINTOS y después hay que poder separarlos. Es una tupla de pares y no tres `if` encadenados
# para que las mutaciones puedan sacar una regla de verdad —quitando su entrada— en vez de filtrar
# el resultado, que no es lo mismo: filtrar deja a la medición sin poder caer en las que siguen.
REGLAS = (("vacio", _es_vacio), ("parcial", _es_parcial), ("derrumbe", _es_derrumbe))


def motivo_guardado(brazo, ronda, registros, total, total_previo):
    """
    Por qué se guarda este body crudo, o None si no se guarda.

    Uno pesa entre 1,5 KB (la forma anómala, que ni arma la página) y ~390 KB, así que guardarlos
    todos son 8 MB para contestar una pregunta que es de FORMA, no de volumen.
    """
    for nombre, predicado in REGLAS:
        if predicado(brazo, ronda, registros, total, total_previo):
            return nombre
    return None


def una_ronda(n, n_rondas, cap, pausa, previos):
    """
    `previos` es {consulta: último `totalNumRecs` visto}, y lo mantiene el llamador entre rondas:
    la regla `derrumbe` compara contra la ronda anterior de la MISMA consulta.
    """
    for brazo, consultas in brazos():
        for q in consultas:
            fila, crudo = pedir(q)
            fila["ronda"], fila["brazo"] = n, brazo
            motivo = motivo_guardado(brazo, n, fila.get("registros"), fila.get("total"),
                                     previos.get(q))
            if motivo:
                fila["motivo_guardado"] = motivo
                cap["__motivos"][motivo] = cap["__motivos"].get(motivo, 0) + 1
            # Se anota SIEMPRE, incluso None: "la ronda anterior no declaró total" tiene que poder
            # distinguirse de "no hubo ronda anterior", y las dos frenan la comparación.
            previos[q] = fila.get("total")
            if crudo is not None:
                # El hash va SIEMPRE que haya body: sirve para comparar si el vacío de una misma
                # consulta es idéntico entre rondas, que es la pregunta de la intermitencia.
                h = hashlib.sha1(crudo.encode("utf-8", "replace")).hexdigest()[:12]
                fila["body_sha1"], fila["body_bytes"] = h, len(crudo)
                if motivo:
                    cap["__formas"][h] = cap["__formas"].get(h, 0) + 1
                    if len(cap["bodies"]) < MAX_BODIES and h not in cap["__guardados"]:
                        cap["__guardados"].append(h)
                        cap["bodies"].append({"ronda": n, "brazo": brazo, "consulta": q,
                                              "sha1": h, "motivo": motivo, "body": crudo})
            cap["mediciones"].append(fila)
            if "error" in fila:
                marca = f"ERROR {fila['error'][:40]}"
            else:
                marca = (f"reg={fila['registros']:>3} of={fila['ofertas']:>3} "
                         f"total={fila['total'] if fila['total'] is not None else '?':>5}")
            sello = f"  ⟵ {motivo}" if motivo else ""
            print(f"  [r{n}/{n_rondas}] {brazo:<10} {q[:32]:<32} {marca}  {fila['ms']:>5} ms{sello}",
                  flush=True)
            time.sleep(pausa)


def estado_git():
    """
    Commit de `sepa_backend` y si el árbol estaba sucio, para la cabecera de la captura.

    Sin esto, una captura de 6 horas no dice contra qué código se corrió, y ya hubo una corrida
    cuyo archivo hubo que fechar a mano contra el `git log`. `git` es local: no sale a la red.
    """
    def git(*args):
        return subprocess.run(["git", "-C", str(RAIZ), *args],
                              capture_output=True, text=True, timeout=10)
    try:
        head = git("rev-parse", "HEAD")
        if head.returncode != 0:
            return {"error": (head.stderr or "git rev-parse falló").strip()[:120]}
        sucio = git("status", "--porcelain")
        archivos = [ln for ln in sucio.stdout.splitlines() if ln.strip()]
        return {"commit": head.stdout.strip(),
                "sucio": bool(archivos),
                "archivos_sucios": archivos[:40]}
    except Exception as e:                                   # noqa: BLE001 — informativo, no crítico
        return {"error": f"{type(e).__name__}: {str(e)[:120]}"}


def hora_hhmm(txt):
    """`HH:MM` para argparse. Devuelve (hora, minuto) y explica el error en vez de tirar traceback."""
    try:
        h, m = txt.split(":")
        h, m = int(h), int(m)
        if not (0 <= h <= 23 and 0 <= m <= 59):
            raise ValueError
    except ValueError:
        raise argparse.ArgumentTypeError(f"--inicio espera HH:MM entre 00:00 y 23:59, no {txt!r}")
    return h, m


def esperar_hasta(hm):
    """
    Duerme hasta la próxima HH:MM local. Si esa hora ya pasó hoy, es la de mañana.

    Existe para poder dejar la corrida lanzada y que arranque sola a las 06:30, sin que nadie tenga
    que estar despierto. El nombre del archivo lo decide la ronda 1, no el lanzamiento.
    """
    h, m = hm
    ahora = datetime.now()
    objetivo = ahora.replace(hour=h, minute=m, second=0, microsecond=0)
    if objetivo <= ahora:
        objetivo += timedelta(days=1)
    espera = (objetivo - ahora).total_seconds()
    print(f"esperando hasta {objetivo:%Y-%m-%d %H:%M} para la ronda 1 "
          f"({espera / 3600:.2f} h desde ahora, {ahora:%H:%M:%S})\n", flush=True)
    time.sleep(espera)


def replay(ruta):
    """
    Aplica la regla de guardado de HOY a una captura YA HECHA, sin red. El oráculo del ajuste.

    La captura del 24/09 se corrió con la regla vieja (solo los 0) pero anotó `registros`, `total`,
    `body_sha1` y `body_bytes` de las 480 mediciones, así que alcanza para decir exactamente qué
    habría guardado la regla nueva. Recorre en el orden en que se midió, que es como corre en vivo:
    así `total_previo` sale de la ronda anterior real y no de una reconstrucción.
    """
    cap = json.loads(Path(ruta).read_text(encoding="utf-8"))
    ms = cap["mediciones"]
    previos, marcadas = {}, []
    for f in ms:
        motivo = motivo_guardado(f["brazo"], f["ronda"], f.get("registros"),
                                 f.get("total"), previos.get(f["consulta"]))
        previos[f["consulta"]] = f.get("total")
        if motivo:
            marcadas.append((motivo, f))

    print("=" * 104)
    print(f"REPLAY de la regla de guardado sobre {Path(ruta).name}")
    print(f"  {len(ms)} mediciones · {cap['__rondas']} rondas · reglas activas: "
          f"{', '.join(n for n, _ in REGLAS)}")
    print("=" * 104)

    por_motivo = {}
    for motivo, f in marcadas:
        por_motivo.setdefault(motivo, []).append(f)
    print(f"\n  {'motivo':<12} {'marcadas':>9}  consultas")
    for nombre, _p in REGLAS:
        fs = por_motivo.get(nombre, [])
        qs = sorted({f["consulta"] for f in fs})
        print(f"  {nombre:<12} {len(fs):>9}  {', '.join(qs) if qs else '—'}")
    print(f"  {'TOTAL':<12} {len(marcadas):>9}")

    print(f"\n  detalle ({len(marcadas)} mediciones marcadas):")
    print(f"    {'motivo':<10} {'r':>3} {'brazo':<10} {'consulta':<24} {'reg':>4} {'total':>6} "
          f"{'prev':>6} {'sha1':>14}")
    previos = {}
    for f in ms:                                             # segunda pasada, solo para imprimir `prev`
        antes = previos.get(f["consulta"])
        previos[f["consulta"]] = f.get("total")
        m = motivo_guardado(f["brazo"], f["ronda"], f.get("registros"), f.get("total"), antes)
        if not m:
            continue
        t = f.get("total")
        print(f"    {m:<10} {f['ronda']:>3} {f['brazo']:<10} {f['consulta'][:24]:<24} "
              f"{str(f.get('registros')):>4} {(str(t) if t is not None else '?'):>6} "
              f"{(str(antes) if antes is not None else '—'):>6} {f.get('body_sha1', '—'):>14}")

    # Lo que de verdad entraría al archivo: la dedup por hash y el tope, que son otra capa.
    guardados, bytes_tot = [], 0
    for motivo, f in marcadas:
        h = f.get("body_sha1")
        if h and h not in guardados and len(guardados) < MAX_BODIES:
            guardados.append(h)
            bytes_tot += f.get("body_bytes") or 0
    print(f"\n  marcadas por la regla : {len(marcadas)}")
    print(f"  bodies escritos       : {len(guardados)} (dedup por sha1, tope MAX_BODIES={MAX_BODIES})"
          f" ≈ {bytes_tot / 1024:,.0f} KB")
    return marcadas


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
    ap.add_argument("--inicio", type=hora_hhmm, default=None, metavar="HH:MM",
                    help="espera hasta esa hora local antes de la ronda 1 (si ya pasó, es mañana)")
    ap.add_argument("--replay", default=None, metavar="CAPTURA",
                    help="SIN RED: aplica la regla de guardado a una captura ya hecha y sale")
    args = ap.parse_args()

    if args.replay:
        replay(args.replay)
        return

    # Antes de dormir hasta `--inicio`, no después: si el árbol se toca durante la espera, lo que
    # queda anotado es el código con el que se lanzó, que es lo que se quiere saber.
    git = estado_git()

    if args.inicio:
        esperar_hasta(args.inicio)

    n_consultas = sum(len(c) for _b, c in brazos())
    # `__fecha` y el nombre del archivo salen de ACÁ, ya pasada la espera: son la hora de la ronda 1
    # y no la del lanzamiento. Una corrida lanzada a las 22:00 con `--inicio 06:30` es la captura de
    # las 06:30.
    arranque = datetime.now()
    cap = {"__fecha": arranque.isoformat(timespec="seconds"),
           "__origen": "red real desde la máquina que corrió tests/sonda_coto.py — UN SOLO punto",
           "__request": "el de FuenteCoto.buscar: Ntt=<q>&format=json, sin Nrpp (el de producción)",
           "__hipotesis": ("toda consulta de la canasta tiene palabras conocidas, así que un 0 en la "
                           "canasta es anómalo sea cual sea el catálogo"),
           "__git": git,
           "__reglas_guardado": [n for n, _p in REGLAS],
           "__rondas": args.rondas, "__intervalo_min": args.intervalo, "__pausa_s": args.pausa,
           "__inicio_pedido": f"{args.inicio[0]:02d}:{args.inicio[1]:02d}" if args.inicio else None,
           "__brazos": {b: c for b, c in brazos()},
           "__prueba": args.rondas < RONDAS_REALES,
           "__formas": {}, "__motivos": {}, "__guardados": [], "mediciones": [], "bodies": []}

    # Siempre a `tests/capturas/`, nunca a un temporal: una corrida real son 6 horas de red y
    # perderla por escribir en un directorio efímero es el error caro. Las de prueba llevan
    # `_prueba` en el nombre para que se distingan de un vistazo y las borre quien corresponda —
    # la sonda NO borra capturas, ni las suyas.
    marca = "_prueba" if cap["__prueba"] else ""
    ruta = Path(args.salida) if args.salida else (
        RAIZ / "tests" / "capturas" / f"sonda_coto{marca}_{arranque:%Y-%m-%d_%H%M}.json")
    ruta.parent.mkdir(parents=True, exist_ok=True)

    print(f"{n_consultas} consultas × {args.rondas} ronda(s) = {n_consultas * args.rondas} requests, "
          f"1 cada {args.pausa} s, rondas cada {args.intervalo} min")
    if git.get("commit"):
        print(f"sepa_backend {git['commit']}" + ("  ⚠️ árbol SUCIO" if git["sucio"] else "  (árbol limpio)"))
    else:
        print(f"⚠️ sin estado de git: {git.get('error')}")
    print(f"escribiendo a {ruta.name} al final de cada ronda\n")
    previos: dict = {}
    for n in range(1, args.rondas + 1):
        una_ronda(n, args.rondas, cap, args.pausa, previos)
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
          f"({bytes_bodies / 1024:,.0f} KB) · formas distintas guardadas: {len(cap['__formas'])}")
    print(f"  motivos de guardado: "
          + (", ".join(f"{k}={v}" for k, v in sorted(cap["__motivos"].items())) or "ninguno"))
    if len(cap["bodies"]) >= MAX_BODIES:
        print(f"  ⚠️ se alcanzó MAX_BODIES={MAX_BODIES}: puede haber formas sin guardar.")
    if args.rondas == 1:
        # ✏️ 24/09: la proyección ya no puede decir "los bodies no se repiten". Sacada la cláusula de
        # `semantica`, cada ronda puede aportar formas nuevas —los vacíos legítimos rotaron 3 sha1 en
        # 12 rondas—, así que el piso es el de una ronda y el techo, `MAX_BODIES`. El replay de la
        # captura del 24/09 da la referencia real: 42 marcadas → 17 bodies ≈ 2,5 MB.
        meta_kb = kb - bytes_bodies / 1024
        print(f"  proyección a 12 rondas: entre ~{(meta_kb * 12 + bytes_bodies / 1024) / 1024:,.1f} MB "
              f"(si no aparece ninguna forma nueva) y ~{(meta_kb * 12) / 1024 + MAX_BODIES * 0.19:,.1f} MB "
              f"(con los {MAX_BODIES} bodies del tope) · {meta_kb:,.0f} KB de mediciones × 12")


if __name__ == "__main__":
    main_cli()
