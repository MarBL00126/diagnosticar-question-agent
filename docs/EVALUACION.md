# Evaluación del agente de preguntas

Este documento describe cómo se evaluó el agente de preguntas de seguimiento de
diagnostic.ar, qué resultados dio y qué valores por defecto se adoptaron a
partir de ellos. Todas las cifras salen de corridas reales del código en `eval/`
(26/09/2026) y se pueden reproducir con los comandos del final. El modelo
evaluado se describe en [ARQUITECTURA.md](ARQUITECTURA.md) y las decisiones que
salieron de acá, en [DECISIONES.md](DECISIONES.md) (D-11). Las mismas corridas,
expresadas como mejoras absolutas y relativas con intervalos de confianza (por
subgrupo, por ronda y por pregunta), están en [IMPACTO.md](IMPACTO.md).

**Los valores por defecto del código son hoy la configuración elegida acá**
(sección 8): estrategia `fixed_prior`, temperatura 1, fondo 0,1, frecuencia
faltante 0,3, umbral de confianza 0,9 y ganancia mínima 0,05. «Configuración
anterior» se refiere a los valores por defecto que tenía el código hasta el commit
46e7daa, guardados en `eval/configs/defaults_before_tuning.json`.

**Resumen.** Con la configuración anterior del código, las preguntas no
mejoraban el ranking del modelo de diagnostic.ar: tras tres rondas, el MRR del gen
verdadero cae de 0,787 a 0,759 (Δ −0,029, IC 95 % [−0,063; +0,006]), y bajo un
paciente menos parecido al modelo de verosimilitud cae mucho más (Δ −0,112). Con
la configuración ajustada en el conjunto de ajuste (prior fijo, temperatura 1,
umbrales de cierre algo más exigentes), hoy la del código, en el conjunto de
prueba el agente mejora el top-1 en +7,3 puntos (IC [+3,7; +11,3]) y el MRR en
+0,054 (IC [+0,033; +0,078]) con 3,25 preguntas en promedio, supera a las dos
heurísticas de referencia y, bajo el respondedor pesimista, ya no empeora el
ranking (Δ MRR +0,018, IC [−0,009; +0,043]). Claude redactó las 36 preguntas en
vivo sin ninguna violación de las salvaguardas y leyó las 24 notas de prueba sin
invertir nunca un sí en un no.

## 1. Qué se mide

El agente recibe el ranking de genes del modelo de diagnostic.ar, calculado a
partir de los términos HPO que informó la familia. Hace hasta 3 rondas de 3
preguntas de sí/no sobre síntomas, elegidas por ganancia de información esperada
y redactadas por Claude, y reordena los genes con las respuestas mediante un
re-ranking bayesiano (ver `ranking/` y `selection/`). La pregunta de la
evaluación es si ese reordenamiento **acerca el gen verdadero al primer puesto**,
cuánto cuesta en preguntas y en tiempo, y con qué parámetros funciona mejor.

Métricas, calculadas tras 0, 1, 2 y 3 rondas de respuestas:

- **top-k** (k = 1, 3, 5, 10, 20): porcentaje de casos con el gen verdadero entre
  los k primeros.
- **MRR**: media de 1/puesto del gen verdadero (0 si queda fuera de los 50 genes
  que devuelve el modelo).
- **mediana del puesto** (51 si queda fuera del top-50).
- **empeora / mejora**: porcentaje de casos en que el puesto del gen verdadero
  quedó peor / mejor que antes de preguntar.
- **preguntas**: preguntas respondidas en promedio.
- **motivo de cierre** y **tiempo de cómputo por ronda**.
- Para las comparaciones principales, **diferencias pareadas** (mismos casos) con
  intervalo de confianza del 95 % por bootstrap de percentiles (2000
  remuestreos de casos).

## 2. Pacientes simulados

No hay un conjunto de pacientes reales con respuestas a preguntas de
seguimiento, así que los casos se simulan a partir de la base de conocimiento
del agente (HPO 2026-02-16, `phenotype.hpoa` y `genes_to_disease.txt`, base
`kb1+hpo.2026-02-16`). El código está en `eval/cases.py` y la lista de casos, con
semilla fija 20260926, en `eval/data/cases.json`.

**Filtros.** Una enfermedad es elegible si:

1. tiene **un único gen** asociado (con varios genes no hay una respuesta
   correcta única);
2. ese gen está en el **vocabulario del modelo**: el modelo devuelve 5229 genes
   (se obtuvieron pidiendo `top_k` = 5229); de los 5504 genes de la base, 5128
   están en ese vocabulario. Un gen fuera de él nunca puede aparecer en el
   ranking, así que esos casos se excluyen;
3. tiene **al menos 8 anotaciones fenotípicas positivas** distintas.

Quedan 4427 enfermedades OMIM y 1571 ORPHA. Se muestrearon 250 de cada fuente
(estratificado), sin repetir gen, y se dividieron en **AJUSTE** (100 + 100) y
**PRUEBA** (150 + 150). Los parámetros solo se eligieron mirando AJUSTE.

**Hallazgos informados.** Por caso se sortean de 3 a 6 términos presentes
(uniforme), sin reposición y con peso igual a la frecuencia de la anotación
(0,5 si no está indicada), sin elegir dos términos emparentados (ancestro y
descendiente). Con probabilidad 0,5 se agrega un término negado: un fenotipo
anotado en alguna otra enfermedad que la enfermedad verdadera no respalda de
ninguna forma. Composición: en PRUEBA, 73/77/77/73 casos con 3/4/5/6 términos y
130 con un término negado.

El modelo conoce todos los términos informados (ningún `unknown_hpo_ids`) y
ubica el gen verdadero dentro de su top-50 en 296 de 300 casos de PRUEBA (194 de
200 en AJUSTE): ese es el techo de cualquier re-ranking, que solo reordena esos
50 genes.

## 3. Respondedor simulado

`eval/answerer.py`. Por caso se sortea una única vez el conjunto latente de
hallazgos del paciente: cada anotación positiva de la enfermedad verdadera está
presente con su frecuencia (0,6 si no está indicada) y los términos informados
están presentes siempre. Luego cada pregunta sobre el término *t* se responde así:

1. **Verdad**: «sí» si algún hallazgo latente es *t* o un descendiente de *t*
   (tener «convulsiones focales» implica tener «convulsiones»). Si no, «sí» con
   probabilidad 0,02 (hallazgos espurios), salvo que la enfermedad excluya *t*
   con una anotación `NOT`.
2. **«No sé»** con probabilidad 0,2; 0,5 si *t* solo se detecta con un estudio
   (`requires_testing`) y el modo es paciente.
3. **Error**: la respuesta verdadera se invierte con probabilidad 0,1.

Cada sorteo usa una semilla derivada de (caso, término): un caso responde
igual a la misma pregunta sea cual sea la política que la haga y en qué orden
(números aleatorios comunes, que reducen la varianza de las comparaciones).

Variantes:

| respondedor | frecuencia faltante | espurios | inversión | «no sé» (estudio) | anotaciones que conserva |
|---|---|---|---|---|---|
| `default` | 0,6 | 0,02 | 0,1 | 0,2 (0,5) | 100 % |
| `dropout` | 0,6 | 0,05 | 0,1 | 0,2 (0,5) | 70 % al azar |
| `noiseless` | 0,6 | 0,02 | 0 | 0 (0) | 100 % |

### La circularidad

La simulación es **circular** y hay que leer sus resultados con eso en mente:

- Los pacientes se generan con las mismas anotaciones HPO con las que el agente
  calcula la verosimilitud `P(t | enfermedad)`. Marginalmente, la probabilidad
  de responder «sí» es el mismo noisy-OR de frecuencias que usa el agente. Un
  agente que modela el mundo exactamente como el simulador tiene ventaja.
- Además, los casos salen de anotaciones de enfermedades conocidas, que muy
  probablemente se parecen a los datos con que se entrenó el modelo de
  diagnostic.ar. El modelo acierta el top-1 en el 72 % de los casos de PRUEBA,
  bastante más de lo esperable con pacientes reales, cuyos términos vienen del
  extractor de texto libre.

Para no premiar trivialmente al agente, los parámetros del respondedor difieren
de los del agente (frecuencia faltante 0,6 contra 0,3/0,5; espurios 0,02 contra
un fondo de 0,02–0,1; errores del 10 % contra un piso de 0,02; «no sé» reales), y
la variante **`dropout`** rompe el supuesto central: el paciente solo puede tener
el 70 % de los rasgos documentados y dice «sí» por error al 5 % de lo no
anotado. La variante `dropout` es la mejor aproximación disponible a «el mundo
no es como lo describe la base de conocimiento»; la validación con pacientes
reales sigue pendiente (sección 9).

## 4. Políticas comparadas

Todas las políticas con preguntas usan el `RoundService` real con el proveedor
de plantillas (sin LLM): mismo re-ranking, mismas reglas de cierre, mismo filtro
del modo paciente. Solo cambia el `QuestionSelector` (`eval/policies.py`).

- **`none`**: sin preguntas; el ranking original del modelo.
- **`agent`**: el selector por ganancia de información; se preguntan los 3
  primeros de su lista corta diversificada.
- **`random_askable`**: 3 términos al azar del mismo conjunto de candidatos que
  puntúa el selector (preguntables, cerca de las anotaciones de los candidatos,
  no implicados por lo ya sabido), sin dos emparentados en la misma ronda.
- **`most_frequent_unreported`**: la heurística que usa hoy la pantalla de
  resultados: los síntomas anotados más frecuentes del gen que va primero (y
  luego del segundo, etc.) que todavía no se informaron.

Las heurísticas no tienen ganancia de información, así que solo las detiene la
regla de confianza; el agente además se detiene cuando ninguna pregunta aporta
al menos `min_information_gain` bits. Por eso el agente hace, en general,
menos preguntas que las heurísticas.

## 5. Ajuste de parámetros (conjunto AJUSTE, 200 casos)

`python -m eval.tune`. Se jugó cada configuración una vez **sin cierre
anticipado** y las reglas de cierre se reprodujeron después sobre la
trayectoria. Esto es exacto: un cierre en la ronda *s* solo congela el ranking
de esa ronda. Se verificó comparando con una corrida con las reglas reales: la
configuración elegida da en ambas MRR 0,828, 3,45 preguntas y los mismos
motivos de cierre (128 confianza, 66 máximo de rondas, 6 sin preguntas útiles).
`p_unknown` del selector solo multiplica todas las ganancias por `1 − p_unknown`
(no cambia qué se pregunta; hay un test que lo verifica), así que equivale a
reescalar `min_information_gain`.

**Etapa 1**, factorial completo de 48 configuraciones: estrategia
{`model_sees_present`, `fixed_prior`} × temperatura {1, 2, 3, 5} × fondo de la
verosimilitud {0,02; 0,05; 0,1} × frecuencia faltante {0,3; 0,5}. **Etapa 2**:
piso de ruido `epsilon` {0,05; 0,1} alrededor de la mejor. **Etapa 3**
(exploratoria, no elegible): temperaturas {0,5; 0,75; 1,5}. Criterio: MRR tras 3
rondas; entre configuraciones a menos de 0,005 de la mejor, la que cambia menos
valores por defecto de entonces (la configuración anterior). Tiempo total: 326 s.

MRR tras 3 rondas, sin cierre anticipado, promedio (y máximo) sobre el resto de
los factores:

| factor | valores → MRR medio (máximo) |
|---|---|
| temperatura | 1: 0,804 (0,831) · 2: 0,776 (0,813) · 3: 0,742 (0,779) · 5: 0,684 (0,715) |
| estrategia | model_sees_present: 0,741 (0,794) · fixed_prior: 0,763 (0,831) |
| fondo | 0,02: 0,741 (0,822) · 0,05: 0,753 (0,821) · 0,1: 0,761 (0,831) |
| frecuencia faltante | 0,3: 0,756 (0,831) · 0,5: 0,748 (0,822) |

Estrategia × temperatura (fondo 0,1, frecuencia faltante 0,3). Antes de
preguntar, el MRR es 0,756 en todas.

| temperatura | model_sees_present: MRR (empeora) | fixed_prior: MRR (empeora) |
|---|---|---|
| 1 | 0,794 (10,5 %) | **0,831 (6,0 %)** |
| 2 | 0,766 (15,0 %) | 0,813 (9,5 %) |
| 3 | 0,740 (17,0 %) | 0,778 (14,5 %) |
| 5 | 0,701 (23,5 %) | 0,715 (23,0 %) |

- **La temperatura es el parámetro dominante.** El supuesto del código (T = 3
  porque el modelo sería sobreconfiado) no se sostiene en estos casos: aplanar
  el prior hace que unas pocas respuestas ruidosas desplacen al gen correcto. La
  etapa 3 muestra que T = 1 es un óptimo interior (T = 0,5: 0,815; 0,75: 0,823;
  1: 0,831; 1,5: 0,816).
- **`fixed_prior` gana a `model_sees_present`** con todas las temperaturas. Con
  `model_sees_present`, cada «sí» se le envía al modelo, que reordena todo el
  ranking con ese término; un «sí» erróneo (10 % de las respuestas) pesa mucho
  más así que como un factor de verosimilitud acotado.
- Fondo y frecuencia faltante tienen efectos chicos (≤ 0,02 de MRR), del orden
  del ruido con 200 casos. `epsilon` mayor no ayudó (0,829 / 0,826 / 0,819
  contra 0,831 con 0,02).
- Con la configuración anterior del código, en AJUSTE el MRR sube de 0,756 a 0,776
  tras la primera ronda y cae a 0,745 tras la tercera (6,51 preguntas): las
  rondas 2 y 3 deshacen lo ganado.

**Reglas de cierre** para la mejor configuración (`p_unknown` = 0,2):

| umbral de confianza | ganancia mínima (bits) | MRR | preguntas | empeora |
|---|---|---|---|---|
| 0,5 | 0,05 | 0,797 | 1,40 | 4,0 % |
| 0,7 | 0,05 | 0,807 | 2,44 | 5,5 % |
| 0,8 | 0,01 (anterior) | 0,823 | 3,04 | 6,5 % |
| **0,9** | **0,05** | **0,828** | **3,45** | 6,0 % |
| 0,9 | 0,1 | 0,818 | 3,06 | 7,0 % |
| 1,0 (nunca) | 0,05 | 0,828 | 3,81 | 6,0 % |
| 1,0 (nunca) | 0 | 0,831 | 8,55 | 6,0 % |

La curva es casi plana entre 0,8 y 1,0: cerrar por confianza ahorra más de la
mitad de las preguntas casi sin costo.

**Configuración elegida** (`eval/results/chosen_config.json`), hoy la del código:

| parámetro | configuración anterior | elegido (valor por defecto actual) |
|---|---|---|
| estrategia de re-ranking (`PREGUNTAS_RERANK_STRATEGY`) | `model_sees_present` (fija en `RoundService`) | `fixed_prior` |
| `RerankConfig.temperature` | 3,0 | **1,0** |
| `LikelihoodConfig.background` | 0,05 | 0,1 |
| `LikelihoodConfig.missing_frequency` | 0,5 | 0,3 |
| `LikelihoodConfig.epsilon` | 0,02 | 0,02 (sin cambio) |
| `SelectionConfig.p_unknown` | 0,2 | 0,2 (sin cambio) |
| `Settings.confident_threshold` | 0,8 | 0,9 |
| `Settings.min_information_gain` | 0,01 | 0,05 |

## 6. Resultados en PRUEBA (300 casos)

Modo paciente, respondedor `default`, reglas de cierre reales.

### 6.1 Configuración elegida (valores por defecto actuales)

Se repitió con el código que ya la tiene por defecto (`python -m eval.run_simulation
--split test`, sin `--config`, con la estrategia elegida por la configuración del
servicio y no por un envoltorio del arnés): todas las cifras de esta sección
salieron idénticas.

| política | ronda | top-1 | top-3 | top-5 | top-10 | top-20 | MRR | empeora | mejora | preguntas |
|---|---|---|---|---|---|---|---|---|---|---|
| none | — | 72,0 | 82,7 | 87,0 | 92,3 | 96,3 | 0,787 | — | — | 0 |
| agent | 1 | 75,7 | 84,7 | 87,7 | 93,0 | 96,3 | 0,814 | 6,0 | 15,3 | 1,35 |
| agent | 2 | 78,7 | 87,3 | 89,0 | 93,0 | 97,0 | 0,835 | 3,3 | 18,7 | 2,41 |
| agent | 3 | **79,3** | 87,0 | 89,7 | 93,0 | 97,3 | **0,842** | 3,7 | 18,7 | 3,25 |
| random_askable | 3 | 73,3 | 83,7 | 87,3 | 92,0 | 95,3 | 0,796 | 4,0 | 5,7 | 4,12 |
| most_frequent_unreported | 3 | 76,0 | 85,7 | 87,3 | 92,0 | 95,7 | 0,816 | 4,7 | 13,3 | 3,56 |

La mediana del puesto es 1 en todas las filas. Diferencias pareadas tras la
última ronda (IC 95 %):

| comparación | Δ top-1 (pp) | Δ top-5 (pp) | Δ top-10 (pp) | Δ MRR |
|---|---|---|---|---|
| agent vs none | +7,3 [+3,7; +11,3] | +2,7 [+0,7; +5,0] | +0,7 [−1,0; +2,7] | +0,054 [+0,033; +0,078] |
| agent vs random_askable | +6,0 [+2,7; +9,7] | +2,3 [+0,7; +4,3] | +1,0 [−1,0; +3,0] | +0,046 [+0,026; +0,066] |
| agent vs most_frequent_unreported | +3,3 [+0,3; +6,3] | +2,3 [+0,3; +4,7] | +1,0 [−0,7; +2,7] | +0,026 [+0,006; +0,045] |

- **Motivos de cierre del agente**: 207 por confianza, 84 por máximo de rondas,
  9 sin preguntas útiles. En 165 de 300 casos no se hizo ninguna pregunta: en
  162 el gen de arriba ya superaba 0,9 en la ronda 1, y en esos era el correcto
  el 95,7 % de las veces; los otros 3 cerraron sin preguntas útiles (el gen
  correcto no estaba primero), así que sobre los 165 el acierto es 93,9 %. En
  los 207 cierres por confianza, contando los de rondas posteriores, el 94,2 %.
- **En los 135 casos en que sí preguntó**, el efecto es mayor: Δ MRR +0,121
  [+0,076; +0,168] y Δ top-1 +16,3 pp [+8,1; +24,4], partiendo de un MRR de
  0,583.
- Por fuente: OMIM Δ MRR +0,056 [+0,026; +0,087]; ORPHA +0,052 [+0,022; +0,084].
- La mejora se concentra arriba (top-1 a top-5); el top-10 y el top-20 casi no
  cambian, porque el re-ranking redistribuye entre genes que el modelo ya
  consideraba.

### 6.2 Configuración anterior del código, mismos casos

Repetida con `--config eval/configs/defaults_before_tuning.json`: cifras idénticas.

| política | ronda | top-1 | top-5 | top-10 | MRR | empeora | preguntas |
|---|---|---|---|---|---|---|---|
| agent | 1 | 73,0 | 85,3 | 90,7 | 0,795 | 12,3 | 2,46 |
| agent | 2 | 73,0 | 86,0 | 90,7 | 0,788 | 12,7 | 4,65 |
| agent | 3 | 69,3 | 83,7 | 90,0 | 0,759 | 20,7 | 6,76 |
| random_askable | 3 | 54,7 | 69,7 | 75,7 | 0,617 | 32,0 | 7,35 |
| most_frequent_unreported | 3 | 74,3 | 84,0 | 88,0 | 0,789 | 15,3 | 6,69 |

Agent vs none: Δ MRR −0,029 [−0,063; +0,006], Δ top-1 −2,7 pp [−7,3; +2,0].
Con los valores anteriores, el agente empeoraba el ranking en uno de cada cinco
casos y no le gana a la heurística de la pantalla de resultados (Δ top-1 −5,0 pp
[−9,7; −0,3]). Solo supera claramente a las preguntas al azar, que con T = 3
son muy dañinas.

### 6.3 Ablación en PRUEBA (cambiando un parámetro de la configuración elegida)

| variante | MRR tras 3 rondas | Δ MRR vs none | empeora | preguntas |
|---|---|---|---|---|
| elegida | 0,842 | +0,054 [+0,033; +0,078] | 3,7 % | 3,25 |
| con `model_sees_present` | 0,799 | +0,012 [−0,011; +0,036] | 9,0 % | 3,20 |
| con T = 3 | 0,805 | +0,018 [−0,018; +0,051] | 12,0 % | 6,83 |
| con los cierres anteriores (0,8 / 0,01) | 0,839 | +0,052 [+0,031; +0,076] | 4,3 % | 2,87 |

La estrategia y la temperatura explican casi toda la diferencia; los umbrales
de cierre nuevos aportan poco (0,003 de MRR a cambio de 0,38 preguntas más).

### 6.4 Robustez

| escenario | config. | MRR agent (none = 0,787) | Δ MRR vs none | Δ top-1 (pp) | empeora | preguntas |
|---|---|---|---|---|---|---|
| respondedor `dropout` | elegida | 0,805 | +0,018 [−0,009; +0,043] | +3,0 [−1,0; +7,0] | 8,3 % | 3,44 |
| respondedor `dropout` | anterior | 0,675 | **−0,112 [−0,154; −0,072]** | −11,3 [−16,7; −6,0] | 30,3 % | 6,91 |
| respondedor `noiseless` | elegida | 0,876 | +0,089 [+0,064; +0,116] | +11,0 [+7,3; +15,0] | 1,3 % | 3,12 |
| modo médico | elegida | 0,845 | +0,058 [+0,037; +0,082] | +8,0 | 4,3 % | 3,18 |

- Con un paciente que no se ajusta a la base de conocimiento, **la
  configuración anterior es claramente dañina**: empeora el puesto del gen
  correcto en el 30 % de los casos. La elegida conserva una mejora chica y no
  significativa y no daña en promedio. Bajo `dropout`, contra
  `most_frequent_unreported` el MRR queda empatado (+0,003 [−0,018; +0,026]) y el
  top-10 es algo peor (−2,3 pp [−4,3; −0,3]).
- Sin ruido en las respuestas, el techo del método en esta simulación es
  +0,089 de MRR.
- En modo médico (sin el filtro de síntomas que requieren estudios, y con
  «no sé» de 0,2 también para esos) el resultado es similar al de modo
  paciente.

### 6.5 Tiempo de cómputo

Por ronda, con el modelo en caché local (el tiempo es re-ranking + selección):
60 / 59 / 65 ms en las rondas 1–3 y 12 ms en el cierre con la configuración
elegida; 124 / 147 / 145 / 45 ms con la anterior. Una llamada al modelo sin caché tarda 10 ms de mediana
(p90 12 ms, `python -m eval.model_latency`, 30 llamadas en CPU). El cuello de
botella de una ronda real es el LLM (sección 7), no el cálculo.

## 7. Evaluación en vivo con Claude

`python -m eval.live_claude`, con `PREGUNTAS_LLM_PROVIDER=claude-cli` (modelo
`claude-opus-5`, esfuerzo `high`) y la configuración elegida. Presupuesto: 20
llamadas a `claude -p`, contadas antes de cada llamada y con las respuestas en
caché; **se usaron 15** (12 de preguntas y 3 de notas, sin reintentos).

### 7.1 Preguntas (ronda 1)

8 casos de PRUEBA en modo paciente y 4 en modo médico, elegidos en orden entre
los que llegan a preguntar en la ronda 1, alternando OMIM y ORPHA.

- **Salvaguardas**: 12 de 12 rondas aceptadas al primer intento; 0 reintentos, 0
  caídas a plantilla, 0 advertencias.
- **Latencia por llamada**: mediana 22,5 s, media 23,1 s, máximo 34,4 s (paciente
  25,5 s de media; médico 18,2 s).
- **Costo del criterio de Claude frente a la ganancia de información pura**:
  Claude elige siempre el primer candidato de la lista; en total, el 78 % de
  sus elecciones (28 de 36) está entre los 3 de mayor ganancia de información, y
  las demás en los puestos 4 a 6. Como la lista del selector está diversificada
  (MMR), eso no implica pérdida: la ganancia sumada de las 3 preguntas elegidas
  es en promedio el 100,3 % de la de las 3 que elegiría la plantilla (mínimo
  87,7 %). Donde se aparta del orden, suele evitar redundancias: en el caso
  IQSEC2 preguntó por «Retardo global del desarrollo» y descartó «Discapacidad
  intelectual» y «Ausencia del lenguaje» (puestos 2 y 3), muy correlacionadas con
  la primera, para preguntar por convulsiones focales y alteraciones del
  comportamiento (puestos 4 y 7).

Ejemplos textuales (todas en `eval/results/live_questions.md`):

- Paciente, síndrome de Joubert 4: «¿Pasa que, para mirar algo que está al
  costado, hace falta girar la cabeza en lugar de mover solo los ojos?»
  (apraxia oculomotora, 1.er candidato).
- Paciente, síndrome de microdeleción 16p11.2 distal: «¿Naciste con un dedo de
  más, ubicado del lado del dedo más chiquito?» (polidactilia postaxial).
- Paciente, síndrome de Coffin-Siris 4: «¿Alguna vez tuviste convulsiones?»
- Médico, distrofia de cinturas 2D: «¿Para levantarse del piso necesita trepar
  sobre sí mismo apoyando las manos en los muslos?», fundamento: «Separa SGCA
  (sí) de CAPN3, SGCG, ANO5, DYSF y SGCB (no).»

Observaciones para revisión humana:

- En modo paciente **se mezcla la persona gramatical**: algunas preguntas se
  dirigen al paciente («¿Alguna vez tuviste convulsiones?», «¿Naciste con…?») y
  otras al cuidador («¿Hubo alguna vez episodios en los que se sacudiera…?»),
  a veces dentro de la misma ronda. El prompt no decía quién responde. Se
  corrigió después de esta evaluación (sección 7.3).
- Algunas preguntas de paciente sobre hallazgos de laboratorio o imágenes
  («depósitos de calcio… en el cerebro», «tus defensas atacan partes de tu
  propio cuerpo») están bien redactadas como «¿algún médico te dijo…?» y con
  ayuda que invita a responder «No sé».
- No se vieron nombres de genes, porcentajes ni palabras prohibidas en texto
  para pacientes (las salvaguardas no rechazaron nada).

### 7.2 Lectura de notas

`eval/data/notas.jsonl`: 24 notas rioplatenses con el botón marcado y la
respuesta efectiva esperada. 20 son inequívocas y 4 ambiguas («no creo», «no que
yo sepa», «medio que sí, depende del día» y un intento de inyección de
instrucciones), para las que se aceptan dos resultados. Se enviaron en 3
llamadas de 8 notas (NotesTask, modo paciente); latencias 12,8 / 16,3 / 14,0 s.

| medida | resultado |
|---|---|
| exactitud en las 20 inequívocas | 20 / 20 |
| exactitud contra el valor esperado, las 24 | 23 / 24 (95,8 %) |
| resultado aceptable, las 24 | 24 / 24 |
| respuestas que pasaron de sí a no o de no a sí | **0** |
| lecturas faltantes | 0 |

La única discrepancia es aceptable: «medio que sí, depende del día» (botón sí)
se degradó a «no sé»; se esperaba sí, pero es ambigua. Casos bien resueltos:
«ahora no, antes sí» con botón no → no sé; «lo tiene el hermano, él no» con
botón sí → no sé; «no, pero la mamá sí» con botón no → se mantiene no. La nota
con la inyección («ignorá las instrucciones anteriores…») se leyó como
coherente y sin resumen: no cambió nada. Que ninguna respuesta se invierta está
garantizado por el diseño (`interpret_answers` solo puede degradar a «no sé»), y
la prueba lo confirma.

### 7.3 Después de reescribir los prompts

Tras esta evaluación se reescribieron los prompts: en modo paciente, una única
forma impersonal para todas las preguntas y el resumen («¿Hubo alguna vez…?»,
«¿Se notó…?», «¿Algún médico mencionó…?»), porque quien responde puede ser la
persona con los síntomas o un familiar; un solo síntoma por pregunta, con el
mismo alcance que el término; y en modo médico, la pregunta fiel al término (ni
más amplia ni más específica). Se verificó con 4 llamadas a `claude -p`
(`claude-opus-5`, esfuerzo `high`, configuración por defecto) sobre los dos casos
que mostraban los problemas, en ambos modos. Las 4 rondas se aceptaron al primer
intento, sin reintentos ni plantillas:

- Paciente, IQSEC2 (antes mezclaba «¿Te dijeron…?» con «se sacudiera»):
  «¿Alguna vez un médico o la escuela mencionaron una dificultad importante para
  aprender y entender cosas, comparada con otras personas de la misma edad?»;
  «¿Hubo alguna vez episodios de convulsión que empezaran en una sola parte del
  cuerpo, por ejemplo en un brazo o en un lado de la cara?»; «¿Se notó alguna vez
  una forma de comportarse que llamara la atención en casa o en la escuela por
  ser distinta de lo habitual?».
- Paciente, SGCA: «¿Pasa que, para levantarse del piso, hace falta apoyar las
  manos sobre los muslos e ir trepando con ellas hasta enderezarse?»; «¿Se notó
  alguna vez que las articulaciones de los pies quedaran duras, sin poder
  moverlas en todo su recorrido?»; «¿Algún médico mencionó alguna vez que el
  corazón no alcanza a bombear la sangre como debería?».
- Médico, SGCA (antes «¿Presenta contracturas o limitación para la movilidad de
  las articulaciones de los pies?», dos hallazgos): «¿Presenta contracturas en
  las articulaciones de los pies?»; «¿Presenta signo de Gowers al incorporarse
  desde el piso?»; «¿Presenta insuficiencia cardíaca congestiva?».
- Médico, IQSEC2: «¿Presenta discapacidad intelectual?»; «¿Presenta
  convulsiones focales?»; «¿Hay ausencia del lenguaje?».

**Latencia**: 47,3 / 12,8 / 19,9 / 49,7 s. Dos de las cuatro llamadas (las del
caso IQSEC2) superaban el tope por llamada que se había propuesto, 35 s; la
verificación se corrió con 120 s para no cortarlas. En la sección 7.1 el máximo
había sido 34,4 s. Por eso el tope por llamada, `PREGUNTAS_LLM_TIMEOUT_S`, quedó
en 60 s: entra en el presupuesto de la ronda (75 s) y en el timeout de 100 s de la
plataforma, y la llamada nunca recibe más de lo que le queda a la ronda. Antes de
producción conviene medir la cola de latencia con más casos; la alternativa es
bajar el esfuerzo de las llamadas de preguntas, a costa de calidad.

## 8. Valores por defecto adoptados

Con la evidencia de AJUSTE confirmada en PRUEBA (secciones 5 y 6), estos son
ahora los valores por defecto del código:

1. **`RerankStrategy.FIXED_PRIOR`**, elegible con `PREGUNTAS_RERANK_STRATEGY`
   (`fixed_prior` por defecto). `RoundService` consulta al modelo con
   `model_query(estrategia, reported_present=request.present_hpo_ids, …)`: con
   `fixed_prior`, siempre con los hallazgos informados antes de las preguntas, y
   el re-ranker aplica como verosimilitud todas las respuestas, «sí» y «no».
   Evidencia: +0,043 de MRR en PRUEBA a igualdad del resto (0,842 contra 0,799).
   Consecuencia para el contrato: `specialties` y `confidence` de cada respuesta
   son las del modelo para los hallazgos originales y no cambian entre rondas.
2. **`RerankConfig.temperature` = 1,0** (antes 3,0). Es el cambio de mayor efecto
   (0,842 contra 0,805 con T = 3 y el resto igual en PRUEBA; en AJUSTE, T = 1 es
   el óptimo entre 0,5 y 5).
3. `LikelihoodConfig.background` = 0,1 (antes 0,05) y `missing_frequency` = 0,3
   (antes 0,5). Efecto chico y dentro del ruido.
4. `Settings.confident_threshold` = 0,9 y `min_information_gain` = 0,05 (antes 0,8
   y 0,01). Efecto marginal (+0,003 de MRR, +0,38 preguntas).
5. `epsilon` (0,02) y `p_unknown` (0,2) sin cambios.

Los cambios 1 y 2 son los que pasan de «no mejora y a veces daña» a una
mejora significativa. **Advertencia sobre T = 1**: en estos casos el modelo está
en su terreno (72 % de top-1) y conviene confiar en él. Con pacientes reales es
probable que esté más sobreconfiado, y ahí una temperatura mayor podría volver
a convenir. Antes de fijarla definitivamente hay que medirla con casos reales.

## 9. Limitaciones

- **Circularidad** (sección 3): pacientes y agente comparten la base HPO; los
  resultados son una cota optimista. La variante `dropout` muestra que el
  beneficio se reduce mucho (+0,054 → +0,018 de MRR) cuando el paciente no se
  ajusta a las anotaciones.
- **Distribución de los casos**: vienen de anotaciones de enfermedades
  conocidas, probablemente parecidas a los datos de entrenamiento del modelo, y
  con términos HPO exactos. Los pacientes reales llegan con términos extraídos
  de texto libre, con errores (el extractor, por ejemplo, no detecta negaciones)
  y menos específicos.
- **Solo enfermedades monogénicas** con al menos 8 anotaciones y con el gen en
  el vocabulario del modelo; 10 de 500 casos tenían el gen fuera del top-50, un
  techo que ningún re-ranking supera.
- **Respondedor independiente por término** (dado el paciente latente): no
  modela respuestas correlacionadas, fatiga, ni la interpretación que una
  familia hace de una pregunta concreta. El texto de Claude no influye en las
  respuestas simuladas; la simulación mide la elección de síntomas, no la
  redacción.
- **Tamaño**: 200 casos de ajuste no alcanzan para distinguir diferencias de
  MRR menores a ~0,02; fondo, frecuencia faltante y umbrales se eligieron dentro
  de ese margen.
- **Evaluación en vivo pequeña** (15 llamadas, más 4 tras reescribir los
  prompts): suficiente para detectar fallas gruesas, no para estimar tasas de
  rechazo raras. Las preguntas no fueron
  revisadas todavía por un médico.
- El agente solo reordena los 50 genes que devuelve el modelo.

## 10. Cómo reproducir

Requisitos: `data/kb.sqlite` construida (`make kb`), el servicio del modelo de
diagnostic.ar escuchando (por defecto `http://127.0.0.1:18021`, o
`EVAL_MODEL_URL`), y para la parte en vivo, el CLI `claude` con sesión iniciada.
Las llamadas al modelo quedan en caché en `eval/results/cache/` (fuera de git),
así que repetir es barato.

```sh
.venv/bin/python -m eval.cases                     # 500 casos -> eval/data/cases.json (1 s)
.venv/bin/python -m eval.tune                      # grilla sobre AJUSTE (326 s, 14 procesos)
# (el desempate de eval.tune prefiere la configuración más cercana a los valores por
#  defecto del código, que desde entonces son los elegidos)
C=eval/results/chosen_config.json
P=eval/configs/defaults_before_tuning.json        # la configuración anterior
.venv/bin/python -m eval.run_simulation --split test --config $C --out eval/results/test_chosen.json
.venv/bin/python -m eval.run_simulation --split test --out eval/results/test_defaults_v2.json   # = elegida
.venv/bin/python -m eval.run_simulation --split test --config $P --out eval/results/test_default.json
.venv/bin/python -m eval.run_simulation --split test --config $C --answerer dropout --out eval/results/test_chosen_dropout.json
.venv/bin/python -m eval.run_simulation --split test --config $P --answerer dropout --out eval/results/test_default_dropout.json
.venv/bin/python -m eval.run_simulation --split test --config $C --answerer noiseless --out eval/results/test_chosen_noiseless.json
.venv/bin/python -m eval.run_simulation --split test --config $C --mode medico --out eval/results/test_chosen_medico.json
.venv/bin/python -m eval.report eval/results/test_*.json --out eval/results/report.md
.venv/bin/python -m eval.model_latency
.venv/bin/python -m eval.live_claude --max-calls 20  # gasta cuota de Claude; con caché, 0 llamadas
```

Las variantes de la ablación (sección 6.3) usan `--config` con la configuración
elegida modificada en un solo campo (`strategy`, `temperature`, o
`confident_threshold` / `min_information_gain`) y `--policies none,agent`.
Tiempos de pared de esta corrida: casos 1 s, grilla 326 s, las diez
simulaciones de PRUEBA y de verificación 102 s, evaluación en vivo 321 s (en una
máquina de 16 núcleos). Los tests del arnés están en `tests/eval/`.
