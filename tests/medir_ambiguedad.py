"""
Tarea 19, paso 3 — ¿qué cuesta la regla de ambigüedad? Instrumento de MEDICIÓN, fuera del CI.

La regla (`main._forma_ambigua`): un título que declara peso o volumen Y un contable ("Pack x 4
380 Gr.", "95g 4u", "473 CC 6 Unidades") no publica métrica, porque no se puede decidir si la
medida es la de una unidad o la del total. Salvo el "N x M" pelado ("2x1.75L"). Decisión de
Santiago, 13/09. Esto mide su costo POR CADENA, sobre los MISMOS productos (una captura):

  - filas que pierden métrica, y de esas cuáles hoy estaban bien;
  - representantes que quedan sin métrica;
  - fichas de la canasta por defecto: filas que pierden el % o caen a `unico`;
  - el papel higiénico antes y después del fix (§0).

Tres parsers:
  hoy     `_extraer_cantidades_desc` de a71c83d, cargado de git en memoria (sin tocar el disco)
  metros  el working tree SIN la regla: el fix de longitud solo
  regla   el working tree CON la regla (desde el 14/09 está cableada: es producción)

"Hoy estaba bien" sale de UNA sola fuente y se rotula así: el cociente activePrice/referencePrice
de Coto, de la fila o de su gemela por EAN. Se validó por rubro en la Tarea 19 (volumen 16/16,
peso 18/19 contra títulos de una sola medida), pero el EAN no identifica el envase: una lata suelta
puede llevar el EAN del pack. Sin cociente, la fila se lista para leerla a mano; no se adivina.

Desde sepa_backend/:
    venv\\Scripts\\python.exe -m tests.medir_ambiguedad --guardar tests/capturas/canasta_default_AAAA-MM-DD.json
    venv\\Scripts\\python.exe -m tests.medir_ambiguedad --desde tests/capturas/canasta_default_AAAA-MM-DD.json [--sepa X.parquet]
    venv\\Scripts\\python.exe -m tests.medir_ambiguedad --consultas "Papel higienico" --solo-papel --guardar papel.json

Salida: 0 medición completa · 1 el instrumento no reproduce lo que dice medir · 2 captura incompleta.
"""
import argparse
import json
import subprocess
import sys
import types
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import main
from fuentes import FuenteVTEX
from precios_vtex import _precio_por_100u, buscar_precios_online
from tests import medir_metrica_vivo as M
from tests.medir_regresion import FIXTURES, ofertas_de_fixture

RAIZ = Path(__file__).resolve().parents[1]
REF_ANTES = "a71c83d"
CAPTURA_13_09 = RAIZ / "tests" / "capturas" / "metrica_vivo_2026-09-13.json"
CADENAS = M.CADENAS
CANASTA = main.CANASTA_DEFAULT
EXTRAS = [i for i in M.ITEMS if i["nombre"] not in {p["nombre"] for p in CANASTA}]
CONSULTAS = [p["nombre"] for p in CANASTA] + [i["nombre"] for i in EXTRAS]
PAPEL = next(p for p in CANASTA if p["nombre"] == "Papel higienico")
PARSERS = ("hoy", "metros", "regla")


class InstrumentoInvalido(Exception):
    pass


# ─────────────────────────────────────────────────────────────────
#  PARSERS Y CAPTURA
# ─────────────────────────────────────────────────────────────────

def cargar_parsers():
    src = subprocess.check_output(["git", "show", f"{REF_ANTES}:main.py"], cwd=RAIZ)
    viejo = types.ModuleType(f"main_{REF_ANTES}")
    exec(compile(src.decode("utf-8"), f"main_{REF_ANTES}.py", "exec"), viejo.__dict__)
    ambigua = main._forma_ambigua

    def metros(desc):
        main._forma_ambigua = lambda _d: False      # la regla está cableada: se apaga acá
        try:
            return main._extraer_cantidades_desc(desc)
        finally:
            main._forma_ambigua = ambigua

    def regla(desc):
        return [] if ambigua(desc) else main._extraer_cantidades_desc(desc)

    return {"hoy": viejo._extraer_cantidades_desc, "metros": metros, "regla": regla}


def capturar(consultas):
    """Mismo request que producción: `FuenteVTEX._pedir` y el de `FuenteCoto.buscar`."""
    cap = {"__fecha": datetime.now().isoformat(timespec="seconds"),
           "__origen": "red real desde la máquina que corrió el script",
           "__consultas": consultas, "datos": {c: {} for c in CADENAS}, "fallas": {}}
    vtex = FuenteVTEX()
    for q in consultas:
        with ThreadPoolExecutor(max_workers=len(CADENAS)) as pool:
            futs = {c: pool.submit(vtex._pedir, c, q) for c in M.VTEX}
            futs["Coto"] = pool.submit(M.pedir_coto, q)
            for c, fut in futs.items():
                try:
                    crudos = fut.result()
                except Exception as e:                   # noqa: BLE001 — se reporta
                    cap["fallas"][f"{c} / {q}"] = f"{type(e).__name__}: {str(e)[:120]}"
                    continue
                recortar = M.recortar_coto if c == "Coto" else M.recortar_vtex
                cap["datos"][c][q] = [recortar(r) for r in crudos]
    return cap


def distintas(filas):
    """{(cadena, consulta, producto, precio): fila}."""
    out = {}
    for c in CADENAS:
        for q, fs in filas[c].items():
            for f in fs:
                out.setdefault((c, q, f["of"].producto, f["of"].precio), f)
    return out


def metrica(of, extraer):
    return _precio_por_100u(of, extraer, main.normalizar)


def txt(pu):
    return "SIN MÉTRICA" if not pu else f"{pu['tipo']} {pu['cantidad_base']:g}{pu['unidad_base']}"


# ─────────────────────────────────────────────────────────────────
#  VALIDACIÓN — el instrumento mide lo que dice medir
# ─────────────────────────────────────────────────────────────────

def validar(P, filas_nuevas):
    """
    (a) "hoy" es a71c83d: en la captura del 13/09 da el total en 29 de 135 papeles N×M, el
        número medido esa noche con el código de producción.
    (b) "metros" no tiene la regla y "regla" solo QUITA métricas: nunca agrega ni cambia una.
    """
    errores = []
    cap = json.loads(CAPTURA_13_09.read_text(encoding="utf-8"))
    viejas = distintas(M.parsear(cap))
    ok = tot = 0
    for (_c, q, _t, _p), f in viejas.items():
        ref = f["ref"]
        if q == "Papel higienico" and f["of"].disponible and ref.get("n", 1) > 1 and ref.get("m"):
            tot += 1
            pu = metrica(f["of"], P["hoy"])
            ok += bool(pu and pu["tipo"] == "longitud" and M.cerca(pu["cantidad_base"], ref["base"], M.TOL))
    if (ok, tot) != (29, 135):
        errores.append(f"(a) 'hoy' da {ok}/{tot} papeles N×M en la captura del 13/09, no 29/135")
    for filas in (viejas, filas_nuevas):
        for k, f in filas.items():
            a, b = metrica(f["of"], P["metros"]), metrica(f["of"], P["regla"])
            if b is not None and b != a:
                errores.append(f"(b) la regla cambió una métrica en vez de quitarla: {k} {a} → {b}")
    if errores:
        raise InstrumentoInvalido("\n  ".join(errores[:10]))


# ─────────────────────────────────────────────────────────────────
#  EVIDENCIA Y FICHAS
# ─────────────────────────────────────────────────────────────────

def evidencia(f, pu_hoy, idx_coto):
    """Qué dice el cociente de Coto sobre la métrica de hoy. Una sola fuente: se rotula."""
    if f["c"] == "Coto":
        g, ambiguo = f, None
    else:
        g, ambiguo = M.gemela_coto(idx_coto, f["of"].ean)
    if ambiguo:
        return "sin cociente (EAN en varias filas de Coto)"
    if not g or not g["coto"]["cociente"] or pu_hoy["tipo"] not in M.COTO_A_BASE:
        return "sin cociente"
    base = g["coto"]["cociente"] * M.COTO_A_BASE[pu_hoy["tipo"]]
    veredicto = "BIEN" if M.cerca(pu_hoy["cantidad_base"], base, M.TOL_COTO) else "MAL"
    origen = "propio" if g is f else "gemela EAN"
    return f"{veredicto} según cociente {origen} ({base:g})"


def fichas(filas, extraer):
    precios, _ = buscar_precios_online(CANASTA + EXTRAS, CADENAS, M.FuenteCaptura(filas),
                                       main.normalizar, extraer)
    de_canasta = {k: v for k, v in precios.items() if k[1] in {p["nombre"] for p in CANASTA}}
    resultado = main._analizar(CANASTA, de_canasta, [], 0, None)
    promedios = main._promedios_por_producto(CANASTA, de_canasta)
    out = main._fichas(CANASTA, de_canasta, resultado, promedios, list(CADENAS))
    estados = {(fi["cadena"], d["producto"]): (d["estado"], d["delta_pct"])
               for fi in out for d in fi["detalle"]}
    return precios, estados, promedios


# ─────────────────────────────────────────────────────────────────
#  INFORME
# ─────────────────────────────────────────────────────────────────

def informe_papel(filas, P):
    print("\n" + "=" * 100)
    print(f"§0 — PAPEL HIGIÉNICO: hoy ({REF_ANTES}) → fix, mismos productos")
    print("=" * 100)
    reps = {p: buscar_precios_online([PAPEL], CADENAS, M.FuenteCaptura(filas), main.normalizar, P[p])[0]
            for p in ("hoy", "regla")}
    por_m = {"hoy": [], "regla": []}
    for c in CADENAS:
        h, r = reps["hoy"].get((c, PAPEL["nombre"])), reps["regla"].get((c, PAPEL["nombre"]))
        if not (h or r):
            print(f"  {c:<11} sin representante")
            continue
        mismo = bool(h and r and h["ean"] == r["ean"] and h["precio_min"] == r["precio_min"])
        linea = []
        for p, d in (("hoy", h), ("regla", r)):
            pu = (d or {}).get("precio_por_100u")
            if pu and pu["tipo"] == "longitud":
                por_m[p].append(pu["precio_base"])
            linea.append(f"{txt(pu):<16} {('$%.2f/m' % pu['precio_base']) if pu and pu['tipo'] == 'longitud' else '':<11}")
        pu_r = (r or {}).get("precio_por_100u") or (h or {}).get("precio_por_100u") or {}
        print(f"  {c:<11} ${(r or h)['precio_min']:>9,.2f}  {linea[0]} → {linea[1]} "
              f"{'' if mismo else '[OTRO PRODUCTO] '}{str(pu_r.get('desc_ganadora') or '')[:60]}")
    for p, vs in por_m.items():
        if len(vs) > 1:
            print(f"  dispersión $/m entre cadenas ({p}): {max(vs) / min(vs):.2f}× "
                  f"(${min(vs):.2f} a ${max(vs):.2f}, {len(vs)} cadenas con métrica)")
    print("\n  Títulos 'N u. × M' disponibles (referencia N×M): dan el total del paquete, hoy → fix")
    dist = distintas(filas)
    for c in CADENAS:
        fs = [f for k, f in dist.items() if k[0] == c and k[1] == PAPEL["nombre"] and f["of"].disponible
              and f["ref"].get("n", 1) > 1 and f["ref"].get("m")]
        ok = {p: sum(1 for f in fs if (lambda pu: bool(pu and pu["tipo"] == "longitud" and M.cerca(
            pu["cantidad_base"], f["ref"]["base"], M.TOL)))(metrica(f["of"], P[p]))) for p in ("hoy", "regla")}
        print(f"    {c:<11} N×M={len(fs):<4} hoy {ok['hoy']:<4} → fix {ok['regla']}")


def informe_filas(filas, P):
    print("\n" + "=" * 100)
    print("§1 — FILAS DISPONIBLES de la captura: cuántas tienen métrica, y qué pierde la regla")
    print("=" * 100)
    dist = distintas(filas)
    idx = M.indice_coto(filas)
    print(f"  {'cadena':<11} {'filas':>5} {'hoy':>5} {'metros':>7} {'regla':>6}   pierden por la regla: "
          f"{'total':>5} {'BIEN':>5} {'MAL':>5} {'s/coc':>6}")
    detalle = []
    for c in CADENAS:
        disp = [(k, f) for k, f in dist.items() if k[0] == c and f["of"].disponible]
        con = {p: sum(1 for _k, f in disp if metrica(f["of"], P[p])) for p in PARSERS}
        cnt = Counter()
        for k, f in disp:
            pu_m, pu_r = metrica(f["of"], P["metros"]), metrica(f["of"], P["regla"])
            if pu_m and not pu_r:
                pu_h = metrica(f["of"], P["hoy"])
                ev = evidencia(f, pu_h, idx) if pu_h else "hoy tampoco tenía"
                cnt["total"] += 1
                cnt["BIEN" if ev.startswith("BIEN") else "MAL" if ev.startswith("MAL") else "s/coc"] += 1
                detalle.append((c, k[1], f["of"].producto, txt(pu_h), ev))
        print(f"  {c:<11} {len(disp):>5} {con['hoy']:>5} {con['metros']:>7} {con['regla']:>6}   "
              f"{'':>20} {cnt['total']:>5} {cnt['BIEN']:>5} {cnt['MAL']:>5} {cnt['s/coc']:>6}")
    print("\n  DETALLE — filas disponibles que pierden métrica por la regla:")
    for c, q, t, h, ev in sorted(detalle):
        print(f"    {c:<10} {q[:16]:<16} hoy={h:<16} {ev:<38} {t[:80]}")


def informe_representantes(precios, consultas):
    print("\n" + "=" * 100)
    print("§2 — REPRESENTANTES de buscar_precios_online: hoy → regla")
    print("=" * 100)
    cambios = Counter()
    for nombre in consultas:
        for c in CADENAS:
            h, r = precios["hoy"].get((c, nombre)), precios["regla"].get((c, nombre))
            if not h and not r:
                continue
            mismo = bool(h and r and h["ean"] == r["ean"] and h["precio_min"] == r["precio_min"])
            ph, pr = (h or {}).get("precio_por_100u"), (r or {}).get("precio_por_100u")
            if mismo and bool(ph) == bool(pr) and txt(ph) == txt(pr):
                continue
            if not mismo:
                cambios[f"{c}: cambió el representante"] += 1
            elif ph and not pr:
                cambios[f"{c}: queda sin métrica"] += 1
            else:
                cambios[f"{c}: cambió la métrica"] += 1
            dh = (ph or {}).get("desc_ganadora") or (h and f"${h['precio_min']:,.0f}")
            dr = (pr or {}).get("desc_ganadora") or (r and f"${r['precio_min']:,.0f}")
            print(f"    {c:<10} {nombre[:18]:<18} {txt(ph):<18} → {txt(pr):<18} "
                  f"{'' if mismo else 'OTRO PRODUCTO '}{str(dh)[:45]} → {str(dr)[:45]}")
    print(f"\n  Resumen: {dict(cambios) or 'ningún cambio'}")


def informe_fichas(estados, promedios):
    print("\n" + "=" * 100)
    print("§3 — FICHAS de la canasta por defecto (20 productos): estado y % por fila")
    print("    'pierde %' = la fila tenía delta_pct y deja de tenerlo")
    print("=" * 100)
    for par in (("hoy", "regla"), ("metros", "regla"), ("hoy", "metros")):
        a, b = par
        cnt = Counter()
        lineas = []
        for clave in sorted(estados[a]):
            (ea, da), (eb, db) = estados[a][clave], estados[b].get(clave, (None, None))
            if (ea, da is None) == (eb, db is None):
                continue
            if da is not None and db is None:
                cnt[f"{clave[0]} pierde %"] += 1
            elif da is None and db is not None:
                cnt[f"{clave[0]} gana %"] += 1
            if eb == "unico" and ea != "unico":
                cnt[f"{clave[0]} cae a unico"] += 1
            lineas.append(f"      {clave[0]:<10} {clave[1]:<20} {ea}/{da} → {eb}/{db}")
        print(f"\n  {a} → {b}: {dict(cnt) or 'ninguna fila cambia de estado ni pierde el %'}")
        print("\n".join(lineas))
    print("\n  Promedio por producto (unidad · n_cadenas), solo donde cambia:")
    for p in CANASTA:
        n = p["nombre"]
        vs = [(promedios[x].get(n) or {}) for x in PARSERS]
        if len({(v.get("unidad_base"), v.get("n_cadenas")) for v in vs}) > 1:
            print("    " + f"{n:<20} " + " · ".join(
                f"{x}={v.get('unidad_base')}×{v.get('n_cadenas')}" for x, v in zip(PARSERS, vs)))


def informe_otros(P, sepa):
    print("\n" + "=" * 100)
    print("§4 — OTROS DATOS: filas con métrica por cadena, hoy / metros / regla")
    print("=" * 100)
    cap = json.loads(CAPTURA_13_09.read_text(encoding="utf-8"))
    viejas = distintas(M.parsear(cap))
    print("\n  Captura del 13/09 (7 consultas, filas disponibles):")
    for c in CADENAS:
        disp = [f for k, f in viejas.items() if k[0] == c and f["of"].disponible]
        print(f"    {c:<11} {len(disp):>4}  " + " / ".join(
            str(sum(1 for f in disp if metrica(f["of"], P[p]))) for p in PARSERS))
    print("\n  Fixtures del 09/09 (títulos):")
    for c, archivo in FIXTURES.items():
        ofs = [of for of in (ofertas_de_fixture(c, archivo) or []) if of.producto]
        print(f"    {c:<11} {len(ofs):>4}  " + " / ".join(
            str(sum(1 for of in ofs if metrica(of, P[p]))) for p in PARSERS))
    if sepa:
        import pyarrow.parquet as pq
        t = pq.read_table(sepa, columns=["_cadena", "_desc_norm"]).to_pydict()
        pares = sorted({(c, d or "") for c, d in zip(t["_cadena"], t["_desc_norm"])})
        print(f"\n  SEPA {Path(sepa).name} ({len(pares)} pares cadena/descripción; con métrica = alguna "
              f"cantidad extraída):")
        por = {}
        for c, d in pares:
            fila = por.setdefault(c, Counter())
            fila["n"] += 1
            for p in PARSERS:
                fila[p] += bool(P[p](d))
        for c, fila in sorted(por.items()):
            print(f"    {c:<22} {fila['n']:>6}  " + " / ".join(str(fila[p]) for p in PARSERS)
                  + f"   (la regla quita {fila['metros'] - fila['regla']})")


def main_cli():
    ap = argparse.ArgumentParser()
    ap.add_argument("--guardar", help="captura en vivo y la guarda en este archivo")
    ap.add_argument("--desde", help="re-analiza una captura guardada, sin red")
    ap.add_argument("--sepa", help="parquet del SEPA con columnas _cadena y _desc_norm (opcional)")
    ap.add_argument("--consultas", help="consultas a capturar, separadas por coma (default: todas)")
    ap.add_argument("--solo-papel", action="store_true", help="informa solo §0, el papel antes/después")
    args = ap.parse_args()

    if args.desde:
        cap = json.loads(Path(args.desde).read_text(encoding="utf-8"))
    else:
        consultas = [q.strip() for q in args.consultas.split(",")] if args.consultas else CONSULTAS
        cap = capturar(consultas)
        if args.guardar:
            Path(args.guardar).write_text(json.dumps(cap, ensure_ascii=False), encoding="utf-8")
    consultas = cap.get("__consultas") or CONSULTAS

    P = cargar_parsers()
    filas = M.parsear(cap)
    try:
        validar(P, distintas(filas))
    except InstrumentoInvalido as e:
        print(f"ERROR (InstrumentoInvalido):\n  {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Captura: {cap['__fecha']} · {cap['__origen']}")
    print(f"Instrumento validado: 'hoy' = {REF_ANTES} (29/135 papeles en la captura del 13/09) · "
          f"la regla solo quita métricas")
    for c in CADENAS:
        print(f"  {c:<11} " + " · ".join(f"{q.split()[0][:7]} {len(filas[c][q])}" if q in filas[c]
                                         else f"{q.split()[0][:7]} FALLÓ" for q in consultas))

    if PAPEL["nombre"] in consultas:
        informe_papel(filas, P)
    if not args.solo_papel:
        precios, estados, promedios = {}, {}, {}
        for p in PARSERS:
            precios[p], estados[p], promedios[p] = fichas(filas, P[p])
        informe_filas(filas, P)
        informe_representantes(precios, consultas)
        informe_fichas(estados, promedios)
        informe_otros(P, args.sepa)

    if cap["fallas"]:
        print(f"\nMEDICIÓN INCOMPLETA: {len(cap['fallas'])} consultas fallaron: {cap['fallas']}",
              file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main_cli()
