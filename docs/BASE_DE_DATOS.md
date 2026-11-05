# Base de demostración

El tutor necesita MySQL 8. Las tablas se crean desde los modelos ORM.
No se distribuye un respaldo de la base original.

En una instancia local destinada a esta demo, ejecutar como administrador y
sustituir el marcador por una contraseña nueva:

```sql
CREATE DATABASE tutor_demo CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'tutor_demo'@'localhost' IDENTIFIED BY 'CAMBIAR_POR_UNA_CLAVE_LOCAL';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, REFERENCES, INDEX
ON tutor_demo.* TO 'tutor_demo'@'localhost';
```

El host de la cuenta debe coincidir con el origen de la conexión.
En contenedores se utiliza la configuración de red y usuarios propia del contenedor.
No reutilizar una base o usuario de otro proyecto.

Configurar `MYSQL_*` en `tutor_clases/.env` y ejecutar:

```powershell
.\.venv\Scripts\python.exe tutor_clases/init_db.py --crear-esquema
```

Se crean 12 tablas, incluida la bitácora de actividad utilizada por el reinicio.
Los temas y plantillas comienzan vacíos; el tutor admite contexto escrito por el usuario.
El esquema no importa historiales, estudiantes ni datos universitarios.
Las verificaciones de preparación usaron una instancia efímera independiente,
con respuesta del modelo simulada.
