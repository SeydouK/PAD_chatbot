import { Component } from '@angular/core';
import { RouterLink } from '@angular/router';

@Component({
  selector: 'app-demo',
  imports: [RouterLink],
  template: `
    <main>
      <h1>Port Autonome de Dakar</h1>
      <p>
        Page de démonstration du portail. L'assistant virtuel est disponible en bas à droite :
        posez-lui vos questions sur les horaires, les services, le suivi des navires ou la documentation du port.
      </p>
      <p class="admin"><a routerLink="/admin">Administration</a></p>
    </main>
  `,
  styles: `main { max-width: 720px; margin: 40px auto; padding: 0 16px; } h1 { color: #0b4f7c; } .admin { margin-top: 60px; font-size: 13px; } .admin a { color: #64748b; }`,
})
export class Demo {}
