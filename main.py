import discord
from datetime import timedelta, datetime, timezone
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
        
        if message.content == '-ls scrape':
            # Define Japan Standard Time (JST) timezone
            jst = timezone(timedelta(hours=9))

            # Get current time in JST
            jst_now = datetime.now(jst)

            # Extract just the date
            jst_extract_date = jst_now.date()
            
            jst_start_of_day = datetime.combine(jst_extract_date, datetime.min.time(), jst)

            # Subtract one day to get the datetime for the previous day
            jst_target_date = jst_start_of_day - timedelta(days=1)
            
            # Format the date for the playlist title
            title_date = jst_target_date.strftime("%Y-%m-%d")
         
            
            # Collect all messages in the channel from the past day
            links = []
            
            async for msg in message.channel.history(limit=1000, after=jst_target_date):
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
                
                
                
                # Create a new playlist with dynamic title
                playlist_request = youtube.playlists().insert(
                    part="snippet,status",
                    body={
                        "snippet": {
                            "title": f"{message.guild.name} {message.channel.name} {title_date}",  # Dynamic title with server name
                            "description": f"A playlist created from links shared in {message.guild.name}'s {message.channel.name} on {title_date}.",
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
                    f"**Playlist Title**: {message.guild.name} {message.channel.name} {title_date}\n"
                    f"**Description**: A playlist created from links shared in {message.guild.name}'s {message.channel.name} on {title_date}.\n"
                    f"**Playlist Link**: {playlist_url}"
                )
            else:
                await message.channel.send("No links found in the past day.")

         
intents = discord.Intents.default()
intents.message_content = True

client = MyClient(intents=intents)
client.run('')  # Replace with your bot's token