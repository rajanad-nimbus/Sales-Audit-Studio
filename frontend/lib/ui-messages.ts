import { currentUserPreferences } from '@/lib/preferences';

const messages = {
  'en-US': {
    preferences: 'My Preferences',
    preferencesSubtitle: 'Regional display and accessibility settings for your account.',
    regionalDisplay: 'Regional display',
    locale: 'Number and date locale',
    timeZone: 'Time zone',
    appearance: 'Appearance and accessibility',
    theme: 'Theme',
    density: 'Information density',
    contrast: 'Contrast',
    motion: 'Motion',
    save: 'Save preferences',
    saving: 'Saving…',
    saved: 'Preferences saved to your account.',
    loadError: 'Your preferences could not be loaded.',
    saveError: 'Your preferences could not be saved.',
    device: 'Use device setting',
    comfortable: 'Comfortable',
    compact: 'Compact',
    standard: 'Standard',
    highContrast: 'High contrast',
    systemMotion: 'Follow system setting',
    reduceMotion: 'Reduce animation',
    light: 'Light',
    dark: 'Dark',
    accountSync: 'Preferences follow your signed-in account across devices.',
  },
  'fr-FR': {
    preferences: 'Mes préférences',
    preferencesSubtitle: "Paramètres régionaux et d'accessibilité de votre compte.",
    regionalDisplay: 'Affichage régional',
    locale: 'Format des nombres et des dates',
    timeZone: 'Fuseau horaire',
    appearance: 'Apparence et accessibilité',
    theme: 'Thème',
    density: "Densité de l'information",
    contrast: 'Contraste',
    motion: 'Animations',
    save: 'Enregistrer les préférences',
    saving: 'Enregistrement…',
    saved: 'Préférences enregistrées dans votre compte.',
    loadError: 'Impossible de charger vos préférences.',
    saveError: "Impossible d'enregistrer vos préférences.",
    device: "Utiliser le réglage de l'appareil",
    comfortable: 'Confortable',
    compact: 'Compact',
    standard: 'Standard',
    highContrast: 'Contraste élevé',
    systemMotion: 'Suivre le réglage système',
    reduceMotion: 'Réduire les animations',
    light: 'Clair',
    dark: 'Sombre',
    accountSync: 'Les préférences suivent votre compte sur tous vos appareils.',
  },
  'de-DE': {
    preferences: 'Meine Einstellungen',
    preferencesSubtitle: 'Regionale Anzeige- und Barrierefreiheitseinstellungen für Ihr Konto.',
    regionalDisplay: 'Regionale Anzeige',
    locale: 'Zahlen- und Datumsformat',
    timeZone: 'Zeitzone',
    appearance: 'Darstellung und Barrierefreiheit',
    theme: 'Design',
    density: 'Informationsdichte',
    contrast: 'Kontrast',
    motion: 'Bewegung',
    save: 'Einstellungen speichern',
    saving: 'Wird gespeichert…',
    saved: 'Einstellungen wurden in Ihrem Konto gespeichert.',
    loadError: 'Ihre Einstellungen konnten nicht geladen werden.',
    saveError: 'Ihre Einstellungen konnten nicht gespeichert werden.',
    device: 'Geräteeinstellung verwenden',
    comfortable: 'Komfortabel',
    compact: 'Kompakt',
    standard: 'Standard',
    highContrast: 'Hoher Kontrast',
    systemMotion: 'Systemeinstellung verwenden',
    reduceMotion: 'Animationen reduzieren',
    light: 'Hell',
    dark: 'Dunkel',
    accountSync: 'Einstellungen folgen Ihrem Konto auf allen Geräten.',
  },
} as const;

export type UiMessageKey = keyof (typeof messages)['en-US'];

const navigation = {
  fr: {
    Workspace: 'Espace de travail', 'Store Day Audit': 'Audit des journées magasin',
    'Investigate & Correct': 'Enquêter et corriger', Reports: 'Rapports',
    Integrations: 'Intégrations', Diagnostics: 'Diagnostics',
    'Command Center': 'Centre de commande', 'Guided Tasks': 'Tâches guidées',
    'Action Center': 'Centre des actions', Cases: 'Dossiers', Transactions: 'Transactions',
    'Your Account': 'Votre compte', 'Store Days': 'Journées magasin',
    'Store Days & Totals': 'Journées magasin et totaux', 'Total Audit Trail': 'Historique des totaux',
    'Miscellaneous Totals': 'Totaux divers', 'Item Summary': 'Récapitulatif des articles',
    'Tender Summary': 'Récapitulatif des moyens de paiement', 'General Ledger View': 'Vue du grand livre',
    'Register Events': 'Événements de caisse', 'Cashier / Register Summary': 'Récapitulatif caissier / caisse',
    'Cash Office': 'Caisse centrale', 'ACH Maintenance': 'Gestion ACH', 'Foundation Data': 'Données de base',
    'Audit Errors': 'Erreurs d’audit', 'Transaction Corrections': 'Corrections de transactions',
    'Audit Trail': 'Journal d’audit', 'Total variances': 'Écarts des totaux',
    'Errors by store': 'Erreurs par magasin', 'Store day status': 'État des journées magasin',
    'Tax / VAT': 'Taxes / TVA', 'Tax corrections': 'Corrections fiscales',
    'Flash Sales Year to Date': 'Ventes cumulées depuis le début de l’année',
    Imports: 'Imports', Exports: 'Exports', 'My Preferences': 'Mes préférences',
    'System health': 'État du système', 'Data feeds': 'Flux de données',
    'Audit integrity': 'Intégrité de l’audit', 'Audit activity': 'Activité d’audit',
    'Agent Activity': 'Activité des agents', General: 'Général', 'Data Exchange': 'Échange de données',
    'Integrations & readiness': 'Intégrations et préparation',
    'Totals, rules & GL mapping': 'Totaux, règles et correspondances GL',
    'Tolerances, store-day order and period close': 'Tolérances, ordre des journées et clôture',
    'Tender types': 'Types de paiement', 'Data retention': 'Conservation des données',
    Agents: 'Agents', 'Export destinations': 'Destinations d’export', 'Roles & access': 'Rôles et accès',
    Admin: 'Administration',
    'Find a page…': 'Rechercher une page…', 'Quick search menus': 'Recherche rapide dans les menus',
    'Close menu': 'Fermer le menu', 'Clear menu search': 'Effacer la recherche', Main: 'Menu principal',
    'Switch workspace': 'Changer d’espace de travail',
    'Available workspaces': 'Espaces disponibles', screen: 'écran', screens: 'écrans', Current: 'Actuel',
    'Signed out': 'Déconnecté', 'Sign in through Ontology Studio': 'Connectez-vous via Ontology Studio',
    'Ontology Studio sign-in required': 'Connexion à Ontology Studio requise',
    'Acting as': 'Agit en tant que',
    'Finance Approver': 'Approbateur Finance', 'IT Support Admin': 'Administrateur IT',
    'Sales Auditor': 'Auditeur des ventes', 'Sales Audit Manager': 'Responsable audit des ventes',
    'Internal Audit': 'Audit interne', 'Store Manager': 'Responsable magasin',
    'Store Sales Staff': 'Équipe de vente magasin', Administrator: 'Administrateur', Viewer: 'Lecteur',
    Impersonate: 'Usurper l’identité', 'Which stores?': 'Quels magasins ?',
    'No stores yet.': 'Aucun magasin.', 'Stop impersonating': 'Arrêter l’usurpation',
    'My preferences': 'Mes préférences', 'Sign in': 'Se connecter', 'Sign out': 'Se déconnecter',
    'Signing out…': 'Déconnexion…', Choose: 'Choisir', 'Act as': 'Agir en tant que',
    'Unknown user': 'Utilisateur inconnu',
    'No pages match': 'Aucune page ne correspond', 'matching pages': 'pages correspondantes',
    'Try another name.': 'Essayez un autre nom.',
  },
  de: {
    Workspace: 'Arbeitsbereich', 'Store Day Audit': 'Filialtagprüfung',
    'Investigate & Correct': 'Untersuchen und korrigieren', Reports: 'Berichte',
    Integrations: 'Integrationen', Diagnostics: 'Diagnose',
    'Your Account': 'Ihr Konto', 'Command Center': 'Leitstand', 'Guided Tasks': 'Geführte Aufgaben',
    'Action Center': 'Aktionscenter', Cases: 'Fälle', Transactions: 'Transaktionen',
    'Store Days': 'Filialtage', 'Store Days & Totals': 'Filialtage und Summen',
    'Total Audit Trail': 'Summen-Auditverlauf', 'Miscellaneous Totals': 'Sonstige Summen',
    'Item Summary': 'Artikelübersicht', 'Tender Summary': 'Zahlungsartenübersicht',
    'General Ledger View': 'Hauptbuchansicht', 'Register Events': 'Kassenereignisse',
    'Cashier / Register Summary': 'Kassierer- / Kassenübersicht', 'Cash Office': 'Kassenbüro',
    'ACH Maintenance': 'ACH-Verwaltung', 'Foundation Data': 'Stammdaten',
    'Audit Errors': 'Auditfehler', 'Transaction Corrections': 'Transaktionskorrekturen',
    'Audit Trail': 'Auditverlauf', 'Total variances': 'Summenabweichungen',
    'Errors by store': 'Fehler nach Filiale', 'Store day status': 'Filialtagstatus',
    'Tax / VAT': 'Steuer / MwSt.', 'Tax corrections': 'Steuerkorrekturen',
    'Flash Sales Year to Date': 'Umsatz seit Jahresbeginn', Imports: 'Importe',
    Exports: 'Exporte', 'My Preferences': 'Meine Einstellungen', 'System health': 'Systemzustand',
    'Data feeds': 'Datenfeeds', 'Audit integrity': 'Auditintegrität', 'Audit activity': 'Auditaktivität',
    'Agent Activity': 'Agentenaktivität', General: 'Allgemein', 'Data Exchange': 'Datenaustausch',
    'Integrations & readiness': 'Integrationen und Bereitschaft',
    'Totals, rules & GL mapping': 'Summen, Regeln und GL-Zuordnung',
    'Tolerances, store-day order and period close': 'Toleranzen, Filialtagreihenfolge und Periodenabschluss',
    'Tender types': 'Zahlungsarten', 'Data retention': 'Datenaufbewahrung', Agents: 'Agenten',
    'Export destinations': 'Exportziele', 'Roles & access': 'Rollen und Zugriff', Admin: 'Administration',
    'Find a page…': 'Seite suchen…', 'Quick search menus': 'Menüschnellsuche',
    'Close menu': 'Menü schließen', 'Clear menu search': 'Suche löschen', Main: 'Hauptmenü',
    'Switch workspace': 'Arbeitsbereich wechseln', 'Available workspaces': 'Verfügbare Arbeitsbereiche',
    screen: 'Bildschirm', screens: 'Bildschirme', Current: 'Aktuell',
    'Signed out': 'Abgemeldet', 'Sign in through Ontology Studio': 'Über Ontology Studio anmelden',
    'Ontology Studio sign-in required': 'Anmeldung über Ontology Studio erforderlich',
    'Acting as': 'Handelt als',
    'Finance Approver': 'Finanzfreigeber', 'IT Support Admin': 'IT-Administrator',
    'Sales Auditor': 'Verkaufsprüfer', 'Sales Audit Manager': 'Leiter Verkaufsprüfung',
    'Internal Audit': 'Interne Revision', 'Store Manager': 'Filialleitung',
    'Store Sales Staff': 'Verkaufsteam der Filiale', Administrator: 'Administrator', Viewer: 'Leser',
    Impersonate: 'Identität übernehmen', 'Which stores?': 'Welche Filialen?',
    'No stores yet.': 'Noch keine Filialen.', 'Stop impersonating': 'Identitätswechsel beenden',
    'My preferences': 'Meine Einstellungen', 'Sign in': 'Anmelden', 'Sign out': 'Abmelden',
    'Signing out…': 'Wird abgemeldet…', Choose: 'Auswählen', 'Act as': 'Handeln als',
    'Unknown user': 'Unbekannter Benutzer',
    'No pages match': 'Keine passende Seite', 'matching pages': 'passende Seiten',
    'Try another name.': 'Versuchen Sie einen anderen Namen.',
  },
} as const;

export function uiNavigationLabel(label: string, locale = currentUserPreferences().locale) {
  const language = locale.split('-')[0] as keyof typeof navigation;
  return (navigation[language] as Record<string, string> | undefined)?.[label] ?? label;
}

export function uiSidebarSearchStatus(count: number, query: string, locale = currentUserPreferences().locale) {
  if (locale.startsWith('fr')) {
    return count === 0
      ? `Aucune page ne correspond à « ${query} ». Essayez un autre nom.`
      : `${count} page${count === 1 ? '' : 's'} correspondante${count === 1 ? '' : 's'}`;
  }
  if (locale.startsWith('de')) {
    return count === 0
      ? `Keine Seite entspricht „${query}“. Versuchen Sie einen anderen Namen.`
      : `${count} passende Seite${count === 1 ? '' : 'n'}`;
  }
  return count === 0
    ? `No pages match “${query}”. Try another name.`
    : `${count} matching pages`;
}

export function uiMessage(key: UiMessageKey, locale = currentUserPreferences().locale): string {
  const language = locale.split('-')[0];
  const localized = Object.entries(messages).find(([tag]) => tag.split('-')[0] === language)?.[1];
  return (localized as (typeof messages)['en-US'] | undefined)?.[key] ?? messages['en-US'][key];
}
