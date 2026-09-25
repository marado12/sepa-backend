"""
Tarea 22, Abierto 2 — el cálculo de espera de `--inicio`, SIN RED y SIN DORMIR.

`--inicio HH:MM` sirve para dejar la corrida lanzada de noche y que arranque sola a las 06:30. El
problema de probarlo es obvio: la única forma de verificarlo "de verdad" es esperar seis horas, y
entonces no se prueba nunca. Por eso el cálculo tiene que estar separado del `sleep`:
`proxima_ocurrencia(hm, ahora)` es pura —recibe el "ahora" en vez de leer el reloj— y esto la corre
con relojes inventados.

Los tres casos son los que pidió Santiago, más dos bordes que conviene dejar clavados:

    lanzada 23:50 con --inicio 06:30  →  mañana 06:30,  espera 6 h 40 min
    lanzada 05:00 con --inicio 06:30  →  hoy    06:30,  espera 1 h 30 min
    lanzada 06:31 con --inicio 06:30  →  mañana 06:30,  espera 23 h 59 min

⚠️ **El borde de las 06:30:00 clavadas queda en "mañana".** `--inicio` significa el comienzo de esa
hora, y si el reloj ya llegó, la hora "ya pasó". Es coherente con el caso de las 06:31 que pidió
Santiago —lanzar 06:30:05 también espera hasta mañana—, y es la única lectura que no depende de
adivinar la intención. Está acá abajo como caso propio para que, si alguna vez se cambia, se vea.

Desde sepa_backend/ (en Windows, con `$env:PYTHONIOENCODING='utf-8'`):
    venv\\Scripts\\python.exe -m tests.sonda_coto_inicio
"""
import sys
from datetime import datetime

from tests import sonda_coto as S

HOY = datetime(2026, 9, 24)            # un miércoles cualquiera; lo que importa son las horas


def d(dia, hh, mm, ss=0):
    return datetime(2026, 9, dia, hh, mm, ss)


#  (descripción,           lanzada,          --inicio,  ronda 1 esperada,  texto de espera)
CASOS = [
    ("lanzada 23:50",      d(24, 23, 50),    (6, 30),   d(25, 6, 30),      "6h 40m"),
    ("lanzada 05:00",      d(24, 5, 0),      (6, 30),   d(24, 6, 30),      "1h 30m"),
    ("lanzada 06:31",      d(24, 6, 31),     (6, 30),   d(25, 6, 30),      "23h 59m"),
    ("lanzada 06:30:00",   d(24, 6, 30, 0),  (6, 30),   d(25, 6, 30),      "24h 0m"),
    ("lanzada 06:29:30",   d(24, 6, 29, 30), (6, 30),   d(24, 6, 30),      "0h 0m"),
]


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    fallos = []

    faltan = [n for n in ("proxima_ocurrencia", "espera_legible", "linea_ronda1")
              if not hasattr(S, n)]
    if faltan:
        for n in faltan:
            print(f"  ✖ falta `sonda_coto.{n}()`: el cálculo de --inicio no se puede probar sin "
                  f"dormir de verdad mientras viva adentro de `esperar_hasta`")
            fallos.append(f"falta {n}")
        print(f"\nFALLARON {len(fallos)}")
        return 1

    print("=" * 96)
    print("CÁLCULO DE ESPERA DE --inicio (relojes inventados, sin red y sin dormir)")
    print("=" * 96)
    print(f"  {'caso':<18} {'lanzada':<20} {'inicio':>7} {'ronda 1':<20} {'espera':>9}  ")
    for desc, ahora, hm, esperado, texto in CASOS:
        obj = S.proxima_ocurrencia(hm, ahora)
        seg = (obj - ahora).total_seconds()
        leg = S.espera_legible(seg)
        ok_obj, ok_txt = obj == esperado, leg == texto
        marca = "✔" if (ok_obj and ok_txt) else "✖"
        print(f"  {marca} {desc:<16} {ahora:%d/%m %H:%M:%S}   {hm[0]:02d}:{hm[1]:02d} "
              f"{obj:%d/%m %H:%M}        {leg:>9}")
        if not ok_obj:
            print(f"      ✖ ronda 1: esperaba {esperado:%d/%m %H:%M}, dio {obj:%d/%m %H:%M}")
            fallos.append(f"{desc}: ronda 1")
        if not ok_txt:
            print(f"      ✖ espera: esperaba {texto!r}, dio {leg!r}")
            fallos.append(f"{desc}: espera")

    print()
    print("=" * 96)
    print("LA PRIMERA LÍNEA QUE SE IMPRIME AL LANZAR")
    print("=" * 96)
    # El formato lo fijó Santiago para poder verificarlo al darle enter, sin depender del resumen.
    esperadas = [
        (d(24, 23, 50), (6, 30), "ronda 1: 2026-09-25 06:30 (espera 6h 40m)"),
        (d(24, 5, 0),   (6, 30), "ronda 1: 2026-09-24 06:30 (espera 1h 30m)"),
        (d(24, 6, 31),  (6, 30), "ronda 1: 2026-09-25 06:30 (espera 23h 59m)"),
        (d(24, 21, 15), None,    "ronda 1: 2026-09-24 21:15 (espera 0h 0m)"),
    ]
    for ahora, hm, esperada in esperadas:
        got = S.linea_ronda1(hm, ahora)
        ok = got == esperada
        print(f"  {'✔' if ok else '✖'} {got}")
        if not ok:
            print(f"      ✖ esperaba: {esperada}")
            fallos.append(f"linea_ronda1 con ahora={ahora:%H:%M}")

    print()
    print("=" * 96)
    if fallos:
        print(f"FALLARON {len(fallos)}:")
        for f in fallos:
            print(f"  ✖ {f}")
        return 1
    print(f"TODOS OK — {len(CASOS)} casos de cálculo y {len(esperadas)} de la primera línea.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
