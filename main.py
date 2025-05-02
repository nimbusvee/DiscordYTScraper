import discord
from datetime import timedelta, datetime, timezone
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import re
import time

class MyClient(discord.Client):
    async def on_ready(self):
        print(f'Logged in as {self.user} (ID: {self.user.id})')
        print('------')

    async def on_message(self, message):
        try:
            if message.author == self.user:
                return
            
            if not message.content.startswith('-ls'):
                return
            
            print(f'Message from {message.author}: {message.content}')
            
            if message.content.startswith('-ls scrape'):
            # Extract the channel name from the command
                command_parts = message.content.split()
                if len(command_parts) < 3:
                    await message.channel.send("Please specify a channel name. Example: `-ls scrape vtuber-music`")
                    return

                target_channel_name = command_parts[2]  # Get the channel name from the command

                # Find the target channel in the server
                target_channel = discord.utils.get(message.guild.text_channels, name=target_channel_name)
                if not target_channel:
                    await message.channel.send(f"Channel `{target_channel_name}` not found.")
                    return

                await message.channel.send(f"Scraping links from `{target_channel_name}`...")
                
                # Define Japan Standard Time (JST) timezone
                jst = timezone(timedelta(hours=9))

                # Get current time in JST
                jst_now = datetime.now(jst)

                # Extract just the date
                jst_extract_date = jst_now.date()
                
                jst_start_of_day = datetime.combine(jst_extract_date, datetime.min.time(), jst)

                # Subtract one day to get the datetime for the previous day
                jst_target_date = jst_start_of_day - timedelta(days=1)
                print(jst_target_date)  # Debugging line to check the target date
                
                # Format the date for the playlist title
                title_date = jst_target_date.strftime("%Y-%m-%d")
            
                
                # Collect all messages in the channel from the past day
                links = []
                
                thread = target_channel if isinstance(target_channel, discord.Thread) else None
                target_channel = thread.parent if thread else target_channel
                
                await message.channel.send("Scraping links...")

                async for msg in target_channel.history(limit=1000, after=jst_target_date, before=jst_start_of_day):
                    if msg.author != self.user and 'http' in msg.content:
                        # Check if the link is a YouTube link
                        for word in msg.content.split():
                            if word.startswith('http') and ('youtube.com' in word or 'youtu.be' in word):
                                links.append(word)
                
                # Compile the collected links into a YouTube playlist
                if links:
                    print(f"\nCollected links: {links}\n")  # Debugging line to check the collected links
                    
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
                    
                    # Function to extract video ID from a YouTube URL
                    def extract_video_id(link):
                        # Regular expression to match YouTube video IDs
                        match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11})", link)
                        return match.group(1) if match else None
                    
                    # Create a new playlist with dynamic title
                    playlist_request = youtube.playlists().insert(
                        part="snippet,status",
                        body={
                            "snippet": {
                                "title": f"{target_channel.guild.name} {target_channel.name} {title_date}",  # Dynamic title with server name
                                "description": f"A playlist created from links shared in {target_channel.guild.name}'s {target_channel.name} on {title_date}.",
                                "tags": ["Discord", "YouTube", "Playlist"],
                                "defaultLanguage": "en"
                            },
                            "status": {
                                "privacyStatus": "unlisted"
                            }
                        }
                    )
                    playlist_response = playlist_request.execute()
                    # print(playlist_response) # Debugging line to check the response
                    playlist_id = playlist_response["id"]
                    
                    # Add each link to the playlist
                    for link in links:
                        video_id = extract_video_id(link)  # Use the new function to extract the video ID
                        if video_id:  # Only proceed if a valid video ID is found
                            try:
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
                                time.sleep(1)  # Add a 1-second delay between requests
                            except Exception as e:
                                print(f"Failed to add video {video_id}: {e}")
                        else:
                            print(f"Invalid YouTube link: {link}")
                    
                    # Send the playlist link back to the channel
                    playlist_url = f"https://www.youtube.com/playlist?list={playlist_id}"
                    print(f"Playlist created: {playlist_url}")  # Debugging line to check the playlist URL
                    await message.channel.send(
                        f"**Playlist Title**: {target_channel.guild.name} {target_channel.name} {title_date}\n"
                        f"**Description**: A playlist created from links shared in {target_channel.guild.name}'s {target_channel.name} on {title_date}.\n"
                        f"**Playlist Link**: {playlist_url}"
                    )
                else:
                    await message.channel.send("No links found in the past day.")
        except Exception as e:
            print(f"An error occurred: {e}")
            await message.channel.send("An error occurred while processing your request. Please try again.")
         
intents = discord.Intents.default()
intents.message_content = True

client = MyClient(intents=intents)
client.run('DISCORD_BOT_TOKEN')  # Replace with your bot's token