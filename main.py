import discord
from datetime import timedelta, datetime
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

class MyClient(discord.Client):
    async def on_ready(self):
        print(f'Logged in as {self.user} (ID: {self.user.id})')
        print('------')

    async def on_message(self, message):
        if message.author == self.user:
            return
        
        if not message.content.startswith('-ls'):
            return
        
        print(f'Message from {message.author}: {message.content}')
        
        # bot sends a message back to the channel
        await message.channel.send('Hello!')
        
        if message.content.startswith('-ls scrape'):
            # Collect all messages in the channel from the past day
            links = []
            
            async for msg in message.channel.history(limit=1000, after=datetime.today() - timedelta(days=1)):
                if msg.author != self.user and 'http' in msg.content:
                    # Check if the link is a YouTube link
                    for word in msg.content.split():
                        if word.startswith('http') and ('youtube.com' in word or 'youtu.be' in word):
                            links.append(word)
            
            # Compile the collected links into a YouTube playlist
            if links:
                
                # Scopes required for YouTube Data API
                SCOPES = ['https://www.googleapis.com/auth/youtube.force-ssl']

                # Authenticate and build the YouTube API client
                def get_authenticated_service():
                    flow = InstalledAppFlow.from_client_secrets_file(
                        'client_secret.json', SCOPES)  # Replace with the path to your downloaded JSON file
                    credentials = flow.run_local_server(port=0)
                    return build('youtube', 'v3', credentials=credentials)
                
                # Create a YouTube API client
                youtube = get_authenticated_service()
                
                # Get today's date in the format YYYY-MM-DD
                target_date = (datetime.today()-timedelta(days=1)).strftime("%Y-%m-%d")
                
                # Create a new playlist with dynamic title
                playlist_request = youtube.playlists().insert(
                    part="snippet,status",
                    body={
                        "snippet": {
                            "title": f"HIVE {message.channel.name} {target_date}",  # Dynamic title
                            "description": f"A playlist created from links shared in {message.channel.name} on {target_date}.",
                            "tags": ["Discord", "YouTube", "Playlist"],
                            "defaultLanguage": "en"
                        },
                        "status": {
                            "privacyStatus": "private"
                        }
                    }
                )
                playlist_response = playlist_request.execute()
                # print(playlist_response) # Debugging line to check the response
                playlist_id = playlist_response["id"]
                
                # Add each link to the playlist
                for link in links:
                    video_id = link.split("v=")[-1] if "youtube.com" in link else link.split("/")[-1]
                    youtube.playlistItems().insert(
                        part="snippet",
                        body={
                            "snippet": {
                                "playlistId": playlist_id,
                                "resourceId": {
                                    "kind": "youtube#video",
                                    "videoId": video_id
                                }
                            }
                        }
                    ).execute()
                
                # Send the playlist link back to the channel
                playlist_url = f"https://www.youtube.com/playlist?list={playlist_id}"
                await message.channel.send(
                    f"**Playlist Title**: HIVE {message.channel.name} {target_date}\n"
                    f"**Description**: A playlist created from links shared in {message.channel.name} on {target_date}.\n"
                    f"**Playlist Link**: {playlist_url}"
                )
            else:
                await message.channel.send("No links found in the past day.")

         
intents = discord.Intents.default()
intents.message_content = True

client = MyClient(intents=intents)
client.run('DISCORD_BOT_TOKEN')  # Replace with your bot's token