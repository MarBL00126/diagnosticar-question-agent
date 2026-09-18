# GenPhenAI Repreguntas

Sistema modular de repreguntas diagnosticas para enfermedades raras. Usa asociaciones reales Orphadata-HPO (`data/en_product4.xml`) para generar preguntas trazables que ayuden a refinar un ranking inicial de Diagnostic.ar.

> Prototipo academico. No emite diagnosticos medicos definitivos.

## Que resuelve

- Indexa enfermedades con fenotipos HPO reales desde Orphadata.
- Generaliza mas alla de las 60 enfermedades originales: por defecto usa todas las enfermedades elegibles del XML local.
- Permite integrar el `top 20` de Diagnostic.ar para abrir un agente de repreguntas enfocado en esas candidatas.
- Evita hardcodear secretos: la API key se carga desde `.env`.
- Expone API FastAPI, CLI y tests.

## Estructura

```text
.
├── data/                         # Orphadata/HPO incluidos en el repo local
├── docs/                         # Documentacion de arquitectura e integracion
├── src/genphenai_repreguntas/
│   ├── api.py                    # FastAPI
│   ├── cli.py                    # Comandos de stats/index
│   ├── config.py                 # Settings + .env
│   ├── documents.py              # Documentos LangChain desde Orphadata
│   ├── engine.py                 # Motor RAG + LLM de repreguntas
│   ├── indexing.py               # Chroma y firma de corpus
│   ├── orphadata.py              # Parser XML Orphadata-HPO
│   ├── prompts.py                # Prompts trazables
│   └── session.py                # Estado de sesion y deduplicacion
├── tests/                        # Tests unitarios sin llamadas a OpenAI
├── diagnostic_chat_service.py    # Wrapper compatible para levantar la API
├── pyproject.toml
├── requirements-repreguntas.txt
└── .env.example
```

## Instalacion

```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -e ".[dev]"
copy .env.example .env
```

En Windows tambien se puede usar:

```powershell
.\scripts\install.ps1 -Dev
```

Editar `.env` y completar:

```env
OPENAI_API_KEY=tu_api_key
```

## Calcular corpus e indice

Ver estadisticas del corpus local:

```bash
python -m genphenai_repreguntas.cli stats
```

o:

```powershell
.\scripts\stats.ps1
```

Crear o reutilizar Chroma:

```bash
python -m genphenai_repreguntas.cli index
```

o:

```powershell
.\scripts\index.ps1
```

Si un indexado anterior fallo a mitad de camino, recrear la coleccion:

```powershell
.\scripts\index.ps1 --force
```

Por defecto `REPREGUNTAS_MAX_DISEASES=all`, o sea todas las enfermedades elegibles por el filtro `REPREGUNTAS_MIN_HPO_TERMS`.

## Levantar API

```bash
python diagnostic_chat_service.py
```

o:

```powershell
.\scripts\run_api.ps1
```

La API queda en `http://127.0.0.1:8010`.

Endpoints:

- `GET /health`
- `POST /sessions`
- `POST /sessions/{session_id}/answers`

Ejemplo de inicio con ranking de Diagnostic.ar:

```json
{
  "case_text": "Paciente de 20 anos con movilidad reducida y problemas de memoria.",
  "top_candidates": [
    {"name": "Alexander disease", "orpha_code": "58", "score": 0.81},
    {"name": "Alpha-mannosidosis", "orpha_code": "61", "score": 0.73}
  ]
}
```

## Probar el chatbot por consola

Antes de publicar, se puede probar el flujo conversacional completo sin levantar la API:

```powershell
.\scripts\index.ps1
.\scripts\chat.ps1 --case "Paciente de 20 anos con movilidad reducida y problemas de memoria." --candidate "58:Alexander disease" --candidate "61:Alpha-mannosidosis"
```

El chatbot imprime una respuesta JSON con `status`, `certainty`, candidatas y `questions`. Si `status` es `asking`, responde en texto libre en la consola. El flujo termina cuando devuelve `status: final` o cuando escribis `salir`.

## Tests

```bash
pytest
```

Los tests cubren parser Orphadata, generacion de documentos, configuracion, estado de sesion y normalizacion de certeza. No llaman a OpenAI.

## Entrega sin notebooks

El repositorio no depende de notebooks. El funcionamiento principal esta en `src/genphenai_repreguntas`, se ejecuta por CLI/API y se verifica con tests automatizados.
