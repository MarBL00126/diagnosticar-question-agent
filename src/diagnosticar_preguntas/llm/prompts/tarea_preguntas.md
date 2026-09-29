# Tarea: elegir y redactar las preguntas de la ronda

En `<datos>` tenés los genes mejor ubicados, los síntomas ya conocidos (presentes, ausentes y sin respuesta) y `candidatos`: síntomas que conviene preguntar, con su `hpo_id`, su etiqueta, cuánta información aportan y los genes candidatos a los que más se asocian (`genes_asociados`).

Elegí exactamente `cantidad_de_preguntas` candidatos distintos y redactá una pregunta por cada uno:

- `hpo_id`: copialo tal cual de `candidatos`. No inventes otros.
- `text`: una sola pregunta de sí o no, de hasta 200 caracteres, que empiece con "¿" y termine con "?".
- `help`: una oración corta que ayude a responder, o `null` si no hace falta.
- `rationale`: una línea con de qué genes candidatos es típico el síntoma.

Cada pregunta es sobre un solo síntoma, el de su candidato, y con su mismo alcance:

- No juntes dos signos con "o" o "y" ("¿Se notó rigidez o temblor?" son dos preguntas). Si la etiqueta ya nombra un solo hallazgo, preguntá por ese hallazgo y nada más.
- No sumes otros signos relacionados, ni como ejemplo: los ejemplos solo pueden ser formas de reconocer ese mismo síntoma.
- No lo vuelvas más amplio ni más específico que la etiqueta.

Priorizá los candidatos que más separan a los genes de arriba y que la persona pueda responder. Si `correcciones` no está vacío, tu intento anterior las violó: corregilas.
