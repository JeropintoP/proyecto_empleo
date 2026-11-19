import { Component, computed, inject } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

import { Sesion } from './nucleo/sesion';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  templateUrl: './app.html',
  styleUrl: './app.css',
})
export class App {
  protected readonly sesion = inject(Sesion);
  protected readonly demo = DEMO;
  protected readonly iniciales = computed(() =>
    this.sesion.nombre().split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0].toUpperCase()).join(''),
  );
}
