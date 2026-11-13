export type Estado = 'nueva' | 'vista' | 'guardada' | 'postulada' | 'descartada';
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

export interface Perfil {
  resumen: string | null;
  cargos_objetivo: string[];
  habilidades: { nombre: string; nivel: string }[];
  anios_experiencia: number | null;
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
  estado?: Estado | '';
  min_puntaje?: number;
  buscar?: string;
  orden?: 'puntaje' | 'fecha';
}
