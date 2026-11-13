import { Routes } from '@angular/router';

import { requiereSesion } from './nucleo/sesion';

export const routes: Routes = [
  { path: 'login', loadComponent: () => import('./paginas/login/login').then((m) => m.Login) },
  {
    path: '',
    canActivate: [requiereSesion],
    children: [
      { path: '', loadComponent: () => import('./paginas/tablero/tablero').then((m) => m.Tablero) },
      { path: 'perfil', loadComponent: () => import('./paginas/perfil/perfil').then((m) => m.Perfil) },
    ],
  },
  { path: '**', redirectTo: '' },
];
