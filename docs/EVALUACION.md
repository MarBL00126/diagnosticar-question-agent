# Evaluación del agente de preguntas

Este documento describe cómo se evaluó el agente de preguntas de seguimiento de
diagnostic.ar, qué resultados dio y qué valores por defecto se adoptaron a
partir de ellos. Todas las cifras salen de corridas reales del código en `eval/`
(26/09/2026; la sección 11, del 28/09/2026) y se pueden reproducir con los
comandos de las secciones 10 y 11.12. El modelo
evaluado se describe en [ARQUITECTURA.md](ARQUITECTURA.md) y las decisiones que
salieron de acá, en [DECISIONES.md](DECISIONES.md) (D-11). Las mismas corridas,
expresadas como mejoras absolutas y relativas con intervalos de confianza (por
subgrupo, por ronda y por pregunta), están en [IMPACTO.md](IMPACTO.md).

**Los valores por defecto del código son la configuración elegida acá**
(sección 8): temperatura 1, fondo 0,1, frecuencia faltante 0,3, umbral de
confianza 0,9 y ganancia mínima 0,05, **con un cambio del 28/09/2026: las
respuestas solo llegan al modelo** (estrategia `model_sees_present`, sin
re-ranking; sección 11.11, [DECISIONES.md](DECISIONES.md) D-18). Las secciones
2 a 8 usan `fixed_prior` y la ponderación calibrada, la configuración anterior. «Configuración
anterior» se refiere a los valores por defecto que tenía el código hasta el commit
ea4b766, guardados en `eval/configs/defaults_before_tuning.json`.

**Lo primero que hay que leer: la sección 11.** Las secciones 2 a 8 miden el
agente con pacientes simulados a partir de las mismas anotaciones HPO que usa el
agente, y esa simulación es optimista por construcción. Con 501 pacientes
reales publicados (phenopacket-store), el agente con los valores por defecto
actuales **empeora** el ranking del modelo: top-1 43,1 % → 37,5 % (−5,6 pp, IC
95 % [−8,2; −3,0]) y MRR 0,508 → 0,461 (Δ −0,047 [−0,065; −0,029]); en el
subconjunto más limpio, −0,008 [−0,024; +0,008], neutro. También empeora cuando
se le ocultan al agente las anotaciones de las que sale el paciente (−0,041) o
cuando el paciente viene de otra fuente de curación (−0,055 y −0,095). El 0,842
de abajo no es sobreajuste a los casos de prueba, pero es una cota superior que
los pacientes reales no alcanzan (sección 11.7). Cambiar la forma en que las
respuestas reordenan los genes (sección 11.8) puede dejar de dañar, pero ninguna
variante probada mejora a los pacientes reales. **Desde el 28/09/2026 el
agente solo le manda las respuestas al modelo** (D-18): cada «sí» se suma a su
consulta, el ranking es el del modelo y los «no» quedan registrados. Con
pacientes reales queda neutro (Δ MRR −0,007 [−0,024; +0,010] en los retenidos);
empeora al modelo con anotaciones retenidas (−0,043) y con pacientes de OMIM y
un agente que solo conoce Orphanet (−0,025), verificado con el código nuevo en
la sección 11.11. Las secciones 2 a 8 describen la configuración anterior.

**Resumen de la simulación.** Con la configuración anterior del código, las preguntas no
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
anotado. La variante `dropout` era, al escribir esta sección, la mejor
aproximación disponible a «el mundo no es como lo describe la base de
conocimiento». Que los parámetros difieran no alcanza: con los errores y los
espurios del respondedor, un «sí» a un término no anotado llega con probabilidad
0,116, muy cerca del fondo 0,1 que eligió el ajuste, y la forma del cálculo es la
misma. La sección 11 mide el agente sin este supuesto, con pacientes reales.

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

| parámetro | configuración anterior | elegido (valor por defecto desde el 26/09) |
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
mejora significativa **en la simulación**. Con pacientes reales (sección 11.6)
el cambio 1 se invierte: `model_sees_present` supera a `fixed_prior` por +0,046
de MRR, y la configuración anterior completa supera a la elegida por +0,034,
aunque ninguna mejora al modelo. Tras la auditoría, el 28/09/2026 el valor por
defecto pasó primero a la ponderación asimétrica (D-17) y después a mandarle
las respuestas solo al modelo (`model_sees_present` sin re-ranking; secciones
11.8 a 11.11 y [DECISIONES.md](DECISIONES.md), D-18). Lo de esta sección sigue
disponible con `PREGUNTAS_RERANK_STRATEGY=fixed_prior` y
`PREGUNTAS_ANSWER_WEIGHTING=calibrated`. **Advertencia sobre T = 1**: en estos casos el modelo está
en su terreno (72 % de top-1) y conviene confiar en él. Con pacientes reales es
probable que esté más sobreconfiado, y ahí una temperatura mayor podría volver
a convenir. Antes de fijarla definitivamente hay que medirla con casos reales.

## 9. Limitaciones

- **Circularidad** (secciones 3 y 11): pacientes y agente comparten la base
  HPO; los resultados de las secciones 5 a 8 son una cota optimista. Medida sin
  esa circularidad, la mejora desaparece o se vuelve un daño: −0,047 de MRR con
  pacientes reales, −0,041 con anotaciones retenidas, −0,055 y −0,095 con
  fuentes cruzadas (sección 11).
- **Distribución de los casos**: vienen de anotaciones de enfermedades
  conocidas, las mismas de las que salen los pacientes sintéticos con que se
  entrenó el modelo (sección 11.2, camino c), y con términos HPO exactos: el
  modelo acierta el top-1 en el 72,0 % de los casos simulados y en el 43,1 % de
  los reales. Los pacientes reales llegan con términos extraídos
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

Para las evaluaciones sin circularidad, ver la sección 11.12. Las cifras de las
secciones 5 a 8 son de la ponderación calibrada con `fixed_prior`:
`eval/results/chosen_config.json` (que `eval.tune` escribe) y
`eval/configs/*.json` la fijan; sin `--config`, `eval.run_simulation` usa hoy
los valores por defecto nuevos (los «sí» al modelo, sin re-ranking).

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
.venv/bin/python -m eval.run_simulation --split test --config eval/configs/tuned_calibrated.json --out eval/results/test_defaults_v2.json   # = elegida
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

## 11. Auditoría de circularidad y evaluación con pacientes reales

Esta sección responde dos preguntas sobre el resultado de la sección 6: **cómo
entran las respuestas en la predicción** y **si el MRR de 0,842 es sobreajuste,
dado que los pacientes sintéticos se generan conociendo la enfermedad
verdadera**. Para contestar la segunda se midió el agente en tres escenarios
donde el paciente no sale de las mismas anotaciones que usa el agente, entre
ellos 501 pacientes reales publicados. Todas las corridas usan los valores por
defecto que tenía el código al hacerla (nada se reajustó), el proveedor de
plantillas (sin LLM), el servicio real del modelo de diagnostic.ar y las mismas
métricas e
intervalos (bootstrap pareado, 2000 remuestreos) que el resto del documento.
Código: `eval/audit.py`, `eval/phenopackets.py`, `eval/kb_views.py`,
`eval/explore.py` y `eval/variants.py` (28/09/2026).

En 11.1 a 11.9, «actual» y «por defecto» se refieren a esos valores
(`fixed_prior`, ponderación calibrada). Lo que los reemplazó el 28/09/2026 está
en 11.10 (la ponderación asimétrica, por unas horas) y 11.11 (las respuestas
solo llegan al modelo, el valor por defecto actual).

**Resumen.** El 0,842 no es sobreajuste a los casos de prueba, pero sí una cota
optimista por construcción: el simulador comparte los supuestos del agente. La
mejora (+0,054 de MRR) aparece solo cuando el paciente y el agente describen la
enfermedad con las mismas anotaciones. Con pacientes reales, con la mitad de las
anotaciones ocultas al agente o con otra fuente de curación, **las preguntas no
mejoran el ranking del modelo y, con la ponderación calibrada que el código usaba,
lo empeoran**: Δ MRR −0,047 [−0,065; −0,029] en pacientes reales, −0,041 en B,
−0,055 y −0,095 en C. Ninguna de las 44 configuraciones exploradas con los
parámetros existentes da una mejora con pacientes reales; la mejor queda neutra.
Entre nueve variantes estructurales (11.8), una verosimilitud que no castiga lo
no anotado y cuenta cada «sí» una vez (V3fp, k = 0,3) deja de dañar en pacientes
reales (−0,001 [−0,009; +0,007]), en B y en C_orpha, pero tampoco mejora; nada de
lo probado ayuda a pacientes reales.

### 11.1 Cómo entran las respuestas en la predicción

Así funcionaba cuando se hizo la pregunta (ponderación calibrada). Desde el
28/09/2026 el paso 3 cambió; ver el final de esta sección.

1. **El modelo se consulta con lo que informó la familia, nada más.** Con la
   estrategia por defecto (`fixed_prior`), cada ronda le manda al modelo de
   diagnostic.ar solo `present_hpo_ids`, los hallazgos informados antes de las
   preguntas. Las respuestas nunca llegan al modelo. El modelo devuelve la
   probabilidad de sus 50 primeros genes.
2. **Prior.** Esas probabilidades se templan con la temperatura $T$ (1 por
   defecto, es decir, se usan tal cual renormalizadas sobre los 50) y se reparten
   en partes iguales entre las enfermedades anotadas de cada gen, las
   hipótesis: $\pi(g, d) = \pi(g) / |D(g)|$.
3. **Cada respuesta multiplica cada hipótesis por la probabilidad de esa
   respuesta si el paciente tuviera esa enfermedad**, calculada con las
   anotaciones de `phenotype.hpoa`: «sí» a $t$ multiplica por $P(t \mid d)$,
   «no» por $1 - P(t \mid d)$ y «no sé» no cambia nada. $P(t \mid d)$ es el
   noisy-OR de las frecuencias anotadas de $t$ y de sus descendientes en $d$
   (0,3 si la frecuencia no está indicada), con un piso de fondo $b = 0{,}1$;
   vale $\varepsilon = 0{,}02$ si $d$ excluye $t$ (o un ancestro) con `NOT` o
   `Excluded`; vale $b$ si $d$ no dice nada de $t$; y todo se recorta a
   $[\varepsilon, 1 - \varepsilon]$ ([ARQUITECTURA.md](ARQUITECTURA.md), 4.2). Los
   hallazgos negados de la extracción (`absent_hpo_ids`) entran igual que un
   «no».
4. **Puntaje del gen**: la suma de sus hipótesis, renormalizada sobre los 50
   genes:

   $$w(g, d) \propto \pi(g, d) \prod_{t\,\text{«sí»}} P(t \mid d) \prod_{t\,\text{«no»}} \bigl(1 - P(t \mid d)\bigr), \qquad \text{puntaje}(g) = \sum_d w(g, d)$$

5. **Qué se muestra.** La interfaz muestra ese orden («Con tus respuestas»). La
   salida del modelo no se toca y sigue disponible («Original»). `specialties` y
   `confidence` son las del modelo para los hallazgos originales.

Ejemplo con dos genes: A (prior 0,6, su enfermedad anota $t$ con frecuencia 0,9)
y B (prior 0,4, no dice nada de $t$). Un «sí» deja A: 0,6·0,9 = 0,54 y B:
0,4·0,1 = 0,04, o sea **A 0,93 / B 0,07**. Un «no» deja A: 0,6·0,1 = 0,06 y B:
0,4·0,9 = 0,36, o sea **A 0,14 / B 0,86**. Una sola respuesta mueve un factor 9
entre dos genes, y todo el peso sale de lo que las anotaciones dicen de $t$. Si
el paciente tiene un síntoma que las anotaciones de su enfermedad no mencionan,
su «sí» lo castiga igual que un «no» a un síntoma anotado que no tiene. Esa es
la fragilidad que mide esta sección.

**Desde el 28/09/2026 (D-18)** no hay paso 3: cada «sí» efectivo se suma a la
consulta del modelo en el paso 1, el ranking que se muestra es el del modelo
para esa consulta y los «no» y negados quedan registrados sin tocarlo. **Por
unas horas del mismo día (ponderación asimétrica, D-17)** el paso 3 fue: un «sí» que
el modelo no vio multiplica a las enfermedades que anotan el término por
$1 + k\,p$ ($k$ = 0,3, $p$ la frecuencia anotada sin piso ni recorte); un «no» o
un negado, por $1 - k\,p$; un hallazgo presente que la enfermedad excluye, por
0,02; y **lo que las anotaciones no mencionan no cambia nada** (factor 1). En el
ejemplo, un «sí» deja A 0,6·1,27 = 0,762 y B 0,4, o sea A 0,66 / B 0,34; un «no»
deja A 0,6·0,73 = 0,438 y B 0,4, o sea A 0,52 / B 0,48. Detalle en
[ARQUITECTURA.md](ARQUITECTURA.md), 4.3.

### 11.2 Caminos de fuga o circularidad

| camino | riesgo | evidencia | impacto medido | mitigación |
|---|---|---|---|---|
| **a. El paciente simulado sale de las mismas anotaciones que usa el agente** | alto | `eval/answerer.py` sortea los hallazgos latentes del paciente de las anotaciones positivas de la enfermedad verdadera con sus frecuencias, y `eval/cases.py` saca de ahí los hallazgos informados. La probabilidad marginal de «sí» es el mismo noisy-OR de frecuencias en $t$ y sus descendientes que usa `ranking/likelihood.py`: salvo los parámetros de ruido, el agente interpreta las respuestas con el mismo modelo que las generó. | Con paciente y agente sobre la misma fuente la mejora se reproduce: +0,054 (simulación), +0,054 y +0,055 (controles de C). Separando lo que sabe el agente de lo que tiene el paciente: −0,041 (B), −0,055 y −0,095 (C), −0,047 (A). Toda la mejora depende de compartir las anotaciones. | Evaluaciones A, B y C (11.3 a 11.5) |
| **b. Hiperparámetros ajustados sobre el simulador** | medio | AJUSTE y PRUEBA salen del mismo generador y del mismo respondedor. El fondo elegido, 0,1, es el borde de la grilla {0,02; 0,05; 0,1} y el valor más cercano a la tasa efectiva de «sí» espurios del respondedor, 0,02·0,9 + 0,98·0,1 = 0,116. No todo coincide: la frecuencia faltante elegida (0,3) se aleja de la del respondedor (0,6), y `p_unknown` (0,2, igual al del respondedor) solo reescala la ganancia. `fixed_prior` se eligió por +0,042 en el simulador. | Con pacientes reales la elección de estrategia se invierte: `model_sees_present` supera a `fixed_prior` por +0,046 [+0,030; +0,062], y la configuración anterior a la calibración supera a la elegida por +0,034 [+0,015; +0,053]. T = 1 sigue siendo mejor que 2 y 3 (11.6). | Sensibilidad y exploración, solo informadas (11.6); no se cambió ningún valor por defecto |
| **c. La línea de base del modelo está en su terreno** | alto para el nivel absoluto, bajo para el incremento | La red en la que se basa el modelo de diagnostic.ar se entrenó solo con pacientes sintéticos generados de las anotaciones HPO («Solving the Diagnostic Odyssey with Synthetic Phenotype Data», bioRxiv 10.64898/2026.03.19.712946). Los casos simulados salen de esas mismas anotaciones. | top-1 del modelo: 72,0 % en la simulación contra 43,1 % con pacientes reales; gen verdadero en el top-50: 98,7 % contra 77,0 %. | Se informa aparte; lo que se mide es el incremento del agente sobre el modelo |
| **d. Sesgo de selección** | medio | Solo enfermedades de un gen (OMIM: 6630 de 6886 vinculadas a un gen; ORPHA: 3030 de 4102), con ≥ 8 anotaciones positivas (4545 y 1573) y el gen en el vocabulario del modelo (4427 y 1571); un caso por gen. Favorece enfermedades bien anotadas, justo donde el agente sabe más. Que el gen esté en el top-50 del modelo **no** es un filtro (4 de 300 casos quedan afuera). | No medible por separado dentro de la simulación. Con otra selección (casos publicados, un gen causal, ≥ 6 rasgos) el efecto es negativo (11.3). | A con 501 genes y con hasta 5 pacientes por gen |
| **e. La identidad del gen o de la enfermedad llega al agente** | bajo | El agente recibe solo `present_hpo_ids`, `absent_hpo_ids` y `answers` (`RoundRequest`); el modelo se consulta solo con términos HPO; la política al azar se siembra con el id del caso, no con el gen. Test nuevo: `tests/eval/test_decircularised.py::test_the_agent_hears_only_the_reported_findings_and_the_patients_answers`. Única vía indirecta: el hallazgo negado se sortea entre términos que la enfermedad verdadera no respalda. | Efecto del hallazgo negado (y de los términos que el modelo no conoce) antes de cualquier respuesta: +0,000005 de MRR en la simulación; −0,006 [−0,011; −0,001] en A, donde el negado viene del registro real. | No hace falta |
| **f. Higiene de la partición AJUSTE / PRUEBA** | bajo | 0 genes y 0 enfermedades compartidos (500 genes distintos en 500 casos). El ajuste terminó antes de la primera corrida de prueba (`tuning_grid.json` el 26/09 a las 11:16:50; los `test_*.json` desde las 11:17:37), y `eval/answerer.py`, `eval/cases.py` y `eval/tune.py` no cambiaron desde el commit ea4b766. Ambas particiones salen del mismo simulador (punto b). | No hay sobreajuste a los casos de prueba. | — |
| **g. Anotaciones HPO derivadas de los mismos artículos** (afecta a A) | alto para A | HPO deriva parte de sus anotaciones de los casos publicados de phenopacket-store: las anotaciones de la enfermedad citan el artículo del paciente en 366 de los 501 casos (73 %; 7266 de 10 377 fenopaquetes de toda la colección). | Con el artículo en la base: +0,014 [−0,001; +0,029]; sin él: −0,047 [−0,065; −0,029]. En los 135 casos cuyo artículo HPO no cita, sin necesidad de enmascarar nada: −0,008 [−0,024; +0,008]. El modelo también se beneficia: top-1 50,5 % con artículo citado contra 23,0 % sin él. | A saca de la base del agente, caso por caso, las filas que citan el artículo |

### 11.3 A. Pacientes reales: phenopacket-store

**Datos.** [phenopacket-store](https://github.com/monarch-initiative/phenopacket-store)
(Monarch Initiative), release 0.1.27, archivo `all_phenopackets.zip`, sha256
`d0c70005bb09b87035087516252b6b539fbc0415be7c664b73c9cde7eedf205a`
(verificado; `eval/phenopackets.py` lo baja a `data/phenopacket-store/`, fuera de
git, y rechaza otro hash). Son fenopaquetes GA4GH curados de artículos
publicados: rasgos HPO observados, rasgos explícitamente excluidos, variantes
causales con su gen y el PMID del artículo.

**Casos.** De 10 377 fenopaquetes quedan 5590 (501 genes): se excluyen 69 sin gen
causal, 1306 con el gen fuera del vocabulario del modelo y 3412 con menos de 6
rasgos observados que sean anomalías fenotípicas de la base (0 tenían más de un
gen causal, 0 tenían menos de 3 rasgos no emparentados y en 0 el modelo no
reconocía ningún término). Para que unos pocos genes no dominen (STXBP1 tiene 462
fenopaquetes), la corrida principal toma **un paciente por gen** (501); la
variante toma hasta 5 por gen (1742). Mediana de 10 rasgos observados y 4
excluidos por paciente.

- **Lo que informa la familia**: de 3 a 6 rasgos observados no emparentados,
  sorteados de forma uniforme con semilla fija (123 / 136 / 121 / 121 casos con
  3 / 4 / 5 / 6), y un rasgo excluido no emparentado con ellos si el registro
  tiene alguno (424 de 501).
- **Respuestas: el registro real.** «Sí» si el término preguntado o un
  descendiente fue observado; «no» si él o un ancestro fue excluido; si no,
  **«no sé»**, porque que un artículo no mencione un hallazgo no dice que el
  paciente no lo tenga. Variante de sensibilidad, que no representa a un
  paciente real: lo no registrado se contesta «no».
- **Base del agente sin el artículo del paciente** (camino g): para cada caso se
  reconstruyen las anotaciones de las enfermedades del gen sin las filas de
  `phenotype.hpoa` que citan el PMID del paciente, con la misma regla de lectura
  y fusión que el build de la base (verificado: sin sacar nada, la
  reconstrucción coincide con la base en las 12 974 enfermedades). En 49 de los
  501 casos el gen se queda sin ninguna enfermedad anotada.

**¿El modelo se entrenó con phenopacket-store?** No hay evidencia directa: el
preprint del modelo dice que se entrenó solo con pacientes sintéticos generados
a partir de las anotaciones HPO y no menciona phenopacket-store. Pero esas
anotaciones incluyen las derivadas de estos artículos (camino g), así que la
exposición indirecta es probable, y la línea de base del modelo en A puede ser
optimista. Lo que se mide acá es el incremento del agente sobre el modelo.

| corrida (modo paciente salvo indicación) | casos | top-1 modelo | top-1 agente | Δ top-1 (pp) | Δ MRR agente − modelo | Δ MRR agente − heurística | preguntas | sí / no / no sé |
|---|---|---|---|---|---|---|---|---|
| simulación (sección 6.1), referencia | 300 | 72,0 | 79,3 | +7,3 [+3,7; +11,3] | +0,054 [+0,033; +0,078] | +0,026 [+0,006; +0,045] | 3,25 | 292 / 494 / 189 |
| **A: un paciente por gen, base sin el artículo** | 501 | 43,1 | 37,5 | **−5,6 [−8,2; −3,0]** | **−0,047 [−0,065; −0,029]** | −0,022 [−0,039; −0,007] | 5,69 | 428 / 87 / 2335 |
| A con la base completa (incluye el artículo) | 501 | 43,1 | 45,3 | +2,2 [−0,2; +4,6] | +0,014 [−0,001; +0,029] | +0,013 [+0,002; +0,026] | 5,50 | 459 / 90 / 2208 |
| A, solo los 135 casos cuyo artículo HPO no cita | 135 | 23,0 | 21,5 | −1,5 [−4,4; +1,5] | −0,008 [−0,024; +0,008] | −0,008 [−0,029; +0,010] | 6,62 | 113 / 21 / 760 |
| A, sin los 23 genes de AJUSTE | 478 | 43,3 | 37,2 | −6,1 [−8,8; −3,3] | −0,051 [−0,070; −0,032] | −0,025 [−0,042; −0,009] | 5,66 | 409 / 82 / 2215 |
| A en modo médico | 501 | 43,1 | 37,5 | −5,6 [−8,2; −3,0] | −0,046 [−0,064; −0,028] | −0,026 [−0,042; −0,010] | 5,62 | 418 / 81 / 2315 |
| A, hasta 5 pacientes por gen | 1742 | 43,0 | 38,6 | −4,4 [−5,7; −3,1] | −0,036 [−0,045; −0,027] | −0,018 [−0,026; −0,010] | 5,69 | 1404 / 368 / 8134 |
| A, lo no registrado = «no» (sensibilidad) | 501 | 43,1 | 37,5 | −5,6 [−8,6; −2,8] | −0,058 [−0,083; −0,035] | −0,019 [−0,037; −0,002] | 5,39 | 515 / 2185 / 0 |

- **Con pacientes reales el agente empeora el ranking del modelo**: MRR 0,508 →
  0,461, top-5 58,1 % → 54,3 %, top-10 64,1 % → 61,5 %. El gen correcto baja en
  el 17,6 % de los casos y sube en el 8,0 %; pierde el primer puesto en 37 casos
  y lo gana en 9. La heurística actual también empeora (−0,024 [−0,036; −0,013])
  pero menos, porque sus preguntas mueven menos el ranking.
- **Las respuestas son las que empeoran**, no el hallazgo negado: antes de
  cualquier respuesta el cambio es −0,006 [−0,011; −0,001]; las respuestas
  agregan −0,041 [−0,059; −0,023]. En los 334 casos en que pregunta, Δ MRR
  −0,070 [−0,098; −0,044].
- **El 82 % de las respuestas es «no sé»** (2335 de 2850; en la simulación, el
  19 %): los artículos describen pocos rasgos. Casi todo el efecto viene de los
  428 «sí». Un «sí» a un rasgo real que las anotaciones de la enfermedad no
  mencionan castiga al gen correcto (factor $b$ = 0,1 contra hasta 0,98 de los
  genes que sí lo anotan).
- **Enmascarar el artículo es pesimista en un extremo y la base completa es
  circular en el otro.** En los 49 casos cuyo gen se queda sin anotaciones el
  daño es grande (−0,172 [−0,266; −0,088]); en los 452 restantes, −0,033
  [−0,050; −0,017]. Con el artículo en la base, la mejora en los 366 casos que lo
  citan es +0,022 [+0,004; +0,040]: el agente mejora cuando las anotaciones
  describen a ese mismo paciente. El caso más limpio, los 135 pacientes cuyo
  artículo HPO nunca citó, da −0,008 [−0,024; +0,008]: neutro.
- Ni el modo médico (que deja preguntar hallazgos que requieren estudios), ni
  más pacientes por gen, ni sacar los genes de AJUSTE cambian la conclusión.

### 11.4 B. Anotaciones retenidas

Para cada uno de los 300 casos de PRUEBA, las anotaciones positivas de la
enfermedad verdadera se parten al azar en dos mitades (semilla fija). El
paciente (hallazgos informados y respuestas, con el respondedor `default`) sale
solo de la mitad B; el agente solo ve la mitad A, más los `NOT` de la
enfermedad. El control `B_control` usa los mismos pacientes con la base completa.

| corrida | top-1 modelo → agente | Δ top-1 (pp) | Δ MRR agente − modelo | Δ MRR agente − heurística | sí / no / no sé |
|---|---|---|---|---|---|
| B: el agente no conoce los rasgos del paciente | 66,7 → 63,7 | −3,0 [−6,7; +1,0] | **−0,041 [−0,072; −0,011]** | −0,015 [−0,040; +0,010] | 184 / 780 / 248 |
| B_control: base completa | 66,7 → 68,3 | +1,7 [−1,7; +5,3] | −0,005 [−0,034; +0,023] | +0,002 [−0,025; +0,027] | 208 / 733 / 250 |

- Ocultarle al agente los rasgos del paciente cuesta −0,036 [−0,056; −0,018] de
  MRR (B − control) y deja el efecto en negativo; el top-5 y el top-10 caen
  6,7 pp.
- Aun con la base completa, un paciente que solo expresa la mitad de los rasgos
  anotados borra la mejora (−0,005). Es el mismo fenómeno que el respondedor
  `dropout` de la sección 6.4 (70 % de los rasgos: +0,018), llevado al 50 %.

### 11.5 C. Fuentes de curación cruzadas

Genes con enfermedades anotadas en las dos fuentes: el paciente sale de las
anotaciones de Orphanet y el agente solo conoce las enfermedades OMIM del gen
(`C_orpha`), o al revés (`C_omim`). Se excluyen los 200 genes de AJUSTE; de 1068
y 2108 genes elegibles se sortean 300 por dirección. Los controles dan al agente
las dos fuentes.

| corrida | top-1 modelo → agente | Δ top-1 (pp) | Δ MRR agente − modelo | agente − control |
|---|---|---|---|---|
| C_orpha: paciente ORPHA, agente solo OMIM | 73,3 → 69,0 | −4,3 [−8,7; −0,3] | **−0,055 [−0,087; −0,025]** | −0,109 [−0,141; −0,080] |
| control (las dos fuentes) | 73,3 → 82,0 | +8,7 [+5,0; +12,3] | +0,054 [+0,033; +0,076] | — |
| C_omim: paciente OMIM, agente solo ORPHA | 70,3 → 59,3 | −11,0 [−15,0; −6,7] | **−0,095 [−0,127; −0,062]** | −0,149 [−0,184; −0,116] |
| control (las dos fuentes) | 70,3 → 77,0 | +6,7 [+3,3; +10,3] | +0,055 [+0,034; +0,077] | — |

Dos curaciones independientes de la misma enfermedad alcanzan para que las
preguntas pasen de +0,054 a −0,055 o −0,095. Los controles reproducen con
exactitud la mejora de la simulación: la ganancia es la de un agente que razona
con la misma descripción de la que salió el paciente.

### 11.6 Sensibilidad a los parámetros ajustados en el simulador

Solo informativa: **no se cambió ningún valor por defecto**.

**Un parámetro por vez, sobre A** (Δ MRR agente − modelo; entre corchetes, el
cambio contra la configuración actual):

| variante | Δ MRR vs modelo | contra la configuración actual |
|---|---|---|
| actual (`fixed_prior`, T = 1, fondo 0,1, ε 0,02) | −0,047 [−0,065; −0,029] | — |
| T = 2 | −0,117 [−0,142; −0,092] | −0,070 [−0,090; −0,052] |
| T = 3 | −0,150 [−0,179; −0,121] | −0,103 [−0,127; −0,080] |
| fondo 0,05 | −0,056 [−0,075; −0,038] | −0,009 [−0,018; −0,001] |
| frecuencia faltante 0,5 | −0,052 [−0,070; −0,034] | −0,005 [−0,013; +0,002] |
| `model_sees_present` | −0,001 [−0,014; +0,012] | +0,046 [+0,030; +0,062] |
| configuración anterior a la calibración | −0,012 [−0,030; +0,003] | +0,034 [+0,015; +0,053] |

`model_sees_present` en B y C: −0,069, −0,074 y −0,098 (peor o igual que
`fixed_prior`: −0,028 [−0,050; −0,008], −0,020 [−0,039; −0,001] y −0,003
[−0,026; +0,020]). Con pacientes reales casi todas las respuestas útiles son «sí»,
y mandárselas al modelo es inocuo; en B y C predominan los «no», que en las dos
estrategias pasan por la verosimilitud.

**Exploración con la mitad de los pacientes reales** (`python -m eval.explore`).
Grilla de 44 configuraciones: estrategia × T {0,5; 1} × fondo {0,1; 0,2; 0,3;
0,5} × ε {0,02; 0,1; 0,2} (ε ≤ fondo). Los genes de A se parten al azar en dos
mitades; la configuración se elige en la primera (250 pacientes) y se informa en
la segunda (251), que no participó de la elección, y en B, C y la simulación.

- Ninguna configuración mejora al modelo con pacientes reales: sobre los 501, el
  mejor Δ MRR de la grilla es +0,005 y el peor −0,050.
- La elegida (`model_sees_present`, T = 1, fondo 0,1, ε = 0,1) en la mitad
  retenida: **−0,002 [−0,020; +0,014]** contra el modelo, frente a −0,044
  [−0,068; −0,020] de la configuración actual (diferencia +0,041 [+0,022;
  +0,062]). Deja de dañar, pero no mejora.
- Fuera de A no sirve: B −0,066 [−0,098; −0,036], C_orpha −0,046 [−0,076;
  −0,019], C_omim −0,093 [−0,126; −0,060]. En la simulación, +0,018 [−0,006;
  +0,043], la mitad de lo que da la configuración actual.

Lo que muestra: ajustar los parámetros del re-ranking no alcanza. El problema es
estructural: la verosimilitud trata el silencio de las anotaciones como evidencia
en contra, con factores de hasta 10 por respuesta, y las anotaciones HPO no
describen a los pacientes reales con esa precisión.

### 11.7 Veredicto: ¿es sobreajuste el 0,84?

- **No es sobreajuste a los casos de prueba.** AJUSTE y PRUEBA no comparten
  genes ni enfermedades, el ajuste se hizo antes de mirar PRUEBA, y los
  controles de C, con casos nuevos de genes fuera de AJUSTE, reproducen la
  mejora (+0,054 y +0,055).
- **Es optimismo del simulador, que comparte los supuestos del agente.** Los
  pacientes se generan con las mismas anotaciones y la misma forma de
  probabilidad (noisy-OR de frecuencias) que usa el agente para interpretar las
  respuestas. En ese mundo, preguntar por ganancia de información y actualizar
  con Bayes es casi óptimo, y +0,054 es lo que vale. Apenas el paciente sale de
  otra descripción de la misma enfermedad (otra fuente, la mitad de las
  anotaciones o un artículo publicado), la mejora desaparece y, con los valores
  por defecto actuales, se vuelve un daño de 0,04 a 0,10 de MRR.
- **Los hiperparámetros también se ajustaron a ese mundo.** `fixed_prior` y los
  cierres elegidos en el simulador son peores que los anteriores con pacientes
  reales (+0,034 a favor de la configuración anterior), aunque ninguna de las dos
  mejora al modelo.
- **Cambiar la forma del cálculo no cambia la conclusión** (11.8): la mejor
  variante estructural deja de dañar a los pacientes reales, pero no los ayuda.
- **La mejor estimación con pacientes reales es «no mejora»**: entre −0,047
  (sin el artículo, pesimista) y +0,014 (con el artículo, circular), con −0,008
  [−0,024; +0,008] en el subconjunto más limpio. El 0,842 no debe citarse como el
  efecto esperado en la plataforma; es una cota superior bajo un supuesto que los
  datos reales no cumplen.

### 11.8 Variantes estructurales de cómo entran las respuestas

La sensibilidad (11.6) mostró que el daño no se arregla con los parámetros. Acá
se cambia **la forma** en que las respuestas reordenan los genes; la selección
de preguntas es la misma en todas (ganancia de información con la verosimilitud
del código). Código: `eval/variants.py`. Cuando se corrió, reemplazaba el
re-ranker por caso con una subclase de `BayesianReranker`; hoy V1, V2, V3fp, V4
y V5 corren con el re-ranker del código (secciones 11.10 y 11.11) y solo V2 literal y V3,
que el código no expresa, con la subclase. $p$ es el noisy-OR de
las frecuencias anotadas del término o sus descendientes para la enfermedad
(0,3 si no está indicada), sin piso de fondo ni recorte.

| variante | qué hace |
|---|---|
| **V1** enriquecer | Cada «sí» se le manda al modelo (`model_sees_present`) y nada más reordena: decide el modelo con los síntomas extra. |
| **V2** V1 + exclusiones explícitas | Además, un hallazgo presente (informado o «sí») multiplica por ε = 0,02 las enfermedades que lo excluyen (`NOT` o `Excluded` en el término o un ancestro). Todo lo demás, y todo «no», es neutro. |
| V2 literal | V1, y un «no» multiplica por ε las enfermedades que excluyen el término. Es la redacción original del pedido; castiga a las enfermedades que coinciden con la respuesta, y se corrió solo para mostrarlo. |
| **V3** verosimilitud asimétrica, $k$ = 0,3 o 0,5 | `model_sees_present`; un «sí» multiplica las enfermedades que lo anotan por $1 + k\,p$ y las que lo excluyen por ε; un «no» (o un negado informado) multiplica las que lo anotan por $1 - k\,p$. **Lo no anotado queda neutro** (factor 1). Equivale a una verosimilitud con $P(t \mid d) = (1 + k\,p)/2$ para lo anotado y 1/2, sin información, para lo demás. |
| **V3fp** | V3 con `fixed_prior`: los «sí» no se le mandan al modelo, así cada uno cuenta una sola vez. |
| V4 | La elegida en la exploración (11.6): `model_sees_present`, ε = 0,1. |
| V5 / V5 anterior | Los valores por defecto hasta el 28/09/2026 (`fixed_prior`, ponderación calibrada) / la configuración anterior a la calibración. |

Evaluación: A con la base sin el artículo del paciente, partida por gen en las
mismas dos mitades que la exploración (250 pacientes para elegir, **251
retenidos para informar**); B; C en las dos direcciones; y la simulación de
PRUEBA como referencia. Δ MRR contra el modelo solo, con IC 95 % pareado;
«empeora» es el porcentaje de casos en que el gen correcto terminó más abajo que
en el ranking del modelo; «con respuestas» restringe a los casos con al menos un
«sí» o un «no» (en A, el 82 % de las respuestas es «no sé»).

| variante | A retenidos: Δ MRR | Δ top-1 (pp) | Δ top-5 (pp) | empeora | A retenidos con respuestas: Δ MRR (n) | B | C_orpha | C_omim | simulación |
|---|---|---|---|---|---|---|---|---|---|
| **V1** (por defecto desde el 28/09, D-18) | −0,007 [−0,024; +0,010] | −1,6 [−4,8; +1,2] | +1,2 [−0,4; +3,2] | 9,6 % | −0,013 [−0,047; +0,019] (122) | −0,043 [−0,067; −0,021] | −0,016 [−0,040; +0,006] | −0,025 [−0,044; −0,007] | −0,015 [−0,041; +0,009] |
| V2 | −0,006 [−0,024; +0,010] | −1,6 [−4,8; +1,2] | +1,2 [−0,4; +3,2] | 9,2 % | −0,013 [−0,046; +0,018] (123) | −0,044 [−0,068; −0,021] | −0,015 [−0,039; +0,008] | −0,026 [−0,047; −0,007] | −0,015 [−0,041; +0,009] |
| V2 literal | −0,007 [−0,024; +0,010] | −1,6 [−4,8; +1,2] | +1,2 [−0,4; +3,2] | 9,6 % | −0,013 [−0,047; +0,019] (122) | −0,044 [−0,068; −0,021] | −0,016 [−0,039; +0,008] | −0,025 [−0,044; −0,007] | −0,018 [−0,043; +0,006] |
| V3, k = 0,3 | −0,013 [−0,032; +0,004] | −2,4 [−5,6; +0,8] | −0,4 [−2,4; +1,2] | 10,8 % | −0,028 [−0,065; +0,008] (122) | −0,033 [−0,059; −0,008] | −0,024 [−0,050; +0,001] | −0,040 [−0,064; −0,018] | +0,004 [−0,022; +0,029] |
| V3, k = 0,5 | −0,018 [−0,037; +0,000] | −3,6 [−6,8; −0,4] | −0,4 [−2,4; +1,2] | 12,4 % | −0,038 [−0,074; −0,003] (123) | −0,040 [−0,066; −0,015] | −0,020 [−0,047; +0,007] | −0,047 [−0,072; −0,024] | +0,022 [−0,004; +0,047] |
| **V3fp, k = 0,3** (por defecto unas horas del 28/09) | **−0,001 [−0,009; +0,007]** | +0,0 [−1,6; +1,6] | −0,4 [−1,2; +0,0] | **4,0 %** | −0,002 [−0,019; +0,014] (122) | **+0,004 [−0,008; +0,016]** | **+0,001 [−0,014; +0,017]** | −0,022 [−0,039; −0,006] | +0,025 [+0,012; +0,039] |
| V3fp, k = 0,5 | −0,005 [−0,015; +0,005] | −0,8 [−2,8; +1,2] | −0,4 [−1,2; +0,0] | 5,6 % | −0,010 [−0,031; +0,009] (123) | +0,000 [−0,015; +0,016] | −0,007 [−0,028; +0,013] | −0,044 [−0,065; −0,022] | +0,036 [+0,020; +0,054] |
| V4 | −0,002 [−0,020; +0,014] | −1,2 [−4,4; +2,0] | +0,8 [−1,2; +2,8] | 9,6 % | −0,005 [−0,043; +0,030] (118) | −0,066 [−0,098; −0,036] | −0,046 [−0,076; −0,019] | −0,093 [−0,126; −0,060] | +0,018 [−0,006; +0,043] |
| V5 (calibrada, por defecto hasta el 28/09) | −0,044 [−0,068; −0,020] | −6,0 [−9,6; −2,4] | −4,0 [−6,8; −1,6] | 18,3 % | −0,087 [−0,136; −0,043] (126) | −0,041 [−0,072; −0,011] | −0,055 [−0,087; −0,025] | −0,095 [−0,127; −0,062] | +0,054 [+0,033; +0,078] |
| V5 anterior | −0,012 [−0,033; +0,010] | −2,0 [−5,6; +1,6] | −1,6 [−4,4; +0,8] | 13,9 % | −0,017 [−0,045; +0,011] (180) | −0,221 [−0,269; −0,171] | −0,251 [−0,300; −0,201] | −0,283 [−0,334; −0,233] | −0,029 [−0,063; +0,006] |

Qué dice la tabla:

- **Ninguna variante mejora a los pacientes reales retenidos.** La mejor queda en
  −0,001 [−0,009; +0,007]; restringido a los casos con respuestas, tampoco
  aparece un efecto escondido por la dilución (el mejor, −0,002). Con la regla
  fijada de antemano (la mayor MRR en la mitad de selección) se elegiría V4, que
  daña en B y en C.
- **Mandarle los «sí» al modelo (V1, V2) es inocuo con pacientes reales, que no
  se equivocan, y dañino en B y C_omim**: ahí el 10 % de las respuestas
  simuladas está invertido, así que cerca de la mitad de los «sí» son falsos en
  B y uno de cada cuatro en C_omim (estimado a partir de los conteos de
  respuestas), y un «sí» falso reordena todo el ranking del modelo. Las exclusiones explícitas casi
  nunca se disparan (V2 ≈ V1), y la versión literal de V2 también da lo mismo que
  V1: su daño lógico existe, pero es raro.
- **La verosimilitud asimétrica funciona solo sin contar dos veces cada «sí»**:
  V3 con `model_sees_present` daña en todos los escenarios, y V3fp no.
- **V3fp con k = 0,3 es la única variante que no daña en A, B y C_orpha**, y la
  que menos casos empeora (4,0 % contra 18,3 % de la actual). Daña poco pero de
  forma significativa en C_omim (−0,022 [−0,039; −0,006], contra −0,095 de la
  actual) y conserva algo menos de la mitad de la mejora simulada (+0,025). Es,
  en la práctica, un re-ranking que casi no mueve el ranking con pacientes
  reales: con $k \to 0$ converge al modelo.
- Se eligió después de ver A, B y C; la mitad retenida de A no alcanza para
  confirmarla y hace falta un conjunto nuevo (por ejemplo, genes o casos de una
  release posterior de phenopacket-store) antes de tratarla como validada.

### 11.9 Qué implica

1. **La interfaz no debería mostrar el ranking refinado como vista por
   defecto.** Con la ponderación calibrada empeoraba el puesto del gen correcto en el
   17,6 % de los pacientes reales y lo mejora en el 8,0 %; con la mejor variante
   estructural queda neutro, no mejor. La vista por defecto debería ser la del
   modelo («Original»), con el refinado como opción secundaria, si se mantiene.
2. **Si se mantiene el re-ranking, cambiarlo por V3fp con k = 0,3.** Es el
   único cambio que deja de dañar a los pacientes reales y a B y C_orpha.
   **Hecho el 28/09/2026** ([DECISIONES.md](DECISIONES.md), D-17) y reemplazado
   el mismo día por mandarle las respuestas solo al modelo (D-18, sección
   11.11); sigue disponible. Así se implementó:
   - Estrategia: sin cambios, `RerankStrategy.FIXED_PRIOR`
     (`PREGUNTAS_RERANK_STRATEGY=fixed_prior`), temperatura 1.
   - `ranking/likelihood.py`: que `DiseaseProfile` exponga, además de lo que
     tiene, el noisy-OR sin piso ni recorte de cada término anotado
     (`frequency(t) -> float | None`) y si la enfermedad excluye el término o un
     ancestro (`excludes(t) -> bool`).
   - `ranking/reranker.py`: `RerankConfig` suma `answer_weighting` (enum nuevo
     `AnswerWeighting`, valores `calibrated`, el actual, y `asymmetric`, el nuevo
     valor por defecto), `answer_weight` k = 0,3 y `exclusion_factor` = 0,02.
     Con `asymmetric`, `_log_likelihood` recibe también todos los hallazgos
     presentes y suma: $\log \varepsilon$ por cada hallazgo presente (informado
     o «sí») que la enfermedad excluye; $\log(1 + k\,p)$ por cada presente que
     el modelo no vio (los «sí», y los términos que el modelo no conoce) y la
     enfermedad anota; $\log(1 - k\,p)$ por cada ausente («no» y negados
     informados) que la enfermedad anota; 0 en todo otro caso.
   - Sin cambios: la selección de preguntas (su verosimilitud, fondo 0,1,
     frecuencia faltante 0,3, ε 0,02), las reglas de cierre y la garantía de la
     sección 4.5 de ARQUITECTURA.md (un «no» nunca favorece a una enfermedad que
     anota el término: su factor es ≤ 1 y el de las demás, 1).
   - Variables de entorno: `PREGUNTAS_ANSWER_WEIGHTING=asymmetric` (hoy el
     valor por defecto es `none`), `PREGUNTAS_ANSWER_WEIGHT` (0,3) y
     `PREGUNTAS_RERANK_STRATEGY=fixed_prior`.
3. **Las preguntas pueden seguir siendo útiles como guía para la consulta**
   (qué conviene averiguar), algo que esta evaluación no mide. Lo que no está
   demostrado es que reordenar los genes con las respuestas ayude.

### 11.10 Confirmación de la ponderación asimétrica en el código

Con la ponderación asimétrica ya en `src/` y por defecto (D-17, antes de D-18), se volvieron a correr
los escenarios por el camino normal del código (`build_pipeline` con
`AgentConfig()`, el `BayesianReranker` de `src/`, sin subclase) y se
compararon con las corridas de la sección 11.8, hechas con la subclase de
`eval/`:

- **Trazas idénticas**: de las 17 010 trazas de las 50 corridas de
  `eval.variants`, 17 008 tienen la misma trayectoria del puesto del gen correcto
  y las mismas preguntas. Las 2 que cambian son de V3 con `model_sees_present`
  (una por $k$, en la simulación), la variante que todavía usa la subclase y que
  ahora toma la frecuencia de `src/` sin el recorte a $1 - 10^{-9}$ que tenía; no
  afecta a ninguna cifra publicada.
- **`python -m eval.run_simulation --split test`** sin `--config`, que entonces
  tomaba la ponderación asimétrica (hoy, `--config
  eval/configs/asymmetric_fixed_prior.json`), da las mismas 300 trazas que V3fp
  en la simulación.

Cifras de la configuración por defecto (Δ contra el modelo solo, IC 95 %
pareado):

| escenario | casos | MRR modelo → agente | Δ MRR | Δ top-1 (pp) | Δ top-5 (pp) | empeora / mejora |
|---|---|---|---|---|---|---|
| **A, pacientes reales retenidos** | 251 | 0,500 → 0,499 | **−0,001 [−0,009; +0,007]** | +0,0 [−1,6; +1,6] | −0,4 [−1,2; +0,0] | 4,0 % / 3,2 % |
| A, los 501 | 501 | 0,508 → 0,507 | −0,001 [−0,006; +0,005] | +0,2 [−0,8; +1,2] | +0,0 [−0,8; +0,8] | 5,4 % / 4,2 % |
| A retenidos con al menos un «sí» o «no» | 122 | 0,354 → 0,352 | −0,002 [−0,019; +0,014] | +0,0 [−3,3; +3,3] | −0,8 [−2,5; +0,0] | 8,2 % / 4,9 % |
| B, anotaciones retenidas | 300 | 0,755 → 0,759 | +0,004 [−0,008; +0,016] | +0,0 [−2,0; +2,0] | −0,3 [−1,7; +1,0] | 6,0 % / 8,7 % |
| C_orpha | 300 | 0,811 → 0,812 | +0,001 [−0,014; +0,017] | +0,7 [−1,7; +3,3] | −0,7 [−2,0; +0,7] | 4,7 % / 7,7 % |
| C_omim | 300 | 0,774 → 0,752 | −0,022 [−0,039; −0,006] | −3,3 [−6,0; −0,7] | −1,0 [−2,0; +0,0] | 8,3 % / 7,7 % |
| simulación (PRUEBA) | 300 | 0,787 → 0,812 | +0,025 [+0,012; +0,039] | +3,3 [+1,3; +5,7] | +1,0 [+0,0; +2,3] | 2,0 % / 12,0 % |

Aproximadamente neutra con pacientes reales, sin daño en B ni en C_orpha, un
daño chico en C_omim y la mitad de la mejora simulada. El re-ranking casi no
mueve la lista con pacientes reales. Falta un conjunto nuevo para confirmarla
(sección 11.8).

### 11.11 Confirmación: las respuestas solo llegan al modelo (valor por defecto)

Por decisión de Mate ([DECISIONES.md](DECISIONES.md), D-18), desde el 28/09/2026
el agente no reordena nada: con `PREGUNTAS_RERANK_STRATEGY=model_sees_present` y
`PREGUNTAS_ANSWER_WEIGHTING=none`, cada «sí» efectivo se suma a la consulta del
modelo, el ranking es el del modelo para esa consulta, y los «no» y los
hallazgos negados quedan registrados sin tocarlo. Es la variante V1 de 11.8.
Cómo hacer que el propio modelo use los síntomas negados se investiga aparte.

Se volvieron a correr las 50 corridas de `eval.variants` con V1 por el camino
normal del código (`AgentConfig()`, `AnswerWeighting.NONE` en el
`BayesianReranker` de `src/`): las 17 010 trazas son idénticas, caso por caso
(puesto del gen correcto en cada ronda y preguntas), a las de la corrida
anterior. `python -m eval.run_simulation --split test` sin `--config` da las
mismas 300 trazas que V1 en la simulación.

| escenario | casos | MRR modelo → agente | Δ MRR | Δ top-1 (pp) | Δ top-5 (pp) | empeora / mejora |
|---|---|---|---|---|---|---|
| **A, pacientes reales retenidos** | 251 | 0,500 → 0,493 | **−0,007 [−0,024; +0,010]** | −1,6 [−4,8; +1,2] | +1,2 [−0,4; +3,2] | 9,6 % / 10,8 % |
| A, los 501 | 501 | 0,508 → 0,507 | −0,000 [−0,012; +0,011] | −0,4 [−2,4; +1,4] | +0,4 [−0,8; +1,6] | 8,0 % / 11,4 % |
| A retenidos con al menos un «sí» o «no» | 122 | 0,358 → 0,345 | −0,013 [−0,047; +0,019] | −3,3 [−9,0; +2,5] | +2,5 [−0,8; +6,6] | 18,9 % / 22,1 % |
| B, anotaciones retenidas | 300 | 0,755 → 0,712 | −0,043 [−0,067; −0,021] | −4,0 [−7,3; −1,0] | −6,7 [−10,0; −3,7] | 17,3 % / 6,3 % |
| C_orpha | 300 | 0,811 → 0,795 | −0,016 [−0,040; +0,006] | +0,0 [−3,3; +3,7] | −4,3 [−7,0; −2,0] | 12,7 % / 8,3 % |
| C_omim | 300 | 0,774 → 0,749 | −0,025 [−0,044; −0,007] | −4,3 [−7,3; −1,3] | +0,0 [−2,3; +2,3] | 14,3 % / 9,7 % |
| simulación (PRUEBA) | 300 | 0,787 → 0,773 | −0,015 [−0,041; +0,009] | +0,3 [−3,3; +3,7] | −5,3 [−8,7; −2,3] | 14,3 % / 10,3 % |

- **Con pacientes reales, neutro**, igual que la ponderación asimétrica: ni el
  MRR ni el top-1 cambian de forma significativa. Mueve más la lista (el gen
  correcto cambia de puesto en el 20 % de los retenidos, contra el 7 % de la
  asimétrica), en las dos direcciones. En los pacientes reales, las respuestas
  son del registro publicado y nunca se equivocan.
- **Empeora al modelo cuando las respuestas tienen errores.** En B y C_omim el
  respondedor simulado invierte el 10 % de las respuestas, y un «sí» falso entra
  en la consulta y reordena toda la lista del modelo; en B, el top-5 cae 6,7 pp.
  Con pacientes o familias que se equivocan, esto es lo que hay que vigilar.
- **Frente a la asimétrica** (11.10): igual con pacientes reales (−0,007 contra
  −0,001, intervalos que se superponen), peor en B (−0,043 contra +0,004), en
  C_orpha (−0,016 contra +0,001) y en la simulación (−0,015 contra +0,025),
  parecida en C_omim (−0,025 contra −0,022).
- En modo médico, `genes_if_no` va vacío y el fundamento dice de qué genes es
  típico el síntoma; el resumen de cierre compara con el ranking de los
  hallazgos informados (una segunda llamada al modelo, solo si el presupuesto
  de la ronda la cubre).

### 11.12 Cómo reproducir

```sh
.venv/bin/python -m eval.audit             # A, B, C y sensibilidad: 20 corridas en ~10 min con 4 procesos
                                           # (baja phenopacket-store 0.1.27 la primera vez y verifica el sha256)
.venv/bin/python -m eval.explore           # exploración de 44 configuraciones: ~14 min
.venv/bin/python -m eval.variants          # variantes estructurales (11.8, 11.10 y 11.11): 50 corridas, ~12 min
.venv/bin/python -m eval.run_simulation --split test --policies none,agent --out eval/results/test_defaults_none.json
.venv/bin/python -m eval.run_simulation --split test --policies none,agent \
    --config eval/configs/asymmetric_fixed_prior.json --out eval/results/test_defaults_asymmetric.json
.venv/bin/python -m eval.audit --summary-only   # rehace eval/results/audit/summary.json sin simular
```

Las trazas quedan en `eval/results/audit/<corrida>.json` (formato de
`eval.run_simulation`) y cada cifra de esta sección en
`eval/results/audit/summary.json`, `eval/results/audit/explore/summary.json` y
`eval/results/audit/variants/summary.json` (clave
`variants.<variante>.<escenario>.{held_out,held_out_informative,all,informative}`).
Requiere lo mismo que la sección 10: la base (`make kb`, que deja
`data/sources/phenotype.hpoa`) y el servicio del modelo. Los tests de estos
módulos están en `tests/eval/test_decircularised.py`.
