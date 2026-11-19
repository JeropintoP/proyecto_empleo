-- =====================================================================
-- 006 - Monitor de empresas: revisa a diario la pagina de empleo de empresas puntuales
-- Idempotente:
--   docker compose exec -T db psql -U empleo -d proyecto_empleo < db/init/006_empresas.sql
--
-- Tipos de empresa:
--   smartrecruiters : API publica de SmartRecruiters (identificador = id de la empresa, p. ej. konecranes)
--   successfactors  : sitio de empleo de SAP SuccessFactors (identificador = URL .../career?company=XXX)
--   pagina          : cualquier pagina publica; se toman los enlaces que parecen vacantes
-- =====================================================================

DO $$ BEGIN
    CREATE TYPE tipo_empresa AS ENUM ('smartrecruiters', 'successfactors', 'pagina');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS empresas (
    id              SERIAL PRIMARY KEY,
    nombre          VARCHAR(120) NOT NULL,
    tipo            tipo_empresa NOT NULL,
    identificador   VARCHAR(500) NOT NULL,
    pais            CHAR(2),                 -- filtro de pais (SmartRecruiters); NULL = todos
    usuario_id      UUID REFERENCES usuarios(id) ON DELETE CASCADE,   -- NULL = para todos los usuarios
    activa          BOOLEAN     NOT NULL DEFAULT TRUE,
    ultima_revision TIMESTAMPTZ,
    ultimo_total    INTEGER,                 -- vacantes encontradas en la ultima revision
    ultimo_error    TEXT,
    creada_en       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tipo, identificador)
);

INSERT INTO fuentes (codigo, nombre, tipo) VALUES ('empresas', 'Páginas de empresas', 'api')
ON CONFLICT (codigo) DO NOTHING;

-- Empresas iniciales (el sector naval de Cartagena y afines). Se pueden pausar o borrar desde la app.
INSERT INTO empresas (nombre, tipo, identificador, pais) VALUES
    ('Konecranes', 'smartrecruiters', 'konecranes', 'co'),
    ('Cotecmar',   'successfactors',  'https://career17.sapsf.com/career?company=cotecmarcoP', NULL),
    ('Astivik',    'pagina',          'https://astivik.buk.co/trabaja-con-nosotros', NULL),
    ('Dimar',      'pagina',          'https://www.dimar.mil.co/ofertas-de-empleo', NULL)
ON CONFLICT (tipo, identificador) DO NOTHING;
