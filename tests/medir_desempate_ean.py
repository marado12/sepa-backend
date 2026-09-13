"""
Tarea 19, puntos 2 y 3 — cuánto decide el desempate del matcher y cuánto sirve el EAN.

Instrumento de MEDICIÓN, no guard: no está en el CI a propósito. Sin red. Corre contra
las 5 fixtures reales de tests/fixtures/ con el parser, el scoring y el pipeline de
producción (`FuenteVTEX._a_oferta`, `FuenteCoto._parsear`, `precios_vtex.puntuar`,
`precios_vtex._precio_por_100u`, `buscar_precios_online`) sin modificarlos.

Todo sale desagregado POR CADENA: un número global o medido en una sola cadena no cuenta
como medición (CLAUDE.md, regla de las constantes derivadas de datos).

Punto 2 — el desempate (`precios_vtex.py:263`, hallazgo 15):
    (s, -of.precio) > (actual[0], -actual[1].precio)
  `of.precio` es precio de ENVASE. Para cada (cadena, consulta) se agrupan los candidatos
  aceptados por score. Baldes que no se colapsan: empate medible, empate sin métrica
  (algún empatado sin `precio_base`) y sin empate. Se agrega "tipos mixtos": todos tienen
  métrica pero de tipos distintos ($/u contra $/m), que tampoco se compara por unidad.
  Dos filas: empate exacto (el score ya viene redondeado a 3 decimales desde `puntuar`,
  así que es el mismo empate que ve el matcher) y cuasi-empate (|Δscore| < 0.01 respecto
  del ganador; AGREGADO A LA SPEC).

Punto 3 — EAN (hallazgo 16): presencia del campo en la captura, cobertura, validez
  formal, duplicados por cadena, y en cuántas consultas los representantes de 2+ cadenas
  comparten EAN. AGREGADO A LA SPEC: para un mismo EAN visto en varias cadenas, si la
  métrica que extrae el backend coincide — es la única verificación de la extracción de
  cantidades que no depende de leer el título de una sola cadena.

Límite que este script no puede saltar: las fixtures VTEX no traen `measurementUnit` ni
`unitMultiplier`, así que toda métrica sale del título. En producción VTEX usa esos campos
primero; Coto no los tiene, así que para Coto la métrica de acá es la de producción.

Validación del instrumento — si falla, exit 1: el script está mal, no la medición previa.
  - El representante que recalcula este script es el mismo que da `buscar_precios_online`.
  - Reproduce el empate de Coto de la Tarea 20 (papel higiénico, score 0,714, $13.750,99
    contra $2.420,99, gana $2.420,99). Si se recapturan las fixtures (Tarea 7) y ese caso
    desaparece, hay que elegir otra referencia, no borrar el chequeo.

Falta una fixture, `__campos` o una consulta → error explícito y exit 1. Nunca se saltea.

Correr como módulo desde sepa_backend/:
    venv\\Scripts\\python.exe -m tests.medir_desempate_ean
"""
import json
import statistics
import sys
from collections import defaultdict

from fuentes import FuenteVTEX
from precios_vtex import UMBRAL_MATCH, _precio_por_100u, buscar_precios_online, puntuar
from tests.evaluar_matching import (CANASTA, FIX, FuenteFixture, a_producto_vtex,
                                    ofertas_coto_de_filas)
from main import _extraer_cantidades_desc, normalizar

FIXTURES = {
    "Carrefour": "carrefour.json",
    "Día": "dia.json",
    "Vea": "vea.json",
    "Chango Más": "changomas.json",
    "Coto": "coto_estructura_real.json",
}
CONSULTAS = [i["nombre"] for i in CANASTA]

# Nombre del campo EAN en `__campos` de cada captura. Los parsers que se reutilizan
# (`a_producto_vtex`, `a_producto_coto`) lo leen de la posición 2: si la captura lo
# declara en otra, el parseo estaría leyendo otra columna y la medición no vale.
CAMPO_EAN = {"Coto": "product.eanPrincipal"}          # el resto de las cadenas: "ean"
POS_EAN = 2

CUASI_MILESIMAS = 10          # |Δscore| < 0.01, en milésimas enteras: sin restas de float
TOLERANCIA_METRICA = 0.02     # dos cantidades base "coinciden" si difieren menos de 2%

REFERENCIA_T20 = {"cadena": "Coto", "consulta": "Papel higienico", "score": 0.714,
                  "precios": (13750.99, 2420.99), "gana": 2420.99}


class FixtureInvalida(Exception):
    pass


class InstrumentoInvalido(Exception):
    pass


# ─────────────────────────────────────────────────────────────────
#  CARGA
# ─────────────────────────────────────────────────────────────────

def cargar():
    """{cadena: {"raw": dict, "ofertas": {consulta: [Oferta]}}}. Falla fuerte."""
    out = {}
    for cadena, archivo in FIXTURES.items():
        p = FIX / archivo
        if not p.exists():
            raise FixtureInvalida(f"falta la fixture {p} ({cadena})")
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw.get("__campos"), list):
            raise FixtureInvalida(f"{archivo}: sin __campos, no se sabe qué columnas capturó")
        faltan = [q for q in CONSULTAS if q not in raw]
        if faltan:
            raise FixtureInvalida(f"{archivo}: faltan las consultas {faltan}")
        ofertas = {}
        for q in CONSULTAS:
            if cadena == "Coto":
                ofertas[q] = ofertas_coto_de_filas(raw[q])
            else:
                parseadas = (FuenteVTEX._a_oferta(cadena, a_producto_vtex(f)) for f in raw[q])
                ofertas[q] = [of for of in parseadas if of]
        out[cadena] = {"raw": raw, "ofertas": ofertas}
    return out


def metrica(of):
    return _precio_por_100u(of, _extraer_cantidades_desc, normalizar)


def indice_ean(datos):
    """{ean: {cadena: {"q", "pu", "of"}}} sobre todas las ofertas parseadas."""
    idx = defaultdict(dict)
    for c in FIXTURES:
        for q in CONSULTAS:
            for of in datos[c]["ofertas"][q]:
                if of.ean:
                    idx[of.ean.strip()].setdefault(c, {"q": q, "pu": metrica(of), "of": of})
    return idx


def txt_metrica(pu):
    if not pu:
        return "SIN MÉTRICA"
    return f"{pu['tipo']} {pu['cantidad_base']:g}{pu['unidad_base']}"


# ─────────────────────────────────────────────────────────────────
#  PUNTO 2 — DESEMPATE
# ─────────────────────────────────────────────────────────────────

def milesimas(s):
    return round(s * 1000)


def candidatos(item, ofertas):
    """Los mismos que mira `buscar_precios_online`, en el mismo orden."""
    vivas, aceptados = 0, []
    for of in ofertas:
        if not of.disponible or of.precio <= 0:
            continue
        vivas += 1
        s = puntuar(item, of, normalizar, _extraer_cantidades_desc)
        if s < UMBRAL_MATCH:
            continue
        aceptados.append({"s": s, "of": of, "pu": metrica(of)})
    return vivas, aceptados


def ganador(grupo):
    """Réplica literal del desempate de `buscar_precios_online` (precios_vtex.py:263)."""
    mejor = None
    for c in grupo:
        if mejor is None or (c["s"], -c["of"].precio) > (mejor["s"], -mejor["of"].precio):
            mejor = c
    return mejor


def clasificar(grupo, gan):
    if len(grupo) < 2:
        return {"balde": "sin_empate", "n": len(grupo)}
    sin = sum(1 for c in grupo if not c["pu"])
    if sin:
        return {"balde": "sin_metrica", "n": len(grupo), "sin": sin,
                "ganador_con_metrica": bool(gan["pu"])}
    tipos = sorted({c["pu"]["tipo"] for c in grupo})
    if len(tipos) > 1:
        return {"balde": "tipos_mixtos", "n": len(grupo), "tipos": tipos}
    bases = [c["pu"]["precio_base"] for c in grupo]
    distinto = max(bases) > min(bases) * (1 + 1e-9)
    return {"balde": "medible_distinto" if distinto else "medible_igual", "n": len(grupo),
            "ratio": gan["pu"]["precio_base"] / min(bases)}


def analizar(datos):
    """{cadena: {consulta: analisis}}"""
    out = {}
    for cadena in FIXTURES:
        out[cadena] = {}
        for item in CANASTA:
            q = item["nombre"]
            vivas, acc = candidatos(item, datos[cadena]["ofertas"][q])
            a = {"filas": len(datos[cadena]["raw"][q]),
                 "ofertas": len(datos[cadena]["ofertas"][q]),
                 "vivas": vivas, "acc": acc, "gan": None}
            out[cadena][q] = a
            if not acc:
                continue
            gan = ganador(acc)
            top = milesimas(gan["s"])
            a["gan"] = gan
            a["exacto"] = [c for c in acc if milesimas(c["s"]) == top]
            a["cuasi"] = [c for c in acc if top - milesimas(c["s"]) < CUASI_MILESIMAS]
            a["cls_exacto"] = clasificar(a["exacto"], gan)
            a["cls_cuasi"] = clasificar(a["cuasi"], gan)
            debajo = [milesimas(c["s"]) for c in acc if milesimas(c["s"]) < top]
            a["delta"] = top - max(debajo) if debajo else None
            niveles = defaultdict(list)
            for c in acc:
                niveles[milesimas(c["s"])].append(c)
            a["niveles"] = [clasificar(g, ganador(g)) for g in niveles.values() if len(g) > 1]
    return out


def validar(datos, analisis):
    """El instrumento tiene que reproducir el pipeline real y el caso de la Tarea 20."""
    fuente = FuenteFixture({c: datos[c]["ofertas"] for c in FIXTURES})
    precios, _ = buscar_precios_online(CANASTA, list(FIXTURES), fuente,
                                       normalizar, _extraer_cantidades_desc)
    errores = []
    for cadena in FIXTURES:
        for q in CONSULTAS:
            gan, real = analisis[cadena][q]["gan"], precios.get((cadena, q))
            if (gan is None) != (real is None):
                errores.append(f"{cadena}/{q}: script={gan is not None} pipeline={real is not None}")
            elif gan and (real["precio_min"] != gan["of"].precio or real["match_score"] != gan["s"]
                          or real["ean"] != gan["of"].ean):
                errores.append(f"{cadena}/{q}: script ${gan['of'].precio} s={gan['s']} "
                               f"≠ pipeline ${real['precio_min']} s={real['match_score']}")
    if errores:
        raise InstrumentoInvalido("representantes distintos del pipeline real:\n  "
                                  + "\n  ".join(errores))

    ref = REFERENCIA_T20
    a = analisis[ref["cadena"]][ref["consulta"]]
    empatados = {round(c["of"].precio, 2) for c in a.get("exacto", [])}
    ok = (a["gan"] and a["gan"]["s"] == ref["score"]
          and all(p in empatados for p in ref["precios"])
          and round(a["gan"]["of"].precio, 2) == ref["gana"])
    if not ok:
        raise InstrumentoInvalido(
            f"no reproduce el empate de la Tarea 20 en {ref['cadena']}/{ref['consulta']}: "
            f"ganador s={a['gan'] and a['gan']['s']} ${a['gan'] and a['gan']['of'].precio}, "
            f"precios empatados {sorted(empatados)}")
    return precios


def linea_candidato(c, gan):
    of, pu = c["of"], c["pu"]
    flecha = "→" if c is gan else " "
    if pu:
        met = (f"{pu['tipo']:<8} {pu['cantidad_base']:>7g}{pu['unidad_base']:<2} "
               f"pb={pu['precio_base']:<10.4f}")
    else:
        met = "SIN MÉTRICA"
    return (f"     {flecha} s={c['s']:.3f} ${of.precio:>10,.2f}  {met:<34} "
            f"ean={of.ean or '-':<14} {of.producto[:58]}")


def fmt_ratio(xs):
    if not xs:
        return "      -           -"
    return f"{max(xs):>7.4f}×  {statistics.median(xs):>7.4f}× (n={len(xs)})"


def informe_desempate(analisis, idx):
    print("\n" + "=" * 100)
    print(f"PUNTO 2 — DESEMPATE · UMBRAL_MATCH={UMBRAL_MATCH} · {len(CONSULTAS)} consultas por cadena")
    print("=" * 100)

    print("\nCandidatos por cadena (control contra la tabla de la Tarea 20: 311/430 aceptados)")
    tv = ta = 0
    for cadena in FIXTURES:
        v = sum(analisis[cadena][q]["vivas"] for q in CONSULTAS)
        a = sum(len(analisis[cadena][q]["acc"]) for q in CONSULTAS)
        tv, ta = tv + v, ta + a
        print(f"  {cadena:<11} vivas={v:<4} aceptados={a:<4} ({a / v * 100:.1f}%)")
    print(f"  {'TOTAL':<11} vivas={tv:<4} aceptados={ta}")

    for clave, titulo in (("cls_exacto", "EMPATE EXACTO (score idéntico a 3 decimales)"),
                          ("cls_cuasi", "CUASI-EMPATE |Δscore| < 0.01 con el ganador "
                                        "(incluye los exactos) — AGREGADO A LA SPEC")):
        print(f"\n{titulo}")
        print("  Grupo de arriba = el que decide el representante. Unidad: (cadena, consulta).")
        print(f"  {'cadena':<11} {'sin repr':>8} {'sin emp':>7} {'med pb≠':>7} {'med pb=':>7} "
              f"{'sin mét':>7} {'tip mix':>7}   ratio ganador/mejor por unidad: máx · mediana")
        tot = defaultdict(int)
        for cadena in FIXTURES:
            cnt = defaultdict(int)
            ratios = []
            for q in CONSULTAS:
                a = analisis[cadena][q]
                if not a["gan"]:
                    cnt["sin_repr"] += 1
                    continue
                cls = a[clave]
                cnt[cls["balde"]] += 1
                if "ratio" in cls:
                    ratios.append(cls["ratio"])
            for k, v in cnt.items():
                tot[k] += v
            print(f"  {cadena:<11} {cnt['sin_repr']:>8} {cnt['sin_empate']:>7} "
                  f"{cnt['medible_distinto']:>7} {cnt['medible_igual']:>7} {cnt['sin_metrica']:>7} "
                  f"{cnt['tipos_mixtos']:>7}   {fmt_ratio(ratios)}")
        print(f"  {'TOTAL/30':<11} {tot['sin_repr']:>8} {tot['sin_empate']:>7} "
              f"{tot['medible_distinto']:>7} {tot['medible_igual']:>7} {tot['sin_metrica']:>7} "
              f"{tot['tipos_mixtos']:>7}")

    print("\n  Δ en milésimas entre el score ganador y el siguiente score distinto aceptado")
    print("  ('—' = no hay otro nivel; 'sin repr' = sin representante):")
    for cadena in FIXTURES:
        partes = []
        for q in CONSULTAS:
            a = analisis[cadena][q]
            d = "sin repr" if not a["gan"] else ("—" if a["delta"] is None else a["delta"])
            partes.append(f"{q.split()[0]} {d}")
        print(f"  {cadena:<11} " + " · ".join(partes))

    print("\nEMPATES EXACTOS EN CUALQUIER NIVEL de los aceptados (no solo el que decide) — spec (c)")
    print("  Unidad: grupo de score con 2+ candidatos. Decide solo si ese nivel queda arriba.")
    print(f"  {'cadena':<11} {'grupos':>6} {'med pb≠':>7} {'med pb=':>7} {'sin mét':>7} {'tip mix':>7}")
    for cadena in FIXTURES:
        cnt = defaultdict(int)
        for q in CONSULTAS:
            for cls in analisis[cadena][q].get("niveles", []):
                cnt[cls["balde"]] += 1
                cnt["grupos"] += 1
        print(f"  {cadena:<11} {cnt['grupos']:>6} {cnt['medible_distinto']:>7} "
              f"{cnt['medible_igual']:>7} {cnt['sin_metrica']:>7} {cnt['tipos_mixtos']:>7}")

    print("\nDETALLE de cada (cadena, consulta) con empate o cuasi-empate arriba  (→ = representante)")
    print("  En los grupos no medibles se muestra qué métrica extrae OTRA cadena para el mismo EAN.")
    for cadena in FIXTURES:
        for q in CONSULTAS:
            a = analisis[cadena][q]
            if not a["gan"]:
                print(f"\n  {cadena} / {q}: SIN REPRESENTANTE — filas={a['filas']} "
                      f"ofertas={a['ofertas']} vivas={a['vivas']} aceptados=0")
                continue
            if a["cls_cuasi"]["balde"] == "sin_empate":
                continue
            ce, cc = a["cls_exacto"], a["cls_cuasi"]
            extra_e = f" ratio={ce['ratio']:.4f}×" if "ratio" in ce else ""
            extra_c = f" ratio={cc['ratio']:.4f}×" if "ratio" in cc else ""
            precios = [c["of"].precio for c in a["exacto"]]
            print(f"\n  {cadena} / {q}: exacto={ce['balde']} (n={ce['n']}){extra_e} · "
                  f"cuasi={cc['balde']} (n={cc['n']}){extra_c} · "
                  f"envase max/min en el exacto={max(precios) / min(precios):.2f}×")
            no_medible = cc["balde"] in ("sin_metrica", "tipos_mixtos")
            for c in sorted(a["cuasi"], key=lambda c: (-c["s"], c["of"].precio)):
                print(linea_candidato(c, a["gan"]))
                if not no_medible or not c["of"].ean:
                    continue
                for c2, v in idx.get(c["of"].ean.strip(), {}).items():
                    if c2 != cadena:
                        print(f"         mismo EAN en {c2:<11} {txt_metrica(v['pu']):<22} "
                              f"{v['of'].producto[:60]}")


# ─────────────────────────────────────────────────────────────────
#  PUNTO 3 — EAN
# ─────────────────────────────────────────────────────────────────

def checksum_gtin(codigo):
    """Dígito de control GTIN (el mismo algoritmo para EAN-8, UPC-A/GTIN-12 y EAN-13)."""
    cuerpo = [int(d) for d in codigo[:-1]]
    suma = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(cuerpo)))
    return (10 - suma % 10) % 10 == int(codigo[-1])


def tipo_ean(codigo):
    if not (codigo.isascii() and codigo.isdigit()):
        return "no_numerico"
    if len(codigo) in (8, 13):
        return "valido" if checksum_gtin(codigo) else "checksum_malo"
    if len(codigo) == 12 and checksum_gtin(codigo):
        return "gtin12"
    return f"largo_{len(codigo)}"


def clase_discrepancia(metricas):
    """Compara las métricas que extraen varias cadenas para un mismo EAN."""
    if all(m is None for m in metricas):
        return "ninguna_con_metrica"
    if any(m is None for m in metricas):
        return "alguna_sin_metrica"
    if len({m["tipo"] for m in metricas}) > 1:
        return "tipo_distinto"
    cant = [m["cantidad_base"] for m in metricas]
    if max(cant) > min(cant) * (1 + TOLERANCIA_METRICA):
        return "cantidad_distinta"
    return "coincide"


def iguales(m1, m2):
    return clase_discrepancia([m1, m2]) in ("coincide", "ninguna_con_metrica")


def informe_ean(datos, analisis, precios, idx):
    print("\n" + "=" * 100)
    print("PUNTO 3 — EAN")
    print("=" * 100)

    print("\nPresencia del campo en la captura (antes de contar nada)")
    ausentes = []
    for cadena, d in datos.items():
        campos = d["raw"]["__campos"]
        nombre = CAMPO_EAN.get(cadena, "ean")
        pos = campos.index(nombre) if nombre in campos else None
        estado = ("PRESENTE" if pos == POS_EAN else
                  "AUSENTE — la captura no lo incluyó: NO MEDIBLE, sube la Tarea 7" if pos is None else
                  f"EN OTRA POSICIÓN ({pos}): el parser lee la {POS_EAN}, NO MEDIBLE")
        if pos != POS_EAN:
            ausentes.append(cadena)
        print(f"  {cadena:<11} __campos[{POS_EAN}]={campos[POS_EAN]!r:<24} → {estado} · "
              f"fecha={d['raw'].get('__fecha')} · nota={d['raw'].get('__nota', '-')}")

    print("\nCobertura por cadena (filas crudas de la fixture, las 6 consultas)")
    print("  'pref 2' = EAN-13 que empieza con 2: número de circulación restringida (código de")
    print("  tienda, típico de pesables). Se cuenta con o sin checksum válido.")
    print(f"  {'cadena':<11} {'filas':>5} {'null':>4} {'vacío':>5} {'con EAN':>12} {'válido 8/13':>12} "
          f"{'gtin12':>6} {'otros':>5} {'pref 2':>12} │ {'dup mismo nombre':>16} {'dup nombre ≠':>12}"
          f" │ ofertas / con Oferta.ean")
    for cadena, d in datos.items():
        if cadena in ausentes:
            print(f"  {cadena:<11} NO MEDIBLE con esta fixture")
            continue
        n = nulos = vacios = pref2 = 0
        tipos = defaultdict(int)
        por_ean = defaultdict(list)
        for q in CONSULTAS:
            for fila in d["raw"][q]:
                n += 1
                e = fila[POS_EAN]
                if e is None:
                    nulos += 1
                    continue
                e = str(e).strip()
                if not e:
                    vacios += 1
                    continue
                tipos[tipo_ean(e)] += 1
                if len(e) == 13 and e.isdigit() and e.startswith("2"):
                    pref2 += 1
                por_ean[e].append((q, normalizar(str(fila[0]))))
        con = n - nulos - vacios
        dups = {e: v for e, v in por_ean.items() if len(v) > 1}
        distinto = {e: v for e, v in dups.items() if len({nom for _, nom in v}) > 1}
        mismo = len(dups) - len(distinto)
        otros = sum(v for k, v in tipos.items() if k not in ("valido", "gtin12"))
        ofs = [of for q in CONSULTAS for of in d["ofertas"][q]]
        print(f"  {cadena:<11} {n:>5} {nulos:>4} {vacios:>5} {con:>4} ({con / n * 100:5.1f}%) "
              f"{tipos['valido']:>4} ({tipos['valido'] / n * 100:5.1f}%) {tipos['gtin12']:>6} {otros:>5} "
              f"{pref2:>4} ({pref2 / n * 100:5.1f}%) │ {mismo:>16} {len(distinto):>12} │ "
              f"{len(ofs)} / {sum(1 for of in ofs if of.ean)}")
        raros = [(e, tipo_ean(e)) for e in por_ean if tipo_ean(e) != "valido"]
        if raros:
            print(f"  {'':11} no válidos: {raros}")
        for e, v in distinto.items():
            print(f"  {'':11} EAN {e} en productos con nombre distinto: {v}")
        for e, v in dups.items():
            if e not in distinto:
                print(f"  {'':11} EAN {e} repetido, mismo producto, en consultas {[q for q, _ in v]}")

    print("\nEANs distintos en común entre cada par de cadenas (fixture completa, todas las consultas)")
    eans = {c: {str(f[POS_EAN]).strip() for q in CONSULTAS for f in datos[c]["raw"][q] if f[POS_EAN]}
            for c in datos if c not in ausentes}
    cads = list(eans)
    print("  " + " " * 11 + "".join(f"{c[:10]:>11}" for c in cads))
    for a in cads:
        print(f"  {a:<11}" + "".join(f"{len(eans[a] & eans[b]):>11}" for b in cads))

    print("\nRepresentantes (los de buscar_precios_online): ¿2+ cadenas con el mismo EAN?")
    con_compartido = 0
    for q in CONSULTAS:
        reps = {c: precios[(c, q)]["ean"] for c in FIXTURES if (c, q) in precios}
        grupos = defaultdict(list)
        grupos_sin_ceros = defaultdict(list)
        for c, e in reps.items():
            if e:
                grupos[e.strip()].append(c)
                grupos_sin_ceros[e.strip().lstrip("0")].append(c)
        comp = {e: cs for e, cs in grupos.items() if len(cs) > 1}
        comp0 = {e: cs for e, cs in grupos_sin_ceros.items() if len(cs) > 1}
        con_compartido += bool(comp)
        faltan = [c for c in FIXTURES if (c, q) not in precios]
        print(f"  {q:<16} representantes={len(reps)} con EAN={sum(1 for e in reps.values() if e)} "
              f"sin representante={faltan or '-'} · compartidos={comp or 'ninguno'}"
              + (f" · sin ceros a la izquierda={comp0}" if len(comp0) != len(comp) else ""))
        for c, e in reps.items():
            of = analisis[c][q]["gan"]["of"]
            print(f"  {'':16}   {c:<11} ean={e or '-':<14} ${of.precio:>10,.2f}  {of.producto[:60]}")
    print(f"  → consultas con 2+ representantes que comparten EAN: {con_compartido} de {len(CONSULTAS)}")

    print("\nAGREGADO A LA SPEC — el mismo EAN entre cadenas, más allá del representante elegido")
    print("  Por consulta: EANs presentes en 2+ cadenas entre los ACEPTADOS (s >= umbral) y entre")
    print("  TODAS las filas de la fixture (tope 24 por búsqueda, solo disponibles en 3 cadenas).")
    for q in CONSULTAS:
        acc = defaultdict(dict)
        todas = defaultdict(set)
        for c in FIXTURES:
            if c in ausentes:
                continue
            for cand in analisis[c][q]["acc"]:
                if cand["of"].ean:
                    acc[cand["of"].ean.strip()][c] = cand
            for f in datos[c]["raw"][q]:
                if f[POS_EAN]:
                    todas[str(f[POS_EAN]).strip()].add(c)
        acc2 = {e: cs for e, cs in acc.items() if len(cs) > 1}
        todas2 = {e: cs for e, cs in todas.items() if len(cs) > 1}
        print(f"\n  {q}: EANs en 2+ cadenas → aceptados={len(acc2)} · todas las filas={len(todas2)}")
        for e, cs in sorted(acc2.items(), key=lambda t: -len(t[1])):
            partes = []
            for c, cand in cs.items():
                rep = analisis[c][q]["gan"]
                marca = "REPR" if rep is cand else f"repr s={rep['s']:.3f}"
                partes.append(f"{c} s={cand['s']:.3f} ${cand['of'].precio:,.0f} ({marca})")
            nombre = next(iter(cs.values()))["of"].producto[:40]
            print(f"    {e} {nombre:<40} " + " · ".join(partes))
        solo_filas = {e: cs for e, cs in todas2.items() if e not in acc2}
        if solo_filas:
            print(f"    en filas pero no aceptado en 2+: "
                  + ", ".join(f"{e}:{sorted(cs)}" for e, cs in list(solo_filas.items())[:8])
                  + (" …" if len(solo_filas) > 8 else ""))

    print("\nAGREGADO A LA SPEC — mismo EAN en 2+ cadenas: ¿el backend extrae la misma métrica?")
    print("  Métrica = (tipo, cantidad base) de `_precio_por_100u`, desde el TÍTULO (las fixtures")
    print(f"  no traen measurementUnit/unitMultiplier). Coinciden si difieren < {TOLERANCIA_METRICA:.0%}.")
    print("  Una cadena 'discrepa' en un EAN si su métrica no coincide con la de al menos otra.")
    print("  No se decide cuál cadena tiene razón: eso requiere leer los títulos.")
    compartidos = {e: cs for e, cs in idx.items() if len(cs) > 1}
    por_cadena = {c: defaultdict(int) for c in FIXTURES}
    por_consulta = defaultdict(lambda: defaultdict(int))
    discrepantes = []
    for e, cs in compartidos.items():
        clase = clase_discrepancia([v["pu"] for v in cs.values()])
        q = next(iter(cs.values()))["q"]
        por_consulta[q]["compartidos"] += 1
        por_consulta[q][clase] += 1
        for c, v in cs.items():
            por_cadena[c]["compartidos"] += 1
            if any(not iguales(v["pu"], w["pu"]) for c2, w in cs.items() if c2 != c):
                por_cadena[c]["discrepa"] += 1
        if clase not in ("coincide", "ninguna_con_metrica"):
            discrepantes.append((e, clase, cs))
    print(f"\n  {'cadena':<11} {'EANs compartidos':>16} {'discrepa':>14}")
    for c in FIXTURES:
        k = por_cadena[c]
        pct = k["discrepa"] / k["compartidos"] * 100 if k["compartidos"] else 0
        print(f"  {c:<11} {k['compartidos']:>16} {k['discrepa']:>5} ({pct:5.1f}%)")
    print(f"\n  {'consulta':<16} {'compart.':>8} {'coincide':>8} {'tipo ≠':>7} {'cant ≠':>7} "
          f"{'1 sin mét':>9} {'ninguna':>7}")
    for q in CONSULTAS:
        k = por_consulta[q]
        print(f"  {q:<16} {k['compartidos']:>8} {k['coincide']:>8} {k['tipo_distinto']:>7} "
              f"{k['cantidad_distinta']:>7} {k['alguna_sin_metrica']:>9} {k['ninguna_con_metrica']:>7}")
    print(f"\n  Detalle de los {len(discrepantes)} EANs con métrica distinta entre cadenas:")
    for e, clase, cs in discrepantes:
        print(f"    {e} [{next(iter(cs.values()))['q']}] {clase}")
        for c, v in cs.items():
            print(f"       {c:<11} {txt_metrica(v['pu']):<22} {v['of'].producto[:70]}")
    return ausentes


if __name__ == "__main__":
    try:
        datos = cargar()
        analisis = analizar(datos)
        precios = validar(datos, analisis)
    except (FixtureInvalida, InstrumentoInvalido) as e:
        print(f"ERROR ({type(e).__name__}): {e}", file=sys.stderr)
        sys.exit(1)
    print("Instrumento validado: representantes == buscar_precios_online · "
          "empate de la Tarea 20 reproducido (Coto, papel higiénico, 0.714, $13.750,99 vs $2.420,99)")
    idx = indice_ean(datos)
    informe_desempate(analisis, idx)
    ausentes = informe_ean(datos, analisis, precios, idx)
    if ausentes:
        print(f"\nMEDICIÓN INCOMPLETA: EAN no medible en {ausentes}", file=sys.stderr)
        sys.exit(3)
