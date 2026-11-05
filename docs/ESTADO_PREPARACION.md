# Comprobaciones técnicas y preparación

## Resultado

Proyecto personal de Sebastian Monga preparado para compartir el código fuente.
La publicación fue autorizada por el autor el 5 de noviembre de 2026.
El informe siguiente conserva el alcance de las comprobaciones del 23 de octubre de 2026;
no afirma una nueva ejecución con servicios externos ni un despliegue de los tutores.

## Comprobaciones realizadas

| Comprobación | Resultado y alcance |
|---|---|
| Sintaxis Python | 13 archivos analizados sin errores |
| Integridad RAG | 4 pruebas sin red aprobadas: orden, fallo de embedding, corpus incompleto e índice histórico desalineado |
| Tutor informativo | HTML, health, validación y respuesta HTTP verificados con generación simulada |
| Orquestador | Enrutamiento, fragmentación, fallo de servicio y circuito abierto verificados con dobles |
| MySQL 8 | 12 tablas creadas en un contenedor desechable con credenciales aleatorias |
| Tutor de clases | Flujo intake → plan → teach, historial y reset comprobados contra MySQL real; llamadas LLM simuladas |
| Puente .NET 8 | Restore desde caché NuGet local y build correctos; 3 advertencias de nulabilidad heredadas |
| Original | Hashes SHA-256 de los 19 archivos fuente copiados coinciden con el original tras la preparación |
| Configuración privada | No hay archivos activos `.env` ni `appsettings.json` en la copia |
| Credenciales | Sin coincidencias con los valores privados de las configuraciones originales ni patrones de claves revisados |
| Limpieza | Contenedor MySQL temporal retirado; compilados y resultados locales ignorados por Git |

Se utilizó el Python 3.12 del entorno existente. Las dependencias directas se fijaron
a las versiones instaladas que permitieron las comprobaciones; no se ha certificado
una instalación en un equipo limpio ni fijado todavía todo el árbol transitivo.

El informe detallado local está en `.local/verificacion.json` (ignorado por Git).
El verificador usado para MySQL y las rutas se conserva fuera del repositorio,
en la carpeta de preparación del portafolio.

## Cómo repetir las comprobaciones públicas sin red

Con las dependencias instaladas, desde la raíz:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
dotnet build ComponentePuente/TutorBot.sln --no-restore
```

El build requiere haber restaurado previamente los paquetes NuGet.
Las pruebas de integridad no utilizan una clave real ni llaman a Gemini.

## Pendientes para demostrar funcionamiento completo

- Ejecutar con una clave propia y modelos disponibles: ingesta del corpus ficticio y respuestas reales de ambos tutores.
- Validar la calidad de las respuestas, el comportamiento sin evidencia y el flujo pedagógico.
- Comprobar el puente dentro de Second Life con una cuenta y ubicación autorizadas.
- Revisar la región, coordenadas y animaciones históricas del puente antes de la sesión real.
- Migrar el SDK heredado `google-generativeai` antes de considerar un despliegue mantenido.
- Resolver las advertencias de nulabilidad de C# en una revisión posterior.

Las pruebas con respuestas simuladas verifican la integración de software; no miden
la exactitud del modelo, el aprendizaje del usuario ni el funcionamiento en Second Life.
Las rutas locales no deben exponerse directamente a Internet.
