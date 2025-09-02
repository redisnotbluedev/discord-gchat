from flask import Flask
import threading
import os

import main as bot_module

app = Flask(__name__)

@app.route("/")
def health_check():
	return {"status": "alive", "bot_ready": bot_module.is_ready}, 200

@app.route("/health")
def detailed_health():
	return {
		"status": "healthy",
		"bot_connected": bot_module.is_ready,
		"guild_count": len(bot_module.bot.guilds) if bot_module.is_ready else 0,
		"latency": round(bot_module.bot.latency * 1000, 2) if bot_module.is_ready else None
	}, 200

def run_bot():
	"""Run the Discord bot"""
	try:
		bot_module.bot.run(bot_module.bot_token)
	except Exception as e:
		print(f"Bot crashed: {e}")

# Start bot in background thread when module loads
bot_thread = threading.Thread(target=run_bot)
bot_thread.daemon = True
bot_thread.start()

if __name__ == "__main__":
	# For development
	app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))