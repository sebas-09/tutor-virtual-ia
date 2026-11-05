# Ficha del proyecto para el portafolio

**Nombre:** Tutor virtual con IA integrado en Second Life.
**Tipo:** proyecto personal de desarrollo individual.
**Autor y desarrollo:** Sebastian Monga; desarrollo de los cuatro componentes.

## Problema y solución

El proyecto combina un tutor de información institucional con recuperación documental
y un tutor de clases guiadas, accesibles mediante un avatar en Second Life.
Las respuestas se generan con Gemini; el módulo informativo recupera contexto con FAISS.
El módulo de clases conserva sesiones y progreso mediante SQLAlchemy/MySQL.

## Contribución

Desarrollo del puente C#/.NET, orquestador Python, tutor informativo RAG y tutor de clases.
Las bibliotecas externas y servicios de modelos mantienen su autoría y condiciones propias.

## Decisiones que se pueden explicar en una entrevista

- Separación entre consulta documental y tutoría guiada.
- Flujo de tutoría con toma de contexto, planificación, enseñanza y reflexión.
- Persistencia de sesiones y progreso.
- Control de reintentos, tiempos de espera y fallos entre servicios.
- Adaptación de respuestas a los límites del chat de Second Life.

## Presentación en el portafolio

Ficha del proyecto, esquema de arquitectura y código fuente.
No se afirman mejoras de aprendizaje, precisión o rendimiento sin evaluación.

## Publicación

Esta copia utiliza un corpus ficticio y excluye configuraciones privadas y datos originales.
El autor autorizó su publicación como proyecto personal el 5 de noviembre de 2026.
No se ha asignado una licencia de redistribución. El estado efectivo de publicación
se registra fuera del repositorio al completar la subida a GitHub.
