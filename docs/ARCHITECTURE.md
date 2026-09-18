# Arquitectura

## Flujo principal

```text
Caso clinico inicial
  -> ranking opcional Diagnostic.ar top 20
  -> recuperacion RAG sobre Chroma/Orphadata-HPO
  -> evaluacion de certeza
  -> repreguntas HPO trazables
  -> respuesta del usuario
  -> nueva evaluacion
  -> diagnostico diferencial no definitivo
```

## Componentes

- `orphadata.py`: parsea `en_product4.xml` y obtiene enfermedades con sus fenotipos HPO.
- `documents.py`: convierte cada enfermedad en un documento LangChain con metadatos trazables.
- `indexing.py`: crea una coleccion Chroma con nombre derivado de la firma del corpus.
- `session.py`: conserva historial clinico, preguntas realizadas, HPO ya consultados y candidatas Diagnostic.ar.
- `engine.py`: coordina recuperacion, prompts, respuesta JSON y reglas de seguridad.
- `api.py`: expone endpoints HTTP.
- `cli.py`: permite calcular estadisticas e indexar desde consola.

## Corpus

La version inicial trabajaba como demo con un subconjunto pequeno. Esta base usa por defecto:

- `REPREGUNTAS_MAX_DISEASES=all`
- `REPREGUNTAS_MIN_HPO_TERMS=8`
- `REPREGUNTAS_MIN_DIAG_CRIT=0`
- `REPREGUNTAS_MAX_HPO_PER_DISEASE=20`

Esto permite indexar todas las enfermedades del XML local que tengan suficiente informacion HPO. Si se quiere reproducir un subconjunto, se puede configurar `REPREGUNTAS_MAX_DISEASES=60`.

## Trazabilidad

Cada pregunta generada debe indicar:

- pregunta en lenguaje de paciente,
- HPO asociado,
- enfermedades que ayuda a distinguir,
- motivo clinico.

El prompt prohibe usar sintomas no presentes en el contexto recuperado como evidencia a favor. Si un fenotipo relevante no aparece en el contexto, debe quedar en `fenotipos_no_explicados`.

## Certeza

El sistema no debe forzar un diagnostico. Si hay evidencia mixta o fenotipos importantes sin explicar, la certeza se limita a 70. Si no hay candidatas claras, la salida esperada es `Diagnostico diferencial no concluyente`.

La certeza no es una barra de progreso. Puede mantenerse igual o bajar entre iteraciones si la nueva respuesta no discrimina entre candidatas, o si niega fenotipos importantes de una enfermedad previamente plausible.

## Persistencia Chroma

La coleccion Chroma incluye una firma del corpus:

```text
repreguntas_hpo_orphadata_<firma>
```

Esto evita reutilizar por error una coleccion vieja cuando cambian filtros o datos del corpus.
