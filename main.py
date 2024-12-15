import discord
from discord import app_commands
from discord.ext import tasks
import asyncio
import logging
import json
from datetime import datetime, timezone
from twikit import Client as TwitterClient

# Configuration du logging
logging.basicConfig(level=logging.DEBUG, 
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    handlers=[
                        logging.FileHandler("bot.log"),
                        logging.StreamHandler()
                    ])
logger = logging.getLogger('bot')

# Configuration du bot Discord
intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# Configuration Twitter
CONFIG_FILE = 'config-bot.json'
SERVER_CONFIGS = {}

twitter_client = TwitterClient()

async def fetch_tweets(user_id, count=1):
    logger.debug(f"Tentative de récupération de {count} tweets pour l'utilisateur {user_id}")
    try:
        tweets = await twitter_client.get_user_tweets(user_id, 'Tweets', count=count)
        logger.debug(f"Tweets récupérés : {tweets}")
        return tweets
    except Exception as e:
        logger.exception(f"Erreur lors de la récupération des tweets: {e}")
    return []

def create_tweet_embed(tweet):
    embed = discord.Embed(title=f"Nouveau tweet de {tweet.user.screen_name}", 
                          description=tweet.full_text, 
                          url=f"https://twitter.com/user/status/{tweet.id}", 
                          color=0x1DA1F2)
    embed.set_footer(text=f"Publié le {tweet.created_at_datetime.strftime('%d-%m-%Y %H:%M:%S UTC')}")
    
    if tweet.media:
        for media in tweet.media:
            media_url = media.get('media_url_https')
            media_type = media.get('type')
            logger.info(f"URL du média : {media_url}")
            logger.info(f"Type du média : {media_type}")
            
            if media_type == 'photo':
                embed.set_image(url=media_url)
            elif media_type in ['video', 'animated_gif']:
                embed.add_field(name="Média", value=f"[Voir la vidéo/GIF]({media.get('expanded_url')})", inline=False)
    
    return embed

@tasks.loop(minutes=5)
async def check_new_tweets():
    logger.info("Début de la vérification des nouveaux tweets")
    for guild_id, config in SERVER_CONFIGS.items():
        channel = client.get_channel(config['channel_id'])
        if channel is None:
            logger.error(f"Canal avec l'ID {config['channel_id']} non trouvé pour le serveur {guild_id}")
            continue
        
        for user_id, last_tweet_id in config['followed_accounts'].items():
            tweets = await fetch_tweets(user_id)
            if not tweets:
                logger.info(f"Aucun tweet récupéré pour l'utilisateur {user_id}")
                continue

            new_tweets = []
            for tweet in tweets:
                if tweet.id != last_tweet_id:
                    new_tweets.append(tweet)
                else:
                    break

            for tweet in reversed(new_tweets):
                embed = create_tweet_embed(tweet)
                try:
                    await channel.send(embed=embed)
                    logger.info(f"Nouveau tweet envoyé : {tweet.id}")
                    config['followed_accounts'][user_id] = tweet.id
                except Exception as e:
                    logger.exception(f"Erreur lors de l'envoi du tweet : {e}")

    save_config()

@tree.command(name="config-account", description="Configure le compte Twitter à utiliser pour scraper")
@app_commands.checks.has_permissions(administrator=True)
async def config_account(interaction: discord.Interaction, username: str, password: str):
    logger.info(f"Commande 'config-account' exécutée par {interaction.user}")
    await interaction.response.defer(ephemeral=True)

    try:
        await twitter_client.login(auth_info_1='XIrearAPI', auth_info_2=username, password=password)
        logger.info("Nouvelle configuration Twitter appliquée")
        await interaction.followup.send("Configuration Twitter mise à jour avec succès.", ephemeral=True)
    except Exception as e:
        logger.exception(f"Erreur lors de la configuration du compte Twitter : {e}")
        await interaction.followup.send("Une erreur s'est produite lors de la configuration du compte Twitter.", ephemeral=True)

@tree.command(name="config-settings", description="Configure les paramètres du bot pour ce serveur")
@app_commands.checks.has_permissions(administrator=True)
async def config_settings(interaction: discord.Interaction, salon: discord.TextChannel, intervalle: int):
    logger.info(f"Commande 'config-settings' exécutée par {interaction.user}")
    guild_id = interaction.guild_id

    if intervalle < 5 or intervalle > 60:
        await interaction.response.send_message("L'intervalle doit être entre 5 et 60 minutes.", ephemeral=True)
        return

    if guild_id not in SERVER_CONFIGS:
        SERVER_CONFIGS[guild_id] = {'channel_id': None, 'interval': 5, 'followed_accounts': {}}

    SERVER_CONFIGS[guild_id]['channel_id'] = salon.id
    SERVER_CONFIGS[guild_id]['interval'] = intervalle

    save_config()
    await interaction.response.send_message(f"Configuration mise à jour. Les tweets seront envoyés dans {salon.mention} toutes les {intervalle} minutes.", ephemeral=True)

    # Redémarrer la tâche de vérification avec le nouvel intervalle
    check_new_tweets.change_interval(minutes=min(SERVER_CONFIGS[guild_id]['interval'] for guild_id in SERVER_CONFIGS))

@tree.command(name="follow", description="Suivre un compte Twitter")
@app_commands.checks.has_permissions(administrator=True)
async def follow(interaction: discord.Interaction, compte: str):
    logger.info(f"Commande 'follow' exécutée par {interaction.user} pour le compte {compte}")
    await interaction.response.defer()

    guild_id = interaction.guild_id
    if guild_id not in SERVER_CONFIGS:
        await interaction.followup.send("Veuillez d'abord configurer les paramètres du bot avec /config-settings.", ephemeral=True)
        return

    try:
        if compte.startswith('@'):
            user = await twitter_client.get_user_by_username(compte[1:])
        else:
            user = await twitter_client.get_user_by_id(int(compte))

        if user:
            SERVER_CONFIGS[guild_id]['followed_accounts'][user.id] = None
            save_config()
            await interaction.followup.send(f"Le compte {user.name} est maintenant suivi.")
        else:
            await interaction.followup.send("Compte Twitter non trouvé.")
    except Exception as e:
        logger.exception(f"Erreur lors du suivi du compte : {e}")
        await interaction.followup.send("Une erreur s'est produite lors du suivi du compte.")

@tree.command(name="unfollow", description="Ne plus suivre un compte Twitter")
@app_commands.checks.has_permissions(administrator=True)
async def unfollow(interaction: discord.Interaction, compte: str):
    logger.info(f"Commande 'unfollow' exécutée par {interaction.user} pour le compte {compte}")
    await interaction.response.defer()

    guild_id = interaction.guild_id
    if guild_id not in SERVER_CONFIGS:
        await interaction.followup.send("Aucune configuration trouvée pour ce serveur.", ephemeral=True)
        return

    try:
        if compte.startswith('@'):
            user = await twitter_client.get_user_by_username(compte[1:])
            user_id = user.id if user else None
        else:
            user_id = int(compte)

        if user_id in SERVER_CONFIGS[guild_id]['followed_accounts']:
            del SERVER_CONFIGS[guild_id]['followed_accounts'][user_id]
            save_config()
            await interaction.followup.send(f"Le compte n'est plus suivi.")
        else:
            await interaction.followup.send("Ce compte n'était pas suivi.")
    except Exception as e:
        logger.exception(f"Erreur lors du désabonnement du compte : {e}")
        await interaction.followup.send("Une erreur s'est produite lors du désabonnement.")

def save_config():
    with open(CONFIG_FILE, 'w') as f:
        json.dump(SERVER_CONFIGS, f)
    logger.info("Configuration sauvegardée")

def load_config():
    global SERVER_CONFIGS
    try:
        with open(CONFIG_FILE, 'r') as f:
            SERVER_CONFIGS = json.load(f)
        logger.info("Configuration chargée avec succès")
    except FileNotFoundError:
        logger.warning("Fichier de configuration non trouvé, utilisation d'une configuration vide")
        SERVER_CONFIGS = {}
    except json.JSONDecodeError:
        logger.error("Erreur lors du décodage du fichier de configuration")
        SERVER_CONFIGS = {}

@client.event
async def on_ready():
    logger.info(f'Bot connecté en tant que {client.user}')
    load_config()
    await tree.sync()
    logger.info("Arbre de commandes synchronisé")
    check_new_tweets.start()
    logger.info("Tâche de vérification des tweets démarrée")

@client.event
async def on_error(event, *args, **kwargs):
    logger.exception(f"Erreur sur l'événement {event}")

logger.info("Démarrage du bot")
client.run(TOKEN)