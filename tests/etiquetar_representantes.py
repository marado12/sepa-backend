"""
Tarea 26 paso (e) / Tarea 22 — el pipeline de ETIQUETADO a ciegas de los candidatos, y la tabla de
correcciones que trajo la revisión (otra sesión de Claude, que Santiago aprobó y reenvió; ningún humano
revisó etiqueta por etiqueta). Sin red y sin tocar producción.

Las etiquetas son el patrón de oro de `tests/medir_representantes.py`: si no describen a los candidatos
de la captura, la medición mide otra cosa. Este módulo es cómo se producen, para que se puedan reproducir
y auditar sin leer el transcript de una sesión:

  1. `--lotes DIR --fuente F` arma los lotes A CIEGAS: cada archivo lleva el ítem, su criterio y las filas
     con `uid`, `titulo` y `marca`. **No lleva cadena, score, precio ni EAN**, porque si el etiquetador viera
     las mismas señales que después se miden, la medición sería circular.
  2. Las dos pasadas y la adjudicación las corrió la herramienta Workflow de Claude Code, con los prompts
     `PROMPT_ETIQUETAR` y `PROMPT_ADJUDICAR` de este archivo, verbatim (`--prompts` los imprime). Dos
     pasadas por lote con el orden de las filas invertido, y un adjudicador para los desacuerdos, los
     faltantes y los "dudoso" de las dos pasadas. ⚠️ Las dos pasadas y el adjudicador son el MISMO modelo:
     el acuerdo no es evidencia independiente, y la revisión que las corrigió también fue del mismo modelo.
  3. `--unir DIR --journal J --destino D` junta las etiquetas del journal del workflow en el archivo de
     etiquetas, con qué dijo cada pasada y de dónde salió la etiqueta final.
  4. `--correcciones` aplica `CORRECCIONES` —los cambios que trajo la revisión, con su evidencia— **por
     producto** (ítem + título + marca) en los dos archivos, no por uid: los uid son por ronda y se repiten.
     Reporta además las filas con el mismo marcador literal que NO están en la lista, sin cambiarlas:
     extenderlas es criterio, y el criterio lo decide Santiago. Correr el modo dos veces es no-op.

Desde sepa_backend/ (en Windows, con `$env:PYTHONIOENCODING='utf-8'`):
    venv\\Scripts\\python.exe -m tests.etiquetar_representantes --prompts
    venv\\Scripts\\python.exe -m tests.etiquetar_representantes --lotes DIR --fuente rechazos-vtex
    venv\\Scripts\\python.exe -m tests.etiquetar_representantes --unir DIR --journal J --destino 14sep --escribir
    venv\\Scripts\\python.exe -m tests.etiquetar_representantes --correcciones --escribir
"""
import argparse
import json
import logging
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import main
from precios_vtex import UMBRAL_MATCH, puntuar
from tests import medir_pesables as P
from tests import medir_representantes as MR
from tests.evaluar_matching import CANASTA as CANASTA_6
from tests.evaluar_matching import cargar as cargar_fixtures
from tests.medir_regresion import FIXTURES

RAIZ = MR.RAIZ
CADENAS, CANASTA, ITEM = MR.CADENAS, MR.CANASTA, MR.ITEM
CAPTURA_NUEVA = RAIZ / "tests" / "capturas" / "representantes_2026-09-22.json"
DESTINOS = {"14sep": MR.ETIQUETAS, "22sep": MR.ETIQUETAS_NUEVA}
LOTE = 70


# ─────────────────────────────────────────────────────────────────
#  LOS PROMPTS — verbatim, los que corrieron
# ─────────────────────────────────────────────────────────────────

PROMPT_ETIQUETAR = """Sos un etiquetador A CIEGAS para un comparador de precios de supermercados de Argentina.

Leé con la herramienta Read el archivo JSON:
{archivo}
Leé SOLO ese archivo. No abras ningún otro archivo, no busques en el disco (nada de Grep, Glob ni Bash) y no uses la red: tenés que decidir sin información de precios, cadenas ni códigos.

El archivo trae un ítem de la canasta ("item"), su "criterio" (qué cuenta como correcto, qué es otro producto, qué es dudoso) y "filas": {n} productos con "uid", "titulo" y "marca".

Para CADA fila, una etiqueta:
- "correcto": el producto ES el del ítem según el criterio. Una variante del mismo producto (otra marca, otro tamaño, sin sal, light, sin lactosa, larga vida, etc.) es correcta, salvo que el criterio la ponga en otra columna.
- "otro producto": es otra cosa. La columna "otro producto" del criterio da ejemplos; no es exhaustiva (un jabón de tocador para "Manteca" es otro producto aunque no figure).
- "dudoso": el criterio lo pone en la columna "dudoso", o el título y la marca no alcanzan para decidir.

Reglas:
- Decidí por el título, la marca y el criterio. Podés usar lo que sabés de marcas argentinas (qué fabrica una marca), pero nada de precios.
- El envase, el pack o la cantidad NO cambian la etiqueta: se etiqueta la identidad del producto, no el tamaño. Si hay pack (por ej. "x 3", "pack 18"), anotalo en "nota".
- "frase": la parte LITERAL del título que decide la etiqueta (por ej. "Mermelada", "Medialunas de manteca", "de Cerdo").
- "nota": opcional y breve: variante, pack, o por qué es dudoso.
- Devolvé exactamente {n} etiquetas, una por cada uid del archivo, copiando el uid tal cual. Sin omitir ni inventar uids."""

PROMPT_ADJUDICAR = """Sos el adjudicador del etiquetado de un comparador de precios de supermercados de Argentina, ítem "{item}".

Dos etiquetadores A CIEGAS (vieron solo el criterio, el título y la marca) no coincidieron, les faltó una etiqueta, o los dos dijeron "dudoso", en estas filas (uid → lo que dijo cada uno):
{filas}

Paso 1 — decidí por el título, la marca y el criterio. El título y la marca de cada uid están en el archivo que vieron los etiquetadores (leelo con Read):
{archivo}
El criterio del ítem es:
{criterio}

Paso 2 — SOLO si con eso no alcanza para una fila, podés usar EVIDENCIA EXTRA: leé con Read el archivo
{evidencia}
(clave = uid; trae en qué cadenas aparece, precio, precio por unidad, EAN, las características de Coto como "TIPO DE PRODUCTO", y otros títulos con el mismo EAN). Usalo solo para las filas de arriba. No abras otros archivos ni busques en el disco.

Reglas:
- Una variante del mismo producto (marca, tamaño, sin sal, light, sin lactosa) es "correcto". El envase/pack no cambia la etiqueta.
- Si el CRITERIO pone el caso en la columna "dudoso" (por ej. azúcar rubia, parboil, 2 en 1, picada sin especie en el título), queda "dudoso": no lo resuelvas.
- Si decidís por el título y el criterio, "evidencia_extra" = "" (vacío). Si usaste evidencia extra, "evidencia_extra" dice cuál y qué mostró (por ej. "Coto TIPO DE PRODUCTO: EDULCORANTE", "mismo EAN que 'Carne Picada Vacuna ...' en Día"). El precio solo no alcanza como evidencia de identidad.
- Si ni con evidencia extra se puede decidir, "dudoso", y en "nota" por qué.
- "frase": la parte literal del título que decide.
- Una etiqueta por cada uid de la lista de arriba ({n}), sin omitir ninguno."""

# La variante SIN evidencia extra: la usaron las tandas donde no hay evidencia armada (los candidatos de la
# captura del 22/09 que no unen con el 14/09, los rechazos de las VTEX y las fuentes profundas de Coto).
PROMPT_ADJUDICAR_SIN_EVIDENCIA = """Sos el adjudicador del etiquetado de un comparador de precios de supermercados de Argentina, ítem "{item}".

Dos etiquetadores A CIEGAS (vieron solo el criterio, el título y la marca) no coincidieron, les faltó una etiqueta, o los dos dijeron "dudoso", en estas filas:
{filas}

El título y la marca de cada uid están en el archivo que ellos vieron (leelo con Read):
{archivo}
El criterio del ítem es:
{criterio}

Reglas:
- Decidí por el título, la marca y el criterio. No hay evidencia extra disponible para esta tanda.
- Una variante del mismo producto (marca, tamaño, sin sal, light, sin lactosa) es "correcto". El envase/pack no cambia la etiqueta.
- Si el CRITERIO pone el caso en la columna "dudoso" (por ej. azúcar rubia, parboil, 2 en 1, picada sin especie en el título), queda "dudoso": no lo resuelvas.
- Si con el título y el criterio no se puede decidir, "dudoso", y en "nota" por qué.
- "frase": la parte literal del título que decide.
- Una etiqueta por cada uid de la lista de arriba ({n}), sin omitir ninguno."""


# ─────────────────────────────────────────────────────────────────
#  LAS CORRECCIONES DE SANTIAGO — 22/09, hechas ANTES de ver la medición
# ─────────────────────────────────────────────────────────────────

# ⚠️ **Se aplican por PRODUCTO (ítem + título), no por uid.** Los uid son por ronda de etiquetado y se
# repiten: al sumar los 170 rechazos de las VTEX, 171 de los 2.194 uid del archivo del 14/09 pasaron a
# apuntar a dos productos distintos, así que aplicar por uid le cambiaría la etiqueta a filas que nadie
# revisó (lo verificó la revisión del 23/09: "Crema de leche Carrefour Classic 200 ml" comparte uid con la
# leche de La Serenísima 2 %). El uid queda como PROCEDENCIA, en el comentario de cada fila.
# {uid original: (etiqueta nueva, evidencia)} — el título que le corresponde se resuelve una vez, contra el
# archivo del 14/09, y de ahí en más la corrección viaja por (ítem, título).
CORRECCIONES = {
    "Carne picada#15": ("correcto", "el criterio decía 'dudoso hasta tener la categoría': la categoría es "
                                   "'Bovinos' y dtoCaracteristicas dice CARNE VACUNA / PICADA"),
    "Carne picada#16": ("correcto", "ídem #15: categoría 'Bovinos', dtoCaracteristicas CARNE VACUNA / PICADA"),
    "Yerba mate#107": ("dudoso", "La Cumbrecita se vende como 'Clásica con hierbas'; con Adelgamate, "
                                 "Cachamate y Cachamai se usó conocimiento de marca para dudar"),
    "Yerba mate#25": ("dudoso", "Buen Día: mismo criterio que La Cumbrecita"),
    "Yerba mate#26": ("dudoso", "Buen Día: mismo criterio que La Cumbrecita"),
    "Azúcar#14": ("correcto", "Cuquets 500 g: $/100 g 138,2 contra 138,9 del común tipo A, o sea azúcar común"),
    "Leche entera#1": ("dudoso", "La Serenísima 2 %: el mismo caso que el azúcar light"),
    "Leche entera#23": ("dudoso", "La Serenísima 2 %: el mismo caso que el azúcar light"),
    "Leche entera#32": ("dudoso", "La Serenísima 2 %: el mismo caso que el azúcar light"),
    "Fideos spaghetti#29": ("dudoso", "sin TACC / de arroz, como el de maíz (#23)"),
    "Fideos spaghetti#30": ("dudoso", "sin TACC / de arroz, como el de maíz (#23)"),
    "Fideos spaghetti#31": ("dudoso", "sin TACC / de arroz, como el de maíz (#23)"),
    "Fideos spaghetti#57": ("dudoso", "sin TACC / de arroz, como el de maíz (#23)"),
    "Fideos spaghetti#74": ("dudoso", "sin TACC / de arroz, como el de maíz (#23)"),
}
# Inconsistencias por EAN: el mismo ítem y el mismo EAN cruzable con dos etiquetas distintas. Se documentan
# una por una porque **los títulos de las cadenas se contradicen entre sí**, y forzar una etiqueta mecánica
# pisaría una decisión con evidencia. Ninguna de estas filas es representante en ninguna de las dos capturas.
# La validación (5) exige este conjunto exacto: una inconsistencia nueva sale con exit 1.
INCONSISTENCIAS_EAN = {
    "Azúcar|7794940000536":
        "Día lo llama 'Endulzante Hileret Light' (otro producto) y Carrefour 'Azúcar Hileret light' (dudoso, "
        "como el resto de los 'azúcar light').",
    "Yerba mate|7790070509123":
        "el mismo EAN en 4 cadenas: en Vea el título dice 'Sabor Poleo' (dudoso por el criterio) y en "
        "Carrefour, Día y Chango Más es Chamigo común (correcto). O el título de Vea está mal cargado o el "
        "EAN está compartido.",
    "Fideos spaghetti|7790070336002":
        "Chango Más y Vea lo llaman 'Fideos Matarazzo Spaghetti 500g' (correcto) y Carrefour 'Fideos sin tacc "
        "spaghetti' / Coto 'Libre De Gluten' (dudoso desde la corrección del 22/09). Apareció al aplicar esa "
        "corrección: si el EAN contara como identidad, las cuatro serían la misma etiqueta.",
    "Harina 000|7792180139320":
        "el título de Vea omite el tipo ('Harina Cañuelas Ultra Refinada Vitamina D') y quedó dudoso; el mismo "
        "EAN en Día, Chango Más y Carrefour dice 000. Es un caso de FORMA DE TÍTULO donde el EAN resolvería la "
        "duda: la decisión de usarlo es de Santiago.",
}
FUENTE_CORRECCION = "corrección de la revisión del 22/09 (otra sesión de Claude, aprobada por Santiago)"
# El texto que llevaban estas filas antes del 23/09, para que `productos_corregidos` las siga reconociendo.
FUENTES_CORRECCION = (FUENTE_CORRECCION, "corrección de Santiago, 22/09")
# Marcadores literales por ítem: con estos se buscan las filas hermanas que quedaron fuera de la lista.
MARCADORES = {
    "Carne picada": ("picada especial", "picada desgrasada"),
    "Yerba mate": ("cumbrecita", "buen dia", "buen día"),
    "Azúcar": ("cuquets",),
    "Leche entera": ("2%", "2 %", "mas liviana", "más liviana"),
    "Fideos spaghetti": ("sin tacc", "de arroz", "arroz"),
}


# ─────────────────────────────────────────────────────────────────
#  LOTES A CIEGAS
# ─────────────────────────────────────────────────────────────────

def _criterio(item):
    ok, otro, dud = MR.CRITERIOS[item]
    return {"correcto": ok, "otro producto": otro, "dudoso": dud or "—"}


def _clave_texto(item, titulo, marca):
    return (item, re.sub(r"\s+", " ", (titulo or "").strip()).lower(),
            re.sub(r"\s+", " ", (marca or "").strip()).lower())


def escribir_lotes(pendientes, destino, ronda=""):
    """
    `pendientes`: [{item, titulo, marca, cid}] → lotes de {LOTE} filas por ítem, con los uid asignados por
    orden alfabético de título, y las dos copias (A y B, B con el orden invertido).
    `ronda` entra en el uid (`item@ronda#3`): sin eso los uid se repiten entre tandas y ya mordió una vez.
    """
    tag = f"@{ronda}" if ronda else ""
    destino.mkdir(parents=True, exist_ok=True)
    unicos = {}
    for x in pendientes:
        unicos.setdefault(_clave_texto(x["item"], x["titulo"], x["marca"]), []).append(x)
    por_item = defaultdict(list)
    for (item, _t, _m), xs in unicos.items():
        por_item[item].append({"titulo": xs[0]["titulo"].strip(), "marca": (xs[0]["marca"] or "").strip(),
                               "cids": [y["cid"] for y in xs]})
    lotes = []
    for item, xs in sorted(por_item.items()):
        xs.sort(key=lambda z: z["titulo"].lower())
        for i, z in enumerate(xs):
            z["uid"] = f"{item}{tag}#{i}"
        for k in range(0, len(xs), LOTE):
            lotes.append({"item": item, "filas": xs[k:k + LOTE]})
    for i, l in enumerate(lotes):
        base = {"item": l["item"], "criterio": _criterio(l["item"])}
        for suf, orden in (("A", l["filas"]), ("B", list(reversed(l["filas"])))):
            (destino / f"lote_{i:02d}_{suf}.json").write_text(json.dumps(
                {**base, "filas": [{"uid": z["uid"], "titulo": z["titulo"], "marca": z["marca"]} for z in orden]},
                ensure_ascii=False, indent=1), encoding="utf-8")
    (destino / "lotes.json").write_text(json.dumps({"lotes": lotes}, ensure_ascii=False, indent=1), encoding="utf-8")
    return lotes


def etiquetas(destino):
    ruta = DESTINOS[destino]
    if not ruta.exists():
        return {}, None
    doc = json.load(open(ruta, encoding="utf-8"))
    return {x["cid"]: x for x in doc["candidatos"]}, doc


def pendientes_rechazos_vtex(filas, etq):
    """Los rechazos (disponibles, bajo el umbral) de las VTEX del 14/09 que quedaron sin etiqueta: §F los
    necesita para que la comparación con Coto no cuente solo lo etiquetado."""
    out = []
    for c in CADENAS:
        if c == "Coto":
            continue
        for prod in CANASTA:
            for f in MR.U.candidatos(filas, c, prod["nombre"]):
                if MR.s_hoy(prod, f["of"]) < UMBRAL_MATCH and f["cid"] not in etq:
                    out.append({"cid": f["cid"], "item": prod["nombre"], "titulo": f["of"].producto,
                                "marca": f["of"].marca, "ean": f["of"].ean, "precio": f["of"].precio,
                                "cadena": c})
    return out


def fuentes_profundas(cap2):
    """{seccion: {cadena: {consulta: [fila]}}} de la captura nueva, parseadas con el parser de producción."""
    return {"profundo": P.parsear(cap2.get("profundo") or {c: {} for c in CADENAS}),
            "alternativas": P.parsear(cap2.get("alternativas") or {c: {} for c in CADENAS}),
            "coto_no24": P.parsear({"Coto": cap2.get("coto_no24") or {}})}


def pendientes_coto_profundo(cap2, et_of):
    """
    Las filas de `Nrpp=48`, `No=24` y las consultas alternativas de COTO que no tienen etiqueta, sin repetir
    por clave de unión. §H no puede decir "buscador" o "catálogo" sobre filas sin etiquetar.
    """
    secs = fuentes_profundas(cap2)
    alt_de = {qa: q for q, qs in (cap2.get("__alternativas") or {}).items() for qa in qs}
    out, vistos = [], set()
    def agregar(q, f, origen, consulta):
        k = (q, MR.clave_union("Coto", f["of"]))
        if k in vistos or et_of(q, "Coto", f["of"]) is not None:
            return
        vistos.add(k)
        out.append({"cid": f"{origen}|Coto|{consulta}|{len(out)}", "item": q, "titulo": f["of"].producto,
                    "marca": f["of"].marca, "ean": f["of"].ean, "precio": f["of"].precio,
                    "origen": origen, "consulta": consulta})
    for prod in CANASTA:
        q = prod["nombre"]
        for f in secs["profundo"]["Coto"].get(q, []):
            agregar(q, f, "profundo", q)
        for f in secs["coto_no24"]["Coto"].get(q, []):
            agregar(q, f, "coto_no24", q)
    for qa, q in alt_de.items():
        for f in secs["alternativas"]["Coto"].get(qa, []):
            agregar(q, f, "alternativa", qa)
    return out


def pendientes_coto_datos(filas2, et_of):
    """
    Las filas del request de producción de COTO en la captura nueva que no tienen etiqueta (las que quedan
    bajo el umbral y no unen con el 14/09). Sin ellas, §H no puede decir "no hay azúcar común en ninguna
    fuente": diría "no hay azúcar común ENTRE LAS ETIQUETADAS".
    """
    out = []
    for prod in CANASTA:
        for f in filas2["Coto"].get(prod["nombre"], []):
            if et_of(prod["nombre"], "Coto", f["of"]) is None:
                out.append({"cid": f["cid"], "item": prod["nombre"], "titulo": f["of"].producto,
                            "marca": f["of"].marca, "ean": f["of"].ean, "precio": f["of"].precio,
                            "cadena": "Coto"})
    return out


def pendientes_09sep(etq):
    """Los rechazos de Coto de las fixtures del 09/09: calibran contra el conteo a mano de la Tarea 19 §8."""
    datos = cargar_fixtures({"Coto": FIXTURES["Coto"]})
    out = []
    for it in CANASTA_6:
        for i, o in enumerate(datos["Coto"].get(it["nombre"], [])):
            if not o.disponible or o.precio <= 0:
                continue
            if puntuar(it, o, main.normalizar, main._extraer_cantidades_desc) >= UMBRAL_MATCH:
                continue
            cid = f"fix0909|Coto|{it['nombre']}|{i}"
            if cid not in etq:
                out.append({"cid": cid, "item": it["nombre"], "titulo": o.producto, "marca": o.marca,
                            "ean": o.ean, "precio": o.precio, "cadena": "Coto"})
    return out


# ─────────────────────────────────────────────────────────────────
#  UNIR LAS PASADAS DEL WORKFLOW
# ─────────────────────────────────────────────────────────────────

def leer_journal(ruta):
    """{label del agente: resultado} del journal del workflow."""
    label_de, res = {}, {}
    for ln in open(ruta, encoding="utf-8"):
        o = json.loads(ln)
        if o.get("type") == "started":
            label_de[o["key"]] = o["label"]
        elif o.get("type") == "result":
            res[label_de.get(o["key"], o["key"])] = o["result"]
    return res


def unir(dir_lotes, journal, pendientes, tag=None):
    """Las etiquetas finales por cid: acuerdo de las dos pasadas, o lo que dijo el adjudicador."""
    L = json.load(open(Path(dir_lotes) / "lotes.json", encoding="utf-8"))
    res = leer_journal(journal)
    por_cid = {x["cid"]: x for x in pendientes}
    filas, problemas, fuente = [], [], Counter()
    for i, l in enumerate(L["lotes"]):
        id_ = f"{i:02d}" if tag is None else f"{tag}:{i:02d}"
        A = {x["uid"]: x for x in (res.get(f"A:lote_{id_}") or {}).get("labels", [])}
        B = {x["uid"]: x for x in (res.get(f"B:lote_{id_}") or {}).get("labels", [])}
        ADJ = {x["uid"]: x for x in (res.get(f"adj:lote_{id_}") or {}).get("labels", [])}
        if not A or not B:
            problemas.append(f"lote {id_}: falta una pasada (A={bool(A)}, B={bool(B)})")
        for z in l["filas"]:
            u, a, b = z["uid"], A.get(z["uid"]), B.get(z["uid"])
            pas = {"A": a and a["etiqueta"], "B": b and b["etiqueta"]}
            if a and b and a["etiqueta"] == b["etiqueta"] and a["etiqueta"] != "dudoso":
                x, f_ = a, "acuerdo A=B"
            elif u in ADJ:
                x, f_ = ADJ[u], "adjudicado"
            else:
                problemas.append(f"{u}: sin etiqueta final (A={pas['A']}, B={pas['B']})")
                continue
            fuente[f_ + (" con evidencia extra" if x.get("evidencia_extra") else "")] += 1
            for cid in z["cids"]:
                p = por_cid[cid]
                filas.append({"cid": cid, "uid": u, "cadena": p.get("cadena") or cid.split("|")[1],
                              "item": l["item"], "titulo": p["titulo"], "marca": p["marca"],
                              "ean": p.get("ean"), "precio": p.get("precio"),
                              "etiqueta": x["etiqueta"], "frase": x.get("frase", ""), "nota": x.get("nota", ""),
                              "evidencia_extra": x.get("evidencia_extra", ""), "fuente": f_, "pasadas": pas,
                              **({"origen": p["origen"], "consulta": p["consulta"]} if "origen" in p else {})})
    return filas, fuente, problemas


def guardar(destino, nuevas, escribir, reemplazar=False):
    """Agrega filas al archivo de etiquetas, sin pisar las que ya están."""
    etq, doc = etiquetas(destino)
    if doc is None:
        raise SystemExit(f"falta {DESTINOS[destino]}: este modo agrega filas, no crea el archivo")
    if reemplazar:
        cids = {x["cid"] for x in nuevas}
        doc["candidatos"] = [x for x in doc["candidatos"] if x["cid"] not in cids]
    ya = {x["cid"] for x in doc["candidatos"]}
    agregadas = [x for x in nuevas if x["cid"] not in ya]
    doc["candidatos"] += agregadas
    doc["__resumen"] = {**doc.get("__resumen", {}), "candidatos": len(doc["candidatos"]),
                        "etiquetas": dict(Counter(x["etiqueta"] for x in doc["candidatos"]))}
    if escribir:
        DESTINOS[destino].write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return len(agregadas), len(nuevas) - len(agregadas)


def rellenar(destino, escribir=False):
    """
    Completa `ean` y `precio` de las filas que quedaron sin ellos (una tanda de lotes los omitió) leyendo la
    captura por `cid`. No toca etiquetas: solo campos de identidad, y verifica que el título coincida.
    """
    etq, doc = etiquetas(destino)
    if doc is None:
        raise SystemExit(f"falta {DESTINOS[destino]}")
    ruta = None if destino == "14sep" else CAPTURA_NUEVA
    _cap, filas = MR.cargar(ruta) if ruta else MR.cargar()
    porcid = {f["cid"]: f for c in CADENAS for fs in filas[c].values() for f in fs}
    llenas, distintas = 0, []
    for x in doc["candidatos"]:
        if x.get("ean") is not None and x.get("precio") is not None:
            continue
        f = porcid.get(x["cid"])
        if f is None:
            distintas.append((x["cid"], "no está en la captura"))
            continue
        if f["of"].producto != x["titulo"]:
            distintas.append((x["cid"], f"título distinto: {f['of'].producto[:40]!r}"))
            continue
        x["ean"], x["precio"] = f["of"].ean, f["of"].precio
        llenas += 1
    if escribir and not distintas:
        DESTINOS[destino].write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return llenas, distintas


# ─────────────────────────────────────────────────────────────────
#  CORRECCIONES
# ─────────────────────────────────────────────────────────────────

def productos_corregidos(doc14):
    """
    {(ítem, título, marca) → (etiqueta, evidencia, uid)}: qué PRODUCTO corrige cada uid. Se resuelve contra
    las filas que ya llevan la corrección aplicada y, si no hay ninguna, contra el uid —para la primera
    corrida—. Así una segunda corrida es idempotente aunque los uid se hayan repetido después.
    """
    out = {}
    for x in doc14["candidatos"]:
        if x.get("fuente") in FUENTES_CORRECCION:
            out[_clave_texto(x["item"], x["titulo"], x["marca"])] = (x["etiqueta"], x.get("evidencia_extra", ""),
                                                                     x.get("uid", ""))
    if out:
        return out
    for x in doc14["candidatos"]:
        nueva = CORRECCIONES.get(x.get("uid", ""))
        if nueva:
            out[_clave_texto(x["item"], x["titulo"], x["marca"])] = (nueva[0], nueva[1], x["uid"])
    return out


def correcciones(escribir=False):
    """Aplica CORRECCIONES por producto en los dos archivos y reporta las filas hermanas que quedaron fuera."""
    cambiadas, hermanas, docs = [], [], {}
    for destino in DESTINOS:
        _etq, doc = etiquetas(destino)
        if doc is not None:
            docs[destino] = doc
    corregidos = productos_corregidos(docs["14sep"])
    for destino, doc in docs.items():
        for x in doc["candidatos"]:
            k = _clave_texto(x["item"], x["titulo"], x["marca"])
            if k not in corregidos:
                continue
            etiqueta, evidencia, uid = corregidos[k]
            if x.get("fuente") in FUENTES_CORRECCION and x.get("fuente") != FUENTE_CORRECCION:
                x["fuente"] = FUENTE_CORRECCION      # el texto viejo atribuía la revisión a Santiago
            if x["etiqueta"] != etiqueta or x.get("fuente") not in FUENTES_CORRECCION:
                if x["etiqueta"] != etiqueta:
                    cambiadas.append((destino, x["cid"], x["item"], x["titulo"], x["etiqueta"], etiqueta))
                x["etiqueta"], x["evidencia_extra"], x["fuente"] = etiqueta, evidencia, FUENTE_CORRECCION
                x["uid_corregido"] = uid
    # Hermanas: mismo ítem y mismo marcador literal, sin corregir. Se reportan, NO se cambian.
    for destino, doc in docs.items():
        for x in doc["candidatos"]:
            if x.get("fuente") in FUENTES_CORRECCION:
                continue
            t = (x["titulo"] or "").lower()
            for m in MARCADORES.get(x["item"], ()):
                if m in t:
                    hermanas.append((destino, x["cid"], x["item"], x["titulo"], x["etiqueta"], m))
                    break
    if escribir:
        for destino, doc in docs.items():
            DESTINOS[destino].write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return cambiadas, hermanas


def sincronizar_inconsistencias(escribir=False):
    """Recalcula `__inconsistencias_ean` del archivo del 14/09 contra los datos y contra INCONSISTENCIAS_EAN."""
    etq, doc = etiquetas("14sep")
    por_ean = defaultdict(set)
    for x in doc["candidatos"]:
        e = MR.ean_cruzable(x["ean"])
        if e:
            por_ean[f"{x['item']}|{e}"].add(x["etiqueta"])
    vistas = {k: sorted(v) for k, v in por_ean.items() if len(v) > 1}
    faltan = set(vistas) - set(INCONSISTENCIAS_EAN)
    sobran = set(INCONSISTENCIAS_EAN) - set(vistas)
    doc["__inconsistencias_ean"] = {k: {"etiquetas": v, "motivo": INCONSISTENCIAS_EAN.get(k, "SIN DOCUMENTAR")}
                                    for k, v in sorted(vistas.items())}
    if escribir and not faltan and not sobran:
        DESTINOS["14sep"].write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    return vistas, faltan, sobran


# ─────────────────────────────────────────────────────────────────
#  CLI
# ─────────────────────────────────────────────────────────────────

FUENTES = ("rechazos-vtex", "coto-profundo", "coto-datos", "09sep")


def main_cli():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompts", action="store_true", help="imprime los prompts que corrieron, verbatim")
    ap.add_argument("--lotes", metavar="DIR", help="escribe los lotes a ciegas en DIR")
    ap.add_argument("--fuente", choices=FUENTES, help="qué se etiqueta")
    ap.add_argument("--unir", metavar="DIR", help="junta las pasadas del workflow de DIR")
    ap.add_argument("--journal", help="journal.jsonl del workflow")
    ap.add_argument("--tag", help="prefijo de los labels del workflow (A:lote_<tag>:NN)")
    ap.add_argument("--destino", choices=tuple(DESTINOS), help="a qué archivo de etiquetas van")
    ap.add_argument("--correcciones", action="store_true", help="aplica la tabla CORRECCIONES")
    ap.add_argument("--rellenar", choices=tuple(DESTINOS), help="completa ean y precio faltantes")
    ap.add_argument("--inconsistencias", action="store_true",
                    help="sincroniza el bloque __inconsistencias_ean con los datos")
    ap.add_argument("--escribir", action="store_true", help="escribe; sin esto solo informa")
    ap.add_argument("--reemplazar", action="store_true", help="reescribe las filas con el mismo cid")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    logging.disable(logging.INFO)

    if args.prompts:
        print("── PROMPT_ETIQUETAR " + "─" * 60 + f"\n{PROMPT_ETIQUETAR}\n")
        print("── PROMPT_ADJUDICAR " + "─" * 60 + f"\n{PROMPT_ADJUDICAR}")
        return

    if args.inconsistencias:
        vistas, faltan, sobran = sincronizar_inconsistencias(args.escribir)
        print(f"{len(vistas)} inconsistencias por EAN en los datos:")
        for k, v in sorted(vistas.items()):
            print(f"  {k:<36} {v} {'· SIN DOCUMENTAR' if k in faltan else ''}")
        if sobran:
            print(f"  documentadas que ya no están en los datos: {sorted(sobran)}")
        raise SystemExit(1 if (faltan or sobran) else 0)

    if args.rellenar:
        llenas, distintas = rellenar(args.rellenar, args.escribir)
        print(f"{llenas} filas con ean y precio completados"
              + ("" if args.escribir else " (SIN escribir: falta --escribir)"))
        for cid, motivo in distintas[:20]:
            print(f"  SIN COMPLETAR {cid}: {motivo}")
        raise SystemExit(1 if distintas else 0)

    if args.correcciones:
        cambiadas, hermanas = correcciones(args.escribir)
        print(f"{len(cambiadas)} filas corregidas" + ("" if args.escribir else " (SIN escribir: falta --escribir)"))
        for destino, cid, item, titulo, antes, despues in cambiadas:
            print(f"  {destino} {cid:<28} {item:<19} {antes:<13} → {despues:<13} {titulo[:44]}")
        print(f"\n{len(hermanas)} filas con el mismo marcador literal y SIN corregir — las decide Santiago:")
        for destino, cid, item, titulo, etiqueta, marcador in hermanas:
            print(f"  {destino} {cid:<28} {item:<19} {etiqueta:<13} [{marcador}] {titulo[:44]}")
        return

    if args.lotes:
        _cap, filas = MR.cargar()
        etq14, _d = etiquetas("14sep")
        if args.fuente == "rechazos-vtex":
            pend = pendientes_rechazos_vtex(filas, etq14)
            for x in pend:
                x["cadena"] = x["cid"].split("|")[0]
        elif args.fuente == "09sep":
            pend = pendientes_09sep(etq14)
            for x in pend:
                x["cadena"] = "Coto"
        else:
            cap2, filas2 = MR.cargar(CAPTURA_NUEVA)
            _et2, et_of, _her, _ex = MR.etiquetas_nueva(filas, filas2, etq14)
            pend = (pendientes_coto_datos(filas2, et_of) if args.fuente == "coto-datos"
                    else pendientes_coto_profundo(cap2, et_of))
            for x in pend:
                x["cadena"] = "Coto"
        lotes = escribir_lotes(pend, Path(args.lotes), args.fuente)
        # El JSON de los pendientes viaja al lado de los lotes: `--unir` necesita el cid, el EAN y el precio,
        # que los lotes NO llevan a propósito (el etiquetador es ciego).
        (Path(args.lotes) / "pendientes.json").write_text(json.dumps(pend, ensure_ascii=False), encoding="utf-8")
        print(f"{len(pend)} filas · {len(lotes)} lotes → {args.lotes}")
        print(json.dumps([[l["item"], l["filas"][0]["uid"], len(l["filas"])] for l in lotes], ensure_ascii=False))
        return

    if args.unir:
        if not (args.journal and args.destino):
            raise SystemExit("--unir necesita --journal y --destino")
        pend = json.load(open(Path(args.unir) / "pendientes.json", encoding="utf-8"))
        filas, fuente, problemas = unir(args.unir, args.journal, pend, args.tag)
        print(f"{len(filas)} filas etiquetadas · {dict(fuente)} · etiquetas "
              f"{dict(Counter(x['etiqueta'] for x in filas))}")
        for p in problemas[:20]:
            print(f"  PROBLEMA {p}")
        if problemas:
            raise SystemExit(1)
        nuevas, repetidas = guardar(args.destino, filas, args.escribir, args.reemplazar)
        print(f"{nuevas} agregadas a {DESTINOS[args.destino].name}" + (f" · {repetidas} ya estaban" if repetidas else "")
              + ("" if args.escribir else " (SIN escribir: falta --escribir)"))
        return

    ap.print_help()


if __name__ == "__main__":
    main_cli()
