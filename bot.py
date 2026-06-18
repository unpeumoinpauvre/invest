"""
UPMP – Bot Gestion de Patrimoine (multi-users, multi-devises, bilingue FR/EN)
-----------------------------------------------------------------------------
Fonctionnalités :
  - Message de bienvenue UPMP + bouton YouTube
  - Choix de langue FR/EN au démarrage
  - Investissements en € ou $ avec conversion automatique
  - Objectif de liberté financière avec barre de progression
  - Message matinal à 6h00 : % de liberté financière atteint
  - Rapport quotidien à 20h30 : portefeuille complet
  - Multi-utilisateurs : chaque user a ses propres données

Commandes :
  /start      → démarrage + choix de langue
  /add        → ajouter un investissement
  /update     → mettre à jour une valeur
  /sell       → marquer comme vendu
  /portfolio  → tableau complet
  /chart      → graphique d'une plateforme
  /charts     → graphiques de toutes les plateformes
  /total      → résumé global (€ + $)
  /objectif   → définir/voir l'objectif de liberté financière
  /liberte    → barre de progression vers la liberté financière
  /list       → liste rapide
  /langue     → changer de langue
  /delete     → supprimer un investissement
"""

import os
import io
import logging
import datetime
import requests as req

import psycopg2
import psycopg2.extras
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import matplotlib.dates as mdates
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, ConversationHandler,
    MessageHandler, CallbackQueryHandler, filters, ContextTypes,
)

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
DATABASE_URL       = os.environ["DATABASE_URL"]
YOUTUBE_URL        = "https://www.youtube.com/@unpeumoinspauvre"

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("upmp-portfolio")

# Conversation states
(LANG_CHOICE,
 ADD_NOM, ADD_TYPE, ADD_DEVISE, ADD_DATE, ADD_MISE, ADD_VALEUR, ADD_RENDEMENT,
 UPDATE_NOM, UPDATE_VALEUR,
 SELL_NOM, DELETE_NOM,
 OBJECTIF_MONTANT,
 DEBT_NOM, DEBT_TYPE, DEBT_DEVISE, DEBT_MONTANT, DEBT_TAUX, DEBT_ECHEANCE,
 DEBT_UPDATE_NOM, DEBT_UPDATE_MONTANT,
 DEBT_DELETE_NOM) = range(22)

TYPES = ["Crypto", "Crowdlending", "DeFi", "Minage", "Immobilier", "RWA",
         "Pool liquidité", "Vault", "Actions", "Autre"]
DEVISES = ["EUR (€)", "USD ($)"]

# --------------------------------------------------------------------------- #
# Traductions FR/EN
# --------------------------------------------------------------------------- #
T = {
    "welcome": {
        "fr": (
            "👋 <b>Bienvenue sur le Bot de Gestion de Patrimoine</b>\n\n"
            "Toutes les données que tu rentres t'appartiennent exclusivement. "
            "Ce service est proposé gratuitement par <b>Un Peu Moins Pauvre</b> — "
            "la chaîne YouTube dédiée à l'investissement accessible à tous.\n\n"
            "📊 Suis tes investissements en € et $\n"
            "🎯 Définis ton objectif de liberté financière\n"
            "📈 Reçois un rapport chaque soir à 20h30\n"
            "☀️ Chaque matin à 6h, vois où tu en es\n\n"
            "Abonne-toi à la chaîne pour ne rien manquer 👇"
        ),
        "en": (
            "👋 <b>Welcome to the Wealth Management Bot</b>\n\n"
            "All data you enter belongs exclusively to you. "
            "This service is offered for free by <b>Un Peu Moins Pauvre</b> — "
            "the YouTube channel dedicated to accessible investing.\n\n"
            "📊 Track your investments in € and $\n"
            "🎯 Set your financial freedom goal\n"
            "📈 Receive a daily report every evening at 8:30 PM\n"
            "☀️ Every morning at 6 AM, see where you stand\n\n"
            "Subscribe to the channel to stay updated 👇"
        ),
    },
    "choose_lang": {
        "fr": "🌍 Choisis ta langue / Choose your language :",
        "en": "🌍 Choisis ta langue / Choose your language :",
    },
    "lang_set": {
        "fr": "✅ Langue définie : Français. Utilise /add pour commencer 🚀",
        "en": "✅ Language set: English. Use /add to get started 🚀",
    },
    "no_investments": {
        "fr": "Aucun investissement. Utilise /add pour commencer.",
        "en": "No investments yet. Use /add to get started.",
    },
    "add_nom": {
        "fr": "📝 Nom de l'investissement ?",
        "en": "📝 Name of the investment?",
    },
    "add_type": {
        "fr": "Type d'investissement ?",
        "en": "Type of investment?",
    },
    "add_devise": {
        "fr": "Devise ?",
        "en": "Currency?",
    },
    "add_date": {
        "fr": "Date d'entrée ? (JJ/MM/AAAA ou 'aujourd'hui')",
        "en": "Entry date? (DD/MM/YYYY or 'today')",
    },
    "add_date_err": {
        "fr": "Format invalide. Essaie JJ/MM/AAAA.",
        "en": "Invalid format. Try DD/MM/YYYY.",
    },
    "add_mise": {
        "fr": "Mise de départ en {sym} ?",
        "en": "Initial investment in {sym}?",
    },
    "add_valeur": {
        "fr": "Valeur actuelle en {sym} ? (Entrée = même que la mise)",
        "en": "Current value in {sym}? (Enter = same as initial)",
    },
    "add_rendement": {
        "fr": "Rendement annuel déclaré en % ? (ex: 14.5)\nEntrée pour ignorer.",
        "en": "Declared annual yield in %? (e.g. 14.5)\nPress Enter to skip.",
    },
    "added": {
        "fr": "✅ Ajouté (ID {id}) !",
        "en": "✅ Added (ID {id})!",
    },
    "updated": {
        "fr": "✅ Mis à jour !",
        "en": "✅ Updated!",
    },
    "sold": {
        "fr": "✅ '{nom}' marqué comme vendu.",
        "en": "✅ '{nom}' marked as sold.",
    },
    "not_found": {
        "fr": "'{nom}' introuvable.",
        "en": "'{nom}' not found.",
    },
    "deleted": {
        "fr": "🗑️ '{nom}' supprimé.",
        "en": "🗑️ '{nom}' deleted.",
    },
    "cancelled": {
        "fr": "Annulé.",
        "en": "Cancelled.",
    },
    "update_which": {
        "fr": "Quel investissement mettre à jour ?",
        "en": "Which investment to update?",
    },
    "sell_which": {
        "fr": "Quel investissement marquer comme vendu ?",
        "en": "Which investment to mark as sold?",
    },
    "delete_which": {
        "fr": "⚠️ Quel investissement supprimer définitivement ?",
        "en": "⚠️ Which investment to permanently delete?",
    },
    "new_value": {
        "fr": "Nouvelle valeur actuelle en {sym} ?",
        "en": "New current value in {sym}?",
    },
    "invalid_amount": {
        "fr": "Montant invalide.",
        "en": "Invalid amount.",
    },
    "no_active": {
        "fr": "Aucun investissement actif.",
        "en": "No active investments.",
    },
    "generating": {
        "fr": "📊 Génération de {n} graphiques…",
        "en": "📊 Generating {n} charts…",
    },
    "chart_usage": {
        "fr": "Utilise /chart <nom>\nEx : /chart GoMining",
        "en": "Use /chart <name>\nEx: /chart GoMining",
    },
    "list_active": {
        "fr": "Actifs",
        "en": "Active",
    },
    "list_sold": {
        "fr": "Vendus",
        "en": "Sold",
    },
    "list_title": {
        "fr": "📋 <b>Mes investissements</b>",
        "en": "📋 <b>My investments</b>",
    },
    "objectif_ask": {
        "fr": "🎯 Quel est ton objectif de patrimoine total pour la liberté financière ? (ex: 500000 pour 500 000 €)",
        "en": "🎯 What is your total wealth goal for financial freedom? (e.g. 500000 for $500,000)",
    },
    "objectif_set": {
        "fr": "✅ Objectif défini : {montant} !\nUtilise /liberte pour voir ta progression.",
        "en": "✅ Goal set: {montant}!\nUse /liberte to see your progress.",
    },
    "objectif_invalid": {
        "fr": "Montant invalide. Exemple : 500000",
        "en": "Invalid amount. Example: 500000",
    },
    "liberte_title": {
        "fr": "🎯 <b>Liberté Financière</b>",
        "en": "🎯 <b>Financial Freedom</b>",
    },
    "liberte_no_goal": {
        "fr": "Tu n'as pas encore défini d'objectif. Utilise /objectif.",
        "en": "You haven't set a goal yet. Use /objectif.",
    },
    "morning_msg": {
        "fr": (
            "☀️ <b>Bonjour ! Voici où tu en es aujourd'hui</b>\n\n"
            "💰 Patrimoine actuel : <b>{valeur} €</b>\n"
            "🎯 Objectif liberté financière : <b>{objectif} €</b>\n\n"
            "{barre}\n\n"
            "📈 Tu es à <b>{pct:.1f}%</b> de ta liberté financière.\n"
            "⏳ Il te reste <b>{reste} €</b> à accumuler (<b>{pct_restant:.1f}%</b> du chemin).\n\n"
            "{message}"
        ),
        "en": (
            "☀️ <b>Good morning! Here's where you stand today</b>\n\n"
            "💰 Current wealth: <b>{valeur} €</b>\n"
            "🎯 Financial freedom goal: <b>{objectif} €</b>\n\n"
            "{barre}\n\n"
            "📈 You're at <b>{pct:.1f}%</b> of financial freedom.\n"
            "⏳ You still need <b>{reste} €</b> to get there (<b>{pct_restant:.1f}%</b> remaining).\n\n"
            "{message}"
        ),
    },
    "morning_motivation": {
        "fr": [
            "Continue comme ça, chaque euro compte ! 💪",
            "Tu construis ta liberté jour après jour. 🚀",
            "Le chemin est long mais tu avances ! 🎯",
            "Investis régulièrement et la magie des intérêts composés fera le reste. ✨",
            "Un Peu Moins Pauvre chaque jour ! 😊",
        ],
        "en": [
            "Keep it up, every dollar counts! 💪",
            "You're building your freedom day by day. 🚀",
            "The road is long but you're making progress! 🎯",
            "Invest regularly and compound interest will do the rest. ✨",
            "A little less broke every day! 😊",
        ],
    },
    "daily_report_header": {
        "fr": "🌅 <b>Rapport du {date}</b>",
        "en": "🌅 <b>Daily report — {date}</b>",
    },
    "total_title": {
        "fr": "📊 <b>Résumé global</b>",
        "en": "📊 <b>Global summary</b>",
    },
    "rate_label": {
        "fr": "taux EUR/USD",
        "en": "EUR/USD rate",
    },
    "total_invested": {
        "fr": "💰 Total investi",
        "en": "💰 Total invested",
    },
    "current_value": {
        "fr": "📈 Valeur actuelle",
        "en": "📈 Current value",
    },
    "real_gain": {
        "fr": "Gain réel",
        "en": "Real gain",
    },
    "annualized": {
        "fr": "📅 Rendement annualisé moyen pondéré",
        "en": "📅 Weighted avg annualized return",
    },
    "annual_proj": {
        "fr": "🎯 Projection annuelle",
        "en": "🎯 Annual projection",
    },
    "monthly_proj": {
        "fr": "📅 Soit",
        "en": "📅 That is",
    },
    "per_month": {
        "fr": "€/mois",
        "en": "€/month",
    },
    "active_positions": {
        "fr": "📦 Positions actives",
        "en": "📦 Active positions",
    },
    "row_invested": {
        "fr": "💰 Investi le",
        "en": "💰 Invested on",
    },
    "row_current": {
        "fr": "📊 Valeur actuelle",
        "en": "📊 Current value",
    },
    "row_gain": {
        "fr": "Gain réel",
        "en": "Real gain",
    },
    "row_annualized": {
        "fr": "📅 Rendement annualisé réel",
        "en": "📅 Real annualized return",
    },
    "row_proj": {
        "fr": "🎯 Projection annuelle",
        "en": "🎯 Annual projection",
    },
    "row_declared": {
        "fr": "ℹ️  Rendement déclaré",
        "en": "ℹ️  Declared yield",
    },
    "row_sold_on": {
        "fr": "📅 Vendu le",
        "en": "📅 Sold on",
    },
    "vendu": {
        "fr": "VENDU",
        "en": "SOLD",
    },
    "commands": {
        "fr": (
            "/add — Ajouter un investissement\n"
            "/update — Mettre à jour une valeur\n"
            "/sell — Marquer comme vendu\n"
            "/portfolio — Voir tout le portefeuille\n"
            "/chart — Graphique d'une plateforme\n"
            "/charts — Graphiques de toutes les plateformes\n"
            "/total — Résumé global (€ + $)\n"
            "/objectif — Définir ton objectif de liberté financière\n"
            "/liberte — Voir ta barre de progression\n"
            "/list — Liste des investissements\n"
            "/langue — Changer de langue\n"
            "/delete — Supprimer un investissement\n"
            "/dettes — Voir mes dettes\n"
            "/dette_add — Ajouter une dette\n"
            "/dette_update — Mettre à jour une dette\n"
            "/dette_delete — Supprimer une dette"
        ),
        "en": (
            "/add — Add an investment\n"
            "/update — Update a value\n"
            "/sell — Mark as sold\n"
            "/portfolio — View full portfolio\n"
            "/chart — Chart for one platform\n"
            "/charts — Charts for all platforms\n"
            "/total — Global summary (€ + $)\n"
            "/objectif — Set your financial freedom goal\n"
            "/liberte — View your progress bar\n"
            "/list — List investments\n"
            "/langue — Change language\n"
            "/delete — Delete an investment\n"
            "/dettes — View my debts\n"
            "/dette_add — Add a debt\n"
            "/dette_update — Update a debt\n"
            "/dette_delete — Delete a debt"
        ),
    },

    "debt_title": {
        "fr": "💳 <b>Mes dettes / crédits</b>",
        "en": "💳 <b>My debts / loans</b>",
    },
    "debt_none": {
        "fr": "Aucune dette enregistrée. Utilise /dette_add pour en ajouter.",
        "en": "No debts recorded. Use /dette_add to add one.",
    },
    "debt_add_nom": {
        "fr": "📝 Nom de la dette / crédit ? (ex: Crédit immobilier, Crédit auto...)",
        "en": "📝 Name of the debt/loan? (e.g. Mortgage, Car loan...)",
    },
    "debt_add_type": {
        "fr": "Type de dette ?",
        "en": "Type of debt?",
    },
    "debt_add_devise": {
        "fr": "Devise ?",
        "en": "Currency?",
    },
    "debt_add_montant": {
        "fr": "Montant restant dû en {sym} ?",
        "en": "Remaining amount owed in {sym}?",
    },
    "debt_add_taux": {
        "fr": "Taux d'intérêt annuel en % ? (ex: 3.5 — Entrée pour ignorer)",
        "en": "Annual interest rate in %? (e.g. 3.5 — Enter to skip)",
    },
    "debt_add_echeance": {
        "fr": "Date de fin / échéance ? (JJ/MM/AAAA — Entrée pour ignorer)",
        "en": "End date / maturity? (DD/MM/YYYY — Enter to skip)",
    },
    "debt_added": {
        "fr": "✅ Dette ajoutée !",
        "en": "✅ Debt added!",
    },
    "debt_updated": {
        "fr": "✅ Dette mise à jour !",
        "en": "✅ Debt updated!",
    },
    "debt_deleted": {
        "fr": "🗑️ '{nom}' supprimée.",
        "en": "🗑️ '{nom}' deleted.",
    },
    "debt_update_which": {
        "fr": "Quelle dette mettre à jour ?",
        "en": "Which debt to update?",
    },
    "debt_delete_which": {
        "fr": "⚠️ Quelle dette supprimer ?",
        "en": "⚠️ Which debt to delete?",
    },
    "debt_new_montant": {
        "fr": "Nouveau montant restant dû en {sym} ?",
        "en": "New remaining amount owed in {sym}?",
    },
    "patrimoine_net": {
        "fr": "🏦 Patrimoine NET (investissements − dettes)",
        "en": "🏦 NET wealth (investments − debts)",
    },
    "total_dettes": {
        "fr": "💳 Total dettes",
        "en": "💳 Total debts",
    },
}

def t(key, lang, **kwargs):
    """Récupère une traduction et formate avec kwargs."""
    val = T.get(key, {}).get(lang, T.get(key, {}).get("fr", key))
    if kwargs:
        try:
            val = val.format(**kwargs)
        except Exception:
            pass
    return val


# --------------------------------------------------------------------------- #
# Taux de change
# --------------------------------------------------------------------------- #
_rate_cache = {"rate": None, "ts": None}

def get_eur_usd():
    now = datetime.datetime.utcnow()
    if _rate_cache["rate"] and _rate_cache["ts"] and \
            (now - _rate_cache["ts"]).seconds < 3600:
        return _rate_cache["rate"]
    try:
        r = req.get("https://api.exchangerate-api.com/v4/latest/EUR", timeout=10)
        rate = r.json()["rates"]["USD"]
        _rate_cache["rate"] = rate
        _rate_cache["ts"]   = now
        return rate
    except Exception as exc:
        log.warning("Taux EUR/USD indisponible : %s", exc)
        return _rate_cache["rate"] or 1.08


def to_eur(montant, devise):
    return montant / get_eur_usd() if devise == "USD" else montant

def to_usd(montant, devise):
    return montant * get_eur_usd() if devise == "EUR" else montant


# --------------------------------------------------------------------------- #
# DB
# --------------------------------------------------------------------------- #
def get_conn():
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)


def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS investments (
                    id          SERIAL PRIMARY KEY,
                    user_id     BIGINT NOT NULL,
                    nom         TEXT NOT NULL,
                    type        TEXT,
                    devise      TEXT DEFAULT 'EUR',
                    date_entree DATE,
                    mise        NUMERIC(12,2) NOT NULL,
                    valeur      NUMERIC(12,2),
                    rendement   NUMERIC(6,2),
                    vendu       BOOLEAN DEFAULT FALSE,
                    date_vente  DATE,
                    created_at  TIMESTAMP DEFAULT NOW(),
                    updated_at  TIMESTAMP DEFAULT NOW()
                )
            """)
            # Migration automatique : ajoute la colonne devise si elle n'existe pas
            cur.execute("""
                ALTER TABLE investments
                ADD COLUMN IF NOT EXISTS devise TEXT DEFAULT 'EUR'
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_inv_user ON investments(user_id)
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id    BIGINT PRIMARY KEY,
                    chat_id    BIGINT NOT NULL,
                    langue     TEXT DEFAULT 'fr',
                    objectif   NUMERIC(14,2),
                    active     BOOLEAN DEFAULT TRUE,
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """)
        conn.commit()
    log.info("DB initialisée.")


def upsert_user(user_id, chat_id, langue=None):
    with get_conn() as conn:
        with conn.cursor() as cur:
            if langue:
                cur.execute("""
                    INSERT INTO users (user_id, chat_id, langue, active)
                    VALUES (%s, %s, %s, TRUE)
                    ON CONFLICT (user_id) DO UPDATE
                      SET chat_id=%s, langue=%s, active=TRUE, updated_at=NOW()
                """, (user_id, chat_id, langue, chat_id, langue))
            else:
                cur.execute("""
                    INSERT INTO users (user_id, chat_id, active)
                    VALUES (%s, %s, TRUE)
                    ON CONFLICT (user_id) DO UPDATE
                      SET chat_id=%s, active=TRUE, updated_at=NOW()
                """, (user_id, chat_id, chat_id))
        conn.commit()


def get_user(user_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE user_id=%s", (user_id,))
            return cur.fetchone()


def get_lang(user_id):
    u = get_user(user_id)
    return u["langue"] if u and u["langue"] else "fr"


def set_objectif(user_id, montant):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE users SET objectif=%s, updated_at=NOW() WHERE user_id=%s
            """, (montant, user_id))
        conn.commit()


def get_all_users():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE active=TRUE")
            return cur.fetchall()


def db_add(user_id, nom, type_, devise, date_entree, mise, valeur, rendement):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO investments
                  (user_id, nom, type, devise, date_entree, mise, valeur, rendement)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
            """, (user_id, nom, type_, devise, date_entree, mise, valeur, rendement))
            row = cur.fetchone()
        conn.commit()
    return row["id"]


def db_update(user_id, nom, valeur):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE investments SET valeur=%s, updated_at=NOW()
                WHERE user_id=%s AND LOWER(nom)=LOWER(%s) AND vendu=FALSE
            """, (valeur, user_id, nom))
            count = cur.rowcount
        conn.commit()
    return count


def db_sell(user_id, nom):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE investments SET vendu=TRUE, date_vente=NOW(), updated_at=NOW()
                WHERE user_id=%s AND LOWER(nom)=LOWER(%s) AND vendu=FALSE
            """, (user_id, nom))
            count = cur.rowcount
        conn.commit()
    return count


def db_delete(user_id, nom):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM investments WHERE user_id=%s AND LOWER(nom)=LOWER(%s)",
                (user_id, nom)
            )
            count = cur.rowcount
        conn.commit()
    return count


def db_get_all(user_id, include_sold=True):
    with get_conn() as conn:
        with conn.cursor() as cur:
            if include_sold:
                cur.execute(
                    "SELECT * FROM investments WHERE user_id=%s ORDER BY vendu, nom",
                    (user_id,)
                )
            else:
                cur.execute(
                    "SELECT * FROM investments WHERE user_id=%s AND vendu=FALSE ORDER BY nom",
                    (user_id,)
                )
            return cur.fetchall()


def db_get_one(user_id, nom):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM investments WHERE user_id=%s AND LOWER(nom)=LOWER(%s)",
                (user_id, nom)
            )
            return cur.fetchone()


def db_list_names(user_id, include_sold=False):
    with get_conn() as conn:
        with conn.cursor() as cur:
            q = "SELECT nom FROM investments WHERE user_id=%s"
            if not include_sold:
                q += " AND vendu=FALSE"
            q += " ORDER BY nom"
            cur.execute(q, (user_id,))
            return [r["nom"] for r in cur.fetchall()]


# --------------------------------------------------------------------------- #
# Calculs
# --------------------------------------------------------------------------- #
def calc_annualise(mise, valeur, date_entree):
    if not mise or not valeur or not date_entree:
        return None
    jours = (datetime.date.today() - date_entree).days
    if jours <= 0:
        return None
    return (float(valeur) - float(mise)) / float(mise) / (jours / 365) * 100


def make_progress_bar(pct, length=20):
    """Génère une barre de progression texte."""
    pct     = min(pct, 100)
    filled  = int(length * pct / 100)
    empty   = length - filled
    return f"[{'█' * filled}{'░' * empty}] {pct:.1f}%"


def row_summary(r, lang="fr"):
    rate    = get_eur_usd()
    devise  = r.get("devise") or "EUR"
    mise_n  = float(r["mise"])   if r["mise"]   else 0
    val_n   = float(r["valeur"]) if r["valeur"] else mise_n
    mise_e  = to_eur(mise_n, devise)
    val_e   = to_eur(val_n,  devise)
    gain_e  = val_e - mise_e
    pct     = (gain_e / mise_e * 100) if mise_e else 0
    ann     = calc_annualise(mise_n, val_n, r["date_entree"])
    proj_e  = (mise_e * ann / 100) if ann else None
    sym     = "$" if devise == "USD" else "€"
    eg      = "🟢" if gain_e >= 0 else "🔴"
    es      = f"⚪ {t('vendu', lang)}" if r["vendu"] else "🔵"
    ds      = r["date_entree"].strftime("%d/%m/%Y") if r["date_entree"] else "—"

    lines = [
        f"{es} <b>{r['nom']}</b> [{r['type'] or '—'}] — {devise}",
        f"  {t('row_invested', lang)} {ds} : {mise_n:,.2f} {sym}  ({mise_e:,.2f} € / {mise_e*rate:,.2f} $)",
        f"  {t('row_current',  lang)}    : {val_n:,.2f} {sym}  ({val_e:,.2f} € / {val_e*rate:,.2f} $)",
        f"  {eg} {t('row_gain', lang)} : {gain_e:+,.2f} € ({pct:+.2f}%)",
    ]
    if ann is not None:
        lines.append(f"  {t('row_annualized', lang)} : {ann:+.1f}%/an")
    if proj_e is not None:
        lines.append(f"  {t('row_proj', lang)} : {proj_e:+,.2f} €/an")
    if r.get("rendement"):
        lines.append(f"  {t('row_declared', lang)} : {float(r['rendement']):.1f}%/an")
    if r["vendu"] and r.get("date_vente"):
        lines.append(f"  {t('row_sold_on', lang)} : {r['date_vente'].strftime('%d/%m/%Y')}")
    return "\n".join(lines)


def total_summary(rows, lang="fr", user_id=None):
    rate = get_eur_usd()
    total_mise_e = total_val_e = 0
    poids = []
    for r in rows:
        dev    = r.get("devise") or "EUR"
        mise_n = float(r["mise"])   if r["mise"]   else 0
        val_n  = float(r["valeur"]) if r["valeur"] else mise_n
        mise_e = to_eur(mise_n, dev)
        val_e  = to_eur(val_n,  dev)
        total_mise_e += mise_e
        total_val_e  += val_e
        ann = calc_annualise(mise_n, val_n, r["date_entree"])
        if ann is not None:
            poids.append((mise_e, ann))

    gain_e = total_val_e - total_mise_e
    pct    = (gain_e / total_mise_e * 100) if total_mise_e else 0
    eg     = "🟢" if gain_e >= 0 else "🔴"

    if poids:
        tp     = sum(m for m, _ in poids)
        ann_p  = sum(m * a for m, a in poids) / tp
        proj_e = total_mise_e * ann_p / 100
    else:
        ann_p = proj_e = None

    msg = (
        f"{t('total_title', lang)} — {t('rate_label', lang)} : {rate:.4f}\n\n"
        f"{t('total_invested', lang)} : <b>{total_mise_e:,.2f} €  ({total_mise_e*rate:,.2f} $)</b>\n"
        f"{t('current_value',  lang)} : <b>{total_val_e:,.2f} €  ({total_val_e*rate:,.2f} $)</b>\n"
        f"{eg} {t('real_gain', lang)} : <b>{gain_e:+,.2f} €  ({gain_e*rate:+,.2f} $)  ({pct:+.2f}%)</b>\n"
    )
    if ann_p is not None:
        msg += f"\n{t('annualized', lang)} : <b>{ann_p:+.1f}%/an</b>"
    if proj_e is not None:
        msg += f"\n{t('annual_proj', lang)} : <b>{proj_e:+,.2f} €/an  ({proj_e*rate:+,.2f} $/an)</b>"
        msg += f"\n{t('monthly_proj', lang)} : <b>{proj_e/12:+,.2f} €/mois  ({proj_e*rate/12:+,.2f} $/mois)</b>"
    # Dettes
    total_dettes_e = total_debts_eur(user_id) if user_id else 0
    rate = get_eur_usd()
    if total_dettes_e > 0:
        patrimoine_net = total_val_e - total_dettes_e
        msg += f"\n\n{t('total_dettes', lang)} : <b>-{total_dettes_e:,.2f} €  (-{total_dettes_e*rate:,.2f} $)</b>"
        emoji_net = "🟢" if patrimoine_net >= 0 else "🔴"
        msg += f"\n{emoji_net} {t('patrimoine_net', lang)} : <b>{patrimoine_net:+,.2f} €  ({patrimoine_net*rate:+,.2f} $)</b>"
    msg += f"\n\n{t('active_positions', lang)} : {len(rows)}"
    return msg


# --------------------------------------------------------------------------- #
# Graphique
# --------------------------------------------------------------------------- #
def make_chart(nom, mise_eur, val_eur, date_entree, ann):
    fig, ax = plt.subplots(figsize=(8, 4))
    fig.patch.set_facecolor("#f3f8fc")
    ax.set_facecolor("#f3f8fc")
    today = datetime.date.today()
    d0    = date_entree or today

    ax.plot([d0, today], [mise_eur, val_eur],
            color="#4996cc", linewidth=2.5, marker="o", markersize=6, label="Valeur réelle (€)")

    if ann is not None and date_entree:
        d1 = date_entree + datetime.timedelta(days=365)
        ax.plot([date_entree, d1],
                [mise_eur, mise_eur * (1 + ann / 100)],
                color="#fddd07", linewidth=1.5, linestyle="--",
                label=f"Projection ({ann:+.1f}%/an)")

    ax.axhline(mise_eur, color="#e30021", linewidth=1, linestyle=":",
               label=f"Mise : {mise_eur:,.0f} €")

    ax.set_title(nom, fontsize=14, fontweight="bold", color="#010101")
    ax.set_ylabel("Valeur (€)", color="#5b6b78")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:,.0f} €"))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    fig.autofmt_xdate()
    ax.legend(fontsize=9)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", alpha=0.3)

    buf = io.BytesIO()
    plt.tight_layout()
    plt.savefig(buf, format="png", dpi=120)
    plt.close(fig)
    buf.seek(0)
    return buf


def uid(update): return update.effective_user.id
def cid(update): return update.effective_chat.id


# --------------------------------------------------------------------------- #
# Jobs quotidiens
# --------------------------------------------------------------------------- #
async def morning_job(context):
    """6h00 : message de liberté financière."""
    import random
    users = get_all_users()
    for u in users:
        user_id = u["user_id"]
        chat_id = u["chat_id"]
        lang    = u["langue"] or "fr"
        objectif = float(u["objectif"]) if u.get("objectif") else None
        rows     = db_get_all(user_id, include_sold=False)
        if not rows:
            continue
        total_val_e  = sum(to_eur(float(r["valeur"] or r["mise"]), r.get("devise") or "EUR")
                          for r in rows)
        total_dette_e = total_debts_eur(user_id)
        patrimoine_net_e = total_val_e - total_dette_e
        if not objectif:
            continue
        pct          = min(patrimoine_net_e / objectif * 100, 100)
        reste        = max(objectif - patrimoine_net_e, 0)
        pct_restant  = max(100 - pct, 0)
        barre        = make_progress_bar(pct)
        motiv        = random.choice(T["morning_motivation"][lang])
        try:
            msg = t("morning_msg", lang,
                    valeur=f"{patrimoine_net_e:,.2f}",
                    objectif=f"{objectif:,.2f}",
                    barre=barre, pct=pct,
                    reste=f"{reste:,.2f}",
                    pct_restant=pct_restant,
                    message=motiv)
            await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")
        except Exception as exc:
            log.error("Morning job user %s : %s", user_id, exc)


async def daily_report_job(context):
    """20h30 : rapport complet du portefeuille."""
    users = get_all_users()
    for u in users:
        user_id = u["user_id"]
        chat_id = u["chat_id"]
        lang    = u["langue"] or "fr"
        rows    = db_get_all(user_id, include_sold=False)
        if not rows:
            continue
        try:
            date_str = datetime.datetime.now().strftime("%d/%m/%Y")
            header   = t("daily_report_header", lang, date=date_str)
            msg      = f"{header}\n\n" + total_summary(rows, lang, user_id=user_id)
            await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode="HTML")
        except Exception as exc:
            log.error("Daily report user %s : %s", user_id, exc)


# --------------------------------------------------------------------------- #
# /start → choix de langue
# --------------------------------------------------------------------------- #
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    upsert_user(uid(update), cid(update))
    keyboard = [["🇫🇷 Français", "🇬🇧 English"]]
    await update.message.reply_text(
        t("choose_lang", "fr"),
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
    )
    return LANG_CHOICE


async def lang_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt  = update.message.text.strip()
    lang = "en" if "English" in txt or "EN" in txt else "fr"
    upsert_user(uid(update), cid(update), langue=lang)

    # Message de bienvenue avec bouton YouTube
    btn = InlineKeyboardMarkup([[
        InlineKeyboardButton("▶️ YouTube — Un Peu Moins Pauvre", url=YOUTUBE_URL)
    ]])
    await update.message.reply_text(
        t("welcome", lang),
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )
    await update.message.reply_text(
        t("commands", lang),
        parse_mode="HTML",
        reply_markup=btn,
    )
    return ConversationHandler.END


# --------------------------------------------------------------------------- #
# /langue — changer de langue
# --------------------------------------------------------------------------- #
async def cmd_langue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [["🇫🇷 Français", "🇬🇧 English"]]
    await update.message.reply_text(
        t("choose_lang", "fr"),
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
    )
    return LANG_CHOICE


# --------------------------------------------------------------------------- #
# /objectif
# --------------------------------------------------------------------------- #
async def cmd_objectif(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(uid(update))
    u    = get_user(uid(update))
    if u and u.get("objectif"):
        pct   = 0
        rows  = db_get_all(uid(update), include_sold=False)
        total = sum(to_eur(float(r["valeur"] or r["mise"]), r.get("devise") or "EUR") for r in rows)
        pct   = min(total / float(u["objectif"]) * 100, 100)
        barre = make_progress_bar(pct)
        await update.message.reply_text(
            f"{t('liberte_title', lang)}\n\n"
            f"🎯 Objectif : <b>{float(u['objectif']):,.2f} €</b>\n"
            f"💰 Actuel   : <b>{total:,.2f} €</b>\n"
            f"{barre}\n\n"
            f"Veux-tu modifier l'objectif ? Envoie le nouveau montant :",
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(t("objectif_ask", lang))
    return OBJECTIF_MONTANT


async def objectif_montant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(uid(update))
    try:
        montant = float(update.message.text.replace(" ", "").replace(",", ".").replace("€","").replace("$",""))
    except ValueError:
        await update.message.reply_text(t("objectif_invalid", lang))
        return OBJECTIF_MONTANT
    set_objectif(uid(update), montant)
    await update.message.reply_text(
        t("objectif_set", lang, montant=f"{montant:,.2f} €")
    )
    return ConversationHandler.END


# --------------------------------------------------------------------------- #
# /liberte
# --------------------------------------------------------------------------- #
async def cmd_liberte(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(uid(update))
    u    = get_user(uid(update))
    if not u or not u.get("objectif"):
        await update.message.reply_text(t("liberte_no_goal", lang))
        return
    rows     = db_get_all(uid(update), include_sold=False)
    total    = sum(to_eur(float(r["valeur"] or r["mise"]), r.get("devise") or "EUR") for r in rows)
    objectif = float(u["objectif"])
    pct      = min(total / objectif * 100, 100)
    barre    = make_progress_bar(pct)
    await update.message.reply_text(
        f"{t('liberte_title', lang)}\n\n"
        f"💰 Patrimoine actuel : <b>{total:,.2f} €</b>\n"
        f"🎯 Objectif          : <b>{objectif:,.2f} €</b>\n\n"
        f"{barre}\n\n"
        f"📈 Tu es à <b>{pct:.1f}%</b> de ta liberté financière !",
        parse_mode="HTML",
    )


# --------------------------------------------------------------------------- #
# /list, /portfolio, /total, /chart, /charts
# --------------------------------------------------------------------------- #
async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(uid(update))
    rows = db_get_all(uid(update))
    if not rows:
        await update.message.reply_text(t("no_investments", lang))
        return
    actifs = [f"• {r['nom']} ({r.get('devise','EUR')})" for r in rows if not r["vendu"]]
    vendus = [f"⚪ {r['nom']}" for r in rows if r["vendu"]]
    msg = f"{t('list_title', lang)}\n\n"
    if actifs:
        msg += f"<b>{t('list_active', lang)} :</b>\n" + "\n".join(actifs)
    if vendus:
        msg += f"\n\n<b>{t('list_sold', lang)} :</b>\n" + "\n".join(vendus)
    await update.message.reply_text(msg, parse_mode="HTML")


async def cmd_portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(uid(update))
    rows = db_get_all(uid(update))
    if not rows:
        await update.message.reply_text(t("no_investments", lang))
        return
    for r in rows:
        await update.message.reply_text(row_summary(r, lang), parse_mode="HTML")


async def cmd_total(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(uid(update))
    rows = db_get_all(uid(update), include_sold=False)
    if not rows:
        await update.message.reply_text(t("no_active", lang))
        return
    await update.message.reply_text(total_summary(rows, lang, user_id=uid(update)), parse_mode="HTML")


async def cmd_chart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(uid(update))
    if context.args:
        nom = " ".join(context.args)
        r   = db_get_one(uid(update), nom)
        if not r:
            await update.message.reply_text(t("not_found", lang, nom=nom))
            return
        dev  = r.get("devise") or "EUR"
        me   = to_eur(float(r["mise"]),   dev)
        ve   = to_eur(float(r["valeur"]), dev)
        ann  = calc_annualise(float(r["mise"]), float(r["valeur"]), r["date_entree"])
        buf  = make_chart(r["nom"], me, ve, r["date_entree"], ann)
        await update.message.reply_photo(photo=buf, caption=f"📈 {r['nom']}")
    else:
        noms = db_list_names(uid(update))
        if not noms:
            await update.message.reply_text(t("no_active", lang))
            return
        await update.message.reply_text(
            t("chart_usage", lang) + "\n\n" + "\n".join(f"• {n}" for n in noms)
        )


async def cmd_charts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(uid(update))
    rows = db_get_all(uid(update), include_sold=False)
    if not rows:
        await update.message.reply_text(t("no_active", lang))
        return
    await update.message.reply_text(t("generating", lang, n=len(rows)))
    for r in rows:
        dev = r.get("devise") or "EUR"
        me  = to_eur(float(r["mise"]),   dev)
        ve  = to_eur(float(r["valeur"]), dev)
        ann = calc_annualise(float(r["mise"]), float(r["valeur"]), r["date_entree"])
        buf = make_chart(r["nom"], me, ve, r["date_entree"], ann)
        await update.message.reply_photo(photo=buf, caption=f"📈 {r['nom']}")


# --------------------------------------------------------------------------- #
# /add
# --------------------------------------------------------------------------- #
async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(uid(update))
    upsert_user(uid(update), cid(update))
    await update.message.reply_text(t("add_nom", lang), reply_markup=ReplyKeyboardRemove())
    return ADD_NOM

async def add_nom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(uid(update))
    context.user_data["nom"] = update.message.text.strip()
    keyboard = [[ty] for ty in TYPES]
    await update.message.reply_text(
        t("add_type", lang),
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
    )
    return ADD_TYPE

async def add_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(uid(update))
    context.user_data["type"] = update.message.text.strip()
    keyboard = [["EUR (€)", "USD ($)"]]
    await update.message.reply_text(
        t("add_devise", lang),
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
    )
    return ADD_DEVISE

async def add_devise(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(uid(update))
    txt  = update.message.text.strip()
    context.user_data["devise"] = "USD" if "USD" in txt or "$" in txt else "EUR"
    await update.message.reply_text(t("add_date", lang), reply_markup=ReplyKeyboardRemove())
    return ADD_DATE

async def add_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(uid(update))
    txt  = update.message.text.strip().lower()
    if txt in ("aujourd'hui", "today", "auj"):
        context.user_data["date"] = datetime.date.today()
    else:
        try:
            context.user_data["date"] = datetime.datetime.strptime(txt, "%d/%m/%Y").date()
        except ValueError:
            await update.message.reply_text(t("add_date_err", lang))
            return ADD_DATE
    sym = "$" if context.user_data["devise"] == "USD" else "€"
    await update.message.reply_text(t("add_mise", lang, sym=sym))
    return ADD_MISE

async def add_mise(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(uid(update))
    try:
        context.user_data["mise"] = float(
            update.message.text.replace(",", ".").replace("€", "").replace("$", "").strip()
        )
    except ValueError:
        await update.message.reply_text(t("invalid_amount", lang))
        return ADD_MISE
    sym = "$" if context.user_data["devise"] == "USD" else "€"
    await update.message.reply_text(t("add_valeur", lang, sym=sym))
    return ADD_VALEUR

async def add_valeur(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(uid(update))
    txt  = update.message.text.strip()
    if txt == "":
        context.user_data["valeur"] = context.user_data["mise"]
    else:
        try:
            context.user_data["valeur"] = float(
                txt.replace(",", ".").replace("€", "").replace("$", "").strip()
            )
        except ValueError:
            await update.message.reply_text(t("invalid_amount", lang))
            return ADD_VALEUR
    await update.message.reply_text(t("add_rendement", lang))
    return ADD_RENDEMENT

async def add_rendement(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(uid(update))
    txt  = update.message.text.strip()
    context.user_data["rendement"] = None
    if txt:
        try:
            context.user_data["rendement"] = float(
                txt.replace(",", ".").replace("%", "").strip()
            )
        except ValueError:
            await update.message.reply_text(t("invalid_amount", lang))
            return ADD_RENDEMENT
    d   = context.user_data
    iid = db_add(uid(update), d["nom"], d["type"], d["devise"],
                 d["date"], d["mise"], d["valeur"], d["rendement"])
    r   = db_get_one(uid(update), d["nom"])
    await update.message.reply_text(
        f"{t('added', lang, id=iid)}\n\n" + row_summary(r, lang),
        parse_mode="HTML",
    )
    return ConversationHandler.END


# --------------------------------------------------------------------------- #
# /update
# --------------------------------------------------------------------------- #
async def update_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(uid(update))
    noms = db_list_names(uid(update))
    if not noms:
        await update.message.reply_text(t("no_active", lang))
        return ConversationHandler.END
    keyboard = [[n] for n in noms]
    await update.message.reply_text(
        t("update_which", lang),
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
    )
    return UPDATE_NOM

async def update_nom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(uid(update))
    context.user_data["update_nom"] = update.message.text.strip()
    r   = db_get_one(uid(update), context.user_data["update_nom"])
    sym = "$" if r and r.get("devise") == "USD" else "€"
    await update.message.reply_text(
        t("new_value", lang, sym=sym),
        reply_markup=ReplyKeyboardRemove(),
    )
    return UPDATE_VALEUR

async def update_valeur(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(uid(update))
    try:
        valeur = float(
            update.message.text.replace(",", ".").replace("€", "").replace("$", "").strip()
        )
    except ValueError:
        await update.message.reply_text(t("invalid_amount", lang))
        return UPDATE_VALEUR
    nom   = context.user_data["update_nom"]
    count = db_update(uid(update), nom, valeur)
    if count:
        r = db_get_one(uid(update), nom)
        await update.message.reply_text(
            f"{t('updated', lang)}\n\n" + row_summary(r, lang), parse_mode="HTML"
        )
    else:
        await update.message.reply_text(t("not_found", lang, nom=nom))
    return ConversationHandler.END


# --------------------------------------------------------------------------- #
# /sell
# --------------------------------------------------------------------------- #
async def sell_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(uid(update))
    noms = db_list_names(uid(update))
    if not noms:
        await update.message.reply_text(t("no_active", lang))
        return ConversationHandler.END
    keyboard = [[n] for n in noms]
    await update.message.reply_text(
        t("sell_which", lang),
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
    )
    return SELL_NOM

async def sell_nom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang  = get_lang(uid(update))
    nom   = update.message.text.strip()
    count = db_sell(uid(update), nom)
    msg   = t("sold", lang, nom=nom) if count else t("not_found", lang, nom=nom)
    await update.message.reply_text(msg, reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# --------------------------------------------------------------------------- #
# /delete
# --------------------------------------------------------------------------- #
async def delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(uid(update))
    noms = db_list_names(uid(update), include_sold=True)
    if not noms:
        await update.message.reply_text(t("no_active", lang))
        return ConversationHandler.END
    keyboard = [[n] for n in noms]
    await update.message.reply_text(
        t("delete_which", lang),
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
    )
    return DELETE_NOM

async def delete_nom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang  = get_lang(uid(update))
    nom   = update.message.text.strip()
    count = db_delete(uid(update), nom)
    msg   = t("deleted", lang, nom=nom) if count else t("not_found", lang, nom=nom)
    await update.message.reply_text(msg, reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


async def conv_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(uid(update))
    await update.message.reply_text(t("cancelled", lang), reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
# =========================================================================== #
# DETTES / CRÉDITS
# =========================================================================== #

DEBT_TYPES = ["Crédit immobilier", "Crédit auto", "Crédit conso", "Prêt perso",
              "Découvert", "Carte de crédit", "Autre"]

# --------------------------------------------------------------------------- #
# DB dettes
# --------------------------------------------------------------------------- #
def init_db_debts():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS debts (
                    id          SERIAL PRIMARY KEY,
                    user_id     BIGINT NOT NULL,
                    nom         TEXT NOT NULL,
                    type        TEXT,
                    devise      TEXT DEFAULT 'EUR',
                    montant     NUMERIC(14,2) NOT NULL,
                    taux        NUMERIC(6,2),
                    echeance    DATE,
                    created_at  TIMESTAMP DEFAULT NOW(),
                    updated_at  TIMESTAMP DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_debts_user ON debts(user_id)
            """)
        conn.commit()


def debt_add(user_id, nom, type_, devise, montant, taux, echeance):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO debts (user_id, nom, type, devise, montant, taux, echeance)
                VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id
            """, (user_id, nom, type_, devise, montant, taux, echeance))
            row = cur.fetchone()
        conn.commit()
    return row["id"]


def debt_update(user_id, nom, montant):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE debts SET montant=%s, updated_at=NOW()
                WHERE user_id=%s AND LOWER(nom)=LOWER(%s)
            """, (montant, user_id, nom))
            count = cur.rowcount
        conn.commit()
    return count


def debt_delete(user_id, nom):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM debts WHERE user_id=%s AND LOWER(nom)=LOWER(%s)",
                        (user_id, nom))
            count = cur.rowcount
        conn.commit()
    return count


def debt_get_all(user_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM debts WHERE user_id=%s ORDER BY nom", (user_id,))
            return cur.fetchall()


def debt_get_one(user_id, nom):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM debts WHERE user_id=%s AND LOWER(nom)=LOWER(%s)",
                        (user_id, nom))
            return cur.fetchone()


def debt_list_names(user_id):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT nom FROM debts WHERE user_id=%s ORDER BY nom", (user_id,))
            return [r["nom"] for r in cur.fetchall()]


def total_debts_eur(user_id):
    """Retourne le total des dettes converties en EUR."""
    rows = debt_get_all(user_id)
    return sum(to_eur(float(r["montant"]), r.get("devise") or "EUR") for r in rows)


# --------------------------------------------------------------------------- #
# Formatage d'une dette
# --------------------------------------------------------------------------- #
def debt_row_summary(r, lang="fr"):
    devise = r.get("devise") or "EUR"
    montant_n = float(r["montant"])
    montant_e = to_eur(montant_n, devise)
    rate      = get_eur_usd()
    sym       = "$" if devise == "USD" else "€"

    lines = [
        f"💳 <b>{r['nom']}</b> [{r['type'] or '—'}] — {devise}",
        f"  💸 Montant restant : {montant_n:,.2f} {sym}  ({montant_e:,.2f} € / {montant_e*rate:,.2f} $)",
    ]
    if r.get("taux"):
        cout_annuel = montant_e * float(r["taux"]) / 100
        lines.append(f"  📅 Taux : {float(r['taux']):.2f}%/an  (coût ≈ {cout_annuel:,.2f} €/an)")
    if r.get("echeance"):
        jours = (r["echeance"] - datetime.date.today()).days
        if jours > 0:
            lines.append(f"  🗓️  Échéance : {r['echeance'].strftime('%d/%m/%Y')}  ({jours} jours restants)")
        else:
            lines.append(f"  🗓️  Échéance : {r['echeance'].strftime('%d/%m/%Y')}  (⚠️ échue)")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Commande /dettes
# --------------------------------------------------------------------------- #
async def cmd_dettes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(uid(update))
    rows = debt_get_all(uid(update))
    if not rows:
        await update.message.reply_text(t("debt_none", lang))
        return
    rate        = get_eur_usd()
    total_e     = total_debts_eur(uid(update))
    header      = f"{t('debt_title', lang)}\n"
    await update.message.reply_text(header, parse_mode="HTML")
    for r in rows:
        await update.message.reply_text(debt_row_summary(r, lang), parse_mode="HTML")
    await update.message.reply_text(
        f"💳 <b>Total dettes : {total_e:,.2f} € ({total_e*rate:,.2f} $)</b>",
        parse_mode="HTML"
    )


# --------------------------------------------------------------------------- #
# /dette_add
# --------------------------------------------------------------------------- #
async def dette_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(uid(update))
    await update.message.reply_text(t("debt_add_nom", lang), reply_markup=ReplyKeyboardRemove())
    return DEBT_NOM

async def dette_add_nom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(uid(update))
    context.user_data["debt_nom"] = update.message.text.strip()
    keyboard = [[ty] for ty in DEBT_TYPES]
    await update.message.reply_text(
        t("debt_add_type", lang),
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
    )
    return DEBT_TYPE

async def dette_add_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(uid(update))
    context.user_data["debt_type"] = update.message.text.strip()
    keyboard = [["EUR (€)", "USD ($)"]]
    await update.message.reply_text(
        t("debt_add_devise", lang),
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
    )
    return DEBT_DEVISE

async def dette_add_devise(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(uid(update))
    txt  = update.message.text.strip()
    context.user_data["debt_devise"] = "USD" if "USD" in txt or "$" in txt else "EUR"
    sym  = "$" if context.user_data["debt_devise"] == "USD" else "€"
    await update.message.reply_text(
        t("debt_add_montant", lang, sym=sym),
        reply_markup=ReplyKeyboardRemove(),
    )
    return DEBT_MONTANT

async def dette_add_montant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(uid(update))
    try:
        context.user_data["debt_montant"] = float(
            update.message.text.replace(",", ".").replace("€","").replace("$","").strip()
        )
    except ValueError:
        await update.message.reply_text(t("invalid_amount", lang))
        return DEBT_MONTANT
    await update.message.reply_text(t("debt_add_taux", lang))
    return DEBT_TAUX

async def dette_add_taux(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(uid(update))
    txt  = update.message.text.strip()
    context.user_data["debt_taux"] = None
    if txt:
        try:
            context.user_data["debt_taux"] = float(txt.replace(",",".").replace("%","").strip())
        except ValueError:
            pass
    await update.message.reply_text(t("debt_add_echeance", lang))
    return DEBT_ECHEANCE

async def dette_add_echeance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(uid(update))
    txt  = update.message.text.strip().lower()
    context.user_data["debt_echeance"] = None
    if txt and txt not in ("", "skip", "ignorer"):
        try:
            context.user_data["debt_echeance"] = datetime.datetime.strptime(txt, "%d/%m/%Y").date()
        except ValueError:
            pass
    d   = context.user_data
    iid = debt_add(uid(update), d["debt_nom"], d["debt_type"], d["debt_devise"],
                   d["debt_montant"], d["debt_taux"], d["debt_echeance"])
    r   = debt_get_one(uid(update), d["debt_nom"])
    await update.message.reply_text(
        f"{t('debt_added', lang)}\n\n" + debt_row_summary(r, lang),
        parse_mode="HTML",
    )
    return ConversationHandler.END


# --------------------------------------------------------------------------- #
# /dette_update
# --------------------------------------------------------------------------- #
async def dette_update_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(uid(update))
    noms = debt_list_names(uid(update))
    if not noms:
        await update.message.reply_text(t("debt_none", lang))
        return ConversationHandler.END
    keyboard = [[n] for n in noms]
    await update.message.reply_text(
        t("debt_update_which", lang),
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
    )
    return DEBT_UPDATE_NOM

async def dette_update_nom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(uid(update))
    context.user_data["debt_update_nom"] = update.message.text.strip()
    r   = debt_get_one(uid(update), context.user_data["debt_update_nom"])
    sym = "$" if r and r.get("devise") == "USD" else "€"
    await update.message.reply_text(
        t("debt_new_montant", lang, sym=sym),
        reply_markup=ReplyKeyboardRemove(),
    )
    return DEBT_UPDATE_MONTANT

async def dette_update_montant(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(uid(update))
    try:
        montant = float(update.message.text.replace(",",".").replace("€","").replace("$","").strip())
    except ValueError:
        await update.message.reply_text(t("invalid_amount", lang))
        return DEBT_UPDATE_MONTANT
    nom   = context.user_data["debt_update_nom"]
    count = debt_update(uid(update), nom, montant)
    if count:
        r = debt_get_one(uid(update), nom)
        await update.message.reply_text(
            f"{t('debt_updated', lang)}\n\n" + debt_row_summary(r, lang),
            parse_mode="HTML"
        )
    else:
        await update.message.reply_text(t("not_found", lang, nom=nom))
    return ConversationHandler.END


# --------------------------------------------------------------------------- #
# /dette_delete
# --------------------------------------------------------------------------- #
async def dette_delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(uid(update))
    noms = debt_list_names(uid(update))
    if not noms:
        await update.message.reply_text(t("debt_none", lang))
        return ConversationHandler.END
    keyboard = [[n] for n in noms]
    await update.message.reply_text(
        t("debt_delete_which", lang),
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
    )
    return DEBT_DELETE_NOM

async def dette_delete_nom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang  = get_lang(uid(update))
    nom   = update.message.text.strip()
    count = debt_delete(uid(update), nom)
    msg   = t("debt_deleted", lang, nom=nom) if count else t("not_found", lang, nom=nom)
    await update.message.reply_text(msg, reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END




def main():
    init_db()
    init_db_debts()
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Jobs planifiés (UTC — Paris est UTC+2 en été)
    app.job_queue.run_daily(
        morning_job,
        time=datetime.time(hour=4, minute=0, tzinfo=datetime.timezone.utc),  # 6h00 Paris
        name="morning_job",
    )
    app.job_queue.run_daily(
        daily_report_job,
        time=datetime.time(hour=18, minute=30, tzinfo=datetime.timezone.utc),  # 20h30 Paris
        name="daily_report",
    )

    # /start + /langue → même conversation de sélection de langue
    lang_conv = ConversationHandler(
        entry_points=[
            CommandHandler("start",  cmd_start),
            CommandHandler("langue", cmd_langue),
        ],
        states={
            LANG_CHOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, lang_choice)],
        },
        fallbacks=[CommandHandler("cancel", conv_cancel)],
    )

    # /add
    add_conv = ConversationHandler(
        entry_points=[CommandHandler("add", add_start)],
        states={
            ADD_NOM:       [MessageHandler(filters.TEXT & ~filters.COMMAND, add_nom)],
            ADD_TYPE:      [MessageHandler(filters.TEXT & ~filters.COMMAND, add_type)],
            ADD_DEVISE:    [MessageHandler(filters.TEXT & ~filters.COMMAND, add_devise)],
            ADD_DATE:      [MessageHandler(filters.TEXT & ~filters.COMMAND, add_date)],
            ADD_MISE:      [MessageHandler(filters.TEXT & ~filters.COMMAND, add_mise)],
            ADD_VALEUR:    [MessageHandler(filters.TEXT & ~filters.COMMAND, add_valeur)],
            ADD_RENDEMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_rendement)],
        },
        fallbacks=[CommandHandler("cancel", conv_cancel)],
    )

    # /update
    update_conv = ConversationHandler(
        entry_points=[CommandHandler("update", update_start)],
        states={
            UPDATE_NOM:    [MessageHandler(filters.TEXT & ~filters.COMMAND, update_nom)],
            UPDATE_VALEUR: [MessageHandler(filters.TEXT & ~filters.COMMAND, update_valeur)],
        },
        fallbacks=[CommandHandler("cancel", conv_cancel)],
    )

    # /sell
    sell_conv = ConversationHandler(
        entry_points=[CommandHandler("sell", sell_start)],
        states={SELL_NOM: [MessageHandler(filters.TEXT & ~filters.COMMAND, sell_nom)]},
        fallbacks=[CommandHandler("cancel", conv_cancel)],
    )

    # /delete
    delete_conv = ConversationHandler(
        entry_points=[CommandHandler("delete", delete_start)],
        states={DELETE_NOM: [MessageHandler(filters.TEXT & ~filters.COMMAND, delete_nom)]},
        fallbacks=[CommandHandler("cancel", conv_cancel)],
    )

    # /objectif
    objectif_conv = ConversationHandler(
        entry_points=[CommandHandler("objectif", cmd_objectif)],
        states={OBJECTIF_MONTANT: [MessageHandler(filters.TEXT & ~filters.COMMAND, objectif_montant)]},
        fallbacks=[CommandHandler("cancel", conv_cancel)],
    )

    app.add_handler(lang_conv)
    app.add_handler(add_conv)
    app.add_handler(update_conv)
    app.add_handler(sell_conv)
    app.add_handler(delete_conv)
    app.add_handler(objectif_conv)

    app.add_handler(CommandHandler("list",      cmd_list))
    app.add_handler(CommandHandler("portfolio", cmd_portfolio))
    app.add_handler(CommandHandler("total",     cmd_total))
    app.add_handler(CommandHandler("chart",     cmd_chart))
    app.add_handler(CommandHandler("charts",    cmd_charts))
    app.add_handler(CommandHandler("liberte",   cmd_liberte))
    app.add_handler(CommandHandler("dettes",    cmd_dettes))

    # /dette_add
    dette_add_conv = ConversationHandler(
        entry_points=[CommandHandler("dette_add", dette_add_start)],
        states={
            DEBT_NOM:      [MessageHandler(filters.TEXT & ~filters.COMMAND, dette_add_nom)],
            DEBT_TYPE:     [MessageHandler(filters.TEXT & ~filters.COMMAND, dette_add_type)],
            DEBT_DEVISE:   [MessageHandler(filters.TEXT & ~filters.COMMAND, dette_add_devise)],
            DEBT_MONTANT:  [MessageHandler(filters.TEXT & ~filters.COMMAND, dette_add_montant)],
            DEBT_TAUX:     [MessageHandler(filters.TEXT & ~filters.COMMAND, dette_add_taux)],
            DEBT_ECHEANCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, dette_add_echeance)],
        },
        fallbacks=[CommandHandler("cancel", conv_cancel)],
    )
    dette_update_conv = ConversationHandler(
        entry_points=[CommandHandler("dette_update", dette_update_start)],
        states={
            DEBT_UPDATE_NOM:    [MessageHandler(filters.TEXT & ~filters.COMMAND, dette_update_nom)],
            DEBT_UPDATE_MONTANT:[MessageHandler(filters.TEXT & ~filters.COMMAND, dette_update_montant)],
        },
        fallbacks=[CommandHandler("cancel", conv_cancel)],
    )
    dette_delete_conv = ConversationHandler(
        entry_points=[CommandHandler("dette_delete", dette_delete_start)],
        states={
            DEBT_DELETE_NOM: [MessageHandler(filters.TEXT & ~filters.COMMAND, dette_delete_nom)],
        },
        fallbacks=[CommandHandler("cancel", conv_cancel)],
    )
    app.add_handler(dette_add_conv)
    app.add_handler(dette_update_conv)
    app.add_handler(dette_delete_conv)

    log.info("Bot patrimoine démarré — 6h00 morning job, 20h30 rapport quotidien.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

