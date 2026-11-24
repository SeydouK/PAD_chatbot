export const CHAT_CONFIG = {
  // Développement : le proxy (proxy.conf.json) redirige /rasa vers http://localhost:5005.
  // Production : mettre l'URL complète du bot, ex. 'https://chatbot.exemple.sn/webhooks/rest/webhook'
  // (et lancer Rasa avec --cors pour autoriser le domaine du portail).
  rasaUrl: '/rasa/webhooks/rest/webhook',
  // Le RAG + le LLM peuvent prendre plusieurs secondes.
  timeoutMs: 120_000,
};

/** API d'administration (proxy de développement : /admin-api -> http://127.0.0.1:8010). */
export const ADMIN_API = '/admin-api';
