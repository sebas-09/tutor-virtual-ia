# Tutor virtual con IA integrado en Second Life

Proyecto personal desarrollado por **Sebastian Monga**.
Combina consulta documental con RAG y tutoría guiada mediante un avatar en Second Life.

Desarrollo individual de los cuatro componentes: tutor informativo, tutor de clases,
orquestador y puente con el mundo virtual. Se presenta como proyecto personal de portafolio.

## Qué incluye

| Componente | Función | Tecnologías |
|---|---|---|
| Tutor informativo | Recuperación de contexto documental y generación de respuestas | Python, Flask, Gemini, FAISS |
| Tutor de clases | Tutoría por fases, sesiones, historial y progreso | Python, Flask, SQLAlchemy, MySQL |
| Orquestador | Enrutamiento, reintentos, límites y adaptación del chat | Python, Flask, HTTP |
| Puente | Interacción mediante chat local y mensajes de Second Life | C#, .NET 8, LibreMetaverse |

[Arquitectura](docs/ARQUITECTURA.md) · [Ficha del proyecto](docs/FICHA_PROYECTO.md) ·
[Preparación y comprobaciones](docs/ESTADO_PREPARACION.md)

## Requisitos

- Python 3.11 o 3.12 de 64 bits.
- MySQL 8 para el tutor de clases, con una base vacía llamada `tutor_demo`.
- Clave propia de Gemini para generación y embeddings. Las llamadas pueden generar consumo.
- .NET SDK 8 y cuenta de Second Life para el puente; opcionales si se usan las interfaces web.

Las dependencias directas están fijadas. El código conserva el SDK
`google-generativeai` del proyecto; está obsoleto y su migración a `google-genai`
queda pendiente. La disponibilidad de los modelos debe comprobarse con la cuenta propia.

## Instalación — PowerShell

Ejecutar desde la raíz de esta carpeta:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item tutor_clases/.env.example tutor_clases/.env
Copy-Item tutor_info/app/.env.example tutor_info/app/.env
```

Editar ambos `.env`: introducir una clave propia y configurar únicamente una base
de demostración. Estos archivos están excluidos de Git. Si ya existen, conservarlos;
no volver a copiarlos encima.

Crear la base y un usuario dedicado con una herramienta MySQL ([instrucciones](docs/BASE_DE_DATOS.md)). El esquema se genera
desde los modelos ORM; no hace falta importar datos originales:

```powershell
.\.venv\Scripts\python.exe tutor_clases/init_db.py --crear-esquema
```

La herramienta crea tablas faltantes solo si la base se llama `tutor_demo`.
No elimina tablas ni agrega alumnos o conversaciones.

## Tutor informativo

Regenerar el índice a partir del corpus ficticio incluido. Requiere Gemini:

```powershell
.\.venv\Scripts\python.exe tutor_info/app/ingest.py
.\.venv\Scripts\python.exe tutor_info/app/app.py
```

Abrir http://127.0.0.1:8000. Pregunta sugerida: «¿Cómo solicito una tutoría?».
No editar ni cargar archivos `.pkl` procedentes de fuentes desconocidas.

## Tutor de clases

En otra terminal, desde la raíz:

```powershell
.\.venv\Scripts\python.exe tutor_clases/app.py
```

Abrir http://127.0.0.1:5000. Usar un identificador ficticio de estudiante.
El flujo completo requiere MySQL y una clave de Gemini válida.

## Orquestador

En otra terminal:

```powershell
.\.venv\Scripts\python.exe orquestador/orchestrator.py
```

Escucha solo en 127.0.0.1:7000 y espera los otros servicios en los puertos anteriores.
Ejemplo desde PowerShell:

```powershell
$consulta = @{mode="info"; message="¿Cuál es el horario de atención?"; session_id="demo-001"; channel="web"} | ConvertTo-Json
Invoke-RestMethod -Uri http://127.0.0.1:7000/route -Method Post -ContentType "application/json; charset=utf-8" -Body ([System.Text.Encoding]::UTF8.GetBytes($consulta))
```

## Second Life — opcional

```powershell
Copy-Item ComponentePuente/TutorBot/appsettings.example.json ComponentePuente/TutorBot/appsettings.json
dotnet restore ComponentePuente/TutorBot.sln
dotnet build ComponentePuente/TutorBot.sln --no-restore
dotnet run --project ComponentePuente/TutorBot
```

Antes de `dotnet run`, editar el archivo privado de configuración con una cuenta propia
y una ubicación permitida. Ejecutarlo inicia sesión y puede enviar mensajes en el mundo
virtual. La configuración de ejemplo exige mencionar al tutor para que responda.
La demo web permite presentar el proyecto sin ejecutar este puente.

## Autoría y alcance de publicación

**Autor: Sebastian Monga.** Proyecto personal de desarrollo individual.
La copia pública incluye código fuente, documentación, configuraciones de ejemplo y un corpus ficticio.
No incluye credenciales ni datos originales de usuarios.

No se ha otorgado una licencia de redistribución o modificación. Las bibliotecas externas
conservan sus propias licencias y condiciones de uso.
