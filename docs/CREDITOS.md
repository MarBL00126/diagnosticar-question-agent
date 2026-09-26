# Créditos

## Autoría

- **Mariano Brizuela** creó este repositorio (`MarBL00126/diagnosticar-question-agent`,
  commit 0a39103) y el primer prototipo del agente de repreguntas (commit c2e3548,
  2026-09-18). Figura como autor en `pyproject.toml`.
- **Mate** es autor de la versión 2 (commits b7cc6c6 a c3d7033, 2026-09-26) y de
  su integración en la plataforma diagnostic.ar.

El prototipo sigue completo en la historia de git: `git show c2e3548 --stat`
lista sus archivos y `git show c2e3548:<ruta>` muestra cada uno.

## Qué del prototipo sigue vivo en la versión 2

La versión 2 reescribió el código (los motivos están en D-01 de
[DECISIONES.md](DECISIONES.md)), pero conserva las ideas clínicas y de diseño
del prototipo que seguían siendo correctas.

### Reglas clínicas de sus prompts

Las reglas de los prompts del prototipo (`prompts.py`: plantillas de
evaluación y de cierre) están ahora en `llm/prompts/comun.md`, numeradas, y
mapeadas en `RULES_FROM_PROTOTYPE` de `llm/prompting.py`. Varias pasaron además
de regla de prompt a garantía del código:

| Regla del prototipo | Dónde vive en la versión 2 |
|---|---|
| "No inventes enfermedades ni fenotipos fuera del contexto." | `comun.md` regla 1 (solo lo que está en `<datos>`); el código rechaza cualquier `hpo_id` fuera de la lista corta. |
| "Nunca uses un HPO o fenotipo NEGADO para subir la probabilidad de una enfermedad"; tratar las respuestas negativas como evidencia en contra. | `comun.md` regla 2; en el re-ranking, un "no" nunca sube una hipótesis que tiene ese hallazgo (sección 4.5 de [ARQUITECTURA.md](ARQUITECTURA.md), con test). |
| "Si un fenotipo aparece en varias candidatas, no debe aumentar por sí solo la certeza; debe motivar una nueva pregunta discriminativa." | `comun.md` regla 3; la ganancia de información hace lo mismo cuantitativamente: un síntoma compartido separa poco a los genes y queda abajo. |
| "La certeza no tiene que subir con cada iteración." | `comun.md` regla 4; el puntaje refinado puede bajar entre rondas. |
| "Cada pregunta debe consultar un solo fenotipo HPO. No combines…" y "Cada hpo_asociado debe corresponder exactamente al síntoma preguntado." | `comun.md` regla 5, `tarea_preguntas.md` (un síntoma, con el mismo alcance que el término) y `medico.md`. |
| "No vuelvas a preguntar por un HPO que ya aparece en CONFIRMADOS o NEGADOS" y "No repitas preguntas ya realizadas ni sus paráfrasis." | `comun.md` regla 6; el selector descarta lo ya conocido y lo implicado por la ontología, y las salvaguardas rechazan un término ya preguntado. |
| "No emitas un diagnóstico médico definitivo." | `comun.md` regla 7, las salvaguardas del modo paciente y el aviso "Sugiere preguntas para conversar con un médico. No diagnostica." |
| "Prioriza preguntas que distingan directamente entre las enfermedades candidatas principales." | Selección por ganancia de información sobre los genes y `tarea_preguntas.md` ("Priorizá los candidatos que más separan a los genes de arriba"). |
| "Formula preguntas en lenguaje claro para paciente." | `paciente.md`. |
| "Si Diagnostic.ar aporta candidatas previas, usalas solo como priorización; nunca como evidencia clínica por sí solas." | El ranking del modelo de diagnostic.ar es el prior del re-ranking; la evidencia son los síntomas y las respuestas. |

### El esquema de pregunta trazable

Cada pregunta del prototipo llevaba `pregunta`, `hpo_asociado`, `distingue` y
`motivo`. `QuestionOut` del contrato v2 mantiene esa trazabilidad:

| Prototipo | Versión 2 |
|---|---|
| `pregunta` | `text` (más `help`, una ayuda opcional para responder) |
| `hpo_asociado` | `hpo_id`, y `label` con su etiqueta oficial en español |
| `distingue` (enfermedades) | `genes_if_yes` / `genes_if_no`: los genes que suben con cada respuesta, calculados por el selector (modo médico) |
| `motivo` | `rationale` (modo médico) |

### Otras ideas

- **Revalidar la evidencia en el servidor.** El prototipo recalculaba del lado
  del servidor qué síntomas coincidían con cada candidata, en lugar de confiar
  en el LLM (`validate_candidate_evidence` en `engine.py`). La versión 2 lleva
  la idea hasta el final: toda la evidencia se calcula desde la base de
  conocimiento y el LLM nunca puntúa genes.
- **Condiciones de corte.** El prototipo terminaba por certeza suficiente
  (umbral 90), por máximo de iteraciones o por falta de preguntas útiles. La
  versión 2 conserva las tres (`confident` con umbral 0,9, `max_rounds`,
  `no_useful_questions`) y agrega `user_finished`.
- **"Diagnóstico diferencial no concluyente" como resultado legítimo.** En la
  versión 2 ningún cierre es un diagnóstico: los motivos de cierre describen por
  qué se dejó de preguntar, y el resumen es una orientación para la consulta.
- **La certeza puede bajar**, como regla de prompt y como propiedad del
  re-ranking.
- **Separar síntomas vecinos** (el prototipo reescribía preguntas que mezclaban
  convulsiones con espasticidad o hepatomegalia con esplenomegalia): hoy es la
  regla de un síntoma por pregunta, con el término fijado por el selector.

## Qué se reemplazó y por qué

| Prototipo | Versión 2 | Motivo |
|---|---|---|
| Recuperación semántica (Chroma) sobre enfermedades de Orphadata | Base HPO v2026-02-16 fijada por sha256 | El modelo de diagnostic.ar rankea genes (D-01, D-02). |
| OpenAI (generación y embeddings) | Claude con salida estructurada validada, y plantillas como respaldo | Proveedor intercambiable por configuración, y ninguna ronda depende de que el LLM responda (D-04, D-12). |
| Respuestas en texto libre con un intérprete por palabras clave | Botones sí / no / no sé y nota opcional que solo puede bajar a "no sé" | Casos simples se interpretaban mal (D-01, D-06). |
| Certeza numérica acotada por reglas (tope de 70 con evidencia mixta) | Posterior bayesiana sobre pares gen–enfermedad; los textos para pacientes no muestran porcentajes | Una cantidad calculada y evaluable en lugar de una estimada por el LLM. |
| Diagnóstico principal (una enfermedad) | Ranking de genes y resumen sin diagnóstico | La plataforma no diagnostica. |
| Sesiones en memoria del proceso | Agente sin estado; la sesión la guarda la plataforma con el diagnóstico | Autenticación, pertenencia y persistencia (D-07, D-09). |
| Scripts de PowerShell | `Makefile` e imagen Docker | Se despliega en Linux. |

## Datos

- **Human Phenotype Ontology (HPO), versión v2026-02-16**: `hp.json`,
  `phenotype.hpoa`, `genes_to_disease.txt` y `genes_to_phenotype.txt`, descargados
  de la publicación oficial y fijados en `kb/sources.py`. `hp.json` declara como
  titulares de derechos a "Peter Robinson, Sebastian Koehler, The Human Phenotype
  Ontology Consortium, and The Monarch Initiative" y su licencia en
  <https://hpo.jax.org/app/license>; el uso de estos datos sigue esos términos.
- **Etiquetas oficiales en español** del proyecto de traducciones del HPO
  (`hp-es.babelon.tsv` del repositorio `obophenotype/hpo-translations`, commit
  69f7f96).
- **Anotaciones de enfermedades**: según su encabezado, `phenotype.hpoa` reúne
  anotaciones de OMIM, DECIPHER y Orphanet. Orphanet no se lee directamente en
  tiempo de ejecución: sus anotaciones llegan a través de ese archivo del HPO.
- **Modelo de diagnostic.ar**: el agente usa su ranking de genes y sus
  especialidades sugeridas a través de `POST /rank-genes`.
