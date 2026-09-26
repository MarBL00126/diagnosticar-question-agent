# Tarea: leer las notas de las respuestas

En `<datos>` hay respuestas con el botón que se marcó ("si", "no" o "no_se") y una nota libre. Para cada una devolvé una lectura:

- `hpo_id`: el de la respuesta.
- `consistent`: `false` si la nota contradice el botón o expresa duda sobre él (por ejemplo, marcó "si" y escribió "creo que no" o "no estoy seguro"); `true` en cualquier otro caso. No decidas otro valor: una nota solo puede volver dudosa una respuesta, nunca cambiar un sí por un no.
- `summary`: la nota resumida en pocas palabras y sin agregar nada, o `null` si no aporta información.
