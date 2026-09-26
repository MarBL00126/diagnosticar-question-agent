# diagnosticar-preguntas

Agente de preguntas de seguimiento (repreguntas) de diagnostic.ar. Después del
primer ranking de genes, hace unas pocas preguntas sobre síntomas y reordena el
ranking con las respuestas.

> Sugiere preguntas para conversar con un médico. No diagnostica.

Versión 2 en construcción en la rama `feat/agente-claude`. La documentación
completa vive en `docs/`.

## Uso local

Solo hace falta `python3` en el PATH.

```sh
make install   # crea .venv e instala el paquete con las dependencias de desarrollo
make kb        # construye data/kb.sqlite desde las fuentes HPO fijadas e imprime su sha256
make run       # sirve la API en :8010 contra el modelo en http://127.0.0.1:18021
make ci        # lint, formato, mypy estricto y tests (sin los marcados live)
```

La configuración son variables `PREGUNTAS_*`, documentadas en `.env.example`.

## API

- `POST /v1/rounds`: una ronda. Contrato (versión 2) en
  `src/diagnosticar_preguntas/api/schemas.py` y ejemplo en
  `docs/contrato/rondas-ejemplo.json`. Exige el encabezado `X-Internal-Token` con el
  valor de `PREGUNTAS_SERVICE_TOKEN`; sin token configurado rechaza todo (401), salvo
  con `PREGUNTAS_ALLOW_UNAUTHENTICATED=true`, que `make run` activa para desarrollo
  local. Cuerpos de más de 64 KiB responden 413. Los errores responden
  `{"error": "<código>"}` sin repetir nada del pedido (la tabla está en `api/app.py`).
- Tiempos: una ronda gasta como mucho `PREGUNTAS_ROUND_BUDGET_S` (75 s) en llamadas al
  LLM, más `PREGUNTAS_MODEL_SERVICE_TIMEOUT_S` (10 s) en la llamada al modelo, y puede
  esperar `PREGUNTAS_QUEUE_TIMEOUT_S` (10 s) un lugar libre. El timeout del backend
  tiene que superar esa suma (95 s); la plataforma usa 100 s.
- `GET /healthz`: versión, base de conocimiento, proveedor LLM y si tiene
  credenciales, y si el servicio del modelo responde. Siempre 200 mientras el
  proceso vive; `status: "degraded"` indica un problema de dependencias.

## Imagen

```sh
make docker-build
```

La imagen reconstruye la base de conocimiento desde las fuentes fijadas, trae el
CLI de Claude fijado (sin Node ni npm) y corre como el usuario `app` (uid 10001).
