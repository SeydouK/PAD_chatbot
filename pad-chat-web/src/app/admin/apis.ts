import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { AdminApi, ApiConfig, ApiForm, ApiSource, ChampApi, messageErreur } from './admin-api';

function configVide(): ApiConfig {
  return {
    chemin_liste: '', pagination: { type: 'aucune', param: 'page', debut: 1, champ_suivant: 'next' },
    max_elements: 500, libelle: '', champ_id: '', champ_titre: '', champs_texte: [],
  };
}

@Component({
  selector: 'app-apis',
  imports: [FormsModule],
  styleUrl: './admin.scss',
  template: `
    <h1>API</h1>
    <p class="muted">
      Importe les textes d'une API externe (JSON) pour que le chatbot puisse y répondre. La clé d'accès est chiffrée,
      n'est envoyée qu'à l'adresse indiquée et n'est jamais réaffichée.
    </p>

    @if (info()) {
      <p class="msg" [class.ok]="!infoErreur()" [class.err]="infoErreur()" [attr.role]="infoErreur() ? 'alert' : 'status'">{{ info() }}</p>
    }

    @for (s of sources(); track s.id) {
      <section class="card" [attr.aria-label]="'API ' + s.nom">
        <div class="row">
          <h2 style="margin:0">{{ s.nom }}</h2>
          @if (s.a_une_cle) {
            <span class="badge">clé enregistrée</span>
          }
          <span class="muted" style="margin:0">{{ s.url }}</span>
        </div>
        <p class="muted" style="margin:6px 0">
          Texte : {{ s.config.champs_texte.join(', ') || 'à définir' }}
        </p>
        <div class="row">
          <button type="button" (click)="importer(s)" [disabled]="enCours() || !s.config.champs_texte.length">Importer maintenant</button>
          <button type="button" class="sec" (click)="modifier(s)">Modifier</button>
          <button type="button" class="danger" (click)="supprimer(s)">Supprimer</button>
        </div>
      </section>
    } @empty {
      <p class="muted">Aucune API configurée.</p>
    }

    @if (formulaire(); as f) {
      <section class="card" aria-label="Formulaire d'API">
        <h2>{{ f.id ? "Modifier l'API" : 'Nouvelle API' }}</h2>
        <div class="grid">
          <label>Nom
            <input [(ngModel)]="f.nom" name="nom" placeholder="ex. actualites_api" />
          </label>
          <label>Adresse (URL)
            <input [(ngModel)]="f.url" name="url" placeholder="https://api.exemple.sn/articles" />
          </label>
          <label>Nom de l'en-tête de la clé
            <input [(ngModel)]="f.entete" name="entete" placeholder="X-API-Key ou Authorization" autocomplete="off" />
          </label>
          <label>Valeur de la clé
            <input type="password" [(ngModel)]="f.cle" name="cle" autocomplete="new-password"
              [placeholder]="f.id ? 'laisser vide pour conserver' : 'ex. Bearer abc123 (vide si API publique)'" />
          </label>
        </div>

        <h2 style="margin-top:8px">Où se trouvent les éléments ?</h2>
        <div class="grid">
          <label>Chemin de la liste
            <input [(ngModel)]="f.config.chemin_liste" name="chemin" placeholder="ex. data.items (vide si la réponse est une liste)" />
          </label>
          <label>Pages
            <select [(ngModel)]="f.config.pagination.type" name="pagtype">
              <option value="aucune">Une seule page</option>
              <option value="page">Paramètre de page (?page=1, 2…)</option>
              <option value="suivant">Lien « page suivante » dans la réponse</option>
            </select>
          </label>
          @if (f.config.pagination.type === 'page') {
            <label>Nom du paramètre
              <input [(ngModel)]="f.config.pagination.param" name="pagparam" />
            </label>
          }
          @if (f.config.pagination.type === 'suivant') {
            <label>Champ du lien suivant
              <input [(ngModel)]="f.config.pagination.champ_suivant" name="pagnext" />
            </label>
          }
          <label>Éléments maximum
            <input type="number" [(ngModel)]="f.config.max_elements" name="max" min="1" max="5000" />
          </label>
          <label>Nom affiché comme source
            <input [(ngModel)]="f.config.libelle" name="libelle" placeholder="ex. actualités du PAD" />
          </label>
        </div>

        <div class="row">
          <button type="button" class="sec" (click)="tester(f)" [disabled]="enCours()">Tester l'API</button>
        </div>
        @if (resultat(); as r) {
          <p class="msg" [class.ok]="r.ok" [class.err]="!r.ok" role="status">
            {{ r.ok ? 'Réponse reçue · ' + r.nombre + ' élément(s) sur la première page.' : 'Échec : ' + r.message }}
          </p>
        }

        @if (champs().length) {
          <h2 style="margin-top:8px">Champs à utiliser</h2>
          <div class="grid">
            <label>Identifiant
              <select [(ngModel)]="f.config.champ_id" name="cid">
                <option value="">—</option>
                @for (c of champs(); track c.nom) { <option [value]="c.nom">{{ c.nom }}</option> }
              </select>
            </label>
            <label>Titre
              <select [(ngModel)]="f.config.champ_titre" name="ctitre">
                <option value="">—</option>
                @for (c of champs(); track c.nom) { <option [value]="c.nom">{{ c.nom }}</option> }
              </select>
            </label>
          </div>
          <p class="muted" style="margin-bottom:4px">Champs de texte à importer :</p>
          <div class="cols">
            @for (c of champs(); track c.nom) {
              <label class="check" [title]="c.exemple">
                <input type="checkbox" [checked]="f.config.champs_texte.includes(c.nom)" (change)="basculer(f, c.nom)" />{{ c.nom }}
                <span class="muted" style="margin:0 0 0 6px">{{ c.exemple }}</span>
              </label>
            }
          </div>
        } @else if (f.config.champs_texte.length) {
          <p class="muted">Champs actuels : {{ f.config.champs_texte.join(', ') }} (testez l'API pour les modifier).</p>
        }

        <p class="warn">Tout ce qui est importé devient consultable par n'importe quel visiteur du chatbot.</p>
        <div class="row">
          <button type="button" (click)="enregistrer(f)" [disabled]="enCours()">Enregistrer</button>
          <button type="button" class="sec" (click)="formulaire.set(null)">Annuler</button>
        </div>
        @if (erreurForm()) {
          <p class="msg err" role="alert">{{ erreurForm() }}</p>
        }
      </section>
    } @else {
      <button type="button" (click)="nouveau()">Ajouter une API</button>
    }
  `,
})
export class Apis implements OnInit {
  private readonly api = inject(AdminApi);
  protected readonly sources = signal<ApiSource[]>([]);
  protected readonly formulaire = signal<ApiForm | null>(null);
  protected readonly champs = signal<ChampApi[]>([]);
  protected readonly resultat = signal<{ ok: boolean; message?: string; nombre?: number } | null>(null);
  protected readonly info = signal('');
  protected readonly infoErreur = signal(false);
  protected readonly erreurForm = signal('');
  protected readonly enCours = signal(false);

  ngOnInit(): void {
    void this.recharger();
  }

  private async recharger(): Promise<void> {
    try {
      this.sources.set(await this.api.apis());
    } catch (e) {
      this.dire(messageErreur(e), true);
    }
  }

  private dire(m: string, erreur: boolean): void {
    this.info.set(m);
    this.infoErreur.set(erreur);
  }

  private ouvrir(f: ApiForm): void {
    this.champs.set([]);
    this.resultat.set(null);
    this.erreurForm.set('');
    this.formulaire.set(f);
  }

  protected nouveau(): void {
    this.ouvrir({ nom: '', url: '', entete: '', cle: '', config: configVide() });
  }

  protected modifier(s: ApiSource): void {
    this.ouvrir({
      id: s.id, nom: s.nom, url: s.url, entete: s.entete, cle: '',
      config: { ...configVide(), ...structuredClone(s.config), pagination: { ...configVide().pagination, ...s.config.pagination } },
    });
  }

  protected basculer(f: ApiForm, nom: string): void {
    const l = f.config.champs_texte;
    f.config.champs_texte = l.includes(nom) ? l.filter((x) => x !== nom) : [...l, nom];
  }

  protected async tester(f: ApiForm): Promise<void> {
    this.enCours.set(true);
    this.champs.set([]);
    try {
      const r = await this.api.testerApi(f);
      this.resultat.set(r);
      if (r.ok) this.champs.set(r.champs ?? []);
    } catch (e) {
      this.resultat.set({ ok: false, message: messageErreur(e) });
    } finally {
      this.enCours.set(false);
    }
  }

  protected async enregistrer(f: ApiForm): Promise<void> {
    this.enCours.set(true);
    this.erreurForm.set('');
    try {
      await this.api.enregistrerApi(f);
      this.formulaire.set(null);
      this.dire('API enregistrée.', false);
      await this.recharger();
    } catch (e) {
      this.erreurForm.set(messageErreur(e));
    } finally {
      this.enCours.set(false);
    }
  }

  protected async importer(s: ApiSource): Promise<void> {
    this.enCours.set(true);
    this.dire('Import en cours…', false);
    try {
      const r = await this.api.importerApi(s.id);
      const v = r[s.nom];
      this.dire(typeof v === 'number' ? `${s.nom} : ${v} document(s) importé(s).` : `${s.nom} : ${v}`, typeof v !== 'number');
    } catch (e) {
      this.dire(messageErreur(e), true);
    } finally {
      this.enCours.set(false);
    }
  }

  protected async supprimer(s: ApiSource): Promise<void> {
    if (!confirm(`Supprimer l'API « ${s.nom} » ? Les documents déjà importés restent jusqu'au prochain import.`)) return;
    try {
      await this.api.supprimerApi(s.id);
      this.dire('API supprimée.', false);
      await this.recharger();
    } catch (e) {
      this.dire(messageErreur(e), true);
    }
  }
}
