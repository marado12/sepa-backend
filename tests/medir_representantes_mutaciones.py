"""
Tarea 26 paso (e) / Tarea 22 — MUTACIONES de `tests/medir_representantes.py`: la validación (9).

Una validación que nunca se puso en rojo no vale nada. Este script rompe a propósito, en memoria, cada
cosa que el instrumento dice cuidar, y exige que salga con error. Fuera del CI, sin red y sin tocar
producción: es el guard del guard.

Cada mutación tiene que dar **exit 1** y el control sin mutar, **exit 0**. Si alguna no se detecta, este
script sale con exit 1 y dice cuál. Son M1–M13.

⚠️ Una mutación tiene que romper **lo que el informe imprime**, no una función vecina que se le parezca:
M10 rompía `fuentes_coto` y no la tabla de §H1b, así que el bug original de §H1b, reintroducido, pasaba en
verde. Lo encontró la revisión del 24/09 y por eso M10 muta `conteos_h1b`, que es de donde imprime la tabla.

Desde sepa_backend/ (en Windows, con `$env:PYTHONIOENCODING='utf-8'`):
    venv\\Scripts\\python.exe -m tests.medir_representantes_mutaciones
"""
import copy
import json
import logging
import sys
from datetime import datetime

from tests import medir_representantes as MR

logging.disable(logging.INFO)
TMP = MR.RAIZ / "tests" / "capturas" / "_etiquetas_mutadas.json"


def cargar_todo():
    cap, filas = MR.cargar()
    dia = datetime.fromisoformat(cap["__fecha"]).weekday()
    ctx = MR.validar(cap, filas, dia)
    bases = MR.bases_aceptacion(filas)
    etq, doc = MR.cargar_etiquetas(filas, ctx["elegidos"], ctx["reps"])
    nueva = None
    if MR.ETIQUETAS_NUEVA.exists() and (MR.RAIZ / "tests" / "capturas" / "representantes_2026-09-22.json").exists():
        cap2, filas2 = MR.cargar(MR.RAIZ / "tests" / "capturas" / "representantes_2026-09-22.json")
        _et2, _et_of, _her, extra = MR.etiquetas_nueva(filas, filas2, etq)
        nueva = (cap2, filas2, extra)
    return cap, filas, ctx, bases, etq, doc, nueva


CAP, FILAS, CTX, BASES, ETQ0, DOC0, NUEVA = cargar_todo()
HOY = CTX["elegidos"]["hoy"]
REP_MALO = HOY[("Coto", "Azúcar")][0]["cid"]                      # la mermelada
NO_REP = next(cid for cid, x in ETQ0.items()
              if not x["representante"] and MR.ean_cruzable(x["ean"]) and x["item"] == "Leche entera")


def corrida(etq, bases=None, hoy=None):
    """Las mismas validaciones que corre el instrumento, devolviendo los errores."""
    errores = []
    try:
        errores += MR.validar_aceptacion(bases or BASES)
        e7, _s = MR.validar_respuestas_conocidas(FILAS, etq, hoy or HOY)
        e8, _t = MR.validar_esperado_e(CAP, etq, hoy or HOY, bases or BASES)
        errores += e7 + e8
        if NUEVA:
            cap2, filas2, extra = NUEVA
            errores += (MR.validar_nueva(cap2, filas2) + MR.validar_etiquetas_profundas(cap2, extra)
                        + MR.validar_reconstruccion_coto(cap2, filas2))
    except MR.InstrumentoInvalido as e:
        errores.append(str(e))
    return errores


def con_etiquetas(mut):
    """Escribe las etiquetas mutadas y las carga con las validaciones (3), (4) y (5)."""
    doc = copy.deepcopy(DOC0)
    mut(doc)
    TMP.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    try:
        etq, _d = MR.cargar_etiquetas(FILAS, CTX["elegidos"], CTX["reps"], ruta=TMP)
        return corrida(etq)
    except MR.InstrumentoInvalido as e:
        return [str(e)]
    finally:
        TMP.unlink()


def m1(doc):    # una etiqueta cambiada: la mermelada de Coto pasa a "correcto" → (8)
    for x in doc["candidatos"]:
        if x["cid"] == REP_MALO:
            x["etiqueta"] = "correcto"


def m2(doc):    # la etiqueta describe otro candidato → (3)
    for x in doc["candidatos"]:
        if x["cid"] == REP_MALO:
            x["titulo"], x["precio"] = "Azúcar Ledesma 1 Kg", x["precio"] + 1


def m3(doc):    # falta la etiqueta de un representante → (4)
    doc["candidatos"] = [x for x in doc["candidatos"] if x["cid"] != REP_MALO]


def m4(doc):    # inconsistencia por EAN nueva, en una fila que no es representante → (5)
    for x in doc["candidatos"]:
        if x["cid"] == NO_REP:
            x["etiqueta"] = "dudoso" if x["etiqueta"] != "dudoso" else "otro producto"


def m5():
    """El umbral de producción movido a 0,47: cambia quién es representante → (8)."""
    import precios_vtex
    viejo = precios_vtex.UMBRAL_MATCH
    precios_vtex.UMBRAL_MATCH = MR.UMBRAL_MATCH = 0.47
    try:
        return corrida(ETQ0, hoy=MR.seleccionar(FILAS, umbral=0.47))
    finally:
        precios_vtex.UMBRAL_MATCH = MR.UMBRAL_MATCH = viejo


def m6():
    """El score inflado en 0,05: la aceptación deja de reproducir test_unidades.py → (6)."""
    viejo = MR.s_hoy
    MR.s_hoy = lambda prod, of: min(1.0, viejo(prod, of) + 0.05)
    try:
        return corrida(ETQ0, bases=MR.bases_aceptacion(FILAS))
    finally:
        MR.s_hoy = viejo


def m7():
    """
    Un ítem que la consulta de producción devuelve VACÍO y que no está documentado → (10).
    Es la mutación que pide la revisión del 22/09: hasta entonces la (10) miraba que el ítem estuviera en la
    captura, no que tuviera filas, y no vio los 0 de Detergente y Atún en Coto.
    """
    if not NUEVA:
        return ["(10) no se puede probar: falta la captura nueva"]
    cap2, filas2, _extra = NUEVA
    c2 = copy.deepcopy(cap2)
    c2["datos"]["Carrefour"]["Leche entera"] = []
    return MR.validar_nueva(c2, filas2)


def m8():
    """
    Una etiqueta de fuente profunda que no describe ninguna fila de esas fuentes → (3b).
    """
    if not NUEVA:
        return ["(3b) no se puede probar: falta la captura nueva"]
    cap2, _filas2, extra = NUEVA
    falso = dict(next(iter(x for cid, x in extra.items() if cid.split("|")[0] not in MR.CADENAS)))
    falso["titulo"] = "Producto Que No Existe 1 Kg"
    return MR.validar_etiquetas_profundas(cap2, {**extra, "profundo|Coto|inventado|0": falso})


def m9():
    """
    Control de la corrección de §H: sin deduplicar por clave de unión, "aceptados nuevos" de Coto se
    infla. No es una validación con exit 1: es una comprobación de que el deduplicado NO es cosmético.
    """
    if not NUEVA:
        return ["no se puede probar: falta la captura nueva"]
    cap2, filas2, _extra = NUEVA
    secs = MR.fuentes_profundas(cap2)
    crudo = dedup = 0
    for prod in MR.CANASTA:
        q = prod["nombre"]
        vistos = {MR.clave_union("Coto", f["of"]) for f in filas2["Coto"].get(q, [])}
        fuentes = dict(MR.fuentes_coto(cap2, filas2, secs, q))
        for nombre in ("Nrpp=48", "No=24 (2ª página)"):
            for f in fuentes.get(nombre, []):
                if not (f["of"].disponible and f["of"].precio > 0
                        and MR.s_hoy(prod, f["of"]) >= MR.UMBRAL_MATCH):
                    continue
                crudo += 1
                k = MR.clave_union("Coto", f["of"])
                if k not in vistos:
                    vistos.add(k)
                    dedup += 1
    return ([] if crudo == dedup
            else [f"sin deduplicar serían {crudo} aceptados nuevos en Coto; deduplicados son {dedup}"])


def recorte_de(cap2):
    """Por qué no se puede probar la (11) con esta captura, o None si sí se puede."""
    if not ((cap2.get("__recorte") or {}).get("quitadas_por_seccion") or {}).get("profundo/Coto"):
        return "(11) no se puede probar: la captura nueva no está recortada, no hay nada que reconstruir"
    return None


def m10():
    """
    §H1b contando con `len()` sobre la captura RECORTADA, sin reconstruir lo que el recorte se llevó → (11).

    Son **las dos líneas exactas** del bug que encontró la segunda revisión del 23/09: `Nrpp=48` daba 258
    filas en vez de 563 y ocho ítems mostraban 0 — entre ellos el arroz, los fideos y el aceite, que sí
    traen filas. Muta `conteos_h1b`, que es de donde imprime la tabla; hasta el 24/09 rompía `fuentes_coto`,
    o sea la reconstrucción y no la tabla, así que el bug de verdad le pasaba en verde.
    """
    if not NUEVA:
        return ["(11) no se puede probar: falta la captura nueva"]
    cap2, filas2, _extra = NUEVA
    if (motivo := recorte_de(cap2)):
        return [motivo]

    def con_len(c2, f2, secs, faltantes=None):
        salida = []
        for prod in MR.CANASTA:
            q = prod["nombre"]
            n_prof = len((c2.get("profundo", {}).get("Coto") or {}).get(q) or [])
            n_no24 = len((c2.get("coto_no24") or {}).get(q) or [])
            salida.append((q, len((c2["datos"]["Coto"] or {}).get(q) or []), n_prof, n_no24))
        return salida

    viejo = MR.conteos_h1b
    MR.conteos_h1b = con_len
    try:
        return MR.validar_reconstruccion_coto(cap2, filas2)
    finally:
        MR.conteos_h1b = viejo


def m11():
    """
    `fuentes_coto` descartando en silencio una clave que el recorte anotó y que no encuentra en `datos` → (11).

    Es la otra mitad del mismo bug: la reconstrucción se completa "con lo que haya" y el conteo baja sin que
    nada lo diga. La clave que se rompe es de `alternativas`, que no entra en la tabla de §H1b ni en el total
    de `Nrpp=48`: si la (11) no contara los descartes, nada la vería.
    """
    if not NUEVA:
        return ["(11) no se puede probar: falta la captura nueva"]
    cap2, filas2, _extra = NUEVA
    if (motivo := recorte_de(cap2)):
        return [motivo]
    qc = (cap2.get("__recorte") or {}).get("quitadas_claves") or {}
    usadas = {qa for prod in MR.CANASTA for qa in (cap2.get("__alternativas") or {}).get(prod["nombre"], [])}
    k = next((k for k, v in qc.items()
              if k.startswith("alternativas|Coto|") and v and k.split("|", 2)[2] in usadas), None)
    if k is None:
        return ["(11) no se puede probar: el recorte no anotó claves de alternativas de Coto"]
    c2 = copy.deepcopy(cap2)
    # La clave sigue anotada, pero ya no describe ninguna fila de `datos`: `fuentes_coto` no la reconstruye.
    c2["__recorte"]["quitadas_claves"][k][0][2] += " que no existe"
    return MR.validar_reconstruccion_coto(c2, filas2)


def columna_en_cero(cap2, filas2, i):
    """`conteos_h1b` con la columna `i` de la tupla (q, n_hoy, n_prof, n_no24) forzada a 0."""
    viejo = MR.conteos_h1b

    def mutado(c2, f2, secs, faltantes=None):
        return [t[:i] + (0,) + t[i + 1:] for t in viejo(c2, f2, secs, faltantes)]

    MR.conteos_h1b = mutado
    try:
        return MR.validar_reconstruccion_coto(cap2, filas2)
    finally:
        MR.conteos_h1b = viejo


def m12():
    """
    `conteos_h1b` con la columna `No=24` en 0 → (11).

    El caso: un revisor puso `n_no24 = 0` y el instrumento siguió dando **exit 0**, con `No=24` en 0 en los
    20 ítems, porque la (11) validaba solo la columna `Nrpp=48`. Es la misma forma de las otras dos: un
    número impreso que nadie compara contra la captura. Desde el 24/09 la (11) valida las tres columnas.
    """
    if not NUEVA:
        return ["(11) no se puede probar: falta la captura nueva"]
    cap2, filas2, _extra = NUEVA
    if (motivo := recorte_de(cap2)):
        return [motivo]
    return columna_en_cero(cap2, filas2, 3)


def m13():
    """
    `conteos_h1b` con la columna de la consulta de producción en 0 → (11).

    La tercera columna del mismo agujero. Con `n_hoy = 0` §H1b dice que producción devolvió 0 filas en los
    **20** ítems y las notas de la tabla lo repiten ("producción devuelve 0 y los otros requests traen
    filas"), que es justo el hallazgo de §H1b: el informe afirmaría un 0 que la captura desmiente. Antes del
    commit del 24/09 no lo veía nada — ni la (11), que solo miraba `Nrpp=48`, ni la (10), que mira la
    captura y no la tabla.
    """
    if not NUEVA:
        return ["(11) no se puede probar: falta la captura nueva"]
    cap2, filas2, _extra = NUEVA
    if (motivo := recorte_de(cap2)):
        return [motivo]
    return columna_en_cero(cap2, filas2, 1)


PRUEBAS = [
    ("Control, sin mutación", lambda: corrida(ETQ0), True),
    ("M1 la mermelada de Coto etiquetada 'correcto'", lambda: con_etiquetas(m1), False),
    ("M2 la etiqueta describe otro candidato", lambda: con_etiquetas(m2), False),
    ("M3 falta la etiqueta de un representante", lambda: con_etiquetas(m3), False),
    ("M4 dos etiquetas para el mismo EAN, sin documentar", lambda: con_etiquetas(m4), False),
    ("M5 umbral de producción en 0,47", m5, False),
    ("M6 el score inflado en 0,05", m6, False),
    ("M7 un ítem vacío sin documentar (10)", m7, False),
    ("M8 etiqueta de fuente profunda inventada (3b)", m8, False),
    ("M9 §H sin deduplicar (control, no validación)", m9, False),
    ("M10 §H1b contado sobre la captura recortada (11)", m10, False),
    ("M11 `fuentes_coto` descarta una clave del recorte (11)", m11, False),
    ("M12 `conteos_h1b` con la columna No=24 en 0 (11)", m12, False),
    ("M13 `conteos_h1b` con la columna de producción en 0 (11)", m13, False),
]


def main_cli():
    sys.stdout.reconfigure(encoding="utf-8")
    print(f"Representante mutado: {REP_MALO} · fila no representante: {NO_REP}")
    print(f"Captura nueva: {'sí' if NUEVA else 'NO (M7 a M13 no se pueden probar)'}\n")
    malas = 0
    for nombre, fn, espera_verde in PRUEBAS:
        errores = fn()
        ok = (not errores) if espera_verde else bool(errores)
        malas += not ok
        print(f"{'OK   ' if ok else 'FALLA'} {nombre:<50} {'exit 0' if not errores else 'exit 1'}")
        for e in errores[:2]:
            print(f"         {e.splitlines()[0][:150]}")
    print(f"\n{'todas las mutaciones se detectan' if not malas else f'{malas} mutaciones NO se detectan'}")
    sys.exit(1 if malas else 0)


if __name__ == "__main__":
    main_cli()
