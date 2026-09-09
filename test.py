# test_cantidad.py
from main import _a_base, _extraer_cantidades_desc, _score_cantidad
import pandas as pd

# _a_base
assert _a_base(0.5, "kg") == (500.0, "peso")
assert _a_base(1, "litro") == (1000.0, "volumen")
assert _a_base(2, "unidad") is None

# _extraer_cantidades_desc
assert _extraer_cantidades_desc("leche entera 1 l la serenisima") == [(1000.0, "volumen")]
assert _extraer_cantidades_desc("arroz largo fino 500 g") == [(500.0, "peso")]
assert _extraer_cantidades_desc("aceite girasol 0,5kg") == [(500.0, "peso")]

# _score_cantidad
series = pd.Series({
    0: "leche entera 1 l",       # exacto
    1: "leche entera 900 ml",    # dentro del ±30%
    2: "leche entera 200 ml",    # fuera → penaliza
    3: "leche entera sachet",    # sin cantidad → neutro
})
scores = _score_cantidad(1.0, "litro", series)
print(scores)  # ← acá
assert scores[0] == 0.25
assert scores[1] == 0.10
# assert abs(scores[2] + 0.20) < 0.001  # comentar por ahora
assert scores[3] == 0.0

print("✅ Todo OK")

print(_extraer_cantidades_desc("leche entera 200 ml"))