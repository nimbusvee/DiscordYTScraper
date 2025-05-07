import discord
from discord.ext import commands
from discord import app_commands
from datetime import timedelta, datetime, timezone
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import re
import os
from google.auth.transport.requests import Request

# Define intents
intents = discord.Intents.default()
intents.message_content = True

# Create the bot instance
class MyClient(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        # Sync the slash commands with Discord
        await self.tree.sync()

    async def on_ready(self):
        print(f'Logged in as {self.user} (ID: {self.user.id})')
        print('------')
        
    async def on_message(self, message):
        # Ignore messages from the bot itself
        if message.author == self.user:
            return

        if message.content.startswith("-ls"):
            print(f'Message from {message.author} in server {message.guild.name}: {message.content}')
            
            await message.channel.send("please use /scrape command")
        

# Create the bot instance
client = MyClient()

# Slash command to scrape links and create a playlist
@client.tree.command(name="scrape", description="Scrape YouTube links from a channel and create a playlist.")
@app_commands.describe(channel_name="Select the channel to scrape links from")
async def scrape(interaction: discord.Interaction, channel_name: str):
    try:
        # Defer the response to avoid timeout
        await interaction.response.defer()

        # Find the target channel in the server
        target_channel = discord.utils.get(interaction.guild.text_channels, name=channel_name)
        if not target_channel:
            await interaction.followup.send(f"Channel `{channel_name}` not found.")
            return

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

        async for msg in target_channel.history(limit=1000, after=jst_target_date, before=jst_start_of_day):
            if msg.author != client.user and 'http' in msg.content:
                # Check if the link is a YouTube link
                for word in msg.content.split():
                    if word.startswith('http://', 'https://') and ('youtube.com' in word or 'youtu.be' in word):
                        links.append(word)

        # Compile the collected links into a YouTube playlist
        if links:
            print(f"\nCollected links: {links}\n")  # Debugging line to check the collected links

            # Scopes required for YouTube Data API
            SCOPES = ['https://www.googleapis.com/auth/youtube.force-ssl']

            # Authenticate and build the YouTube API client
            def get_authenticated_service():
                creds = None

                # Check if token.json exists
                if os.path.exists('token.json'):
                    # Load saved credentials
                    creds = Credentials.from_authorized_user_file('token.json', SCOPES)

                # If no valid credentials are available, prompt the user to log in
                if not creds or not creds.valid:
                    if creds and creds.expired and creds.refresh_token:
                        creds.refresh(Request())  # Refresh the access token
                    else:
                        flow = InstalledAppFlow.from_client_secrets_file(
                            'client_secret.json', SCOPES)  # Replace with your client secrets file
                        creds = flow.run_local_server(port=0)

                    # Save the credentials for the next run
                    with open('token.json', 'w') as token_file:
                        token_file.write(creds.to_json())

                return build('youtube', 'v3', credentials=creds)

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
                        "title": f"{interaction.guild.name} {channel_name} {title_date}",  # Dynamic title with server name
                        "description": f"A playlist created from links shared in {interaction.guild.name}'s {channel_name} on {title_date}.",
                        "tags": ["Discord", "YouTube", "Playlist"],
                        "defaultLanguage": "en"
                    },
                    "status": {
                        "privacyStatus": "unlisted"
                    }
                }
            )
            playlist_response = playlist_request.execute()
            playlist_id = playlist_response["id"]

            # Add each link to the playlist
            invalid_links = []

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
                    except Exception as e:
                        print(f"Failed to add video {video_id}: {e}")
                else:
                    invalid_links.append(link)
                    print(f"Invalid YouTube link: {link}")

            # Send the playlist link back to the channel
            playlist_url = f"https://www.youtube.com/playlist?list={playlist_id}"
            print(f"Playlist created: {playlist_url}")  # Debugging line to check the playlist URL
            print(f"Invalid links: {invalid_links}")  # Debugging line to check invalid links

            await interaction.followup.send(
                f"**Playlist Title**: {interaction.guild.name} {channel_name} {title_date}\n"
                f"**Description**: A playlist created from links shared in {interaction.guild.name}'s {channel_name} on {title_date}.\n"
                f"**Playlist Link**: {playlist_url}"
            )
        else:
            await interaction.followup.send("No links found in the past day.")
    except Exception as e:
        print(f"An error occurred: {e}")
        await interaction.followup.send("An error occurred while processing your request. Please try again.")


# Autocomplete function for channel names
@scrape.autocomplete("channel_name")
async def channel_name_autocomplete(interaction: discord.Interaction, current: str):
    # Get all text channels in the guild
    channels = interaction.guild.text_channels

    # Filter channels based on the user's input
    matching_channels = [
        app_commands.Choice(name=channel.name, value=channel.name)
        for channel in channels if current.lower() in channel.name.lower()
    ]

    # Return up to 25 matching channels (Discord's limit for choices)
    return matching_channels[:25]

# Run the bot
client.run('DISCORD_BOT_TOKEN')  # Replace with your bot's token

