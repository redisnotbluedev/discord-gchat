import os
import json
import logging
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Supabase setup
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
	raise ValueError("Missing SUPABASE_URL or SUPABASE_ANON_KEY environment variables")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def load_settings():
	"""Load settings from Supabase"""
	try:
		result = supabase.table("bot_config").select("data").eq("id", 1).execute()
		
		if result.data and len(result.data) > 0:
			return json.loads(result.data[0]["data"])
		else:
			logger.warning("No settings found in database, using defaults")
			return {}
	except Exception as e:
		logger.error(f"Error loading settings: {e}")
		raise

def save_settings(settings):
	"""Save settings to Supabase"""
	try:
		data = {"id": 1, "data": json.dumps(settings, indent=2)}
		supabase.table("bot_config").upsert(data).execute()
		logger.debug("Settings saved to database")
	except Exception as e:
		logger.error(f"Error saving settings: {e}")
		raise

def migrate_from_json():
	"""One-time migration from config.json"""
	if os.path.exists("config.json"):
		try:
			with open("config.json") as f:
				settings = json.load(f)
			save_settings(settings)
			logger.info("Successfully migrated config.json to database")
			return True
		except Exception as e:
			logger.error(f"Migration failed: {e}")
			return False
	return False

if __name__ == "__main__":
	print(migrate_from_json())