"""
Tarea 26 paso (e) / Tarea 22 — MUTACIONES de `tests/medir_representantes.py`: la validación (9).

Una validación que nunca se puso en rojo no vale nada. Este script rompe a propósito, en memoria, cada
cosa que el instrumento dice cuidar, y exige que salga con error. Fuera del CI, sin red y sin tocar
producción: es el guard del guard.

Cada mutación tiene que dar **exit 1** y el control sin mutar, **exit 0**. Si alguna no se detecta, este
script sale con exit 1 y dice cuál.

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
            errores += MR.validar_nueva(cap2, filas2) + MR.validar_etiquetas_profundas(cap2, extra)
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
]


def main_cli():
    sys.stdout.reconfigure(encoding="utf-8")
    print(f"Representante mutado: {REP_MALO} · fila no representante: {NO_REP}")
    print(f"Captura nueva: {'sí' if NUEVA else 'NO (M7, M8 y M9 no se pueden probar)'}\n")
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
