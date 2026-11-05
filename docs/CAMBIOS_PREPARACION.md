# Preparación del proyecto para el portafolio

El original se conserva fuera del repositorio y no fue modificado.
La copia incluye los ajustes de configuración y documentación descritos a continuación.

## Material seleccionado

Se copiaron 19 archivos fuente y de interfaz de los cuatro componentes.
No se copiaron las credenciales, datos universitarios, índices FAISS, metadatos pickle,
archivos RAR, imágenes de terceros, ejecutables ni carpetas del IDE.
Se omitió el script original que ejecutaba una llamada de Gemini al importarlo.

## Correcciones

- Dependencias directas declaradas por componente y agregadas en la raíz.
- Configuración de Gemini cargada desde la carpeta del tutor.
- Conexión MySQL construida mediante URL.create para admitir contraseñas con caracteres especiales.
- Herramienta de creación de esquema limitada a la base de demostración `tutor_demo`.
- Modelo de la tabla `session_activity_log`, requerida por el código existente de reinicio.
- Ingesta interrumpida si falla un embedding, evitando asociar vectores a textos incorrectos.
- Validación de longitud de corpus/vectores y de índice/metadatos al cargar.
- Identidad del tutor informativo configurable para usar un corpus ficticio.
- Debug desactivado en el arranque del tutor informativo.
- Compilación C# posible sin copiar un archivo privado de configuración.
- Dockerfile del tutor de clases sin dependencias multimedia que su código no utiliza.
- Exclusión de configuración privada del contexto de construcción Docker.

## Documentación y ejemplos

README, diagrama de arquitectura, ficha del proyecto, informe de comprobaciones,
configuraciones de ejemplo, corpus ficticio y guía de base de datos.
No se añadieron métricas de aprendizaje o rendimiento sin una evaluación.
No se ha asignado una licencia de redistribución.
