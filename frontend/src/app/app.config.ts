import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { ApplicationConfig, LOCALE_ID, provideBrowserGlobalErrorListeners } from '@angular/core';
import { registerLocaleData } from '@angular/common';
import localeEsCo from '@angular/common/locales/es-CO';
import { provideRouter, withHashLocation } from '@angular/router';

import { routes } from './app.routes';
import { interceptorDemo } from './nucleo/demo';
import { interceptorSesion } from './nucleo/sesion';

registerLocaleData(localeEsCo);

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    // En la demo la app vive en una sola pagina estatica: rutas con # y datos de ejemplo
    DEMO ? provideRouter(routes, withHashLocation()) : provideRouter(routes),
    provideHttpClient(withInterceptors(DEMO ? [interceptorSesion, interceptorDemo] : [interceptorSesion])),
    { provide: LOCALE_ID, useValue: 'es-CO' },
  ],
};
