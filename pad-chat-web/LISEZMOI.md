# Assistant PAD – widget de chat Angular

Widget de chat (bulle flottante en bas à droite) qui parle au bot Rasa par son canal REST.

## 1. Côté Rasa (projet `pad-chatbot`)

Dans `credentials.yml`, active le canal REST (décommente la ligne) :

```yaml
rest:
```

Lance les deux serveurs dans deux terminaux (venv activé, `GEMINI_API_KEY` définie dans le second) :

```bat
rasa run actions
rasa run --enable-api --cors "*"
```

`--cors "*"` n'est utile qu'en production (ou sans proxy). En développement, le proxy Angular suffit.

## 2. Côté Angular

Prérequis : Node.js 22 LTS.

```bat
npm install
npm start
```

Ouvre http://localhost:4200. Si `npm install` échoue avec l'erreur `Cannot read properties of null (reading 'edgesOut')`, utilise `npm install --legacy-peer-deps`.

`npm start` utilise `proxy.conf.json` : les appels vers `/rasa/...` sont redirigés vers `http://localhost:5005`.

## 3. Mise en production

Dans `src/app/config.ts`, remplace `rasaUrl` par l'URL complète du bot
(`https://…/webhooks/rest/webhook`), puis `npm run build`. Le résultat est dans `dist/pad-chat-web/browser`.
Pense à restreindre `--cors` au domaine du portail (au lieu de `"*"`).

## Fichiers

| Fichier | Rôle |
|---|---|
| `src/app/config.ts` | URL de Rasa et délai maximum d'attente |
| `src/app/chat.service.ts` | Appels à Rasa, état de la conversation, suggestions d'accueil |
| `src/app/chat-widget.*` | Interface (bulle, panneau, boutons, indicateur de frappe) |
| `proxy.conf.json` | Proxy de développement vers Rasa |
