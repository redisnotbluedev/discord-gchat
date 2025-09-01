from googleapiclient.http import MediaFileUpload
from main import get_chat_client

SCOPES = ["https://www.googleapis.com/auth/chat.messages"]

def main():
	service = get_chat_client(SCOPES)

	media = MediaFileUpload('image.png', mimetype='image/png')

	attachment_uploaded = service.media().upload(
		parent='spaces/AAAAxz1Ylrs',
		body={'filename': 'test_image.png'},
		media_body=media
	).execute()

	print(attachment_uploaded)

	result = service.spaces().messages().create(
		parent='spaces/AAAAxz1Ylrs',
		body={
			'text': 'TEST',
			'attachment': [attachment_uploaded]
		}

	).execute()

	print(result)

if __name__ == '__main__':
	main()
