import { Routes } from '@angular/router';
import { adminGuard } from './admin/auth.service';
import { Demo } from './demo';

export const routes: Routes = [
  { path: '', component: Demo },
  { path: 'admin/connexion', loadComponent: () => import('./admin/login').then((m) => m.Login) },
  {
    path: 'admin',
    canActivate: [adminGuard],
    loadComponent: () => import('./admin/shell').then((m) => m.Shell),
    children: [
      { path: '', pathMatch: 'full', redirectTo: 'documents' },
      { path: 'documents', loadComponent: () => import('./admin/documents').then((m) => m.Documents) },
      { path: 'api', loadComponent: () => import('./admin/apis').then((m) => m.Apis) },
      { path: 'utilisateurs', loadComponent: () => import('./admin/utilisateurs').then((m) => m.Utilisateurs) },
      { path: 'bases', loadComponent: () => import('./admin/bases').then((m) => m.Bases) },
    ],
  },
  { path: '**', redirectTo: '' },
];
