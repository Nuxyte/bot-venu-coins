import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
import random
from datetime import datetime
import os

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# --------- DATABASE INIT ----------
async def init_db():
    async with aiosqlite.connect("eco.db") as db:
        await db.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, balance INTEGER)")
        await db.execute("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)")
        await db.execute("""CREATE TABLE IF NOT EXISTS quotas (
            user_id INTEGER, action TEXT, count INTEGER, date TEXT,
            PRIMARY KEY (user_id, action, date))""")
        await db.commit()


import re
def parse_duration(text):
    """
    Convertit une chaîne de type "1h", "20m", "2j 5m", "3h10m" en secondes.
    Unités supportées : s (seconde), m (minute), h (heure), j ou d (jour).
    """
    text = text.strip().lower().replace(" ", "")
    matches = re.findall(r"(\d+)([smhdj])", text)
    total_seconds = 0
    for number, unit in matches:
        n = int(number)
        if unit == "s":
            total_seconds += n
        elif unit == "m":
            total_seconds += n * 60
        elif unit == "h":
            total_seconds += n * 3600
        elif unit in ("d", "j"):
            total_seconds += n * 86400
    return total_seconds
    

def human_duration(seconds):
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} seconde{'s' if seconds > 1 else ''}"
    elif seconds < 3600:
        minutes = seconds // 60
        s = seconds % 60
        return f"{minutes} minute{'s' if minutes > 1 else ''}" + (f" {s}s" if s else "")
    elif seconds < 86400:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        mm = f" {minutes}m" if minutes else ""
        return f"{hours} heure{'s' if hours > 1 else ''}{mm}"
    else:
        days = seconds // 86400
        h = (seconds % 86400) // 3600
        if h:
            return f"{days} jour{'s' if days > 1 else ''} {h}h"
        return f"{days} jour{'s' if days > 1 else ''}"



# --------- CONFIG (Catégories) ----------
DEFAULT_CONFIG = {
    # Daily
    "daily_amount": 100,
    "daily_cooldown": 86400,

    # Vol
    "vols_max_par_jours": 3,
    "minimum_volable": 10,
    "maximum_volable": 120,
    "vol_cooldown": 3600,

    # Echange
    "echanges_max_per_day": 5,
    "echange_max_amount": 500,
    "echange_cooldown": 0,  # pas de cooldown
}

CATEGORIES = {
    "Daily": ["daily_amount", "daily_cooldown"],
    "Vol": ["vols_max_par_jours", "minimum_volable", "maximum_volable", "vol_cooldown"],
    "Échange": ["echanges_max_per_day", "echange_max_amount", "echange_cooldown"],
}

async def get_config(key):
    async with aiosqlite.connect("eco.db") as db:
        cursor = await db.execute("SELECT value FROM config WHERE key = ?", (key,))
        res = await cursor.fetchone()
        if not res:
            await db.execute("INSERT INTO config VALUES (?, ?)", (key, str(DEFAULT_CONFIG[key])))
            await db.commit()
            return DEFAULT_CONFIG[key]
        return type(DEFAULT_CONFIG[key])(res[0])

async def set_config(key, value):
    async with aiosqlite.connect("eco.db") as db:
        await db.execute("REPLACE INTO config (key, value) VALUES (?, ?)", (key, str(value)))
        await db.commit()

# --------- ECONOMY HELPERS ----------
async def get_balance(user_id):
    async with aiosqlite.connect("eco.db") as db:
        cursor = await db.execute("SELECT balance FROM users WHERE id = ?", (user_id,))
        res = await cursor.fetchone()
        if not res:
            await db.execute("INSERT INTO users VALUES (?, ?)", (user_id, 100))
            await db.commit()
            return 100
        return int(res[0])

async def update_balance(user_id, change):
    balance = await get_balance(user_id)
    new_balance = max(balance + change, 0)
    async with aiosqlite.connect("eco.db") as db:
        await db.execute("UPDATE users SET balance = ? WHERE id = ?", (new_balance, user_id))
        await db.commit()

# --------- QUOTA HELPERS ----------
async def check_quota(user_id, action, max_allowed):
    today = datetime.utcnow().strftime('%Y-%m-%d')
    async with aiosqlite.connect("eco.db") as db:
        row = await db.execute("SELECT count FROM quotas WHERE user_id=? AND action=? AND date=?", (user_id, action, today))
        res = await row.fetchone()
        return (res is None or int(res[0]) < max_allowed)

async def increment_quota(user_id, action):
    today = datetime.utcnow().strftime('%Y-%m-%d')
    async with aiosqlite.connect("eco.db") as db:
        old = await db.execute("SELECT count FROM quotas WHERE user_id=? AND action=? AND date=?", (user_id, action, today))
        res = await old.fetchone()
        if res is None:
            await db.execute("INSERT INTO quotas VALUES (?, ?, ?, ?)", (user_id, action, 1, today))
        else:
            await db.execute("UPDATE quotas SET count = ? WHERE user_id=? AND action=? AND date=?",
                             (int(res[0])+1, user_id, action, today))
        await db.commit()

# --------- BOT READY ----------
@bot.event
async def on_ready():
    await init_db()
    await tree.sync()
    print(f"Connecté en tant que {bot.user}")

# --------- COMMANDES SLASH ----------

@tree.command(name="solde", description="Voir ton solde")
async def solde(interaction: discord.Interaction, membre: discord.User = None):
    user = membre or interaction.user
    balance = await get_balance(user.id)
    embed = discord.Embed(title="💰 Solde", description=f"{user.mention} a **{balance}€**", color=0x3498db)
    await interaction.response.send_message(embed=embed)

@tree.command(name="daily", description="Réclame ton bonus quotidien")
@app_commands.checks.cooldown(1, DEFAULT_CONFIG["daily_cooldown"], key=lambda i: i.user.id)
async def daily(interaction: discord.Interaction):
    montant_daily = await get_config("daily_amount")
    await update_balance(interaction.user.id, montant_daily)
    await increment_quota(interaction.user.id, "daily")
    thumb_url = "https://media.discordapp.net/attachments/1065772841426444360/1149979816433465414/daily.gif" # image bonus
    embed = discord.Embed(
        title="🎁 Bonus quotidien !",
        description=f"{interaction.user.mention}, tu reçois **{montant_daily}₽** aujourd'hui. Profite bien !",
        color=0xFFD700  # or 0xF7B731 pour un jaune plus doux
    )
    embed.set_thumbnail(url=thumb_url)
    embed.set_footer(text="Économie Discord", icon_url=interaction.user.avatar.url if interaction.user.avatar else None)
    await interaction.response.send_message(embed=embed)


@daily.error
async def daily_error(interaction, error):
    if isinstance(error, app_commands.CommandOnCooldown):
        mins = round(error.retry_after / 60)
        await interaction.response.send_message(f"⏳ Attends {mins} minutes avant de rejouer.", ephemeral=True)

@tree.command(name="voler", description="Voler de l'argent à quelqu'un")
async def voler(interaction: discord.Interaction, cible: discord.User):
    if cible.id == interaction.user.id:
        await interaction.response.send_message("Impossible de te voler toi-même !", ephemeral=True)
        return
    max_vols = await get_config("vols_max_per_day")
    allowed = await check_quota(interaction.user.id, "vol", max_vols)
    if not allowed:
        await interaction.response.send_message("Tu as atteint le maximum de vols/jour.", ephemeral=True)
        return
    cible_balance = await get_balance(cible.id)
    min_vol = await get_config("vol_min_amount")
    max_vol = await get_config("vol_max_amount")
    if cible_balance < min_vol:
        await interaction.response.send_message("La cible est trop pauvre pour être volée !", ephemeral=True)
        return
    amount = random.randint(min_vol, min(max_vol, cible_balance))
    await update_balance(interaction.user.id, amount)
    await update_balance(cible.id, -amount)
    await increment_quota(interaction.user.id, "vol")
    embed = discord.Embed(
    title="🦹 VOL RÉUSSI !",
    description=f"Tu dérobes 🔥 **{amount}₽** à {cible.mention} !\n\n💀 Mauvaise journée pour {cible.mention}...",
    color=0xC0392B
    )
    embed.set_thumbnail(url="https://media.tenor.com/gKxMfe9o3PoAAAAd/robbery-robber.gif")
    embed.add_field(name="Solde de la cible", value=f"{cible.mention}: **{cible_balance-amount}₽**", inline=False)
    embed.add_field(name="Ton nouveau solde", value=f"{interaction.user.mention}: **{await get_balance(interaction.user.id)}₽**", inline=False)
    embed.set_footer(text="La criminalité paie... parfois.", icon_url=cible.avatar.url if cible.avatar else None)
    await interaction.response.send_message(embed=embed)



@tree.command(name="echanger", description="Échanger de l'argent avec une personne")
async def echanger(interaction: discord.Interaction, cible: discord.User, montant: int):
    if montant <= 0:
        await interaction.response.send_message("Le montant doit être positif.", ephemeral=True)
        return
    max_echanges = await get_config("echanges_max_per_day")
    allowed = await check_quota(interaction.user.id, "echange", max_echanges)
    if not allowed:
        await interaction.response.send_message("Tu as atteint la limite d'échanges aujourd'hui.", ephemeral=True)
        return
    max_money_echange = await get_config("echange_max_amount")
    if montant > max_money_echange:
        await interaction.response.send_message(f"Max par échange : {max_money_echange}€.", ephemeral=True)
        return
    balance = await get_balance(interaction.user.id)
    if balance < montant:
        await interaction.response.send_message("Fonds insuffisants.", ephemeral=True)
        return
    await update_balance(interaction.user.id, -montant)
    await update_balance(cible.id, montant)
    await increment_quota(interaction.user.id, "echange")
    embed = discord.Embed(
    title="🔄 Transaction réussie !",
    description=f"{interaction.user.mention} a transféré **{montant}₽** à {cible.mention}.\n🤝 Merci pour l’échange !",
    color=0x00CED1
    )
    embed.set_thumbnail(url="https://media.discordapp.net/attachments/1065772841426444360/1149979816732076052/exchange.gif")
    embed.add_field(name="Solde de l’émetteur", value=f"{interaction.user.mention}: **{await get_balance(interaction.user.id)}₽**", inline=True)
    embed.add_field(name="Solde du récepteur", value=f"{cible.mention}: **{await get_balance(cible.id)}₽**", inline=True)
    embed.set_footer(text="Économie Discord", icon_url=interaction.user.avatar.url if interaction.user.avatar else None)
    await interaction.response.send_message(embed=embed)

@tree.command(name="classement", description="Classement des plus riches")
async def classement(interaction: discord.Interaction):
    async with aiosqlite.connect("eco.db") as db:
        cursor = await db.execute("SELECT id, balance FROM users ORDER BY balance DESC LIMIT 10")
        rows = await cursor.fetchall()
    desc = ""
    for idx, (uid, bal) in enumerate(rows, 1):
        desc += f"{idx}. <@{uid}> — {bal}€\n"
    embed = discord.Embed(
    title="🏆 CLASSEMENT DES RICHES",
    description="**Top 10 des joueurs les plus fortunés :**\n\n" + desc,
    color=0x2ECC71
    )
    embed.set_thumbnail(url="https://media.discordapp.net/attachments/1065772841426444360/1149979817055051847/leaderboard.gif")
    embed.set_footer(text=f"Actualisé le {datetime.now().strftime('%d/%m/%Y %H:%M')}", icon_url=None)
    await interaction.response.send_message(embed=embed)


# --------- ADMIN CONFIG UI EN CATÉGORIES ---------

class ConfigView(discord.ui.View):
    def __init__(self, cur_config):
        super().__init__(timeout=120)
        self.cur_config = cur_config
        options = [discord.SelectOption(label=cat, value=cat) for cat in CATEGORIES]
        self.category_select = discord.ui.Select(placeholder="Choisis une catégorie", options=options)
        self.category_select.callback = self.category_selected
        self.add_item(self.category_select)

    async def category_selected(self, interaction: discord.Interaction):
        cat = self.category_select.values[0]
        params = CATEGORIES[cat]
        param_options = [discord.SelectOption(label=p, value=p) for p in params]
        param_select = discord.ui.Select(placeholder=f"Paramètre de {cat}", options=param_options)
        param_select.callback = lambda i: self.param_selected(i, cat)
        self.clear_items()
        self.add_item(param_select)
        await interaction.response.edit_message(view=self)

    async def param_selected(self, interaction: discord.Interaction, cat):
        param = interaction.data["values"][0]
        value = self.cur_config[param]
        await interaction.response.send_modal(ConfigModal(param, value))

class CategoryButton(discord.ui.Button):
    def __init__(self, cat, emoji):
        super().__init__(
            label=cat,
            style=discord.ButtonStyle.primary,
            emoji=emoji,
            custom_id=cat
        )

    async def callback(self, interaction: discord.Interaction):
        cat = self.custom_id
        cur_config = {p: await get_config(p) for p in CATEGORIES[cat]}
        embed = discord.Embed(
            title=f"{cat} • Configuration",
            description=(
                f"**Paramètres modifiables pour {cat}**\n"
                "Modifie facilement chaque paramètre grâce aux boutons ci-dessous.\n"
                "• Les durées sont affichées en unités humaines.\n"
                "• Les montants sont indiqués en € (ou monnaie de ton serveur).\n"
            ),
            color={"Daily": 0xF1C40F, "Vol": 0xC0392B, "Échange": 0x00CED1}[cat]
        )
        embed.set_thumbnail(url={
            "Daily": "https://cdn-icons-png.flaticon.com/512/481/481144.png",
            "Vol": "https://cdn-icons-png.flaticon.com/512/1674/1674291.png",
            "Échange": "https://cdn-icons-png.flaticon.com/512/1041/1041907.png"
        }[cat])
        for param in CATEGORIES[cat]:
            # Indications plus précises par paramètre
            if "cooldown" in param:
                val = human_duration(cur_config[param])
                typeinfo = "⏱ **Délai** (ex: 1h30m, 2j)."
            elif "amount" in param:
                val = f"{cur_config[param]} €"
                typeinfo = "💰 **Montant** (en €)."
            elif "max" in param or "min" in param:
                val = str(cur_config[param])
                typeinfo = "🔢 **Limite**."
            else:
                val = str(cur_config[param])
                typeinfo = ""
            # Suggestion personnalisée
            suggestions = {
                "daily_amount": "💡 Conseillé: 50-200 €/jour.",
                "vol_max_amount": "💡 Max vol conseillé : < 500.",
                "echange_max_amount": "💡 Pour éviter l'abus, limitez à 500-1000€/échange."
            }
            fieldtext = (
                f"`{val}`\n"
                f"{typeinfo}\n"
                f"{suggestions.get(param, '')}"
            )
            embed.add_field(
                name=f"🔹 {param.replace('_',' ').title()}",
                value=fieldtext,
                inline=False
            )
        param_view = discord.ui.View()
        for param in CATEGORIES[cat]:
            param_view.add_item(ParamButton(cat, param, cur_config[param]))
        await interaction.response.send_message(embed=embed, view=param_view, ephemeral=True)

class ParamButton(discord.ui.Button):
    def __init__(self, cat, param, value):
        super().__init__(
            label=param.replace('_',' ').title()[:43],
            style=discord.ButtonStyle.secondary
        )
        self.cat = cat
        self.param = param
        self.value = value

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(ConfigModal(self.param, self.value))

class CategoryMenu(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        for cat in CATEGORIES:
            emoji = {
                "Daily": "🎁",
                "Vol": "🦹",
                "Échange": "🔄"
            }.get(cat, "⚙️")
            self.add_item(CategoryButton(cat, emoji))

    async def interaction_check(self, interaction):
        return interaction.user.guild_permissions.administrator

class ConfigModal(discord.ui.Modal, title="🔧 Configuration rapide"):
    def __init__(self, param, value):
        super().__init__()
        self.param = param
        label = param.replace('_',' ').title()
        label_short = label[:45]
        placeholder = str(value)
        help_txt = ""
        if "cooldown" in param:
            help_txt = "Saisir la durée (ex : 1h, 2j, 20m, 1h30m)."
            placeholder = human_duration(value) + " (ex: 30m, 1h, 2j)"
        elif "amount" in param:
            help_txt = "Montant en euros (€)."
        elif "max" in param or "min" in param:
            help_txt = "Nombre entier (limite)."
        self.input = discord.ui.TextInput(
            label=label_short,
            placeholder=placeholder if not help_txt else f"{placeholder} • {help_txt}",
            required=True
        )
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        entry = self.input.value
        # Cooldown: format humain
        if "cooldown" in self.param:
            seconds = parse_duration(entry)
            if seconds <= 0:
                await interaction.response.send_message(
                    "❗ Format de durée invalide (ex : 30m, 2h, 1j)", ephemeral=True)
                return
            final_value = seconds
            text_val = human_duration(seconds)
        else:
            try:
                final_value = int(entry)
                text_val = str(final_value)
                if "amount" in self.param:
                    text_val += " €"
            except ValueError:
                await interaction.response.send_message(
                    "❗ Valeur non valide : doit être un nombre", ephemeral=True)
                return

        await set_config(self.param, final_value)
        explanations = {
            "daily_cooldown": "Le délai daily est lisible et simple à saisir ! (ex : 1h, 1j...)",
        }
        message = explanations.get(self.param, "Configuration enregistrée !")
        confirm = discord.Embed(
            title="✅ Modifié !",
            description=(
                f"**{self.param.replace('_',' ').capitalize()}** → `{entry}`\n"
                f"(Valeur réelle: {text_val})\n\n{message}"
            ),
            color=0x27AE60
        )
        confirm.set_footer(text="Configuration actualisée ✨")
        await interaction.response.send_message(embed=confirm, ephemeral=True)

@tree.command(name="config", description="Configuration avancée économie")
@app_commands.default_permissions(administrator=True)
async def config(interaction: discord.Interaction):
    embed = discord.Embed(
        title="⚙️ Panneau d’administration économie",
        description="Choisissez une catégorie à modifier :",
        color=0x9147FF
    )
    embed.set_thumbnail(url="https://cdn-icons-png.flaticon.com/512/924/924915.png")
    embed.set_footer(text="Géré par " + interaction.user.display_name,
                     icon_url=interaction.user.avatar.url if interaction.user.avatar else None)
    for cat in CATEGORIES:
        emoji = {"Daily": "🎁", "Vol": "🦹", "Échange": "🔄"}.get(cat, "⚙️")
        embed.add_field(name=f"{emoji} {cat.upper()}",
                        value=f"Clique sur le bouton pour personnaliser.", inline=False)
    view = CategoryMenu()
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

@tree.command(name="giveaway", description="pour donner des coins a une personne")
@app_commands.default_permissions(administrator=True)
async def money(interaction: discord.Interaction, cible : discord.User, montant: int):
    embed = discord.Embed(
        title="🎁 Récompense donné",
        description=f"Montant donné : **{montant}**",
        color=0x9147FF
    )
    await update_balance(interaction.user.id, -montant)
    await update_balance(cible.id, montant)
    await increment_quota(interaction.user.id, "giveaway")
    await interaction.response.send_message(embed=embed)

@tree.command(name="remove", description="Retire des coins à un utilisateur")
@app_commands.default_permissions(administrator=True)
async def remove(interaction: discord.Interaction, cible: discord.User, montant: int):
    solde = await get_balance(cible.id)
    if montant > solde:
        embed = discord.Embed(
            title="⛔ Impossible",
            description=f"{cible.mention} n’a que **{solde}** coins, impossible de retirer **{montant}**.",
            color=0xFF0000
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    await update_balance(cible.id, -montant)
    embed = discord.Embed(
        title="💸 Coins retirés",
        description=f"**{montant}** coins retirés à {cible.mention}. Nouveau solde : **{solde - montant}**.",
        color=0xFF9147
    )
    await interaction.response.send_message(embed=embed)

@tree.command(name="reset", description="Remet le solde d'un utilisateur à zéro")
@app_commands.default_permissions(administrator=True)
async def reset(interaction: discord.Interaction, cible: discord.User):
    solde = await get_balance(cible.id)
    if solde == 0:
        embed = discord.Embed(
            title="🔄 Solde déjà à zéro",
            description=f"{cible.mention} a déjà un solde de **0**.",
            color=0x9147FF
        )
        await interaction.response.send_message(embed=embed)
        return
    await update_balance(cible.id, -solde)
    embed = discord.Embed(
        title="🔄 Solde réinitialisé",
        description=f"Le solde de {cible.mention} vient d’être réinitialisé à **0**.",
        color=0xFF4949
    )
    await interaction.response.send_message(embed=embed)


from flask import Flask

app = Flask(__name__)

@app.route('/')
def home():
    return 'Bot is running!'

if __name__ == '__main__':
    app.run()


# --------- LANCEMENT BOT ---------
bot.run(os.getenv(Token))

