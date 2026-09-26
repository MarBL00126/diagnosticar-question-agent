# Decisiones de diseño

Registro corto, al estilo de los ADR, de las decisiones que dieron forma a la
versión 2 del agente. Cada entrada dice el contexto, lo decidido, las
alternativas, las consecuencias y la fecha. El funcionamiento está en
[ARQUITECTURA.md](ARQUITECTURA.md); las cifras de evaluación, en
[EVALUACION.md](EVALUACION.md).

| Id | Decisión | Fecha |
|---|---|---|
| D-01 | Reescribir el prototipo en lugar de portarlo | 2026-09-24 / 26 |
| D-02 | Claves por gen sobre las anotaciones del HPO | 2026-09-26 |
| D-03 | Hipótesis gen–enfermedad, no perfiles de gen fusionados | 2026-09-26 |
| D-04 | Selección híbrida: ganancia de información y Claude eligiendo de una lista corta | 2026-09-26 |
| D-05 | Rondas de 3 preguntas, como mucho 3 rondas | 2026-09-26 |
| D-06 | Botones y nota opcional; la nota solo puede bajar a "no sé" | 2026-09-26 |
| D-07 | Agente sin estado; Express es dueño de la sesión | 2026-09-26 |
| D-08 | Base de conocimiento propia, fijada por sha256 | 2026-09-26 |
| D-09 | Sesión guardada en `diagnoses.preguntas_respuestas` | 2026-09-26 |
| D-10 | Pacientes y médicos, con modo por ronda | 2026-09-26 |
| D-11 | `fixed_prior` y temperatura 1, elegidos por la evaluación | 2026-09-26 |
| D-12 | `claude-cli` en local y staging, API de Anthropic en producción | 2026-09-26 |
| D-13 | El prompt va por stdin | 2026-09-26 |
| D-14 | Presupuesto de tiempo por ronda | 2026-09-26 |
| D-15 | El trabajo vive en el repositorio de Mariano, en ramas propias | 2026-09-26 |

---

## D-01. Reescribir el prototipo en lugar de portarlo

- **Contexto.** El primer prototipo (commit c2e3548, 2026-09-18) era un sistema
  RAG: indexaba en Chroma las enfermedades de Orphadata (`en_product4.xml`) con
  embeddings de OpenAI, y un LLM de OpenAI generaba preguntas y un diagnóstico
  diferencial a partir de lo recuperado. La revisión del 2026-09-24 encontró:
  - **Claves por enfermedad de Orphanet**, mientras que el modelo de
    diagnostic.ar devuelve genes. Medido en esa revisión: puenteando genes a
    enfermedades de Orphanet se alcanzaba el 61,2 % de los genes del modelo;
    usando las anotaciones de genes del HPO, el 97,8 %.
  - Los términos con frecuencia **"Excluded (0%)" se indexaban como fenotipos**
    de la enfermedad (en la lista de "Fenotipos HPO relevantes" y como
    "Preguntar por X para confirmar o descartar"): una exclusión se leía como
    un rasgo.
  - El **intérprete de respuestas en texto libre** clasificaba un "Sí" o un "No"
    sueltos como desconocidos (descartaba las palabras de menos de 3 letras) y
    "Tiene convulsiones pero no fiebre" como negado (cualquier "no" negaba).
  - La salida incluía un **`diagnostico_principal`** (una enfermedad), contra la
    regla de no diagnosticar.
  - **Sesiones en memoria y sin autenticación**; `POST /sessions` guardaba la
    sesión antes de evaluarla, así que una evaluación fallida (500) la dejaba
    guardada.
  - Los tests no cubrían recuperación ni LLM (su README lo aclara: "No llaman a
    OpenAI").
- **Decisión.** Empezar la versión 2 sobre un esqueleto limpio (commit b7cc6c6),
  con otro paquete (`diagnosticar_preguntas`), y conservar del prototipo lo que
  seguía siendo válido: sus reglas clínicas, su esquema de pregunta trazable y
  sus condiciones de corte ([CREDITOS.md](CREDITOS.md)).
- **Alternativas.** Portar incrementalmente: puentear gen → Orphanet (la opción
  que el plan de integración de la plataforma proponía medir primero), o
  alimentarlo solo con el texto del caso, ignorando el ranking del modelo.
- **Consecuencias.** Casi no queda código del prototipo; queda entero en la
  historia de git. Los créditos se documentan explícitamente.
- **Fecha.** Revisión 2026-09-24; esqueleto 2026-09-26.

## D-02. Claves por gen sobre las anotaciones del HPO

- **Contexto.** El agente refina un ranking de genes. La versión v2026-02-16
  del HPO, la misma con la que se entrenó el modelo de diagnostic.ar, publica
  anotaciones de enfermedades (`phenotype.hpoa`) y tablas gen–enfermedad
  (`genes_to_disease.txt`, `genes_to_phenotype.txt`).
- **Decisión.** Construir todo sobre esos archivos: genes unidos a enfermedades
  (OMIM y ORPHA) y enfermedades unidas a términos HPO.
- **Alternativas.** El puente a Orphanet de D-01 (61,2 % de cobertura).
- **Consecuencias.** 5128 de los 5229 genes del vocabulario del modelo están en
  la base ([EVALUACION.md](EVALUACION.md), sección 2). Los XML de Orphanet no se
  leen; las anotaciones de enfermedades ORPHA llegan a través de `phenotype.hpoa`.
- **Fecha.** 2026-09-26 (commit 09c1af7).

## D-03. Hipótesis gen–enfermedad, no perfiles de gen fusionados

- **Contexto.** Muchos genes causan varias enfermedades con síntomas distintos.
- **Decisión.** Cada hipótesis es un par (gen, enfermedad); el prior del gen se
  reparte entre sus enfermedades anotadas y el puntaje del gen es la suma.
- **Alternativas.** Un perfil por gen que une los síntomas de todas sus
  enfermedades: empeora el ranking de forma medible (lo documenta
  `domain.Hypothesis`), porque mezcla síntomas que nunca aparecen juntos.
- **Consecuencias.** La base necesita la tabla `gene_disease`; un gen sin
  enfermedades anotadas tiene una hipótesis `unannotated` al ritmo de fondo.
- **Fecha.** 2026-09-26.

## D-04. Selección híbrida

- **Contexto.** Que el LLM elija libremente qué preguntar no es medible ni
  auditable; la ganancia de información pura puede elegir síntomas muy
  correlacionados o difíciles de responder, y no redacta.
- **Decisión.** El selector arma una lista corta de 8 términos por ganancia de
  información esperada sobre los genes, diversificada; Claude elige de ahí las
  preguntas y las redacta para el modo pedido. El código revisa cada borrador,
  reintenta una vez con las violaciones y completa con plantillas.
- **Alternativas.** Solo Claude (como el prototipo); solo ganancia de
  información con plantillas (es lo que usa la evaluación simulada).
- **Consecuencias.** La elección de síntomas se evalúa sin LLM y la redacción
  por separado. En vivo, 12 de 12 rondas pasaron las salvaguardas al primer
  intento; 28 de las 36 elecciones de Claude estuvieron entre las 3 de mayor
  ganancia, y la ganancia sumada de lo que eligió fue en promedio el 100,3 % de
  la de la plantilla (mínimo 87,7 %) ([EVALUACION.md](EVALUACION.md), sección 7.1).
- **Fecha.** 2026-09-26 (commit 045daae).

## D-05. Rondas de 3 preguntas, como mucho 3 rondas

- **Contexto.** Cada pregunta le cuesta tiempo a una familia; el prototipo
  permitía hasta 7 iteraciones.
- **Decisión.** El contrato fija como máximo 3 preguntas por ronda y 3 rondas
  (`QUESTIONS_PER_ROUND`, `MAX_ROUNDS`); la configuración puede bajarlos. La
  sesión corta antes si el primer gen llega a 0,9 o si ninguna pregunta aporta
  0,05 bits, y la persona puede terminar cuando quiera.
- **Alternativas.** Una sola ronda larga; más rondas.
- **Consecuencias.** En la evaluación, 3,25 preguntas en promedio y ninguna en
  165 de 300 casos, en los que el primer gen ya era el correcto el 95,7 % de las
  veces ([EVALUACION.md](EVALUACION.md), sección 6.1).
- **Fecha.** 2026-09-26.

## D-06. Botones y nota opcional; la nota solo puede bajar a "no sé"

- **Contexto.** El intérprete de texto libre del prototipo fallaba en casos
  simples (D-01).
- **Decisión.** La respuesta es el botón: "sí", "no" o "no sé". La nota (hasta
  280 caracteres) es opcional; Claude la lee una sola vez y solo puede marcar la
  respuesta como dudosa, que la baja a "no sé". Nunca cambia un sí por un no.
  La plataforma devuelve la lectura en las rondas siguientes para que no se
  vuelva a interpretar.
- **Alternativas.** Texto libre con un intérprete; dejar que el LLM decida el
  valor de la respuesta.
- **Consecuencias.** En la prueba en vivo con 24 notas, ninguna respuesta pasó
  de sí a no ni al revés, y una nota con un intento de inyección no cambió
  nada ([EVALUACION.md](EVALUACION.md), sección 7.2). Eso está garantizado por el
  código, no solo observado.
- **Fecha.** 2026-09-26.

## D-07. Agente sin estado; Express es dueño de la sesión

- **Contexto.** Las sesiones del prototipo vivían en la memoria del proceso y
  sin autenticación; cada ronda gasta llamadas a un LLM.
- **Decisión.** `POST /v1/rounds` recibe la sesión completa y no guarda nada. El
  backend Express autentica, limita por cuenta, verifica que el diagnóstico sea
  de quien pregunta, arma la evidencia desde el diagnóstico guardado (nunca
  desde el navegador) y guarda cada ronda.
- **Alternativas.** Sesiones en el agente (el plan inicial de integración las
  dejaba ahí "por ahora"); que el navegador llame al agente directamente.
- **Consecuencias.** Reiniciar el agente no pierde nada y se puede replicar.
  Reintentos y respuestas perdidas se resuelven en Express, que repite la ronda
  guardada sin llamar de nuevo al agente.
- **Fecha.** 2026-09-26.

## D-08. Base de conocimiento propia, fijada por sha256

- **Contexto.** Hacen falta la clausura de la ontología, frecuencias,
  negaciones y etiquetas oficiales en español, y que el resultado se pueda
  reproducir.
- **Decisión.** Una base SQLite construida desde cinco fuentes fijadas por URL y
  sha256, reconstruida dentro de la imagen y cargada en memoria al arrancar.
- **Alternativas.** Descargar o consultar fuentes en tiempo de ejecución;
  versionar la base en git.
- **Consecuencias.** Unos 32 MB y un segundo de carga; una construcción con
  fuentes que no coinciden falla. El archivo es idéntico byte a byte con la
  misma versión de SQLite; con otra, el sha256 puede cambiar
  ([ARQUITECTURA.md](ARQUITECTURA.md), sección 3).
- **Fecha.** 2026-09-26 (commit 09c1af7).

## D-09. Sesión guardada en `diagnoses.preguntas_respuestas`

- **Contexto.** Las respuestas son datos clínicos y pertenecen al diagnóstico.
- **Decisión.** Una columna JSONB nullable en `diagnoses`, con la sesión
  versionada (`{version: 2, finished, rounds}`) y la respuesta validada del
  agente completa, incluidas sus `versions`, bajo las mismas reglas de
  pertenencia que el resto de la fila. Se escribe con compare-and-set y se lee
  con un esquema más tolerante que el contrato (migra la versión 1).
- **Alternativas.** Dejar la sesión en el agente (el plan inicial).
- **Consecuencias.** Un refinamiento guardado se puede rastrear hasta la base de
  conocimiento y el modelo de LLM que lo produjeron; `versions` no se manda al
  navegador.
- **Fecha.** 2026-09-26.

## D-10. Pacientes y médicos, con modo por ronda

- **Contexto.** Lo usan tanto familias como médicos. Un médico que le pregunta
  al paciente necesita otra redacción, y le sirven los genes que separa cada
  pregunta; a una familia no se le muestran genes.
- **Decisión.** Cada pedido trae su `mode` (`paciente` o `medico`), que en la
  interfaz sale del selector "Ver detalle médico": cambiarlo cambia la
  redacción desde la ronda siguiente, y cada ronda guarda el modo en que se
  escribió. `genes_if_yes`, `genes_if_no` y `rationale` solo existen en modo
  médico; el backend los vuelve a quitar en modo paciente.
- **Alternativas.** Un solo modo por sesión, que es como la primera versión
  de la sesión guardada en el backend lo registraba.
- **Consecuencias.** Las lecturas de notas se reenvían, así que la respuesta
  efectiva no cambia al cambiar de modo. La evaluación da resultados similares
  en ambos modos ([EVALUACION.md](EVALUACION.md), sección 6.4).
- **Fecha.** 2026-09-26 (contrato v2, commit c3d7033).

## D-11. `fixed_prior` y temperatura 1, elegidos por la evaluación

- **Contexto.** El plan inicial mandaba al modelo los síntomas respondidos
  "sí" junto con los originales (`model_sees_present`, fijo en el código) y
  aplanaba su ranking con temperatura 3, suponiendo que el modelo estaba
  sobreconfiado. Con esa configuración, en los 300 casos de prueba las
  preguntas **no ayudaban**: el MRR del gen verdadero bajaba de 0,787 a 0,759
  (Δ −0,029, IC 95 % [−0,063; +0,006]), el agente empeoraba el puesto del gen
  correcto en el 20,7 % de los casos y, con un paciente que no se ajusta a las
  anotaciones (respondedor `dropout`), el MRR caía 0,112.
- **Decisión.** Estrategia `fixed_prior` por defecto (el modelo ve siempre solo
  los hallazgos originales y todas las respuestas entran por la verosimilitud)
  y temperatura 1. Se eligió en el conjunto de ajuste y se confirmó en el de
  prueba: MRR 0,842 (Δ +0,054, IC [+0,033; +0,078]), top-1 de 72,0 % a 79,3 %.
  La ablación en prueba muestra que ambas cuentan: con `model_sees_present` el
  MRR queda en 0,799 y con T = 3 en 0,805. Con `dropout` ya no empeora (Δ
  +0,018, IC [−0,009; +0,043]).
- **Alternativas.** `model_sees_present` (sigue disponible con
  `PREGUNTAS_RERANK_STRATEGY`); temperaturas 2, 3 o 5.
- **Consecuencias.** Revierte el plan inicial. `specialties` y `confidence` de
  cada respuesta describen los hallazgos originales y no cambian entre rondas.
  Con pacientes reales el modelo puede estar más sobreconfiado y T > 1 volver a
  convenir: hay que medirlo antes de fijarla ([EVALUACION.md](EVALUACION.md),
  secciones 5, 6 y 8).
- **Fecha.** 2026-09-26 (evaluación en 46e7daa, valores adoptados en c3d7033).

## D-12. `claude-cli` en local y staging, API de Anthropic en producción

- **Contexto.** Localmente y en staging se usa una suscripción de Claude a
  través del CLI; en producción corresponde una clave de API.
- **Decisión.** Una única interfaz de proveedor (`LLMProvider`) con tres
  implementaciones: `claude-cli`, `anthropic` y `template`. Se cambia con
  `PREGUNTAS_LLM_PROVIDER`, sin tocar código.
- **Alternativas.** Usar solo la API, también en desarrollo.
- **Consecuencias.** La imagen trae el CLI fijado (2.1.270, sin Node). Los dos
  proveedores usan salida estructurada con el mismo esquema y los mismos
  errores tipados; `/healthz` informa si hay credenciales (sin validarlas, para
  no gastar cuota).
- **Fecha.** 2026-09-26 (commit 045daae).

## D-13. El prompt va por stdin

- **Contexto.** Los argumentos de un proceso se pueden leer desde cualquier
  cuenta del host en `/proc/<pid>/cmdline`, y el agente puede correr en hosts
  compartidos.
- **Decisión.** Con `claude-cli`, el mensaje con los datos del caso va por
  stdin; por argumentos solo van las reglas fijas y el esquema de salida.
- **Alternativas.** Pasar el prompt como argumento de `claude -p`, que es como
  lo hacía la primera versión del proveedor (commit 045daae).
- **Consecuencias.** Lo fija un test
  (`test_the_case_data_goes_on_stdin_never_in_the_arguments`).
- **Fecha.** 2026-09-26 (commit 831f216).

## D-14. Presupuesto de tiempo por ronda

- **Contexto.** Una llamada de preguntas tardó 22,5 s de mediana y hasta 34,4 s
  en la evaluación en vivo, y dos llegaron a 47 y 50 s después de reescribir los
  prompts. El backend espera 100 s; una ronda que termina después se pierde y
  su cuota se gasta igual.
- **Decisión.** 75 s por ronda para llamadas al LLM, 60 s como tope por llamada,
  ninguna llamada con menos de 10 s por delante, y plantillas con advertencia
  cuando no alcanza el tiempo. La cola espera como mucho 10 s y el modelo 10 s.
- **Alternativas.** Sin presupuesto (cada llamada con su propio timeout); bajar
  el esfuerzo del LLM, a costa de calidad.
- **Consecuencias.** Invariante: el que llama espera más que 10 + 75 + 10 = 95 s.
  Con un LLM lento la persona recibe preguntas de plantilla en vez de un error.
- **Fecha.** 2026-09-26 (commit c3d7033).

## D-15. El trabajo vive en el repositorio de Mariano, en ramas propias

- **Contexto.** diagnostic.ar es un Trabajo Profesional de Ingeniería en
  Informática (FIUBA), y el trabajo de cada integrante se acredita en los
  repositorios de los módulos que creó. Mariano Brizuela creó este repositorio.
- **Decisión.** La versión 2 se hace en la rama `feat/agente-claude` de este
  repositorio, sobre su historia. Los cambios de la plataforma van en ramas
  separadas del repositorio de diagnostic.ar: `feat/question-agent-backend`
  (backend e infraestructura) y `feat/question-agent-ui` (interfaz).
- **Alternativas.** Un repositorio nuevo, o el agente dentro del repositorio de
  la plataforma.
- **Consecuencias.** La plataforma construye la imagen desde este repositorio
  privado, con una clave de despliegue y un commit fijado
  ([INTEGRACION.md](INTEGRACION.md)).
- **Fecha.** 2026-09-26.
