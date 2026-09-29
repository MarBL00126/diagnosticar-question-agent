# Tarea: resumen final de las preguntas

En `<datos>` están las respuestas de la sesión (el síntoma por el que se preguntó, la respuesta y, si hay, un resumen de la nota) y los genes mejor ubicados al cierre, con su puesto actual y, si se conoce, el puesto que tenían antes de las preguntas (`puesto_antes_de_las_preguntas`; `null` si estaban fuera de los 50 primeros). Si `los_no_mueven_la_lista` es falso, las respuestas «no» quedaron registradas para la consulta y no cambiaron el orden: no les atribuyas cambios.

Escribí en `summary` un resumen de dos a cuatro oraciones que solo repase las preguntas sobre los síntomas informados: qué se preguntó, qué se respondió y cómo esas respuestas cambiaron el orden de la lista (si no hay puesto anterior, no digas cómo cambió). Nada más.

El resumen no recomienda nada, ni al médico ni a la familia: diagnostic.ar no tiene cómo validar una indicación clínica. Nunca propongas estudios, análisis, pruebas, especialistas ni otros pasos a seguir, y no digas que hay algo para confirmar, descartar, solicitar, pedir o considerar. No nombres estudios ni pruebas, tampoco los que aparezcan en una nota. Si la etiqueta de un síntoma nombra un estudio (por ejemplo "Anormalidad en el EEG"), escribila tal cual está en `sintoma`.

- Para paciente o familia: en palabras simples y en la misma forma impersonal de las preguntas ("las respuestas", "los síntomas informados"), sin suponer si lee la persona con los síntomas o un familiar. Sin genes, nombres de enfermedades, porcentajes ni frecuencias, y sin las palabras "gen", "variante", "mutación" o "patogénica": contá que la lista cambió de orden, no qué hay en ella. La última oración es una invitación neutral a llevar esta información a la consulta, por ejemplo: "Llevá esta información a la consulta para conversarla con el médico."
- Para médico: qué respuestas movieron qué genes (subieron o bajaron respecto del puesto antes de las preguntas), y nada más. Es una orientación, no un diagnóstico.
