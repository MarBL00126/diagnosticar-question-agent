# Esfuerzo del LLM: medio contra alto

El agente de preguntas llama a `claude-opus-5` con esfuerzo `high`
(`PREGUNTAS_LLM_EFFORT`). Cada llamada de preguntas tardaba entre 13 y 50 s
(`docs/EVALUACION.md`, sección 7), cerca del tope de 60 s por llamada y del
presupuesto de 75 s por ronda. Esta evaluación compara `medium` con `high` con
las mismas entradas, para decidir el valor por defecto.

`python -m eval.effort_benchmark` (código en `eval/effort_benchmark.py`,
resultados crudos en `eval/results/effort/`, fuera de git).

## 1. Método

Diseño apareado: cada unidad se corre con los dos esfuerzos y exactamente la
misma entrada (se verifica con el hash del prompt de la primera llamada).

1. **Redacción de preguntas** (la llamada principal de cada ronda): ronda 1 de
   los mismos 12 casos de PRUEBA de la sección 7 de `EVALUACION.md` (8 en modo
   paciente, 4 en modo médico), por `RoundService` con sus salvaguardas y su
   reintento, con la configuración por defecto. La lista de candidatos es la
   misma para los dos esfuerzos. Se mide la latencia, si la ronda pasa las
   salvaguardas al primer intento, reintentos y caídas a plantilla, el rango
   por ganancia de información de cada síntoma elegido dentro de la lista, la
   ganancia sumada de los elegidos contra la de los 3 de mayor ganancia, y la
   cantidad de preguntas. 24 llamadas si no hay reintentos.
2. **Lectura de notas**: las 24 notas de `eval/data/notas.jsonl` en 3 lotes de
   8, como en la sección 7.2: exactitud, resultado aceptable, inversiones sí↔no
   y latencia. 6 llamadas.
3. **Resumen de cierre**: 5 sesiones (3 en modo paciente, 2 en modo médico)
   armadas con los primeros casos de cada modo. El paciente simulado de
   `eval/answerer.py` responde tres rondas de preguntas de plantilla, y dos
   respuestas llevan una nota ya leída en una ronda anterior: un «sí» con «la
   médica lo vio en el último control y quiere pedir un electroencefalograma»
   (pone a prueba la regla de no nombrar estudios ni pasos a seguir, en los dos
   modos) y un «no» con «no creo, pero no estamos seguros» (queda en «no sé»).
   La ronda de cierre pasa por `RoundService`, con `check_summary` y su
   reintento. Se mide si el resumen pasa al primer intento, las caídas a
   plantilla y la latencia. 10 llamadas si no hay reintentos.
4. **Juez ciego por pares**: para cada uno de los 12 juegos de preguntas y los
   5 resúmenes, una llamada a `claude-opus-5` con esfuerzo `high` recibe los
   datos del caso y las dos versiones como A y B, sin saber qué esfuerzo
   escribió cada una. El orden A/B está balanceado al azar (la mitad de las
   veces medio es A). La rúbrica: claridad para la familia o el médico, un
   síntoma por pregunta, fidelidad al significado del término HPO (etiquetas en
   castellano e inglés a la vista), tono neutral que no alarme, castellano
   rioplatense, y nada de diagnóstico ni consejos. Devuelve
   `{winner: A|B|tie, reasons, fidelity_issues_a, fidelity_issues_b}`; los
   textos completos y los veredictos quedan en `eval/results/effort/textos.md`
   para revisión humana. 17 llamadas.

Total previsto: 57 llamadas a `claude -p`, con un tope duro de 70. Cada llamada
pasa por un script que se usa como binario del CLI, la cuenta en
`eval/results/effort/claude_calls.count` y se niega a correr después de la
70.ª; las 17 del juez quedan reservadas para que los reintentos no las
consuman. Las respuestas quedan en caché: repetir la evaluación no gasta.

**Control del orden**: las llamadas van de a una. Los dos esfuerzos de una
unidad corren uno detrás del otro, y cuál va primero está balanceado al azar
dentro de cada tarea (semilla fija); las unidades se mezclan al azar. Así, ni
la deriva de la latencia a lo largo de la corrida ni la caché de prompts que
calienta la primera llamada de un par favorecen a un esfuerzo.

**Latencia sin censura**: cada llamada tiene 150 s, no los 60 s de producción,
para medir la cola real. Después se reproducen sobre las duraciones medidas
los topes de producción (60 s por llamada, 75 s por ronda, ninguna llamada con
menos de 10 s) para contar cuántas rondas habrían caído a plantilla.

## 2. Regla de decisión (registrada antes de correr)

Registrada el 2026-09-28 a las 19:17 (-03:00), sobre `7173342`, antes de la
primera llamada. Los umbrales están también como constantes en
`eval/effort_benchmark.py` (`decide`).

Para cada tarea (preguntas, notas, resumen), se adopta `medium` si se cumplen
las tres condiciones; si no, queda `high`:

- **(a) No es peor en seguridad.**
  - Preguntas y resumen: caídas a plantilla de medio ≤ las de alto, y rondas
    aprobadas al primer intento de medio ≥ las de alto − 1 (una de tolerancia:
    un reintento de más en 12 rondas es ruido y cuesta una llamada, no
    calidad).
  - Notas: **0 inversiones sí↔no, obligatorio**; resultados aceptables de
    medio ≥ los de alto; exactos de medio ≥ los de alto − 1; lotes fallidos de
    medio ≤ los de alto.
- **(b) El juez no prefiere alto con claridad** (preguntas y resumen; las
  notas tienen respuesta correcta y no se juzgan). Falla solo si alto gana más
  veces que medio + empates/2 **y** la prueba de signos bilateral sobre las
  victorias (sin empates) da p < 0,10. Además, las unidades en las que el juez
  marca al menos un problema de fidelidad (criterios de un síntoma por
  pregunta, fidelidad al término o a los datos, y sin consejos) no pueden ser
  en medio más que en alto + 1. Un veredicto que falla no cuenta como empate.
- **(c) Es más rápido de verdad**: la mediana de la latencia por unidad (la
  suma de las llamadas de la ronda, del lote o del cierre, reintentos
  incluidos) baja al menos 20 %: 1 − mediana(medio) / mediana(alto) ≥ 0,20.

Con las tres tareas:

- las tres en `medium` → `PREGUNTAS_LLM_EFFORT=medium` para todo;
- ninguna → queda `high`;
- algunas → esfuerzo por tarea, que hoy no existe en el código (hay un solo
  `llm_effort`) y se detalla como cambio propuesto. La redacción de preguntas
  es la llamada que pesa: corre en cada ronda y es la más larga.

Qué no decide la regla, y se informa igual: la ganancia de información de los
síntomas elegidos, la cantidad de tokens de salida, las rondas que habrían
caído a plantilla con los topes de producción, y la opinión del juez por
criterio. Con 12 y 5 pares la prueba de signos solo detecta diferencias
grandes: la regla está hecha para no cambiar a medio si el juez ve una
desventaja clara, no para demostrar que son equivalentes.

## 3. Resultados

Corrida del 2026-09-28, 21,7 minutos. **Se usaron 59 llamadas de 70**: 24 de
preguntas, 6 de notas, 12 de resumen (10 más 2 reintentos, los dos de medio) y
17 del juez. Ninguna llamada falló ni se cortó. Las entradas de cada par fueron
idénticas en las 20 unidades (mismo hash del prompt).

### 3.1 Redacción de preguntas (12 pares)

| medida | alto | medio |
|---|---|---|
| latencia p50 / p90 / máx. (s) | 23,7 / 35,7 / 42,2 | **18,0 / 24,1 / 30,0** |
| latencia media (s) | 24,1 | 18,0 |
| tiempo de API p50 (s) | 21,1 | 15,0 |
| tokens de salida p50 (media) | 1569 (1658) | 1024 (1065) |
| llamadas de más de 35 s | 2 | 0 |
| aprobadas al primer intento | 12 / 12 | 12 / 12 |
| reintentos / caídas a plantilla | 0 / 0 | 0 / 0 |
| caídas con los topes de producción (60 s, 75 s) | 0 | 0 |
| preguntas | 36 | 36 |
| elegidas entre las 3 de mayor ganancia | 29 / 36 (80,6 %) | 28 / 36 (77,8 %) |
| ganancia sumada contra las 3 de mayor ganancia (media / mín.) | 96,1 % / 84,2 % | 95,8 % / 80,2 % |
| ganancia sumada contra la elección de la plantilla | 99,8 % | 99,5 % |
| largo medio de la pregunta (caracteres) | 74 | 66 |

Medio baja la mediana un **24,1 %** (23,7 → 18,0 s) y la cola todavía más (p90
−33 %, máximo −29 %). Por pares, medio fue más rápido en 10 de 12 (prueba de
signos p = 0,039; mediana de la razón medio/alto 0,80) y el tiempo sumado de
las 12 rondas bajó de 289,6 a 216,5 s (−25 %). La diferencia viene del
razonamiento: 36 % menos tokens de salida. La llamada que corre segunda en su
par sale un poco más rápida (razón mediana 0,77 cuando alto fue primero, 0,87
cuando medio fue primero), un efecto que el orden balanceado cancela.

La elección de síntomas no cambia: las dos eligen casi siempre el primer
candidato y la ganancia de información sumada es la misma dentro del ruido.

### 3.2 Lectura de notas (3 pares de lotes de 8)

| medida | alto | medio |
|---|---|---|
| latencia por lote p50 / p90 / máx. (s) | 14,8 / 22,0 / 23,8 | 12,6 / 16,3 / 17,3 |
| exactas en las 20 inequívocas | 20 / 20 | 20 / 20 |
| exactas contra el valor esperado, las 24 | 23 / 24 | 21 / 24 |
| resultado aceptable, las 24 | 24 / 24 | 24 / 24 |
| **inversiones sí↔no** | **0** | **0** |
| lecturas faltantes / lotes fallidos | 0 / 0 | 0 / 0 |

Las dos diferencias están en notas ambiguas con el botón «no»: «no creo» y
«no que yo sepa». Alto las baja a «no sé», medio las deja en «no». Las dos
lecturas son aceptables, pero medio es menos prudente con un «no» dicho con
reservas. La mediana baja 14,7 %, con 3 pares.

### 3.3 Resumen de cierre (5 pares)

| medida | alto | medio |
|---|---|---|
| latencia por cierre p50 / p90 / máx. (s) | 19,3 / 23,7 / 25,1 | 15,2 / 23,7 / 24,5 |
| latencia por llamada p50 (s) | 19,3 | 13,5 |
| aprobados al primer intento | 5 / 5 | **3 / 5** |
| reintentos / caídas a plantilla | 0 / 0 | 2 / 0 |
| largo medio del resumen final (caracteres) | 630 | 578 |

Los dos reintentos de medio fueron en modo paciente y por largo (722 y 770
caracteres, el tope es 700); uno además decía «se confirmó que hubo aumento de
peso», que la salvaguarda lee como consejo («confirm…») aunque es un repaso.
Ningún borrador, en ninguno de los dos esfuerzos, nombró el electroencefalograma
de la nota ni propuso estudios o pasos a seguir: la regla sin consejos se
cumplió en los 12 borradores. Con el reintento, medio tarda lo mismo que alto:
fue más rápido solo en 2 de los 5 pares (mediana de la razón 1,04). El 21,4 %
de la regla sale de comparar las medianas de cada brazo, no de los pares.

### 3.4 Juez ciego

| tarea | gana alto | gana medio | empate | prueba de signos | unidades con problemas de fidelidad (alto / medio) |
|---|---|---|---|---|---|
| preguntas (12) | 4 | 3 | 5 | p = 1,0 | 6 / 7 |
| resúmenes (5) | 3 | 2 | 0 | p = 1,0 | 2 / 4 |
| total (17) | 7 | 5 | 5 | p = 0,77 | 8 / 11 |

Los 17 veredictos se obtuvieron sin fallas (mediana 27,7 s por llamada).

**Sesgo de posición**: en las preguntas el juez eligió A 3 veces y B 4; en los
resúmenes eligió A las 5 veces. El orden estaba balanceado (medio fue A en 2 de
los 5), así que el ganador de cada resumen coincide con el que se mostró
primero, y ese resultado vale poco como comparación. Los problemas de fidelidad
que marca, en cambio, son concretos y verificables (sección 3.5).

Patrones en lo que marcó el juez:

- **Preguntas, medio en modo paciente**: tiende a convertir un hallazgo que la
  familia puede observar en «¿Algún médico mencionó…?», lo que achica el
  término (obesidad, artritis, autoinmunidad, alteración del comportamiento;
  marcado en 3 de 8 casos).
- **Preguntas, alto**: agrega ejemplos o matices que no están en el término
  («mucha dificultad para ver con poca luz», «al nacer», enojos y gestos
  repetidos como ejemplos de alteración del comportamiento) y a veces encadena
  varias formulaciones en una pregunta.
- **Compartido**: las dos versiones preguntan por micrognatia como «mentón más
  chico o más hacia atrás» (esto último es retrognatia). Es un problema del
  prompt, no del esfuerzo.
- **Resúmenes, medio en modo paciente**: deja las etiquetas técnicas tal cual
  («anoniquia», «hipertricosis», «nictalopia», «polidactilia postaxial»), que
  el prompt permite pero una familia no entiende; alto las traduce, y el juez
  a veces marca esa traducción como cambio de alcance («aumento de peso» por
  obesidad). También describe con menos precisión el movimiento de la lista
  («algunas subieron y otras bajaron» cuando solo bajó un gen) y confunde «no
  sé» con «sin respuesta».

### 3.5 Ejemplos (todos en `eval/results/effort/textos.md`)

Paciente, GFAP. Alto tardó 42,2 s, medio 12,6 s, y el juez prefirió medio:

- Alto: «¿Se notó rigidez en las piernas o los brazos, con los músculos más
  duros o tensos de lo habitual?» (para «Disfunción del tracto
  corticoespinal», más específica que el término); ayuda del mioclono palatino:
  «A veces se percibe como un chasquido rítmico en el oído o en la garganta»
  (otro signo).
- Medio: «¿Se notó alguna vez un movimiento rítmico y repetido del paladar,
  adentro de la boca?»; «¿Se notó alguna vez dificultad para pensar, aprender
  cosas nuevas o recordar?».

Paciente, IQSEC2. El juez prefirió alto:

- Alto: «¿Se notó alguna vez un comportamiento llamativo o distinto de lo
  habitual para la edad?» (ayuda: «Puede ser algo señalado en casa, en la
  escuela o en una consulta»).
- Medio: «¿Algún médico mencionó alguna vez que hubiera comportamientos poco
  habituales para la edad?» (ayuda: «Si nunca se habló de esto con un médico,
  respondé «No sé»»): deja afuera lo que la familia observa.

Médico, SGCA. El juez prefirió medio:

- Alto: «¿Hay aumento del tejido conectivo endomisial?», un hallazgo de
  biopsia que la base marca como no dependiente de estudios.
- Medio: «¿Tuvo alguna vez insuficiencia cardíaca congestiva?» (ayuda: «Cuadro
  ya diagnosticado por un médico o internación por descompensación
  cardíaca.»).

Resumen para paciente, SMARCA4. El juez prefirió alto:

- Alto: «Se respondió que sí a un crecimiento más lento de lo esperado, a un
  labio inferior más grueso, a huesos poco desarrollados en los dedos de los
  pies y a la falta del dedo gordo del pie…».
- Medio: «…se informó que sí hubo retraso en el crecimiento, bermellón del
  labio inferior grueso, hipoplasia de las falanges de los dedos de los pies y
  ausencia del dedo gordo del pie…».

## 4. Decisión

| tarea | (a) no peor | (b) juez | (c) ≥ 20 % más rápido | esfuerzo |
|---|---|---|---|---|
| preguntas | sí: 12/12 contra 12/12, 0 caídas | sí: 4–3–5, p = 1,0; fidelidad 7 ≤ 6 + 1 | sí: −24,1 % | **medium** |
| notas | **no**: 21 exactas contra 23 (tolerancia 1); 0 inversiones | no aplica | **no**: −14,7 % | high |
| resumen | **no**: 3/5 al primer intento contra 5/5 | **no**: fidelidad 4 > 2 + 1 | sí: −21,4 % | high |

**Resultado de la regla: división por tarea. `medium` para redactar las
preguntas y `high` para leer las notas y escribir el resumen.**

La redacción de preguntas es la llamada que corre en cada ronda y la que se
acercaba a los topes. Con medio, la mediana baja de 23,7 a 18,0 s y el máximo
de 42,2 a 30,0 s, sin rechazos de las salvaguardas, con la misma elección de
síntomas y con el juez parejo (4 a 3 y 5 empates). Sus defectos son distintos
de los de alto, no más: medio tiende al «¿Algún médico mencionó…?» y alto a
agregar ejemplos. Pasar todo a medio no está respaldado: en el resumen reintenta
más (2 de 5, sin ahorrar tiempo en los pares) y deja jerga en el texto para
familias, y en las notas es menos prudente con los «no» dudosos. Esas dos
llamadas son una por sesión, así que dejarlas en alto cuesta poco.

### Cambio propuesto (no aplicado)

Hoy hay un solo `llm_effort`. Hace falta un esfuerzo propio para
`QuestionTask`:

1. `config.py`: `llm_question_effort: Effort = "medium"`, junto a
   `llm_effort: Effort = "high"` (que queda para notas y resumen); variable
   `PREGUNTAS_LLM_QUESTION_EFFORT`.
2. `llm/claude_cli.py` y `llm/anthropic_api.py`: un parámetro
   `question_effort: Effort | None = None` en el constructor, y el esfuerzo de
   cada llamada es `question_effort` si `request.task` es `QuestionTask` (y no
   es None), si no `effort`: en `build_argv` va a `--effort`, en
   `AnthropicProvider._generate` a `"effort"`.
3. `llm/factory.py`: pasar `question_effort=settings.llm_question_effort` a los
   dos proveedores.
4. Pruebas: el valor por defecto en `tests/agent/test_settings.py`, y que
   `build_argv` lleve `--effort medium` para una `QuestionTask` y
   `--effort high` para `NotesTask` y `SummaryTask`.
5. Documentación: la fila de la variable en `README.md`, `.env.example` y la
   lista de variables de `docs/INTEGRACION.md`.

El reintento de preguntas también es una `QuestionTask` y va con medio. Si se
prefiere no tocar código, `PREGUNTAS_LLM_EFFORT=medium` pasaría todo a medio,
que la regla descarta para notas y resumen.

## 5. Limitaciones

- **Tamaño**: 12 pares de preguntas, 5 de resúmenes y 3 lotes de notas. Alcanza
  para ver una diferencia de latencia sistemática (10 de 12 pares) y para
  descartar una ventaja grande de alto según el juez; no para afirmar que la
  calidad es igual ni para estimar tasas de rechazo raras.
- **El juez** es el mismo modelo, con esfuerzo alto, y juzga textos del mismo
  modelo; el sesgo hacia textos propios afecta a los dos brazos por igual. En
  los resúmenes eligió siempre la versión A (sección 3.4). Ningún médico revisó
  todavía los textos: `eval/results/effort/textos.md` los tiene completos, con
  los veredictos, para esa revisión.
- **Latencia de una sola corrida**, de a una llamada por vez y desde una
  máquina: con carga o en otro horario los valores absolutos cambian; la
  comparación apareada y balanceada es lo que vale. En esta corrida ninguna
  llamada de alto pasó de 60 s; en la sección 7.3 de `EVALUACION.md` dos
  llegaron a 47 y 50 s.
- **Costo**: por `claude -p` el costo equivalente por llamada casi no cambia
  (unos US$ 0,32 por llamada de preguntas en los dos esfuerzos), porque lo
  domina la entrada. Con la API directa, el 36 % menos de tokens de salida sí
  se notaría.
- **Casos simulados** con términos HPO exactos, y resúmenes armados con
  respuestas del paciente simulado y dos notas fijas.

## 6. Cómo reproducir

Requisitos: `data/kb.sqlite`, el servicio del modelo de diagnostic.ar en
`http://127.0.0.1:18021` (o `EVAL_MODEL_URL`) y el CLI `claude` con sesión
iniciada.

```sh
.venv/bin/python -m eval.effort_benchmark --dry-run   # sin llamadas: plantilla y veredicto fijo
.venv/bin/python -m eval.effort_benchmark --max-calls 70
```

Salidas en `eval/results/effort/`: `results.json` (resumen, decisión, cada
unidad con sus llamadas, tiempos, tokens y textos, y los veredictos),
`textos.md` (para revisión humana), `claude_calls.count` (el contador del
script) y `cache/` (las respuestas; con ellas, repetir no gasta llamadas).
