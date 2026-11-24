import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { firstValueFrom } from 'rxjs';
import { ADMIN_API } from '../config';

export interface Doc {
  nom: string;
  origine: string;
  taille: number;
  modifie: string;
  supprimable: boolean;
  groupes: number[];
}

export interface Groupe {
  id: number;
  nom: string;
  description: string;
  nb_membres: number;
  nb_documents: number;
}

export interface Utilisateur {
  nom: string;
  actif: boolean;
  cree_le: string;
  groupes: number[];
}

export interface ExportDef {
  table: string;
  id: string;
  titre?: string;
  colonnes: string[];
  filtre?: { colonne: string; valeur: string };
  max_lignes?: number;
  libelle?: string;
}

export interface Source {
  id: number;
  nom: string;
  dialecte: 'postgresql' | 'mysql' | 'mssql' | 'sqlite';
  hote: string | null;
  port: number | null;
  base: string;
  utilisateur: string | null;
  a_un_mdp: boolean;
  role: 'export' | 'principale';
  exports: ExportDef[];
}

export type SourceForm = Partial<Omit<Source, 'a_un_mdp' | 'exports' | 'id'>> & { mot_de_passe?: string; id?: number };

export interface EtatSite {
  en_cours: boolean;
  debut: number | null;
  fin: number | null;
  code: number | null;
  url: string | null;
  pages: number;
  journal: string[];
}

export interface ReglagesSite {
  url: string;
  max_pages: number;
  insecure: boolean;
}

export interface ApiConfig {
  chemin_liste: string;
  pagination: { type: 'aucune' | 'page' | 'suivant'; param?: string; debut?: number; champ_suivant?: string };
  max_elements: number;
  libelle: string;
  champ_id: string;
  champ_titre: string;
  champs_texte: string[];
}

export interface ApiSource {
  id: number;
  nom: string;
  url: string;
  entete: string;
  a_une_cle: boolean;
  config: ApiConfig;
}

export interface ApiForm {
  id?: number;
  nom: string;
  url: string;
  entete: string;
  cle: string;
  config: ApiConfig;
}

export interface ChampApi {
  nom: string;
  exemple: string;
}

export function messageErreur(e: unknown): string {
  const err = e as { error?: { erreur?: string }; status?: number };
  if (err?.error?.erreur) return err.error.erreur;
  if (err?.status === 0) return "Le serveur d'administration ne répond pas.";
  return 'Une erreur est survenue.';
}

@Injectable({ providedIn: 'root' })
export class AdminApi {
  private readonly http = inject(HttpClient);
  private readonly u = (p: string) => `${ADMIN_API}${p}`;

  login(nom: string, mot_de_passe: string) {
    return firstValueFrom(this.http.post<{ jeton: string; nom: string }>(this.u('/login'), { nom, mot_de_passe }));
  }
  documents() {
    return firstValueFrom(this.http.get<Doc[]>(this.u('/documents')));
  }
  televerser(fichier: File) {
    const f = new FormData();
    f.append('fichier', fichier);
    return firstValueFrom(this.http.post<{ nom: string; remplace: boolean; mots: number }>(this.u('/documents'), f));
  }
  supprimerDocument(nom: string) {
    return firstValueFrom(this.http.delete(this.u(`/documents/${nom}`)));
  }
  groupesDuDocument(nom: string, groupes: number[]) {
    return firstValueFrom(this.http.put<{ groupes: number[] }>(this.u(`/documents/${nom}/groupes`), { groupes }));
  }
  groupes() {
    return firstValueFrom(this.http.get<Groupe[]>(this.u('/groupes')));
  }
  enregistrerGroupe(g: { id?: number; nom: string; description: string }) {
    return firstValueFrom(g.id ? this.http.put(this.u(`/groupes/${g.id}`), g) : this.http.post(this.u('/groupes'), g));
  }
  supprimerGroupe(id: number) {
    return firstValueFrom(this.http.delete(this.u(`/groupes/${id}`)));
  }
  utilisateurs() {
    return firstValueFrom(this.http.get<Utilisateur[]>(this.u('/utilisateurs')));
  }
  creerUtilisateur(nom: string, mot_de_passe: string, groupes: number[]) {
    return firstValueFrom(this.http.post(this.u('/utilisateurs'), { nom, mot_de_passe, groupes }));
  }
  modifierUtilisateur(nom: string, d: { mot_de_passe?: string; actif?: boolean; groupes?: number[] }) {
    return firstValueFrom(this.http.put(this.u(`/utilisateurs/${encodeURIComponent(nom)}`), d));
  }
  supprimerUtilisateur(nom: string) {
    return firstValueFrom(this.http.delete(this.u(`/utilisateurs/${encodeURIComponent(nom)}`)));
  }
  sources() {
    return firstValueFrom(this.http.get<Source[]>(this.u('/sources')));
  }
  enregistrerSource(d: SourceForm) {
    return firstValueFrom(
      d.id ? this.http.put<{ id: number }>(this.u(`/sources/${d.id}`), d) : this.http.post<{ id: number }>(this.u('/sources'), d),
    );
  }
  supprimerSource(id: number) {
    return firstValueFrom(this.http.delete(this.u(`/sources/${id}`)));
  }
  tester(d: SourceForm) {
    return firstValueFrom(this.http.post<{ ok: boolean; message?: string; tables?: string[] }>(this.u('/sources/tester'), d));
  }
  tables(id: number) {
    return firstValueFrom(this.http.get<Record<string, string[]>>(this.u(`/sources/${id}/tables`)));
  }
  definirExports(id: number, exports: ExportDef[]) {
    return firstValueFrom(this.http.put(this.u(`/sources/${id}/exports`), { exports }));
  }
  lancerExports(source?: number) {
    return firstValueFrom(this.http.post<Record<string, number | string>>(this.u('/exports/lancer'), { source }));
  }
  reglagesSite() {
    return firstValueFrom(this.http.get<ReglagesSite>(this.u('/site/reglages')));
  }
  etatSite() {
    return firstValueFrom(this.http.get<EtatSite>(this.u('/site/etat')));
  }
  lancerSite(r: ReglagesSite) {
    return firstValueFrom(this.http.post<EtatSite>(this.u('/site/lancer'), r));
  }
  arreterSite() {
    return firstValueFrom(this.http.post<EtatSite>(this.u('/site/arreter'), {}));
  }
  apis() {
    return firstValueFrom(this.http.get<ApiSource[]>(this.u('/apis')));
  }
  enregistrerApi(f: ApiForm) {
    return firstValueFrom(f.id ? this.http.put<{ id: number }>(this.u(`/apis/${f.id}`), f) : this.http.post<{ id: number }>(this.u('/apis'), f));
  }
  supprimerApi(id: number) {
    return firstValueFrom(this.http.delete(this.u(`/apis/${id}`)));
  }
  testerApi(f: ApiForm) {
    return firstValueFrom(
      this.http.post<{ ok: boolean; message?: string; nombre?: number; champs?: ChampApi[] }>(this.u('/apis/tester'), f),
    );
  }
  importerApi(id: number) {
    return firstValueFrom(this.http.post<Record<string, number | string>>(this.u(`/apis/${id}/importer`), {}));
  }
}
