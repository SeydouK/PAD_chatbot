import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { AdminApi, ExportDef, Source, SourceForm, messageErreur } from './admin-api';

const PORTS: Record<string, number> = { postgresql: 5432, mysql: 3306, mssql: 1433 };

@Component({
  selector: 'app-bases',
  imports: [FormsModule],
  styleUrl: './admin.scss',
  template: `
    <h1>Bases de données</h1>
    <p class="muted">
      Connexions utilisées par le chatbot. Les mots de passe sont chiffrés et ne sont jamais réaffichés.
      Utilisez de préférence un compte en <strong>lecture seule</strong>.
    </p>

    @if (info()) {
      <p class="msg" [class.ok]="!infoErreur()" [class.err]="infoErreur()" [attr.role]="infoErreur() ? 'alert' : 'status'">{{ info() }}</p>
    }

    @for (s of sources(); track s.id) {
      <section class="card" [attr.aria-label]="'Connexion ' + s.nom">
        <div class="row">
          <h2 style="margin:0">{{ s.nom }}</h2>
          <span class="badge">{{ s.dialecte }}</span>
          @if (s.role === 'principale') {
            <span class="badge main">base principale du bot</span>
          }
          <span class="muted" style="margin:0">{{ resume(s) }}</span>
        </div>
        <div class="row" style="margin-top:10px">
          <button type="button" class="sec" (click)="modifier(s)">Modifier</button>
          <button type="button" class="sec" (click)="ouvrirExports(s)">Tables à exporter ({{ s.exports.length }})</button>
          <button type="button" class="danger" (click)="supprimer(s)">Supprimer</button>
        </div>

        @if (ouverte() === s.id) {
          <div style="margin-top:14px">
            <h2>Textes exportés vers le chatbot</h2>
            <p class="warn">
              Tout ce qui est exporté devient consultable par n'importe quel visiteur du chatbot.
              N'exportez pas de données personnelles (comptes, rendez-vous, emails).
            </p>
            @if (defs().length) {
              <table>
                <thead><tr><th>Table</th><th>Colonnes</th><th>Filtre</th><th></th></tr></thead>
                <tbody>
                  @for (d of defs(); track $index) {
                    <tr>
                      <td>{{ d.table }}</td>
                      <td>{{ d.colonnes.join(', ') }}</td>
                      <td>{{ d.filtre ? d.filtre.colonne + ' = ' + d.filtre.valeur : '—' }}</td>
                      <td><button type="button" class="danger" (click)="retirer($index)">Retirer</button></td>
                    </tr>
                  }
                </tbody>
              </table>
            }

            <h2 style="margin-top:14px">Ajouter une table</h2>
            <div class="grid">
              <label>Table
                <select [ngModel]="nv.table" (ngModelChange)="choisirTable($event)" name="table">
                  <option value="">— choisir —</option>
                  @for (t of nomsTables(); track t) {
                    <option [value]="t">{{ t }}</option>
                  }
                </select>
              </label>
              <label>Colonne identifiant
                <select [(ngModel)]="nv.id" name="id">
                  @for (c of colonnes(); track c) { <option [value]="c">{{ c }}</option> }
                </select>
              </label>
              <label>Colonne titre (facultatif)
                <select [(ngModel)]="nv.titre" name="titre">
                  <option value="">—</option>
                  @for (c of colonnes(); track c) { <option [value]="c">{{ c }}</option> }
                </select>
              </label>
            </div>
            @if (colonnes().length) {
              <p class="muted" style="margin-bottom:4px">Colonnes de texte à exporter :</p>
              <div class="cols">
                @for (c of colonnes(); track c) {
                  <label class="check">
                    <input type="checkbox" [checked]="nv.colonnes.includes(c)" (change)="basculer(c)" />{{ c }}
                  </label>
                }
              </div>
              <div class="grid">
                <label>Filtre : colonne (facultatif)
                  <select [(ngModel)]="nv.filtreCol" name="fcol">
                    <option value="">—</option>
                    @for (c of colonnes(); track c) { <option [value]="c">{{ c }}</option> }
                  </select>
                </label>
                <label>Filtre : valeur
                  <input [(ngModel)]="nv.filtreVal" name="fval" placeholder="ex. publie" />
                </label>
                <label>Lignes maximum
                  <input type="number" [(ngModel)]="nv.max" name="max" min="1" max="5000" />
                </label>
              </div>
            }
            <div class="row">
              <button type="button" class="sec" (click)="ajouterDef()" [disabled]="!nv.table">Ajouter à la liste</button>
              <button type="button" (click)="enregistrerDefs(s)">Enregistrer la liste</button>
              <button type="button" (click)="lancer(s)" [disabled]="!defs().length">Enregistrer et lancer l'export</button>
            </div>
            @if (bilan()) {
              <p class="msg" [class.ok]="!bilanErreur()" [class.err]="bilanErreur()" role="status">{{ bilan() }}</p>
            }
          </div>
        }
      </section>
    } @empty {
      <p class="muted">Aucune connexion enregistrée.</p>
    }

    @if (formulaire(); as f) {
      <section class="card" aria-label="Formulaire de connexion">
        <h2>{{ f.id ? 'Modifier la connexion' : 'Nouvelle connexion' }}</h2>
        <div class="grid">
          <label>Nom
            <input [(ngModel)]="f.nom" name="nom" placeholder="ex. portail" />
          </label>
          <label>Type de base
            <select [(ngModel)]="f.dialecte" name="dialecte" (ngModelChange)="changerType($event)">
              <option value="postgresql">PostgreSQL</option>
              <option value="mysql">MySQL</option>
              <option value="mssql">SQL Server</option>
              <option value="sqlite">SQLite (fichier)</option>
            </select>
          </label>
          @if (f.dialecte !== 'sqlite') {
            <label>Hôte
              <input [(ngModel)]="f.hote" name="hote" placeholder="localhost" />
            </label>
            <label>Port
              <input type="number" [(ngModel)]="f.port" name="port" />
            </label>
          }
          <label>{{ f.dialecte === 'sqlite' ? 'Chemin du fichier' : 'Nom de la base' }}
            <input [(ngModel)]="f.base" name="base" />
          </label>
          @if (f.dialecte !== 'sqlite') {
            <label>Utilisateur
              <input [(ngModel)]="f.utilisateur" name="utilisateur" autocomplete="off" />
            </label>
            <label>Mot de passe
              <input type="password" [(ngModel)]="f.mot_de_passe" name="mdp" autocomplete="new-password"
                [placeholder]="f.id ? 'laisser vide pour conserver' : ''" />
            </label>
          }
        </div>
        <label class="check">
          <input type="checkbox" [checked]="f.role === 'principale'" (change)="f.role = f.role === 'principale' ? 'export' : 'principale'" />
          Utiliser comme base principale du chatbot (suivi des navires, rendez-vous)
        </label>
        @if (f.role === 'principale') {
          <p class="warn">
            Les tables « navires » et « rendez_vous » seront créées dans cette base si elles n'existent pas. Ce compte
            doit pouvoir écrire. La variable d'environnement DATABASE_URL, si elle est définie, reste prioritaire.
          </p>
        }
        <div class="row">
          <button type="button" class="sec" (click)="tester(f)" [disabled]="enCours()">Tester la connexion</button>
          <button type="button" (click)="enregistrer(f)" [disabled]="enCours()">Enregistrer</button>
          <button type="button" class="sec" (click)="formulaire.set(null)">Annuler</button>
        </div>
        @if (resultatTest(); as r) {
          <p class="msg" [class.ok]="r.ok" [class.err]="!r.ok" role="status">
            {{ r.ok ? 'Connexion réussie · ' + (r.tables?.length ?? 0) + ' table(s) trouvée(s).' : 'Échec : ' + r.message }}
          </p>
        }
      </section>
    } @else {
      <button type="button" (click)="nouveau()">Ajouter une connexion</button>
    }
  `,
})
export class Bases implements OnInit {
  private readonly api = inject(AdminApi);
  protected readonly sources = signal<Source[]>([]);
  protected readonly formulaire = signal<SourceForm | null>(null);
  protected readonly resultatTest = signal<{ ok: boolean; message?: string; tables?: string[] } | null>(null);
  protected readonly info = signal('');
  protected readonly infoErreur = signal(false);
  protected readonly enCours = signal(false);

  protected readonly ouverte = signal<number | null>(null);
  protected readonly defs = signal<ExportDef[]>([]);
  protected readonly tablesDispo = signal<Record<string, string[]>>({});
  protected readonly bilan = signal('');
  protected readonly bilanErreur = signal(false);
  protected nv = vide();

  protected nomsTables = () => Object.keys(this.tablesDispo());
  protected colonnes = () => this.tablesDispo()[this.nv.table] ?? [];

  ngOnInit(): void {
    void this.recharger();
  }

  private async recharger(): Promise<void> {
    try {
      this.sources.set(await this.api.sources());
    } catch (e) {
      this.dire(messageErreur(e), true);
    }
  }

  private dire(m: string, erreur: boolean): void {
    this.info.set(m);
    this.infoErreur.set(erreur);
  }

  protected resume = (s: Source) =>
    s.dialecte === 'sqlite' ? s.base : `${s.utilisateur ?? ''}@${s.hote ?? ''}:${s.port ?? ''}/${s.base}`;

  // ----- formulaire de connexion -----
  protected nouveau(): void {
    this.resultatTest.set(null);
    this.formulaire.set({ dialecte: 'postgresql', hote: 'localhost', port: 5432, role: 'export', mot_de_passe: '' });
  }

  protected modifier(s: Source): void {
    this.resultatTest.set(null);
    this.formulaire.set({
      id: s.id, nom: s.nom, dialecte: s.dialecte, hote: s.hote ?? '', port: s.port ?? undefined,
      base: s.base, utilisateur: s.utilisateur ?? '', role: s.role, mot_de_passe: '',
    });
  }

  protected changerType(d: string): void {
    const f = this.formulaire();
    if (f) f.port = PORTS[d];
  }

  protected async tester(f: SourceForm): Promise<void> {
    this.enCours.set(true);
    try {
      this.resultatTest.set(await this.api.tester(f));
    } catch (e) {
      this.resultatTest.set({ ok: false, message: messageErreur(e) });
    } finally {
      this.enCours.set(false);
    }
  }

  protected async enregistrer(f: SourceForm): Promise<void> {
    this.enCours.set(true);
    try {
      await this.api.enregistrerSource(f);
      this.formulaire.set(null);
      this.dire('Connexion enregistrée.', false);
      await this.recharger();
    } catch (e) {
      this.resultatTest.set({ ok: false, message: messageErreur(e) });
    } finally {
      this.enCours.set(false);
    }
  }

  protected async supprimer(s: Source): Promise<void> {
    if (!confirm(`Supprimer la connexion « ${s.nom} » ?`)) return;
    try {
      await this.api.supprimerSource(s.id);
      if (this.ouverte() === s.id) this.ouverte.set(null);
      this.dire('Connexion supprimée.', false);
      await this.recharger();
    } catch (e) {
      this.dire(messageErreur(e), true);
    }
  }

  // ----- exports de tables vers le RAG -----
  protected async ouvrirExports(s: Source): Promise<void> {
    if (this.ouverte() === s.id) {
      this.ouverte.set(null);
      return;
    }
    this.bilan.set('');
    this.nv = vide();
    this.defs.set(structuredClone(s.exports));
    this.tablesDispo.set({});
    this.ouverte.set(s.id);
    try {
      this.tablesDispo.set(await this.api.tables(s.id));
    } catch (e) {
      this.bilan.set(messageErreur(e));
      this.bilanErreur.set(true);
    }
  }

  protected choisirTable(t: string): void {
    this.nv = { ...vide(), table: t };
    const cols = this.tablesDispo()[t] ?? [];
    this.nv.id = cols.includes('id') ? 'id' : (cols[0] ?? '');
  }

  protected basculer(c: string): void {
    this.nv.colonnes = this.nv.colonnes.includes(c) ? this.nv.colonnes.filter((x) => x !== c) : [...this.nv.colonnes, c];
  }

  protected ajouterDef(): void {
    const n = this.nv;
    if (!n.table || !n.id || !n.colonnes.length) {
      this.bilan.set('Choisissez une table, une colonne identifiant et au moins une colonne de texte.');
      this.bilanErreur.set(true);
      return;
    }
    const d: ExportDef = { table: n.table, id: n.id, colonnes: n.colonnes, max_lignes: Number(n.max) || 500 };
    if (n.titre) d.titre = n.titre;
    if (n.filtreCol) d.filtre = { colonne: n.filtreCol, valeur: n.filtreVal };
    this.defs.update((l) => [...l.filter((x) => x.table !== d.table), d]);
    this.nv = vide();
    this.bilan.set('');
  }

  protected retirer(i: number): void {
    this.defs.update((l) => l.filter((_, k) => k !== i));
  }

  protected async enregistrerDefs(s: Source): Promise<boolean> {
    try {
      await this.api.definirExports(s.id, this.defs());
      this.bilan.set('Liste enregistrée.');
      this.bilanErreur.set(false);
      await this.recharger();
      return true;
    } catch (e) {
      this.bilan.set(messageErreur(e));
      this.bilanErreur.set(true);
      return false;
    }
  }

  protected async lancer(s: Source): Promise<void> {
    if (!(await this.enregistrerDefs(s))) return;
    this.bilan.set('Export en cours…');
    this.bilanErreur.set(false);
    try {
      const r = await this.api.lancerExports(s.id);
      const lignes = Object.entries(r).map(([k, v]) => `${k.split('__')[1] ?? k} : ${typeof v === 'number' ? v + ' document(s)' : v}`);
      this.bilan.set(lignes.join(' · ') || 'Rien à exporter.');
      this.bilanErreur.set(Object.values(r).some((v) => typeof v === 'string'));
    } catch (e) {
      this.bilan.set(messageErreur(e));
      this.bilanErreur.set(true);
    }
  }
}

function vide() {
  return { table: '', id: '', titre: '', colonnes: [] as string[], filtreCol: '', filtreVal: '', max: 500 };
}
