export type Estado = 'nueva' | 'vista' | 'guardada' | 'postulada' | 'descartada';
/** Pestañas del tablero: 'por_aplicar' agrupa nueva, vista y guardada. */
export type Vista = 'por_aplicar' | 'postulada' | 'descartada';
export type Modalidad = 'presencial' | 'remoto' | 'hibrido' | 'indiferente';

export interface Coincidencia {
  id: string;
  puntaje: number | null;
  explicacion: string | null;
  fortalezas: string[];
  brechas: string[];
  estado: Estado;
  notas: string | null;
  veces_mostrada: number;
  creada_en: string;
  reaparecio_en: string | null;
  postulada_en: string | null;
  titulo: string;
  empresa: string | null;
  ubicacion: string | null;
  modalidad: string | null;
  salario_texto: string | null;
  url: string;
  fecha_publicacion: string | null;
  abierta: boolean;
  portal: string | null;
}

export interface Pagina<T> {
  total: number;
  items: T[];
}

export interface Resumen {
  por_estado: Partial<Record<Estado, number>>;
  sin_evaluar: number;
  ultima_actualizacion: string | null;
}

export interface Experiencia {
  cargo: string;
  empresa: string;
  desde: string;
  hasta: string;
  anios: number | null;
  tipo: 'empleo' | 'practica';
  relacionada: boolean;
}

export interface Perfil {
  resumen: string | null;
  carreras: string[];
  cargos_objetivo: string[];
  habilidades: { nombre: string; nivel: string }[];
  experiencias: Experiencia[];
  anios_experiencia: number | null;
  anios_experiencia_area: number | null;
  nivel_educativo: string | null;
  idiomas: { idioma: string; nivel: string }[];
  ciudades: string[];
  modalidad: Modalidad;
  salario_minimo: number | null;
  palabras_excluir: string[];
  cv_archivo: string | null;
  modelo_ia: string | null;
  actualizado_en: string;
  busquedas: { palabras_clave: string; ubicacion: string }[];
}

export interface FiltrosTablero {
  estado?: Vista;
  min_puntaje?: number;
  buscar?: string;
  orden?: 'puntaje' | 'fecha';
}

export interface EstadoFuente {
  codigo: string;
  nombre: string;
  estado: 'en_curso' | 'ok' | 'error' | null;
  fin: string | null;
  vacantes_nuevas: number | null;
  error: string | null;
  configurada: boolean;
}

export interface EstadoActualizacion {
  en_curso: boolean;
  fuentes: EstadoFuente[];
}

export type TipoEmpresa = 'smartrecruiters' | 'successfactors' | 'pagina';

export interface Empresa {
  id: number;
  nombre: string;
  tipo: TipoEmpresa;
  identificador: string;
  pais: string | null;
  activa: boolean;
  solo_para_mi: boolean;
  ultima_revision: string | null;
  ultimo_total: number | null;
  ultimo_error: string | null;
}

export interface NuevaEmpresa {
  nombre: string;
  tipo: TipoEmpresa;
  identificador: string;
  pais: string | null;
  solo_para_mi: boolean;
}

export interface PruebaEmpresa {
  total: number;
  error: string | null;
  vacantes: { titulo: string; url: string; ubicacion: string | null }[];
}
