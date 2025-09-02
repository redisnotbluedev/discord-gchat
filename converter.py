from discord_markdown.discord_markdown import convert_to_html

def discord_to_google(text):
	html = convert_to_html(text)
	html = html.replace("</p>", "\n").replace("<p>", "")
	
	
	return text

def google_to_discord(text):
	return text