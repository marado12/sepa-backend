# Backend — Comparador SEPA

FastAPI + pandas. Se deployea en Railway (gratis).

## Deploy en Railway (15 minutos)

### 1. Instalar Railway CLI
```bash
npm install -g @railway/cli
# o en Windows:
winget install Railway.RailwayCLI
```

### 2. Login y crear proyecto
```bash
railway login
railway init
# Elegí "Empty Project" y ponele nombre: sepa-comparador
```

### 3. Deploy
```bash
# Desde la carpeta sepa_backend/
railway up
```

Railway detecta automáticamente Python y corre el `Procfile`.

### 4. Obtener la URL pública
```bash
railway domain
# Te da algo como: sepa-comparador-production.up.railway.app
```

Guardá esa URL — la vas a necesitar para el frontend.

### 5. Variables de entorno (opcional)
En el dashboard de Railway → Variables:
```
CACHE_DIR=/tmp/sepa_cache
```

## Endpoints

| Método | Path | Descripción |
|--------|------|-------------|
| GET | `/` | Health check |
| GET | `/api/canasta/default` | Canasta y promos por defecto |
| GET | `/api/cadenas` | Lista de cadenas disponibles |
| GET | `/api/status` | Estado del caché en memoria |
| POST | `/api/comparar` | **Comparar precios** |
| POST | `/api/precargar/{dia}` | Precargar un día específico |

## Ejemplo de request a /api/comparar

```json
{
  "lat": -34.5875,
  "lon": -60.9349,
  "radio_km": 5,
  "canasta": [],
  "promos": []
}
```

Si `canasta` y `promos` están vacíos, usa los valores por defecto.

## Notas

- La primera consulta del día descarga ~300MB de SEPA (2-3 minutos).
- Las consultas siguientes usan caché en disco/memoria: ~3-5 segundos.
- En Render free tier el servidor duerme después de 15min. El primer
  request lo despierta (30s de demora). Railway no tiene este problema.
