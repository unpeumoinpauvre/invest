"""
UPMP – Bot Portefeuille
------------------------
Remplace le Google Sheet. Stocke tes investissements dans PostgreSQL Railway.
Affiche retour réel, rendement annualisé, projection et graphiques par plateforme.

Commandes :
  /add        → ajouter un investissement
  /update     → mettre à jour la valeur actuelle
  /sell       → marquer comme vendu
  /portfolio  → tableau complet de tous tes investissements
  /chart      → graphique d'une plateforme
  /charts     → graphiques de toutes les plateformes
  /total      → résumé global
  /delete     → supprimer un investissement
  /list       → liste rapide des noms
"""

import os
import io
import logging
import datetime
from decimal import Decimal

import psycopg2
import psycopg2.extras
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, ConversationHandler,
    MessageHandler, filters, ContextTypes,
)

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID   = os.environ["TELEGRAM_CHAT_ID"]
AUTHORIZED_USER_ID = os.environ.get("AUTHORIZED_USER_ID")
DATABASE_URL       = os.environ["DATABASE_URL"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("upmp-portfolio")

# Conversation states
(ADD_NOM, ADD_TYPE, ADD_DATE, ADD_MISE, ADD_VALEUR, ADD_RENDEMENT,
 UPDATE_NOM, UPDATE_VALEUR,
 SELL_NOM,
 DELETE_NOM) = range(10)

TYPES = ["Crypto", "Crowdlending", "DeFi", "Minage", "Immobilier", "RWA",
         "Pool liquidité", "Vault", "Actions", "Autre"]

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
                    nom         TEXT NOT NULL,
                    type        TEXT,
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
        conn.commit()
    log.info("DB initialisée.")


def db_add(nom, type_, date_entree, mise, valeur, rendement):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO investments (nom, type, date_entree, mise, valeur, rendement)
                VALUES (%s, %s, %s, %s, %s, %s) RETURNING id
            """, (nom, type_, date_entree, mise, valeur, rendement))
            row = cur.fetchone()
        conn.commit()
    return row["id"]


def db_update(nom, valeur):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE investments SET valeur=%s, updated_at=NOW()
                WHERE LOWER(nom)=LOWER(%s) AND vendu=FALSE
            """, (valeur, nom))
            count = cur.rowcount
        conn.commit()
    return count


def db_sell(nom):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE investments SET vendu=TRUE, date_vente=NOW(), updated_at=NOW()
                WHERE LOWER(nom)=LOWER(%s) AND vendu=FALSE
            """, (nom,))
            count = cur.rowcount
        conn.commit()
    return count


def db_delete(nom):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM investments WHERE LOWER(nom)=LOWER(%s)", (nom,))
            count = cur.rowcount
        conn.commit()
    return count


def db_get_all(include_sold=True):
    with get_conn() as conn:
        with conn.cursor() as cur:
            if include_sold:
                cur.execute("SELECT * FROM investments ORDER BY vendu, nom")
            else:
                cur.execute("SELECT * FROM investments WHERE vendu=FALSE ORDER BY nom")
            return cur.fetchall()


def db_get_one(nom):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM investments WHERE LOWER(nom)=LOWER(%s)", (nom,))
            return cur.fetchone()


def db_list_names(include_sold=False):
    with get_conn() as conn:
        with conn.cursor() as cur:
            if include_sold:
                cur.execute("SELECT nom FROM investments ORDER BY nom")
            else:
                cur.execute("SELECT nom FROM investments WHERE vendu=FALSE ORDER BY nom")
            return [r["nom"] for r in cur.fetchall()]


# --------------------------------------------------------------------------- #
# Calculs
# --------------------------------------------------------------------------- #
def calc_rendement_annualise(mise, valeur, date_entree):
    """Rendement annualisé réel depuis la date d'entrée."""
    if not mise or not valeur or not date_entree:
        return None
    jours = (datetime.date.today() - date_entree).days
    if jours <= 0:
        return None
    gain = float(valeur) - float(mise)
    rendement = (gain / float(mise)) / (jours / 365) * 100
    return rendement


def calc_projection_annuelle(mise, rendement_annualise):
    if not mise or rendement_annualise is None:
        return None
    return float(mise) * rendement_annualise / 100


def format_pct(v):
    if v is None:
        return "—"
    color = "🟢" if v >= 0 else "🔴"
    return f"{color} {v:+.2f}%"


def format_eur(v):
    if v is None:
        return "—"
    return f"{v:+.2f} €" if v != 0 else "0 €"


def row_summary(r):
    """Formate une ligne d'investissement pour Telegram."""
    mise  = float(r["mise"])   if r["mise"]   else 0
    valeur = float(r["valeur"]) if r["valeur"] else mise
    gain  = valeur - mise
    pct   = (gain / mise * 100) if mise else 0

    annualise = calc_rendement_annualise(mise, valeur, r["date_entree"])
    projection = calc_projection_annuelle(mise, annualise)

    statut = "⚪ VENDU" if r["vendu"] else "🔵"
    date_str = r["date_entree"].strftime("%d/%m/%Y") if r["date_entree"] else "—"

    lines = [
        f"{statut} <b>{r['nom']}</b> [{r['type'] or '—'}]",
        f"  💰 Investi le {date_str} : {mise:,.2f} €",
        f"  📊 Valeur actuelle : {valeur:,.2f} €",
        f"  {'🟢' if gain >= 0 else '🔴'} Gain réel : {format_eur(gain)} ({pct:+.2f}%)",
    ]
    if annualise is not None:
        lines.append(f"  📅 Rendement annualisé : {annualise:+.1f}%/an")
    if projection is not None:
        lines.append(f"  🎯 Projection annuelle : {format_eur(projection)}")
    if r["rendement"]:
        lines.append(f"  ℹ️ Rendement déclaré : {float(r['rendement']):.1f}%/an")
    if r["vendu"] and r["date_vente"]:
        lines.append(f"  📅 Vendu le : {r['date_vente'].strftime('%d/%m/%Y')}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Graphique
# --------------------------------------------------------------------------- #
def make_chart(nom, mise, valeur, date_entree, rendement_annualise):
    """Génère un graphique courbe projetée vs réelle."""
    fig, ax = plt.subplots(figsize=(8, 4))
    fig.patch.set_facecolor("#f3f8fc")
    ax.set_facecolor("#f3f8fc")

    today = datetime.date.today()
    jours_ecoules = max((today - date_entree).days, 1) if date_entree else 1

    # Courbe réelle (mise → valeur actuelle)
    dates_reelles = [date_entree or today, today]
    vals_reelles  = [float(mise), float(valeur)]
    ax.plot(dates_reelles, vals_reelles, color="#4996cc", linewidth=2.5,
            marker="o", markersize=6, label="Valeur réelle")

    # Courbe projetée sur 1 an depuis entrée
    if rendement_annualise is not None and date_entree:
        date_fin = date_entree + datetime.timedelta(days=365)
        dates_proj = [date_entree, date_fin]
        vals_proj  = [float(mise),
                      float(mise) * (1 + rendement_annualise / 100)]
        ax.plot(dates_proj, vals_proj, color="#fddd07", linewidth=1.5,
                linestyle="--", label=f"Projection ({rendement_annualise:+.1f}%/an)")

    ax.axhline(float(mise), color="#e30021", linewidth=1, linestyle=":",
               label=f"Mise : {float(mise):,.0f} €")

    ax.set_title(f"{nom}", fontsize=14, fontweight="bold", color="#010101")
    ax.set_ylabel("Valeur (€)", color="#5b6b78")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{x:,.0f} €"))
    ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%b %Y"))
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


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
def is_auth(update):
    if not AUTHORIZED_USER_ID:
        return True
    return str(update.effective_user.id) == str(AUTHORIZED_USER_ID)


async def check_auth(update):
    if not is_auth(update):
        await update.message.reply_text("Ce bot est privé.")
        return False
    return True


# --------------------------------------------------------------------------- #
# Commandes simples
# --------------------------------------------------------------------------- #
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return
    await update.message.reply_text(
        "👋 <b>Bot Portefeuille UPMP</b>\n\n"
        "/add — Ajouter un investissement\n"
        "/update — Mettre à jour une valeur\n"
        "/sell — Marquer comme vendu\n"
        "/portfolio — Voir tout le portefeuille\n"
        "/chart — Graphique d'une plateforme\n"
        "/charts — Graphiques de toutes les plateformes\n"
        "/total — Résumé global\n"
        "/list — Liste des investissements\n"
        "/delete — Supprimer un investissement",
        parse_mode="HTML",
    )


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return
    rows = db_get_all()
    if not rows:
        await update.message.reply_text("Aucun investissement enregistré.")
        return
    actifs  = [r["nom"] for r in rows if not r["vendu"]]
    vendus  = [r["nom"] for r in rows if r["vendu"]]
    msg = "📋 <b>Mes investissements</b>\n\n"
    if actifs:
        msg += "<b>Actifs :</b>\n" + "\n".join(f"• {n}" for n in actifs)
    if vendus:
        msg += "\n\n<b>Vendus :</b>\n" + "\n".join(f"⚪ {n}" for n in vendus)
    await update.message.reply_text(msg, parse_mode="HTML")


async def cmd_portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return
    rows = db_get_all()
    if not rows:
        await update.message.reply_text("Aucun investissement enregistré. Utilise /add.")
        return
    for r in rows:
        await update.message.reply_text(row_summary(r), parse_mode="HTML")


async def cmd_total(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return
    rows = db_get_all(include_sold=False)
    if not rows:
        await update.message.reply_text("Aucun investissement actif.")
        return

    total_mise   = sum(float(r["mise"])   for r in rows)
    total_valeur = sum(float(r["valeur"]) for r in rows if r["valeur"])
    gain_total   = total_valeur - total_mise
    pct_total    = (gain_total / total_mise * 100) if total_mise else 0

    # Rendement annualisé moyen pondéré
    poids_annualise = []
    for r in rows:
        ann = calc_rendement_annualise(r["mise"], r["valeur"], r["date_entree"])
        if ann is not None:
            poids_annualise.append((float(r["mise"]), ann))
    if poids_annualise:
        total_poids = sum(p for p, _ in poids_annualise)
        ann_pondere = sum(p * a for p, a in poids_annualise) / total_poids
        projection  = total_mise * ann_pondere / 100
    else:
        ann_pondere = None
        projection  = None

    msg = (
        "📊 <b>Résumé global du portefeuille</b>\n\n"
        f"💰 Total investi : <b>{total_mise:,.2f} €</b>\n"
        f"📈 Valeur actuelle : <b>{total_valeur:,.2f} €</b>\n"
        f"{'🟢' if gain_total >= 0 else '🔴'} Gain réel : <b>{gain_total:+,.2f} €</b> ({pct_total:+.2f}%)\n"
    )
    if ann_pondere is not None:
        msg += f"\n📅 Rendement annualisé moyen pondéré : <b>{ann_pondere:+.1f}%/an</b>"
    if projection is not None:
        msg += f"\n🎯 Projection annuelle totale : <b>{projection:+,.2f} €/an</b>"
        msg += f"\n📅 Soit : <b>{projection/12:+,.2f} €/mois</b>"
    msg += f"\n\n📦 Positions actives : {len(rows)}"
    await update.message.reply_text(msg, parse_mode="HTML")


async def cmd_charts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return
    rows = db_get_all(include_sold=False)
    if not rows:
        await update.message.reply_text("Aucun investissement actif.")
        return
    await update.message.reply_text(f"📊 Génération de {len(rows)} graphiques…")
    for r in rows:
        mise   = float(r["mise"])   if r["mise"]   else 0
        valeur = float(r["valeur"]) if r["valeur"] else mise
        ann    = calc_rendement_annualise(mise, valeur, r["date_entree"])
        buf    = make_chart(r["nom"], mise, valeur, r["date_entree"], ann)
        await update.message.reply_photo(photo=buf, caption=f"📈 {r['nom']}")


# --------------------------------------------------------------------------- #
# /chart — graphique d'une plateforme
# --------------------------------------------------------------------------- #
async def cmd_chart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return
    if context.args:
        nom = " ".join(context.args)
        r = db_get_one(nom)
        if not r:
            await update.message.reply_text(f"Investissement '{nom}' introuvable.")
            return
        mise   = float(r["mise"])   if r["mise"]   else 0
        valeur = float(r["valeur"]) if r["valeur"] else mise
        ann    = calc_rendement_annualise(mise, valeur, r["date_entree"])
        buf    = make_chart(r["nom"], mise, valeur, r["date_entree"], ann)
        await update.message.reply_photo(photo=buf, caption=f"📈 {r['nom']}")
    else:
        noms = db_list_names()
        if not noms:
            await update.message.reply_text("Aucun investissement actif.")
            return
        await update.message.reply_text(
            "Quel investissement ?\nEx : /chart GoMining\n\n"
            + "\n".join(f"• {n}" for n in noms)
        )


# --------------------------------------------------------------------------- #
# /add — conversation
# --------------------------------------------------------------------------- #
async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return ConversationHandler.END
    await update.message.reply_text("📝 Nom de l'investissement ?")
    return ADD_NOM

async def add_nom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["nom"] = update.message.text.strip()
    keyboard = [[t] for t in TYPES]
    await update.message.reply_text(
        "Type ?",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
    )
    return ADD_TYPE

async def add_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["type"] = update.message.text.strip()
    await update.message.reply_text(
        "Date d'entrée ? (format JJ/MM/AAAA ou 'aujourd'hui')",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ADD_DATE

async def add_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip().lower()
    if txt in ("aujourd'hui", "today", "auj"):
        context.user_data["date"] = datetime.date.today()
    else:
        try:
            context.user_data["date"] = datetime.datetime.strptime(txt, "%d/%m/%Y").date()
        except ValueError:
            await update.message.reply_text("Format invalide. Essaie JJ/MM/AAAA.")
            return ADD_DATE
    await update.message.reply_text("Mise de départ en € ? (ex: 1000)")
    return ADD_MISE

async def add_mise(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["mise"] = float(update.message.text.replace(",", ".").replace("€", "").strip())
    except ValueError:
        await update.message.reply_text("Montant invalide.")
        return ADD_MISE
    await update.message.reply_text("Valeur actuelle en € ? (entrée = même que la mise)")
    return ADD_VALEUR

async def add_valeur(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    if txt == "":
        context.user_data["valeur"] = context.user_data["mise"]
    else:
        try:
            context.user_data["valeur"] = float(txt.replace(",", ".").replace("€", "").strip())
        except ValueError:
            await update.message.reply_text("Montant invalide.")
            return ADD_VALEUR
    await update.message.reply_text(
        "Rendement annuel déclaré en % ? (ex: 14.5 pour MacLear — entrée pour ignorer)"
    )
    return ADD_RENDEMENT

async def add_rendement(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    if txt == "":
        context.user_data["rendement"] = None
    else:
        try:
            context.user_data["rendement"] = float(txt.replace(",", ".").replace("%", "").strip())
        except ValueError:
            await update.message.reply_text("Valeur invalide.")
            return ADD_RENDEMENT

    d = context.user_data
    iid = db_add(d["nom"], d["type"], d["date"], d["mise"], d["valeur"], d["rendement"])
    r = db_get_one(d["nom"])
    await update.message.reply_text(
        f"✅ Investissement ajouté (ID {iid}) !\n\n" + row_summary(r),
        parse_mode="HTML",
    )
    return ConversationHandler.END

async def conv_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Annulé.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# --------------------------------------------------------------------------- #
# /update — conversation
# --------------------------------------------------------------------------- #
async def update_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return ConversationHandler.END
    noms = db_list_names()
    if not noms:
        await update.message.reply_text("Aucun investissement actif.")
        return ConversationHandler.END
    keyboard = [[n] for n in noms]
    await update.message.reply_text(
        "Quel investissement mettre à jour ?",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
    )
    return UPDATE_NOM

async def update_nom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["update_nom"] = update.message.text.strip()
    await update.message.reply_text(
        "Nouvelle valeur actuelle en € ?",
        reply_markup=ReplyKeyboardRemove(),
    )
    return UPDATE_VALEUR

async def update_valeur(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        valeur = float(update.message.text.replace(",", ".").replace("€", "").strip())
    except ValueError:
        await update.message.reply_text("Montant invalide.")
        return UPDATE_VALEUR
    nom   = context.user_data["update_nom"]
    count = db_update(nom, valeur)
    if count:
        r = db_get_one(nom)
        await update.message.reply_text("✅ Mis à jour !\n\n" + row_summary(r), parse_mode="HTML")
    else:
        await update.message.reply_text(f"Investissement '{nom}' introuvable.")
    return ConversationHandler.END


# --------------------------------------------------------------------------- #
# /sell — conversation
# --------------------------------------------------------------------------- #
async def sell_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return ConversationHandler.END
    noms = db_list_names()
    if not noms:
        await update.message.reply_text("Aucun investissement actif.")
        return ConversationHandler.END
    keyboard = [[n] for n in noms]
    await update.message.reply_text(
        "Quel investissement marquer comme vendu ?",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
    )
    return SELL_NOM

async def sell_nom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nom   = update.message.text.strip()
    count = db_sell(nom)
    if count:
        await update.message.reply_text(
            f"✅ '{nom}' marqué comme vendu.",
            reply_markup=ReplyKeyboardRemove(),
        )
    else:
        await update.message.reply_text(
            f"'{nom}' introuvable ou déjà vendu.",
            reply_markup=ReplyKeyboardRemove(),
        )
    return ConversationHandler.END


# --------------------------------------------------------------------------- #
# /delete — conversation
# --------------------------------------------------------------------------- #
async def delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return ConversationHandler.END
    noms = db_list_names(include_sold=True)
    if not noms:
        await update.message.reply_text("Aucun investissement.")
        return ConversationHandler.END
    keyboard = [[n] for n in noms]
    await update.message.reply_text(
        "⚠️ Quel investissement supprimer définitivement ?",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
    )
    return DELETE_NOM

async def delete_nom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nom   = update.message.text.strip()
    count = db_delete(nom)
    if count:
        await update.message.reply_text(
            f"🗑️ '{nom}' supprimé.",
            reply_markup=ReplyKeyboardRemove(),
        )
    else:
        await update.message.reply_text(
            f"'{nom}' introuvable.",
            reply_markup=ReplyKeyboardRemove(),
        )
    return ConversationHandler.END


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    init_db()

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # /add
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("add", add_start)],
        states={
            ADD_NOM:      [MessageHandler(filters.TEXT & ~filters.COMMAND, add_nom)],
            ADD_TYPE:     [MessageHandler(filters.TEXT & ~filters.COMMAND, add_type)],
            ADD_DATE:     [MessageHandler(filters.TEXT & ~filters.COMMAND, add_date)],
            ADD_MISE:     [MessageHandler(filters.TEXT & ~filters.COMMAND, add_mise)],
            ADD_VALEUR:   [MessageHandler(filters.TEXT & ~filters.COMMAND, add_valeur)],
            ADD_RENDEMENT:[MessageHandler(filters.TEXT & ~filters.COMMAND, add_rendement)],
        },
        fallbacks=[CommandHandler("cancel", conv_cancel)],
    ))

    # /update
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("update", update_start)],
        states={
            UPDATE_NOM:   [MessageHandler(filters.TEXT & ~filters.COMMAND, update_nom)],
            UPDATE_VALEUR:[MessageHandler(filters.TEXT & ~filters.COMMAND, update_valeur)],
        },
        fallbacks=[CommandHandler("cancel", conv_cancel)],
    ))

    # /sell
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("sell", sell_start)],
        states={
            SELL_NOM: [MessageHandler(filters.TEXT & ~filters.COMMAND, sell_nom)],
        },
        fallbacks=[CommandHandler("cancel", conv_cancel)],
    ))

    # /delete
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("delete", delete_start)],
        states={
            DELETE_NOM: [MessageHandler(filters.TEXT & ~filters.COMMAND, delete_nom)],
        },
        fallbacks=[CommandHandler("cancel", conv_cancel)],
    ))

    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("list",      cmd_list))
    app.add_handler(CommandHandler("portfolio", cmd_portfolio))
    app.add_handler(CommandHandler("total",     cmd_total))
    app.add_handler(CommandHandler("chart",     cmd_chart))
    app.add_handler(CommandHandler("charts",    cmd_charts))

    log.info("Bot portefeuille démarré.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
