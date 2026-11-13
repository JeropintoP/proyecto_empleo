import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { Coincidencia, Estado, FiltrosTablero, Pagina, Perfil, Resumen } from './modelos';

@Injectable({ providedIn: 'root' })
export class Api {
  private readonly http = inject(HttpClient);

  resumen(): Observable<Resumen> {
    return this.http.get<Resumen>('/api/resumen');
  }

  coincidencias(f: FiltrosTablero, limite = 50, desplazamiento = 0): Observable<Pagina<Coincidencia>> {
    let params = new HttpParams().set('limite', limite).set('desplazamiento', desplazamiento);
    if (f.estado) params = params.set('estado', f.estado);
    if (f.min_puntaje) params = params.set('min_puntaje', f.min_puntaje);
    if (f.buscar?.trim()) params = params.set('buscar', f.buscar.trim());
    if (f.orden) params = params.set('orden', f.orden);
    return this.http.get<Pagina<Coincidencia>>('/api/coincidencias', { params });
  }

  cambiar(id: string, cambio: { estado?: Estado; notas?: string }): Observable<{ estado: Estado; notas: string | null }> {
    return this.http.patch<{ estado: Estado; notas: string | null }>(`/api/coincidencias/${id}`, cambio);
  }

  perfil(): Observable<Perfil> {
    return this.http.get<Perfil>('/api/perfil');
  }

  guardarPerfil(datos: Partial<Perfil>): Observable<Perfil> {
    return this.http.put<Perfil>('/api/perfil', datos);
  }

  subirCv(archivo: File): Observable<Perfil> {
    const form = new FormData();
    form.append('archivo', archivo);
    return this.http.post<Perfil>('/api/perfil/cv', form);
  }
}
