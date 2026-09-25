"""
Tarea 22, Abierto 2 — MUTACIONES de la regla de guardado de `tests/sonda_coto.py`.

Una regla que nunca se vio fallar no vale nada. Este script saca cada regla de `REGLAS`, una por vez,
y exige que desaparezca **exactamente** su caso de la captura del 24/09: ni una medición de más, ni
una de menos. Sin red y sin tocar producción — corre sobre una captura que ya está en disco.

Cada mutación tiene que dar **rojo** (perder su caso y solo el suyo) y el control sin mutar, 42
marcadas. Si alguna no se detecta, o arrastra mediciones que no son suyas, el script sale con exit 1
y dice cuál.

⚠️ La mutación saca la regla de `REGLAS`, **no filtra el resultado de `motivo_guardado`**. No es lo
mismo: filtrar la salida deja a la medición sin poder caer en las reglas que siguen, y entonces una
regla que tapa a otra pasaría desapercibida. Sacándola de la tupla, la medición baja por el resto de
la cadena igual que si la regla nunca hubiera existido.

Desde sepa_backend/ (en Windows, con `$env:PYTHONIOENCODING='utf-8'`):
    venv\\Scripts\\python.exe -m tests.sonda_coto_mutaciones
"""
import json
import sys
from pathlib import Path

from tests import sonda_coto as S

CAPTURA = S.RAIZ / "tests" / "capturas" / "sonda_coto_2026-09-24_0343.json"

# Lo que cada regla tiene que estar cazando en esta captura, dicho a mano y por su nombre. Si una
# mutación saca una regla y desaparece otra cosa, es que la regla no era lo que decía ser.
DUENIO = {
    "vacio": "los 29 registros=0 (5 de canasta/positivo + zzqxw y frbnt zzqxw en las 12 rondas)",
    "parcial": "los 12 Huevos (12 registros contra totalNumRecs 171/172)",
    "derrumbe": "Pollo entero en la r9 (total 2 contra 243 en la r8)",
}
ESPERADO_CONTROL = 42


def marcadas(mediciones):
    """
    {(ronda, consulta): motivo} con las reglas que estén activas AHORA en `S.REGLAS`.

    Recorre en el orden en que se midió, que es como corre en vivo: `total_previo` sale de la ronda
    anterior real y no de una reconstrucción.
    """
    previos, out = {}, {}
    for f in mediciones:
        m = S.motivo_guardado(f["brazo"], f["ronda"], f.get("registros"),
                              f.get("total"), previos.get(f["consulta"]))
        previos[f["consulta"]] = f.get("total")
        if m:
            out[(f["ronda"], f["consulta"])] = m
    return out


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    if not CAPTURA.exists():
        print(f"falta la captura: {CAPTURA}")
        return 1
    ms = json.loads(CAPTURA.read_text(encoding="utf-8"))["mediciones"]
    originales = S.REGLAS

    base = marcadas(ms)
    print("=" * 100)
    print(f"CONTROL sin mutar — {CAPTURA.name}, {len(ms)} mediciones")
    print("=" * 100)
    por_motivo = {n: sum(1 for v in base.values() if v == n) for n, _p in originales}
    print(f"  reglas activas : {', '.join(n for n, _p in originales)}")
    print(f"  marcadas       : {len(base)}  ({', '.join(f'{k}={v}' for k, v in por_motivo.items())})")
    fallos = []
    if len(base) != ESPERADO_CONTROL:
        fallos.append(f"control: {len(base)} marcadas, esperaba {ESPERADO_CONTROL}")
        print(f"  ⚠️ esperaba {ESPERADO_CONTROL}")
    else:
        print(f"  ✔ coincide con el esperado ({ESPERADO_CONTROL})")

    for i, (nombre, _pred) in enumerate(originales, 1):
        etiqueta = f"M{i} — sin la regla `{nombre}`"
        print()
        print("=" * 100)
        print(etiqueta)
        print("=" * 100)
        print(f"  tendría que perder: {DUENIO[nombre]}")
        S.REGLAS = tuple(r for r in originales if r[0] != nombre)
        try:
            mut = marcadas(ms)
        finally:
            S.REGLAS = originales

        perdidas = {k: v for k, v in base.items() if k not in mut}
        aparecidas = {k: v for k, v in mut.items() if k not in base}
        cambiadas = {k: (base[k], mut[k]) for k in base if k in mut and base[k] != mut[k]}
        suyas = {k for k, v in base.items() if v == nombre}

        print(f"  marcadas       : {len(base)} → {len(mut)}")
        print(f"  perdidas       : {len(perdidas)}")
        for (r, q), v in sorted(perdidas.items())[:6]:
            print(f"      r{r:<3} {q:<24} era `{v}`")
        if len(perdidas) > 6:
            print(f"      … y {len(perdidas) - 6} más")

        ok = True
        if perdidas.keys() != suyas:
            de_mas = sorted(perdidas.keys() - suyas)
            de_menos = sorted(suyas - perdidas.keys())
            ok = False
            if de_mas:
                print(f"  ✖ arrastró {len(de_mas)} que NO eran de `{nombre}`: {de_mas[:4]}")
            if de_menos:
                print(f"  ✖ NO perdió {len(de_menos)} que sí eran de `{nombre}`: {de_menos[:4]}")
        if aparecidas:
            ok = False
            print(f"  ✖ aparecieron {len(aparecidas)} que no estaban: {sorted(aparecidas)[:4]}")
        if cambiadas:
            ok = False
            print(f"  ✖ {len(cambiadas)} cambiaron de motivo: {list(cambiadas.items())[:3]}")
        if not perdidas:
            ok = False
            print(f"  ✖ NO se detectó: sacar `{nombre}` no cambió nada. La regla no estaba cazando nada.")

        if ok:
            print(f"  ✔ ROJO por su propia aserción: perdió exactamente sus {len(suyas)}, nada más se movió.")
        else:
            fallos.append(etiqueta)

    print()
    print("=" * 100)
    if fallos:
        print(f"FALLARON {len(fallos)}:")
        for f in fallos:
            print(f"  ✖ {f}")
        return 1
    print(f"TODAS OK — el control da {ESPERADO_CONTROL} y las {len(originales)} mutaciones "
          f"pierden exactamente su caso.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
