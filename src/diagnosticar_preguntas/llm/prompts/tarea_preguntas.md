# Tarea: elegir y redactar las preguntas de la ronda

En `<datos>` tenés los genes mejor ubicados, los síntomas ya conocidos (presentes, ausentes y sin respuesta) y `candidatos`: síntomas que conviene preguntar, con su `hpo_id`, su etiqueta, cuánta información aportan, qué genes suben con un "sí" (`suben_con_si`) y cuáles con un "no" (`suben_con_no`).

Elegí exactamente `cantidad_de_preguntas` candidatos distintos y redactá una pregunta por cada uno:

- `hpo_id`: copialo tal cual de `candidatos`. No inventes otros.
- `text`: una sola pregunta de sí o no, de hasta 200 caracteres, que empiece con "¿" y termine con "?". Describí solo el síntoma de ese candidato: no le sumes otros signos relacionados.
- `help`: una oración corta que ayude a responder, o `null` si no hace falta.
- `rationale`: una línea con qué genes separa la pregunta.

Priorizá los candidatos que más separan a los genes de arriba y que la persona pueda responder. Si `correcciones` no está vacío, tu intento anterior las violó: corregilas.
