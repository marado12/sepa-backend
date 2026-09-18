# sepa_backend

FastAPI + Pandas/PyArrow. Repo `marado12/sepa-backend`, desplegado en **Render plan free**.
Producción: https://sepa-backend-bk88.onrender.com

Leé primero el `CLAUDE.md` de la carpeta padre y `../CONTEXTO.md`.

## Entorno

- **Python 3.11.9**, fijado en `.python-version`. **No lo subas.** Con el default de Render
  (3.14) el build de `pyarrow`/`pandas` falla: no hay wheels y compilar desde fuente muere.
- `venv/` está en la carpeta y en `.gitignore`. En Windows: `venv\Scripts\activate`.
- `requirements.txt` **no incluye pytest ni httpx** a propósito: son 512 MB de RAM en Render
  y todo lo que entra ahí se instala en producción.

## Comandos

```bash
uvicorn main:app --reload            # servidor local en :8000

# Los tests NO son pytest: son scripts con asserts. Se corren uno por uno.
python test_resiliencia.py
python test_fuentes.py
python test_sucursales.py
python test_precios_vtex.py
python test_promos_sitio.py
python test_integracion_fuentes.py
python test_snapshot_catalogo.py     # ← existe pero NO está en el CI. Corrélo igual.

python scripts/medir_vtex.py             # necesita red real (paginación, rate limit)
python scripts/snapshot_catalogo.py --probe
python tests/evaluar_matching.py         # corre el matcher real contra las fixtures
```

**No corras `pytest`** en esta carpeta: levantaría también `test.py`, un script viejo suelto
de la raíz que no es parte de la suite.

CI en `.github/workflows/tests.yml`, en cada push. Todos los tests corren **sin red**.
Además verifica dos invariantes: que `sucursales.py` cargue solo con stdlib, y que el catálogo
de sucursales tenga Vea a ~1,5 km de Junín (regresión del problema abierto #2).

## Mapa de archivos

| Archivo | Qué es |
|---|---|
| `main.py` | **2.986 líneas, 131 KB.** La app entera: descarga del SEPA, matching, optimizador y los 26 endpoints. Es el archivo más pesado del proyecto — leé por partes, no entero. |
| `fuentes.py` | `FuenteVTEX`, `FuenteCoto`, `FuenteCompuesta`. Aislamiento de fallos por cadena. Acá vivían los 3 bugs de datos: **corregidos en el commit `4523893`, todavía sin verificar corriendo.** Los comentarios del archivo documentan cada fix y por qué — leelos antes de tocarlo. |
| `precios_vtex.py` | Matching y traducción a la forma que consume el optimizador. `UMBRAL_MATCH = 0.45`. |
| `sucursales.py` | `cadenas_cerca(lat, lon, radio)`. **stdlib puro, sin pandas — el CI lo verifica.** Existe para sobrevivir a que el pipeline de parquet esté roto. No le agregues imports pesados. |
| `promos_sitio.py` | Parseo de promos bancarias desde los Teasers. |
| `outliers.py` | Detección de precios anómalos. |
| `data/sucursales.csv` | 2.162 sucursales con lat/lon. El catálogo congelado. |
| `tests/fixtures/*.json` | **Respuestas reales** de las 5 cadenas. Usalas: las fixtures inventadas son las que dejaron pasar el bug de los Teasers. |
| `a.py`, `test.py`, `railway.toml` | Residuos. `a.py` son 13 bytes de una sesión de debug; `railway.toml` es de antes de migrar a Render. Se pueden borrar. |

### Funciones que importan en `main.py`

- `_buscar_precios()` — camino SEPA (parquet).
- `_resolver_precios()` / `_get_fuente_online()` / `_cadenas_a_consultar()` — camino online.
- `_analizar()` y `_canasta_optima()` — **el optimizador. Ítem 1.1 del roadmap, con sospecha
  de bug previa.** Se dejaron intactos a propósito mientras se sumaba la fuente online, para
  poder atribuir una regresión. No los toques mezclados con otra cosa.
- `_score_cantidad()`, `_extraer_cantidades_desc()`, `_a_base()` — normalización de cantidades.

## Contrato entre fuentes y optimizador

`precios_vtex.buscar_precios_online()` devuelve **exactamente** la misma forma que
`_buscar_precios()`:

```python
{ (cadena, producto): {"precio_min": float, "precio_por_100u": dict | None} }
```

Por eso el optimizador no se tocó al sumar la fuente online. **Si cambiás esta forma,
cambiás las dos rutas a la vez** — es el punto de acople del backend.

✏️ **Tarea 26 (18/09): la ruta online suma dos campos opcionales.** `envases` es cuántas veces
se cobra `precio_min` (los envases enteros que cubren lo pedido; `cantidad` queda como lo
pedido) y `sin_elegible` marca la fila en la que ningún candidato puede contestar lo pedido:
queda fuera del total y del %, pero sigue votando en la mediana. Una entrada sin `envases`
—la ruta SEPA, un precio manual— se cobra `precio_min × cantidad`, como siempre
(`main._envases`, `main._en_el_total`). `_canasta_optima` no los lee.

## Variables de entorno

| Variable | Default | Para qué |
|---|---|---|
| `FUENTE_PRECIOS` | `auto` | `sepa` \| `online` \| `auto`. Con `sepa` el comportamiento es idéntico al de antes del multi-fuente (hay un test que lo verifica). **Es el rollback: una variable, sin deploy.** |
| `INCLUIR_CADENAS_SIN_GEO` | `0` | Jumbo y Disco no están en `sucursales.csv`. Con `1` se consultan igual — recomendar un Jumbo a 200 km rompe la promesa del producto, por eso está en `0`. |
| `CACHE_DIR` | `/tmp/sepa_cache` | Efímero en Render. |
| `PRECIOS_MANUALES_PATH` | `$CACHE_DIR/precios_manuales.json` | |
| `SUCURSALES_PATH` | `data/sucursales.csv` | |
| `SEPA_MAX_REINTENTOS` | `3` | |
| `SEPA_BACKOFF_BASE_S` | `30` | |
| `SEPA_FALLBACK_MAX_DIAS` | `7` | Cuántos días atrás buscar un parquet usable. |

## Render plan free: lo que condiciona el diseño

- 512 MB de RAM. Es la restricción que más aprieta.
- Se duerme a los 15 min sin tráfico; despertar tarda ~1 min y **pierde `/tmp`**.
- **No soporta discos persistentes** (eso es de planes pagos). Cualquier persistencia tiene
  que ser externa. No es opinable.
- El Postgres free de Render **expira a los 30 días**. Si alguna vez hace falta DB, mirá
  Neon o Supabase.

## Endpoints

```
GET    /                                   Health check
GET    /api/status                         Estado del caché + último error/intento
POST   /refresh                            Dispara la descarga del SEPA (async)
POST   /api/precargar/{dia}
GET    /api/canasta/default
GET    /api/cadenas
GET    /api/dias
GET    /api/bancos?cadenas=
GET    /api/buscar-productos?q=&limite=
POST   /api/comparar                       El endpoint principal
GET    /api/precios-manuales
POST   /api/precios-manuales
DELETE /api/precios-manuales?cadena=&producto=
GET    /api/diagnostico-sucursales?lat=&lon=&radio_km=
GET    /api/diagnostico-cencosud
GET    /api/diagnostico-zip?dia=&n_inner=
GET    /api/diagnostico-red
GET    /outliers · /outliers/cadenas · /panel/outliers
```

Los endpoints de diagnóstico del SEPA devuelven **503** si el caché del día no está cargado:
`POST /refresh` primero y esperar a que `/api/status` diga `listo: true`.

⚠️ **La API es pública y sin autenticación.** Hoy cualquiera puede disparar `/refresh` y
escribir en `/api/precios-manuales`. Ítem T.1 del roadmap; sube a prioridad alta apenas
existan cuentas de usuario.
