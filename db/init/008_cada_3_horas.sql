-- =====================================================================
-- 008 - Busqueda de vacantes cada 3 horas
-- Idempotente:
--   docker compose exec -T db psql -U empleo -d proyecto_empleo < db/init/008_cada_3_horas.sql
--
-- La corrida de las 6 a.m. (horario_actualizacion) sigue siendo la "completa": verifica vacantes
-- cerradas y envia el resumen por correo. Las demas solo buscan y evaluan.
-- Los cupos consultas_diarias_<fuente> y evaluaciones_diarias son por DIA: se reparten entre corridas.
-- =====================================================================

INSERT INTO parametros (clave, valor, descripcion) VALUES
    ('horario_busqueda', '0 */3 * * *', 'Cron de la busqueda de vacantes (cada 3 horas). Debe incluir la hora de horario_actualizacion'),
    ('evaluaciones_diarias', '60', 'Maximo de vacantes que la IA evalua en 24 horas (cuota gratuita)')
ON CONFLICT (clave) DO NOTHING;

-- Mas consultas al dia, repartidas en 8 corridas (solo si siguen con el valor anterior)
UPDATE parametros SET valor = '16' WHERE clave = 'consultas_diarias_jooble'    AND valor = '4';
UPDATE parametros SET valor = '24' WHERE clave = 'consultas_diarias_careerjet' AND valor = '8';
UPDATE parametros SET valor = '24' WHERE clave = 'consultas_diarias_getonbrd'  AND valor = '6';
UPDATE parametros SET valor = '16' WHERE clave = 'consultas_diarias_himalayas' AND valor = '4';
