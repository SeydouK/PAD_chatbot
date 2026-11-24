import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { Injectable, computed, inject, signal } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { catchError, throwError } from 'rxjs';
import { ADMIN_API } from '../config';

const CLE = 'pad-admin-jeton';

function lire(): string {
  try {
    return sessionStorage.getItem(CLE) ?? '';
  } catch {
    return '';
  }
}

/** Jeton de connexion gardé le temps de l'onglet (sessionStorage), jamais en localStorage. */
@Injectable({ providedIn: 'root' })
export class AuthService {
  readonly jeton = signal(lire());
  readonly nom = signal('');
  readonly connecte = computed(() => !!this.jeton());

  ouvrir(jeton: string, nom: string): void {
    this.jeton.set(jeton);
    this.nom.set(nom);
    try {
      sessionStorage.setItem(CLE, jeton);
    } catch {
      /* stockage indisponible : la session ne survivra pas à un rechargement */
    }
  }

  fermer(): void {
    this.jeton.set('');
    this.nom.set('');
    try {
      sessionStorage.removeItem(CLE);
    } catch {
      /* rien */
    }
  }
}

export const adminGuard: CanActivateFn = () => {
  const auth = inject(AuthService);
  return auth.connecte() ? true : inject(Router).createUrlTree(['/admin/connexion']);
};

export const authInterceptor: HttpInterceptorFn = (req, next) => {
  if (!req.url.startsWith(ADMIN_API) || req.url.startsWith(`${ADMIN_API}/chat/`)) return next(req); // /chat/* = connexion des utilisateurs du chat, sans rapport avec l'admin
  const auth = inject(AuthService);
  const router = inject(Router);
  const avec = auth.jeton() ? req.clone({ setHeaders: { Authorization: `Bearer ${auth.jeton()}` } }) : req;
  return next(avec).pipe(
    catchError((e: HttpErrorResponse) => {
      if (e.status === 401 && !req.url.endsWith('/login')) {
        auth.fermer(); // jeton expiré ou refusé
        void router.navigate(['/admin/connexion']);
      }
      return throwError(() => e);
    }),
  );
};
