import discord
from discord.ext import commands
from discord import app_commands
from datetime import timedelta, datetime, timezone
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError # Import HttpError
import re
import os
from google.auth.transport.requests import Request
import time # For time.sleep
import random # For jitter in backoff
import traceback # For detailed error logging

# Define intents
intents = discord.Intents.default()
intents.message_content = True

# --- Retry Logic Helper Function ---
async def execute_with_retry(request, max_retries=5, initial_backoff=1.0, max_backoff=16.0):
    """
    Executes a Google API request with exponential backoff and jitter for retries.
    Args:
        request: The API request object to execute (e.g., youtube.playlists().insert(...)).
        max_retries: Maximum number of retries.
        initial_backoff: Initial wait time in seconds for the first retry.
        max_backoff: Maximum wait time in seconds for any single retry.
    Returns:
        The API response if successful.
    Raises:
        HttpError: If the request fails after all retries or for a non-retryable error.
    """
    backoff_time = initial_backoff
    for attempt in range(max_retries + 1):
        try:
            return request.execute()
        except HttpError as e:
            is_retryable_409 = (e.resp.status == 409 and
                                any('SERVICE_UNAVAILABLE' in detail.get('reason', '').upper()
                                    for detail in getattr(e, 'error_details', []) if isinstance(detail, dict)))

            if attempt == max_retries or not (is_retryable_409 or e.resp.status in [429, 500, 502, 503, 504]):
                print(f"Non-retryable error or max retries reached on attempt {attempt + 1}: {e}")
                raise e

            wait_time = min(backoff_time + random.uniform(0, 1), max_backoff)
            print(f"Retryable error encountered (Attempt {attempt + 1}/{max_retries + 1}). Retrying in {wait_time:.2f} seconds: {e}")
            time.sleep(wait_time)
            backoff_time *= 2
        except Exception as e:
            print(f"An unexpected error occurred during API request execution: {e}")
            raise

# Create the bot instance
class MyClient(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await self.tree.sync()

    async def on_ready(self):
        print(f'Logged in as {self.user} (ID: {self.user.id})')
        print('------')

    async def on_message(self, message):
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
    # ADDED PRINT STATEMENT HERE
    print(f"\n--- Scrape command initiated by {interaction.user} (ID: {interaction.user.id}) ---")
    print(f"Guild: '{interaction.guild.name}' (ID: {interaction.guild.id})")
    print(f"Target Channel: '{channel_name}'")
    print(f"Timestamp: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print("---")

    try:
        await interaction.response.defer()

        target_channel = discord.utils.get(interaction.guild.text_channels, name=channel_name)
        if not target_channel:
            await interaction.followup.send(f"Channel `{channel_name}` not found.")
            print(f"Error: Channel '{channel_name}' not found in guild '{interaction.guild.name}'.")
            return

        print(f"Target channel object found: {target_channel.name} (ID: {target_channel.id})")

        jst = timezone(timedelta(hours=9))
        jst_now = datetime.now(jst)
        jst_extract_date = jst_now.date()
        jst_start_of_day = datetime.combine(jst_extract_date, datetime.min.time(), jst)
        jst_target_date = jst_start_of_day - timedelta(days=1)
        title_date = jst_target_date.strftime("%Y-%m-%d")

        print(f"Scraping messages from {jst_target_date.strftime('%Y-%m-%d %H:%M:%S %Z')} to {jst_start_of_day.strftime('%Y-%m-%d %H:%M:%S %Z')}")

        links = []
        async for msg in target_channel.history(limit=1000, after=jst_target_date, before=jst_start_of_day):
            if msg.author != client.user and 'http' in msg.content:
                for word in msg.content.split():
                    # Updated regex for more general YouTube link matching
                    if re.match(r'https?:\/\/(www\.)?(youtube\.com\/(watch\?v=|embed\/|v\/)|youtu\.be\/)[a-zA-Z0-9_-]{11}', word):
                        links.append(word)
        
        # Remove duplicate links before further processing
        unique_links = sorted(list(set(links)))
        if len(links) != len(unique_links):
            print(f"Removed {len(links) - len(unique_links)} duplicate links from initial scrape. Processing {len(unique_links)} unique links.")
        links = unique_links

        if links:
            print(f"\nCollected unique links: {links}\n")

            SCOPES = ['https://www.googleapis.com/auth/youtube.force-ssl']

            def get_authenticated_service():
                creds = None
                if os.path.exists('token.json'):
                    creds = Credentials.from_authorized_user_file('token.json', SCOPES)
                if not creds or not creds.valid:
                    if creds and creds.expired and creds.refresh_token:
                        creds.refresh(Request())
                    else:
                        flow = InstalledAppFlow.from_client_secrets_file(
                            'client_secret.json', SCOPES)
                        creds = flow.run_local_server(port=0)
                    with open('token.json', 'w') as token_file:
                        token_file.write(creds.to_json())
                return build('youtube', 'v3', credentials=creds)

            youtube = get_authenticated_service()
            print("YouTube service authenticated.")

            def extract_video_id_or_playlist_id(link):
                # Regex for standard video links (v=, embed/, v/)
                video_match = re.search(r"(?:youtube\.com\/(?:[^\/]+\/.+\/|(?:v|e(?:mbed)?)\/|.*[?&]v=)|youtu\.be\/)([a-zA-Z0-9_-]{11})", link)
                # Regex for playlist links (list=)
                playlist_match = re.search(r"list=([0-9A-Za-z_-]+)", link)

                vid = video_match.group(1) if video_match else None
                pid = playlist_match.group(1) if playlist_match else None
                return {"video_id": vid, "playlist_id": pid}


            playlist_title = f"{channel_name} {title_date}"
            playlist_description = f"A playlist created from links shared in {interaction.guild.name}'s {channel_name} on {title_date}."
            print(f"Attempting to create playlist with title: '{playlist_title}'")

            playlist_request_body = {
                "snippet": {
                    "title": playlist_title,
                    "description": playlist_description,
                    "tags": ["Discord", "YouTube", "Playlist"],
                    "defaultLanguage": "en"
                },
                "status": {"privacyStatus": "unlisted"}
            }
            playlist_request_obj = youtube.playlists().insert(part="snippet,status", body=playlist_request_body)
            playlist_response = await execute_with_retry(playlist_request_obj)
            playlist_id = playlist_response["id"]
            print(f"Playlist created successfully. ID: {playlist_id}")

            added_video_ids = set()
            invalid_links_details = [] # Store details for invalid links

            for link_idx, link in enumerate(links):
                print(f"Processing link {link_idx + 1}/{len(links)}: {link}")
                ids = extract_video_id_or_playlist_id(link)
                video_id_to_add = ids["video_id"]
                source_playlist_id = ids["playlist_id"]

                if video_id_to_add:
                    if video_id_to_add not in added_video_ids:
                        try:
                            playlist_item_request_body = {
                                "snippet": {
                                    "playlistId": playlist_id,
                                    "resourceId": {"kind": "youtube#video", "videoId": video_id_to_add}
                                }
                            }
                            playlist_item_request_obj = youtube.playlistItems().insert(part="snippet", body=playlist_item_request_body)
                            await execute_with_retry(playlist_item_request_obj)
                            added_video_ids.add(video_id_to_add)
                            print(f"  Successfully added video ID: {video_id_to_add}")
                        except HttpError as e:
                            print(f"  Failed to add video ID {video_id_to_add} after retries: {e}")
                            invalid_links_details.append({'link': link, 'id': video_id_to_add, 'reason': str(e)})
                        except Exception as e:
                            print(f"  An unexpected error occurred while trying to add video ID {video_id_to_add}: {e}")
                            invalid_links_details.append({'link': link, 'id': video_id_to_add, 'reason': f"Unexpected: {str(e)}"})
                    else:
                        print(f"  Skipped duplicate video ID: {video_id_to_add}")

                elif source_playlist_id: # If it's a playlist link, and not a direct video link with a list parameter
                    print(f"  Processing as playlist link. Source Playlist ID: {source_playlist_id}")
                    try:
                        playlist_items_list_request = youtube.playlistItems().list(
                            part="snippet",
                            playlistId=source_playlist_id,
                            maxResults=50 # Consider pagination for >50 videos
                        )
                        playlist_items_response = await execute_with_retry(playlist_items_list_request)
                        videos_from_playlist_count = 0
                        for item_idx, item in enumerate(playlist_items_response.get("items", [])):
                            video_id_from_playlist = item["snippet"]["resourceId"]["videoId"]
                            print(f"    Item {item_idx + 1}: Video ID from source playlist: {video_id_from_playlist}")
                            if video_id_from_playlist not in added_video_ids:
                                try:
                                    playlist_item_request_body = {
                                        "snippet": {
                                            "playlistId": playlist_id,
                                            "resourceId": {"kind": "youtube#video", "videoId": video_id_from_playlist}
                                        }
                                    }
                                    playlist_item_request_obj = youtube.playlistItems().insert(part="snippet", body=playlist_item_request_body)
                                    await execute_with_retry(playlist_item_request_obj)
                                    added_video_ids.add(video_id_from_playlist)
                                    videos_from_playlist_count +=1
                                    print(f"      Successfully added video ID: {video_id_from_playlist} (from source playlist {source_playlist_id})")
                                except HttpError as e:
                                    print(f"      Failed to add video ID {video_id_from_playlist} from source playlist {source_playlist_id} after retries: {e}")
                                    invalid_links_details.append({'link': link, 'id': video_id_from_playlist, 'reason': f"From playlist {source_playlist_id}: {str(e)}"})

                                except Exception as e:
                                    print(f"      An unexpected error occurred for video ID {video_id_from_playlist} from playlist {source_playlist_id}: {e}")
                                    invalid_links_details.append({'link': link, 'id': video_id_from_playlist, 'reason': f"From playlist {source_playlist_id}, Unexpected: {str(e)}"})
                            else:
                                print(f"    Skipped duplicate video ID: {video_id_from_playlist} (from source playlist {source_playlist_id})")
                        if videos_from_playlist_count > 0:
                             print(f"  Added {videos_from_playlist_count} new videos from playlist {source_playlist_id}.")
                        else:
                            print(f"  No new videos added from playlist {source_playlist_id} (either empty, all duplicates, or errors).")

                    except HttpError as e:
                        print(f"  Failed to retrieve videos from source playlist {source_playlist_id} after retries: {e}")
                        invalid_links_details.append({'link': link, 'id': source_playlist_id, 'reason': f"Failed to list items: {str(e)}"})
                    except Exception as e:
                        print(f"  An unexpected error occurred while processing source playlist {source_playlist_id}: {e}")
                        invalid_links_details.append({'link': link, 'id': source_playlist_id, 'reason': f"Unexpected while listing: {str(e)}"})
                else: # Neither a video_id nor a source_playlist_id was clearly extracted
                    print(f"  Invalid or unhandled YouTube link format: {link}")
                    invalid_links_details.append({'link': link, 'id': 'N/A', 'reason': 'Invalid YouTube link format or no ID extracted.'})


            playlist_url = f"https://www.youtube.com/playlist?list={playlist_id}"
            print(f"--- Scrape command finished ---")
            print(f"Playlist created: {playlist_url}")
            if invalid_links_details:
                print("--- Invalid/Failed Links ---")
                for item in invalid_links_details:
                    print(f"  Link: {item['link']}, ID Attempted: {item['id']}, Reason: {item['reason']}")
                print("---")


            followup_message = (
                f"**Playlist Title**: {playlist_title}\n"
                f"**Description**: {playlist_description}\n"
                f"**Playlist Link**: {playlist_url}\n"
                f"Added {len(added_video_ids)} unique videos."
            )
            if invalid_links_details:
                followup_message += f"\nCould not process {len(invalid_links_details)} items/links (see bot logs for details)."

            await interaction.followup.send(followup_message)
        else:
            print("No YouTube links found in the specified channel for the given period.")
            await interaction.followup.send("No YouTube links found in the past day to create a playlist.")
    except HttpError as e:
        print(f"An API error occurred after all retries: {e}")
        traceback.print_exc()
        await interaction.followup.send("An API error occurred while processing your request after several retries. Please check the bot logs and try again later.")
    except Exception as e:
        print(f"An unexpected error occurred in the scrape command: {e}")
        traceback.print_exc()
        await interaction.followup.send("An unexpected error occurred while processing your request. Please check the bot logs.")


@scrape.autocomplete("channel_name")
async def channel_name_autocomplete(interaction: discord.Interaction, current: str):
    channels = interaction.guild.text_channels
    matching_channels = [
        app_commands.Choice(name=channel.name, value=channel.name)
        for channel in channels if current.lower() in channel.name.lower()
    ]
    return matching_channels[:25]

client.run('DISCORD_BOT_TOKEN')