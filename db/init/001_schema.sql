-- =====================================================================
-- Proyecto Empleo - Esquema inicial (PostgreSQL 16)
-- v0.2 - 2026-11-12
-- Reglas:
--   * Actualizacion diaria a las 6:00 a.m. (America/Bogota).
--   * Un correo/vacante puede reprocesarse si la vacante sigue abierta
--     y han pasado al menos 3 dias desde su ultimo procesamiento.
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS pg_trgm;    -- busqueda difusa en titulos

-- ---------------------------------------------------------------------
-- Tipos
-- ---------------------------------------------------------------------
CREATE TYPE modalidad_trabajo AS ENUM ('presencial', 'remoto', 'hibrido', 'indiferente');
CREATE TYPE tipo_fuente       AS ENUM ('api', 'correo');
CREATE TYPE estado_coincidencia AS ENUM ('nueva', 'vista', 'guardada', 'postulada', 'descartada');
CREATE TYPE estado_ejecucion  AS ENUM ('en_curso', 'ok', 'error');

-- ---------------------------------------------------------------------
-- 0. Parametros generales
-- ---------------------------------------------------------------------
CREATE TABLE parametros (
    clave       VARCHAR(60) PRIMARY KEY,
    valor       VARCHAR(200) NOT NULL,
    descripcion TEXT
);

INSERT INTO parametros (clave, valor, descripcion) VALUES
    ('zona_horaria',          'America/Bogota', 'Zona horaria del programador de tareas'),
    ('horario_actualizacion', '0 6 * * *',      'Cron de la actualizacion diaria (6:00 a.m.)'),
    ('dias_reproceso',        '3',              'Dias minimos para volver a procesar una vacante abierta');

-- ---------------------------------------------------------------------
-- 1. Usuarios
-- ---------------------------------------------------------------------
CREATE TABLE usuarios (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    nombre          VARCHAR(120) NOT NULL,
    email           VARCHAR(160) NOT NULL UNIQUE,
    password_hash   TEXT         NOT NULL,
    -- Alias del buzon de alertas: proyecto+<alias>@gmail.com
    -- Permite saber a quien pertenece cada correo de alerta.
    alias_correo    VARCHAR(40)  NOT NULL UNIQUE,
    activo          BOOLEAN      NOT NULL DEFAULT TRUE,
    creado_en       TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- 2. Perfiles (uno activo por usuario)
-- ---------------------------------------------------------------------
CREATE TABLE perfiles (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id          UUID NOT NULL UNIQUE REFERENCES usuarios(id) ON DELETE CASCADE,

    -- Insumo
    cv_texto            TEXT,                 -- texto extraido del CV
    cv_archivo          VARCHAR(255),         -- ruta del archivo original

    -- Datos estructurados (los genera la IA, el usuario los edita)
    resumen             TEXT,
    cargos_objetivo     TEXT[]  NOT NULL DEFAULT '{}',
    habilidades         JSONB   NOT NULL DEFAULT '[]',  -- [{"nombre":"PostgreSQL","nivel":"avanzado"}]
    anios_experiencia   NUMERIC(4,1),
    nivel_educativo     VARCHAR(80),
    idiomas             JSONB   NOT NULL DEFAULT '[]',  -- [{"idioma":"ingles","nivel":"B2"}]

    -- Preferencias de busqueda
    ciudades            TEXT[]  NOT NULL DEFAULT '{}',
    modalidad           modalidad_trabajo NOT NULL DEFAULT 'indiferente',
    salario_minimo      INTEGER,              -- COP mensual
    palabras_excluir    TEXT[]  NOT NULL DEFAULT '{}',

    -- Salida completa de la IA (trazabilidad / re-procesamiento)
    perfil_ia           JSONB,
    modelo_ia           VARCHAR(80),          -- ej. groq/llama-3.3-70b
    actualizado_en      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- 3. Fuentes de vacantes
-- ---------------------------------------------------------------------
CREATE TABLE fuentes (
    id                  SMALLSERIAL PRIMARY KEY,
    codigo              VARCHAR(30) NOT NULL UNIQUE,   -- jooble | careerjet | correo
    nombre              VARCHAR(80) NOT NULL,
    tipo                tipo_fuente NOT NULL,
    activa              BOOLEAN     NOT NULL DEFAULT TRUE,
    horario             VARCHAR(40) NOT NULL DEFAULT '0 6 * * *',  -- cron, zona America/Bogota
    ultima_ejecucion    TIMESTAMPTZ,
    config              JSONB       NOT NULL DEFAULT '{}'  -- parametros no sensibles (las llaves van en .env)
);

INSERT INTO fuentes (codigo, nombre, tipo) VALUES
    ('jooble',    'Jooble',             'api'),
    ('careerjet', 'Careerjet',          'api'),
    ('correo',    'Alertas por correo', 'correo');

-- ---------------------------------------------------------------------
-- 4. Busquedas (consultas que el recolector ejecuta por perfil y fuente)
-- ---------------------------------------------------------------------
CREATE TABLE busquedas (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    perfil_id       UUID     NOT NULL REFERENCES perfiles(id) ON DELETE CASCADE,
    fuente_id       SMALLINT NOT NULL REFERENCES fuentes(id),
    palabras_clave  VARCHAR(200) NOT NULL,
    ubicacion       VARCHAR(120),
    generada_por_ia BOOLEAN  NOT NULL DEFAULT TRUE,
    activa          BOOLEAN  NOT NULL DEFAULT TRUE,
    creada_en       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_busquedas_activas ON busquedas (fuente_id) WHERE activa;

-- ---------------------------------------------------------------------
-- 5. Vacantes (compartidas entre usuarios, sin duplicados)
-- ---------------------------------------------------------------------
CREATE TABLE vacantes (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    fuente_id           SMALLINT NOT NULL REFERENCES fuentes(id),
    id_externo          VARCHAR(200),         -- id en la fuente (o Message-ID + indice para correo)
    titulo              VARCHAR(300) NOT NULL,
    empresa             VARCHAR(200),
    ubicacion           VARCHAR(200),
    modalidad           modalidad_trabajo,
    salario_texto       VARCHAR(120),
    salario_min         INTEGER,
    salario_max         INTEGER,
    descripcion         TEXT,
    url                 TEXT NOT NULL,
    fecha_publicacion   TIMESTAMPTZ,
    -- Huella para detectar la misma vacante en varias fuentes:
    -- md5(lower(titulo) || lower(empresa) || lower(ubicacion))
    huella              CHAR(32) NOT NULL,
    datos_originales    JSONB,

    -- Ciclo de vida
    abierta             BOOLEAN     NOT NULL DEFAULT TRUE,
    recolectada_en      TIMESTAMPTZ NOT NULL DEFAULT now(),   -- primera vez que se vio
    ultima_vez_vista    TIMESTAMPTZ NOT NULL DEFAULT now(),   -- ultima vez que aparecio en una fuente
    verificada_en       TIMESTAMPTZ,                          -- ultima comprobacion de que sigue abierta
    procesada_en        TIMESTAMPTZ NOT NULL DEFAULT now(),   -- ultimo procesamiento (base de la regla de 3 dias)
    cerrada_en          TIMESTAMPTZ,
    UNIQUE (fuente_id, id_externo)
);
CREATE INDEX ix_vacantes_huella  ON vacantes (huella);
CREATE INDEX ix_vacantes_fecha   ON vacantes (fecha_publicacion DESC);
CREATE INDEX ix_vacantes_titulo  ON vacantes USING gin (titulo gin_trgm_ops);
CREATE INDEX ix_vacantes_abiertas ON vacantes (procesada_en) WHERE abierta;

-- Regla de reproceso: vacante abierta y procesada hace >= dias_reproceso
CREATE OR REPLACE FUNCTION vacante_reprocesable(p_vacante_id UUID)
RETURNS BOOLEAN LANGUAGE sql STABLE AS $$
    SELECT v.abierta
       AND v.procesada_en <= now() - make_interval(days => (
               SELECT valor::int FROM parametros WHERE clave = 'dias_reproceso'))
      FROM vacantes v
     WHERE v.id = p_vacante_id;
$$;

-- ---------------------------------------------------------------------
-- 6. Coincidencias (vacante evaluada contra el perfil de un usuario)
-- ---------------------------------------------------------------------
CREATE TABLE coincidencias (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id      UUID NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    vacante_id      UUID NOT NULL REFERENCES vacantes(id) ON DELETE CASCADE,
    puntaje         SMALLINT CHECK (puntaje BETWEEN 0 AND 100),  -- NULL = pendiente de evaluar
    explicacion     TEXT,
    fortalezas      TEXT[] NOT NULL DEFAULT '{}',
    brechas         TEXT[] NOT NULL DEFAULT '{}',
    modelo_ia       VARCHAR(80),
    estado          estado_coincidencia NOT NULL DEFAULT 'nueva',
    notas           TEXT,
    veces_mostrada  SMALLINT NOT NULL DEFAULT 1,   -- aumenta en cada reproceso
    creada_en       TIMESTAMPTZ NOT NULL DEFAULT now(),
    evaluada_en     TIMESTAMPTZ,
    reaparecio_en   TIMESTAMPTZ,                   -- ultima vez que volvio al tablero por reproceso
    postulada_en    TIMESTAMPTZ,
    UNIQUE (usuario_id, vacante_id)
);
CREATE INDEX ix_coinc_tablero    ON coincidencias (usuario_id, estado, puntaje DESC);
CREATE INDEX ix_coinc_pendientes ON coincidencias (creada_en) WHERE puntaje IS NULL;

-- ---------------------------------------------------------------------
-- 7. Ejecuciones del recolector (bitacora / monitoreo)
-- ---------------------------------------------------------------------
CREATE TABLE ejecuciones (
    id                  BIGSERIAL PRIMARY KEY,
    fuente_id           SMALLINT NOT NULL REFERENCES fuentes(id),
    inicio              TIMESTAMPTZ NOT NULL DEFAULT now(),
    fin                 TIMESTAMPTZ,
    estado              estado_ejecucion NOT NULL DEFAULT 'en_curso',
    vacantes_leidas     INTEGER NOT NULL DEFAULT 0,
    vacantes_nuevas     INTEGER NOT NULL DEFAULT 0,
    vacantes_reprocesadas INTEGER NOT NULL DEFAULT 0,
    vacantes_cerradas   INTEGER NOT NULL DEFAULT 0,
    error               TEXT
);
CREATE INDEX ix_ejecuciones_fuente ON ejecuciones (fuente_id, inicio DESC);

-- ---------------------------------------------------------------------
-- 8. Correos procesados (bitacora; se permite repetir)
--    Un mismo correo puede procesarse varias veces. Las vacantes que trae
--    solo se reprocesan si vacante_reprocesable() = TRUE.
-- ---------------------------------------------------------------------
CREATE TABLE correos_procesados (
    id                  BIGSERIAL PRIMARY KEY,
    message_id          VARCHAR(300) NOT NULL,
    ejecucion_id        BIGINT REFERENCES ejecuciones(id),
    usuario_id          UUID REFERENCES usuarios(id) ON DELETE SET NULL,
    remitente           VARCHAR(200),         -- portal que envio la alerta
    asunto              VARCHAR(300),
    vacantes_extraidas  INTEGER NOT NULL DEFAULT 0,
    vacantes_nuevas     INTEGER NOT NULL DEFAULT 0,
    vacantes_reprocesadas INTEGER NOT NULL DEFAULT 0,
    procesado_en        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_correos_message ON correos_procesados (message_id, procesado_en DESC);

-- ---------------------------------------------------------------------
-- 9. Uso de IA (control de cuotas gratuitas)
-- ---------------------------------------------------------------------
CREATE TABLE uso_ia (
    id              BIGSERIAL PRIMARY KEY,
    proveedor       VARCHAR(30) NOT NULL,     -- groq | gemini
    modelo          VARCHAR(80) NOT NULL,
    tarea           VARCHAR(40) NOT NULL,     -- perfilar | generar_busquedas | evaluar
    tokens_entrada  INTEGER,
    tokens_salida   INTEGER,
    exito           BOOLEAN NOT NULL,
    error           TEXT,
    creado_en       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_uso_ia_dia ON uso_ia (proveedor, creado_en);
