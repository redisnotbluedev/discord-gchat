import asyncio
import io
import json
import logging
import os
import urllib.parse

import requests
from dotenv import load_dotenv
from database import load_settings, save_settings, migrate_from_json
from converter import google_to_discord, discord_to_google

import discord
from discord import TextChannel, SyncWebhook, app_commands
from discord.ext import commands

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

DEBUG = False

def save_data():
	save_settings(settings)

def get_chat_client(scopes):
	global settings
	creds = Credentials.from_authorized_user_info(json.loads(settings.get("auth_json", "{}")), scopes)

	if not creds or not creds.valid or not set(scopes).issubset(creds.scopes):
		if creds and creds.expired and creds.refresh_token:
			creds.refresh(Request())
		else:
			flow = InstalledAppFlow.from_client_config(json.loads(os.environ.get("GOOGLE_OAUTH")), scopes=scopes)

			try:
				creds = flow.run_local_server(port=54646, open_browser=False)
			except Exception as e:
				logger.warning("Local server auth failed:", e)
				auth_url, _ = flow.authorization_url(prompt="consent")
				logger.info("Go to this URL and approve access:\n" + auth_url)

				redirect_resp = input("Paste the FULL redirect URL here:\n").strip()

				query = urllib.parse.urlparse(redirect_resp).query
				params = urllib.parse.parse_qs(query)
				code = params.get("code")[0]

				token_data = flow.fetch_token(code=code)
				creds = Credentials(
					token=token_data['access_token'],
					refresh_token=token_data.get('refresh_token'),
					token_uri=flow.client_config['token_uri'],
					client_id=flow.client_config['client_id'],
					client_secret=flow.client_config.get('client_secret'),
					scopes=scopes
				)

		settings["auth_json"] = creds.to_json()
		save_data()

	return build("chat", "v1", credentials=creds)

def chat_get_messages(client, space_id, filter, page_size=1000):
	response = client.spaces().messages().list(
		parent=space_id,
		filter=filter,
		pageSize=page_size
	).execute()

	return response.get("messages", [])

async def chat_send_message(client, space_id, text, attachments=[]):
	try:
		def sync_send():
			message = {
				"text": text,
				"attachment": attachments
			}
			response = client.spaces().messages().create(parent=space_id, body=message).execute()
			return response["name"]
		
		return await asyncio.to_thread(sync_send)
	except Exception as e:
		logger.error(f"Failed to send to Google Chat: {e}")
		return e

async def chat_poll_messages():
	global settings
	await bot.wait_until_ready()
	
	while not bot.is_closed():
		try:
			filter = f'create_time > "{settings["poll_timestamp"]}"'
			messages = await asyncio.to_thread(
				chat_get_messages, client, mgg, filter, 1000
			)
			
			if not messages:
				logger.debug("Found 0 messages.")
			else:
				for message in messages:
					logger.info(f"[CHAT] {message["sender"]["name"]}: {message.get("formattedText", "<empty message>")}")
			
			for msg in messages:
				try:
					content = msg.get("formattedText") or ""
					content = google_to_discord(content)
					author = msg["sender"]["name"]
					if author in settings["blocked_users"]:
						settings["poll_timestamp"] = msg["createTime"]
						continue
					user = settings["users"].get(author, {"name": author})
					user["id"] = author
					if await process_commands(content, user):
						settings["poll_timestamp"] = msg["createTime"]
						continue
					if author == "users/100576874085867438126" and DEBUG:
						logger.debug(json.dumps(msg, indent=4))
					attachments = msg.get("attachment", [])
					media = []
					for attach in attachments:
						path = os.path.join("downloads", attach["contentName"])
						# Wrap download in a thread
						def download_attachment():
							req = client.media().download_media(
								resourceName=attach["attachmentDataRef"]["resourceName"]
							)
							file = io.BytesIO()
							downloader = MediaIoBaseDownload(file, req)
							done = False
							while not done:
								status, done = downloader.next_chunk()
							with open(path, "wb") as f:
								f.write(file.getbuffer())
							return path
						saved_path = await asyncio.to_thread(download_attachment)
						logger.info("Saved file " + saved_path)
						media.append(saved_path)
					if "profile" in user:
						await disc_send_message(content, media, user["name"], user["profile"])
					else:
						await disc_send_message(content, media, user["name"])
				
				except Exception as e:
					logger.error(f"Failed to process message {msg.get('name', 'unknown')}: {e}")
					# Continue processing other messages
				finally:
					# Always update timestamp, even if processing failed
					settings["poll_timestamp"] = msg["createTime"]
			
			save_data()
			await asyncio.sleep(0.5)
			
		except Exception as e:
			logger.error(f"Error in chat polling loop: {e}")
			# Wait a bit longer before retrying to avoid spam
			await asyncio.sleep(0.2)

async def chat_upload_media(client, space, attachment):
	def sync_upload(client, space, attachment):
		resp = requests.get(attachment.url)
		resp.raise_for_status()
		path = os.path.join("downloads", attachment.filename)
		
		with open(path, "wb") as f:
			f.write(resp.content)
		logger.info("Saved file " + path)
		media = MediaFileUpload(path, mimetype=attachment.content_type)

		attachment_uploaded = client.media().upload(
			parent=space,
			body={"filename": path},
			media_body=media
		).execute()

		return attachment_uploaded
	
	return await asyncio.to_thread(sync_upload)

async def disc_send_message(
	text: str = "",
	attachments: list[str] | None = None,
	username: str = "Google Chat",
	profile: str = "https://developers.google.com/static/workspace/chat/images/quickstart-app-avatar.png",
):
	if webhook is None:
		# optional: log or raise
		return

	files = []
	try:
		if attachments:
			for path in attachments:
				# path must exist on disk
				files.append(discord.File(path))

		# Avoid sending empty string as content; Discord rejects completely empty payloads
		content = text if text else None

		webhook.send(
			content=content,
			username=username,
			avatar_url=profile,
			files=files if files else []
		)
	finally:
		for f in files:
			try:
				f.close()
			except Exception:
				pass

async def process_commands(text, user):
	global settings
	if not text.startswith("!"):
		return False
	command = text[1:].split(" ")[0]
	args = text.split(" ")[1:]

	response = ""
	match command:
		case "help":
			response = "Commands\n\t`!hello` — Debug command to say hello to the bot\n\t`!user set <profile link> <name>` — Set your name and profile picture on Discord"
		case "hello":
			response = f"Hello, {user["name"]}!"
		case "user":
			if args[0] == "set" and len(args) > 2:
				name = " ".join(args[2:])
				settings["users"][user["id"]] = {
					"name": name,
					"profile": args[1]
				}
				save_data()
				response = f"Successfully changed your Discord name to {name} and your <{args[1]}|profile picture>."
		case _:
			await chat_send_message(client, mgg, "❌ Invalid command.")
			return
	
	await chat_send_message(client, mgg, response)
	return True

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
	global is_ready
	bot.tree.add_command(users_group)
	bot.tree.add_command(settings_group)
	await bot.tree.sync()
	
	logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
	bot.loop.create_task(chat_poll_messages())
	is_ready = True

@bot.tree.command(name="hello", description="Debug command.")
async def hello(interaction: discord.Interaction):
	await interaction.response.send_message(f"Hello, {interaction.user.mention}!")

users_group = app_commands.Group(name="user", description="Configure user settings")

@users_group.command(name="block", description="Block a user from being bridged.")
@app_commands.describe(user_id="The user being blocked.")
async def block_user(interaction: discord.Interaction, user_id: str):
	global settings

	if not interaction.user.guild_permissions.administrator:
		await interaction.response.send_message(
			"❌ You are not allowed to use this command.", ephemeral=True
		)
		return
	
	if not user_id.startswith("users/"):
		await interaction.response.send_message(
			"❌ Invalid User ID.", ephemeral=True
		)
		return
	
	settings["blocked_users"].append(user_id)
	save_data()

	await interaction.response.send_message(
		f"✅ Successfully blocked `{user_id}`."
	)

@users_group.command(name="unblock", description="Unblock a user from being bridged.")
@app_commands.describe(user_id="The user being unblocked.")
async def unblock_user(interaction: discord.Interaction, user_id: str):
	global settings

	if not interaction.user.guild_permissions.administrator:
		await interaction.response.send_message(
			"❌ You are not allowed to use this command.", ephemeral=True
		)
		return
	
	if not user_id.startswith("users/"):
		await interaction.response.send_message(
			"❌ Invalid User ID.", ephemeral=True
		)
		return
	
	try:
		settings["blocked_users"].remove(user_id)
	except ValueError:
		await interaction.response.send_message(
			"❌ That user is not blocked.", ephemeral=True
		)
		return
	save_data()

	await interaction.response.send_message(
		f"✅ Successfully unblocked `{user_id}`."
	)

@users_group.command(name="find", description="Return a list of the users with that name")
@app_commands.describe(user="The user's name to search for.")
async def find_user(interaction: discord.Interaction, user: str):
	matches = {}
	for user_id, user_data in settings["users"].items():
		if user.lower() in user_data["name"].lower():
			matches[user_id] = user_data

	if not matches:
		await interaction.response.send_message(
			"❌ Could not find any users with that name."
		)
	else:
		text = ""
		for match_id, match in matches.items():
			text += "\nName: " + match["name"]
			text += "\nUser ID: `" + match_id + "`\n"
		
		await interaction.response.send_message(
			"✅ Found " + str(len(matches)) + " user(s):\n" + text
		)

@users_group.command(name="list", description="List all users.")
async def list_users(interaction: discord.Interaction):
	text = ""
	for user_id, user in settings["users"].items():
		text += "\nName: " + user["name"]
		text += "\nUser ID: `" + user_id + "`\n"
	
	await interaction.response.send_message(text)

@users_group.command(name="edit", description="Add or edit a user.")
@app_commands.describe(user_id="The User ID of the user to be added/edited.", name="The new name for the user.", profile="A link to the profile picture of the user.")
async def edit_user(interaction: discord.Interaction, user_id: str, name: str, profile: str):
	global settings

	#if not interaction.user.guild_permissions.administrator:
	#	await interaction.response.send_message(
	#		"❌ You are not allowed to use this command.", ephemeral=True
	#	)
	#	return
	
	if not user_id.startswith("users/"):
		await interaction.response.send_message(
			"❌ Invalid User ID.", ephemeral=True
		)
		return

	settings["users"][user_id] = {
		"name": name,
		"profile": profile
	}
	save_data()
	await interaction.response.send_message(
		f"✅ Successfully edited user `{user_id}`."
	)

settings_group = app_commands.Group(name="set", description="Configure bot settings")

@settings_group.command(name="space", description="Set which Google Chat Space the bot monitors.")
@app_commands.describe(space_id="The Google Chat Space ID to monitor.")
async def set_space(interaction: discord.Interaction, space_id: str):
	global mgg, settings

	if not interaction.user.guild_permissions.administrator:
		await interaction.response.send_message(
			"❌ You are not allowed to use this command.", ephemeral=True
		)
		return

	if not space_id.startswith("spaces/"):
		await interaction.response.send_message(
			"❌ Invalid Space ID.", ephemeral=True
		)
		return

	mgg = space_id
	settings["gchat_space"] = space_id
	save_data()
	await interaction.response.send_message(
		f"✅ Successfully changed Space ID to `{space_id}`."
	)

@settings_group.command(name="channel", description="Set the Discord channel for Google Chat messages.")
@app_commands.describe(channel="The text channel for the bot to send messages to.")
async def set_channel(interaction: discord.Interaction, channel: TextChannel):
	global discord_channel, webhook, settings

	if not interaction.user.guild_permissions.administrator:
		await interaction.response.send_message(
			"❌ You are not allowed to use this command.", ephemeral=True
		)
		return

	discord_channel = channel.id
	settings["disc_channel"] = channel.id
	save_data()

	webhook = webhook.edit(
		channel=channel,
		name="Google Chat",
		avatar=None,
		reason="From command by " + str(interaction.user.id)
	)

	await interaction.response.send_message(
		f"✅ Successfully set the bot channel to {channel.mention}."
	)

@bot.event
async def on_message(message):
	if message.author == bot.user or message.webhook_id is not None:
		return

	if message.channel.id == discord_channel:
		logger.info(f"[DISCORD] {message.author}: {message.content}")
		text = message.content
		text = discord_to_google(text)
		attachments = message.attachments or []
		media = []
		att = []
		for attach in attachments:
			mime = attach.content_type
			if mime.split("/")[0] == "image":
				image = await chat_upload_media(client, mgg, attach)
				media.append(image)
			else:
				att.append(attach.url)
		
		if att:
			text += f"\nContains {len(att)} non-image attachment(s): "
			for attach in range(len(att)):
				text += f"<{att[attach]}|{attach + 1}> "
		
		sent_successfully = False
		attempts = 0
		while not sent_successfully and attempts < 5:
			try:
				resp = await chat_send_message(client=client, space_id=mgg, text=f"{message.author.display_name}: {text}", attachments=media)
				if not isinstance(resp, Exception):
					sent_successfully = True
					break
			except Exception as e:
				logger.error(f"Send attempt {attempts + 1} failed: {e}")
			
			attempts += 1
			if attempts < 5:
				await asyncio.sleep(1)  # Wait between retries

		if not sent_successfully:
			logger.error("Failed to send to Google Chat after 5 attempts")

is_ready = False

logging.getLogger().handlers.clear()
discord.utils.setup_logging(level=logging.DEBUG)
logger = logging.getLogger(__name__)

load_dotenv()
bot_token = os.environ.get("BOT_TOKEN")
settings = load_settings()

client = get_chat_client(["https://www.googleapis.com/auth/chat.messages"])

last_timestamp = settings["poll_timestamp"]
mgg = settings["gchat_space"]
discord_channel = settings["disc_channel"]
webhook = SyncWebhook.partial(id=settings["webhook"]["id"], token=settings["webhook"]["token"], bot_token=bot_token)

if __name__ == "__main__":
	try:
		bot.run(bot_token)
	except KeyboardInterrupt:
		logger.info("Bot stopped by user")
	except SystemExit as e:
		logger.error(f"System exit called: {e}")
	except Exception as e:
		logger.error(f"Bot crashed: {e}", exc_info=True)
	finally:
		logger.info("Bot process ending")