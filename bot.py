"""
UPMP – Bot Portefeuille (multi-utilisateurs)
---------------------------------------------
Chaque utilisateur Telegram a ses propres investissements, isolés par user_id.
Pas de restriction d'accès — tout le monde peut utiliser le bot.

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

import psycopg2
import psycopg2.extras
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import matplotlib.dates as mdates
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, ConversationHandler,
    MessageHandler, filters, ContextTypes,
)

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
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
                    user_id     BIGINT NOT NULL,
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
            # Index pour accélérer les requêtes par user
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_investments_user_id
                ON investments(user_id)
            """)
        conn.commit()
    log.info("DB initialisée.")


def db_add(user_id, nom, type_, date_entree, mise, valeur, rendement):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO investments (user_id, nom, type, date_entree, mise, valeur, rendement)
                VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
            """, (user_id, nom, type_, date_entree, mise, valeur, rendement))
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
            if include_sold:
                cur.execute(
                    "SELECT nom FROM investments WHERE user_id=%s ORDER BY nom",
                    (user_id,)
                )
            else:
                cur.execute(
                    "SELECT nom FROM investments WHERE user_id=%s AND vendu=FALSE ORDER BY nom",
                    (user_id,)
                )
            return [r["nom"] for r in cur.fetchall()]


# --------------------------------------------------------------------------- #
# Calculs
# --------------------------------------------------------------------------- #
def calc_rendement_annualise(mise, valeur, date_entree):
    if not mise or not valeur or not date_entree:
        return None
    jours = (datetime.date.today() - date_entree).days
    if jours <= 0:
        return None
    gain = float(valeur) - float(mise)
    return (gain / float(mise)) / (jours / 365) * 100


def format_eur(v):
    if v is None:
        return "—"
    sign = "+" if v > 0 else ""
    return f"{sign}{v:,.2f} €"


def row_summary(r):
    mise   = float(r["mise"])   if r["mise"]   else 0
    valeur = float(r["valeur"]) if r["valeur"] else mise
    gain   = valeur - mise
    pct    = (gain / mise * 100) if mise else 0

    annualise  = calc_rendement_annualise(mise, valeur, r["date_entree"])
    projection = (mise * annualise / 100) if annualise is not None else None

    statut   = "⚪ VENDU" if r["vendu"] else "🔵"
    date_str = r["date_entree"].strftime("%d/%m/%Y") if r["date_entree"] else "—"
    emoji    = "🟢" if gain >= 0 else "🔴"

    lines = [
        f"{statut} <b>{r['nom']}</b> [{r['type'] or '—'}]",
        f"  💰 Investi le {date_str} : {mise:,.2f} €",
        f"  📊 Valeur actuelle : {valeur:,.2f} €",
        f"  {emoji} Gain réel : {format_eur(gain)} ({pct:+.2f}%)",
    ]
    if annualise is not None:
        lines.append(f"  📅 Rendement annualisé réel : {annualise:+.1f}%/an")
    if projection is not None:
        lines.append(f"  🎯 Projection annuelle : {format_eur(projection)}")
    if r["rendement"]:
        lines.append(f"  ℹ️  Rendement déclaré : {float(r['rendement']):.1f}%/an")
    if r["vendu"] and r["date_vente"]:
        lines.append(f"  📅 Vendu le : {r['date_vente'].strftime('%d/%m/%Y')}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Graphique
# --------------------------------------------------------------------------- #
def make_chart(nom, mise, valeur, date_entree, annualise):
    fig, ax = plt.subplots(figsize=(8, 4))
    fig.patch.set_facecolor("#f3f8fc")
    ax.set_facecolor("#f3f8fc")

    today = datetime.date.today()

    # Courbe réelle
    d0 = date_entree or today
    ax.plot([d0, today], [float(mise), float(valeur)],
            color="#4996cc", linewidth=2.5, marker="o", markersize=6, label="Valeur réelle")

    # Courbe projetée sur 1 an
    if annualise is not None and date_entree:
        d1 = date_entree + datetime.timedelta(days=365)
        ax.plot([date_entree, d1],
                [float(mise), float(mise) * (1 + annualise / 100)],
                color="#fddd07", linewidth=1.5, linestyle="--",
                label=f"Projection ({annualise:+.1f}%/an)")

    # Ligne mise de départ
    ax.axhline(float(mise), color="#e30021", linewidth=1, linestyle=":",
               label=f"Mise : {float(mise):,.0f} €")

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


def uid(update):
    return update.effective_user.id


# --------------------------------------------------------------------------- #
# Commandes simples
# --------------------------------------------------------------------------- #
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 <b>Bot Portefeuille</b>\n\n"
        "Suis tes investissements simplement. Chaque utilisateur a ses propres données.\n\n"
        "/add — Ajouter un investissement\n"
        "/update — Mettre à jour une valeur\n"
        "/sell — Marquer comme vendu\n"
        "/portfolio — Voir tout le portefeuille\n"
        "/chart — Graphique d'une plateforme\n"
        "/charts — Graphiques de toutes les plateformes\n"
        "/total — Résumé global\n"
        "/list — Liste des investissements\n"
        "/delete — Supprimer un investissement\n\n"
        "Commence par /add 🚀",
        parse_mode="HTML",
    )


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = db_get_all(uid(update))
    if not rows:
        await update.message.reply_text("Aucun investissement. Utilise /add pour commencer.")
        return
    actifs = [r["nom"] for r in rows if not r["vendu"]]
    vendus = [r["nom"] for r in rows if r["vendu"]]
    msg = "📋 <b>Mes investissements</b>\n\n"
    if actifs:
        msg += "<b>Actifs :</b>\n" + "\n".join(f"• {n}" for n in actifs)
    if vendus:
        msg += "\n\n<b>Vendus :</b>\n" + "\n".join(f"⚪ {n}" for n in vendus)
    await update.message.reply_text(msg, parse_mode="HTML")


async def cmd_portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = db_get_all(uid(update))
    if not rows:
        await update.message.reply_text("Aucun investissement. Utilise /add pour commencer.")
        return
    for r in rows:
        await update.message.reply_text(row_summary(r), parse_mode="HTML")


async def cmd_total(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = db_get_all(uid(update), include_sold=False)
    if not rows:
        await update.message.reply_text("Aucun investissement actif.")
        return

    total_mise   = sum(float(r["mise"])   for r in rows)
    total_valeur = sum(float(r["valeur"]) for r in rows if r["valeur"])
    gain_total   = total_valeur - total_mise
    pct_total    = (gain_total / total_mise * 100) if total_mise else 0

    # Rendement annualisé moyen pondéré
    poids = [(float(r["mise"]), calc_rendement_annualise(r["mise"], r["valeur"], r["date_entree"]))
             for r in rows]
    poids = [(m, a) for m, a in poids if a is not None]
    if poids:
        total_poids = sum(m for m, _ in poids)
        ann_pondere = sum(m * a for m, a in poids) / total_poids
        projection  = total_mise * ann_pondere / 100
    else:
        ann_pondere = None
        projection  = None

    emoji = "🟢" if gain_total >= 0 else "🔴"
    msg = (
        "📊 <b>Résumé global</b>\n\n"
        f"💰 Total investi : <b>{total_mise:,.2f} €</b>\n"
        f"📈 Valeur actuelle : <b>{total_valeur:,.2f} €</b>\n"
        f"{emoji} Gain réel : <b>{gain_total:+,.2f} €</b> ({pct_total:+.2f}%)\n"
    )
    if ann_pondere is not None:
        msg += f"\n📅 Rendement annualisé moyen pondéré : <b>{ann_pondere:+.1f}%/an</b>"
    if projection is not None:
        msg += f"\n🎯 Projection annuelle : <b>{projection:+,.2f} €/an</b>"
        msg += f"\n📅 Soit : <b>{projection/12:+,.2f} €/mois</b>"
    msg += f"\n\n📦 Positions actives : {len(rows)}"
    await update.message.reply_text(msg, parse_mode="HTML")


async def cmd_chart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        nom = " ".join(context.args)
        r   = db_get_one(uid(update), nom)
        if not r:
            await update.message.reply_text(f"'{nom}' introuvable.")
            return
        mise   = float(r["mise"])   if r["mise"]   else 0
        valeur = float(r["valeur"]) if r["valeur"] else mise
        ann    = calc_rendement_annualise(mise, valeur, r["date_entree"])
        buf    = make_chart(r["nom"], mise, valeur, r["date_entree"], ann)
        await update.message.reply_photo(photo=buf, caption=f"📈 {r['nom']}")
    else:
        noms = db_list_names(uid(update))
        if not noms:
            await update.message.reply_text("Aucun investissement actif.")
            return
        await update.message.reply_text(
            "Utilise /chart <nom>\nEx : /chart GoMining\n\n"
            + "\n".join(f"• {n}" for n in noms)
        )


async def cmd_charts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = db_get_all(uid(update), include_sold=False)
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
# /add
# --------------------------------------------------------------------------- #
async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📝 Nom de l'investissement ?",
                                    reply_markup=ReplyKeyboardRemove())
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
        "Date d'entrée ? (JJ/MM/AAAA ou 'aujourd'hui')",
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
        context.user_data["mise"] = float(
            update.message.text.replace(",", ".").replace("€", "").strip()
        )
    except ValueError:
        await update.message.reply_text("Montant invalide.")
        return ADD_MISE
    await update.message.reply_text(
        "Valeur actuelle en € ? (Entrée = même que la mise si pas encore changé)"
    )
    return ADD_VALEUR

async def add_valeur(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    if txt == "":
        context.user_data["valeur"] = context.user_data["mise"]
    else:
        try:
            context.user_data["valeur"] = float(
                txt.replace(",", ".").replace("€", "").strip()
            )
        except ValueError:
            await update.message.reply_text("Montant invalide.")
            return ADD_VALEUR
    await update.message.reply_text(
        "Rendement annuel déclaré en % ? (ex: 14.5 pour MacLear)\n"
        "Entrée pour ignorer."
    )
    return ADD_RENDEMENT

async def add_rendement(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    if txt == "":
        context.user_data["rendement"] = None
    else:
        try:
            context.user_data["rendement"] = float(
                txt.replace(",", ".").replace("%", "").strip()
            )
        except ValueError:
            await update.message.reply_text("Valeur invalide.")
            return ADD_RENDEMENT

    d   = context.user_data
    iid = db_add(uid(update), d["nom"], d["type"], d["date"],
                 d["mise"], d["valeur"], d["rendement"])
    r   = db_get_one(uid(update), d["nom"])
    await update.message.reply_text(
        f"✅ Ajouté (ID {iid}) !\n\n" + row_summary(r),
        parse_mode="HTML",
    )
    return ConversationHandler.END


# --------------------------------------------------------------------------- #
# /update
# --------------------------------------------------------------------------- #
async def update_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    noms = db_list_names(uid(update))
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
    count = db_update(uid(update), nom, valeur)
    if count:
        r = db_get_one(uid(update), nom)
        await update.message.reply_text("✅ Mis à jour !\n\n" + row_summary(r),
                                        parse_mode="HTML")
    else:
        await update.message.reply_text(f"'{nom}' introuvable.")
    return ConversationHandler.END


# --------------------------------------------------------------------------- #
# /sell
# --------------------------------------------------------------------------- #
async def sell_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    noms = db_list_names(uid(update))
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
    count = db_sell(uid(update), nom)
    emoji = "✅" if count else "❌"
    msg   = f"'{nom}' marqué comme vendu." if count else f"'{nom}' introuvable ou déjà vendu."
    await update.message.reply_text(f"{emoji} {msg}", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# --------------------------------------------------------------------------- #
# /delete
# --------------------------------------------------------------------------- #
async def delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    noms = db_list_names(uid(update), include_sold=True)
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
    count = db_delete(uid(update), nom)
    emoji = "🗑️" if count else "❌"
    msg   = f"'{nom}' supprimé." if count else f"'{nom}' introuvable."
    await update.message.reply_text(f"{emoji} {msg}", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


async def conv_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Annulé.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    init_db()
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("add", add_start)],
        states={
            ADD_NOM:       [MessageHandler(filters.TEXT & ~filters.COMMAND, add_nom)],
            ADD_TYPE:      [MessageHandler(filters.TEXT & ~filters.COMMAND, add_type)],
            ADD_DATE:      [MessageHandler(filters.TEXT & ~filters.COMMAND, add_date)],
            ADD_MISE:      [MessageHandler(filters.TEXT & ~filters.COMMAND, add_mise)],
            ADD_VALEUR:    [MessageHandler(filters.TEXT & ~filters.COMMAND, add_valeur)],
            ADD_RENDEMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_rendement)],
        },
        fallbacks=[CommandHandler("cancel", conv_cancel)],
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("update", update_start)],
        states={
            UPDATE_NOM:    [MessageHandler(filters.TEXT & ~filters.COMMAND, update_nom)],
            UPDATE_VALEUR: [MessageHandler(filters.TEXT & ~filters.COMMAND, update_valeur)],
        },
        fallbacks=[CommandHandler("cancel", conv_cancel)],
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("sell", sell_start)],
        states={
            SELL_NOM: [MessageHandler(filters.TEXT & ~filters.COMMAND, sell_nom)],
        },
        fallbacks=[CommandHandler("cancel", conv_cancel)],
    ))

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

    log.info("Bot portefeuille multi-users démarré.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
