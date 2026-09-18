# Integracion con Diagnostic.ar

## Respuesta corta

Si Diagnostic.ar predice una enfermedad dentro del corpus Orphadata-HPO, el agente puede abrir una sesion de repreguntas para esa enfermedad. No queda limitado a las 60 enfermedades de la demo: la base actual indexa todas las enfermedades elegibles del archivo `en_product4.xml`.

## Contrato propuesto

Diagnostic.ar llama a:

```http
POST /sessions
```

con:

```json
{
  "case_text": "Descripcion del caso clinico inicial",
  "top_candidates": [
    {"name": "Nombre enfermedad", "orpha_code": "58", "score": 0.81}
  ]
}
```

Campos:

- `case_text`: sintomas y datos clinicos iniciales.
- `top_candidates`: ranking inicial de Diagnostic.ar, idealmente top 20.
- `orpha_code`: clave recomendada para mapear con Orphadata.
- `score`: score original de Diagnostic.ar, opcional.

## Comportamiento del agente

1. Si llegan `orpha_code`, intenta recuperar contexto HPO solo dentro de esas candidatas.
2. Si no encuentra documentos para esas candidatas, cae al corpus completo.
3. Genera repreguntas que distinguen entre candidatas cercanas.
4. No toma el ranking como diagnostico: lo usa como priorizacion.
5. Devuelve `questions` hasta alcanzar certeza suficiente, maximo de iteraciones o falta de preguntas utiles.

## Respuesta esperada

Mientras necesita mas datos:

```json
{
  "session_id": "...",
  "status": "asking",
  "certainty": 55,
  "candidates": [],
  "questions": [
    {
      "pregunta": "¿Presenta convulsiones o episodios compatibles con crisis?",
      "hpo_asociado": "HP:0001250",
      "distingue": ["Alexander disease", "otra candidata"],
      "motivo": "Ayuda a discriminar compromiso neurologico documentado."
    }
  ]
}
```

Al cerrar:

```json
{
  "status": "final",
  "final_diagnosis": {
    "diagnostico_principal": "Diagnostico diferencial no concluyente",
    "certeza_final": 70,
    "enfermedades_candidatas": []
  }
}
```

## Cobertura

El numero exacto de enfermedades depende del XML y de los filtros configurados. Para reportarlo en la entrega:

```bash
python -m genphenai_repreguntas.cli stats
```

Con la configuracion por defecto, se consideran todas las enfermedades con al menos 8 fenotipos HPO registrados.
