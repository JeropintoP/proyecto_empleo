import { HttpClient, HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { Injectable, computed, inject, signal } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { catchError, firstValueFrom, throwError } from 'rxjs';

const CLAVE = 'pe_sesion';

interface DatosSesion {
  token: string;
  nombre: string;
}

function leerGuardada(): DatosSesion | null {
  try {
    const crudo = localStorage.getItem(CLAVE);
    return crudo ? (JSON.parse(crudo) as DatosSesion) : null;
  } catch {
    return null;
  }
}

@Injectable({ providedIn: 'root' })
export class Sesion {
  private readonly http = inject(HttpClient);
  private readonly router = inject(Router);
  private readonly datos = signal<DatosSesion | null>(leerGuardada());

  readonly activa = computed(() => this.datos() !== null);
  readonly nombre = computed(() => this.datos()?.nombre ?? '');
  readonly token = computed(() => this.datos()?.token ?? null);

  async iniciar(email: string, clave: string): Promise<void> {
    const r = await firstValueFrom(this.http.post<DatosSesion>('/api/auth/login', { email, clave }));
    this.datos.set(r);
    try {
      localStorage.setItem(CLAVE, JSON.stringify(r));
    } catch {
      /* almacenamiento no disponible: la sesion dura mientras la pestana este abierta */
    }
  }

  cerrar(): void {
    this.datos.set(null);
    try {
      localStorage.removeItem(CLAVE);
    } catch {
      /* nada */
    }
    this.router.navigateByUrl('/login');
  }
}

export const interceptorSesion: HttpInterceptorFn = (req, next) => {
  const sesion = inject(Sesion);
  const token = sesion.token();
  const conToken = token ? req.clone({ setHeaders: { Authorization: `Bearer ${token}` } }) : req;
  return next(conToken).pipe(
    catchError((err: HttpErrorResponse) => {
      if (err.status === 401 && !req.url.endsWith('/auth/login')) sesion.cerrar();
      return throwError(() => err);
    }),
  );
};

export const requiereSesion: CanActivateFn = () => {
  const sesion = inject(Sesion);
  return sesion.activa() ? true : inject(Router).parseUrl('/login');
};

export function mensajeError(err: unknown): string {
  if (err instanceof HttpErrorResponse) {
    if (err.status === 0) return 'No hay conexion con el servidor.';
    const detalle = err.error?.detail;
    if (typeof detalle === 'string') return detalle;
  }
  return 'Ocurrio un error inesperado.';
}
