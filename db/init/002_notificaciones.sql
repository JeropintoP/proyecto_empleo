-- =====================================================================
-- 002 - Resumen diario por correo
-- Idempotente: se puede ejecutar sobre una base existente.
--   docker compose exec -T db psql -U empleo -d proyecto_empleo < db/init/002_notificaciones.sql
-- =====================================================================

ALTER TABLE usuarios      ADD COLUMN IF NOT EXISTS notificar BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE coincidencias ADD COLUMN IF NOT EXISTS notificada_en TIMESTAMPTZ;

INSERT INTO parametros (clave, valor, descripcion) VALUES
    ('umbral_notificacion', '60', 'Puntaje minimo para incluir una vacante en el resumen diario'),
    ('maximo_notificacion', '10', 'Vacantes maximas por resumen')
ON CONFLICT (clave) DO NOTHING;

CREATE TABLE IF NOT EXISTS notificaciones (
    id          BIGSERIAL PRIMARY KEY,
    usuario_id  UUID NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    vacantes    INTEGER NOT NULL,
    exito       BOOLEAN NOT NULL,
    error       TEXT,
    enviada_en  TIMESTAMPTZ NOT NULL DEFAULT now()
);
