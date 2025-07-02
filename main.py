import discord
from discord.ext import commands
from datetime import timedelta, datetime, timezone
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError # Import HttpError
from googleapiclient.http import MediaFileUpload # For YouTube Upload
import re
import os
from google.auth.transport.requests import Request
from dotenv import load_dotenv # For loading environment variables
import time # For time.sleep
import random # For jitter in backoff
import traceback # For detailed error logging
import subprocess # For running yt-dlp
import tempfile # For temporary file handling
import uuid # For unique temporary file names
from typing import Optional
from datetime import date as DateType  # avoid clash with variable name


# Define bot
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

# --- Twitter Media Download Helper ---
async def download_twitter_media(twitter_url, temp_dir):
    """Downloads video/audio from a Twitter URL using yt-dlp."""
    output_template = os.path.join(temp_dir, f"{uuid.uuid4()}.%(ext)s")
    command = [
        'yt-dlp',
        '--no-check-certificate',
        '-f', 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best', # Get best MP4, or best overall
        '--output', output_template,
        '--restrict-filenames',
        '--no-playlist', # Ensure only single video is downloaded if URL is part of a playlist context on Twitter (unlikely but safe)
        twitter_url
    ]
    print(f"Attempting to download Twitter media from: {twitter_url} with command: {' '.join(command)}")
    try:
        process = await asyncio.to_thread(subprocess.run, command, capture_output=True, text=True, check=True)
        # Find the actual downloaded file name (yt-dlp replaces %(ext)s)
        downloaded_files = [f for f in os.listdir(temp_dir) if f.startswith(os.path.basename(output_template).split('.')[0])]
        if downloaded_files:
            full_path = os.path.join(temp_dir, downloaded_files[0])
            print(f"Successfully downloaded Twitter media to: {full_path}")
            return full_path
        else:
            print(f"yt-dlp ran but no output file found. stdout: {process.stdout}, stderr: {process.stderr}")
            return None
    except FileNotFoundError:
        print("ERROR: yt-dlp command not found. Make sure it's installed and in your PATH.")
        return None
    except subprocess.CalledProcessError as e:
        print(f"ERROR: yt-dlp failed for URL {twitter_url}. Return code: {e.returncode}")
        print(f"yt-dlp stdout: {e.stdout}")
        print(f"yt-dlp stderr: {e.stderr}")
        return None
    except Exception as e:
        print(f"An unexpected error occurred during Twitter download: {e}")
        traceback.print_exc()
        return None

# --- YouTube Upload Helper ---
async def upload_video_to_youtube(youtube_service, file_path, title, description, privacy_status="unlisted"):
    """Uploads a video file to YouTube."""
    try:
        print(f"Attempting to upload '{file_path}' to YouTube with title '{title}'")
        body = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": ["Discord Bot Upload", "Twitter Media"], # Add relevant tags
                "categoryId": "22" # People & Blogs, adjust if needed
            },
            "status": {"privacyStatus": privacy_status}
        }
        media_body = MediaFileUpload(file_path, chunksize=-1, resumable=True)
        
        # Use a wrapper for execute_with_retry if direct request object is needed,
        # otherwise, for resumable uploads, google-api-python-client handles some retries internally.
        # For simplicity, direct execute here. For more robustness on the insert metadata call, wrap it.
        request_obj = youtube_service.videos().insert(
            part=",".join(body.keys()),
            body=body,
            media_body=media_body
        )
        
        # Wrap the execution of the request object for the metadata part
        # The media_body upload has its own resumable logic.
        response = None
        backoff_time = 1.0
        max_retries = 3 # Retries for the initial insert request, not the media upload itself
        for attempt in range(max_retries + 1):
            try:
                response = request_obj.execute()
                break # Success
            except HttpError as e:
                if attempt == max_retries or e.resp.status not in [429, 500, 502, 503, 504]: # Non-retryable or max retries
                    print(f"Failed to upload video '{title}' after {attempt + 1} attempts. Error: {e}")
                    raise e
                wait_time = min(backoff_time + random.uniform(0, 1), 16.0)
                print(f"Upload API call failed for '{title}' (Attempt {attempt+1}), retrying in {wait_time:.2f}s: {e}")
                time.sleep(wait_time)
                backoff_time *= 2
            except Exception as e:
                print(f"An unexpected error during YouTube video insert execute for '{title}': {e}")
                raise

        if response and response.get("id"):
            print(f"Successfully uploaded video. Video ID: {response['id']}")
            return response["id"]
        else:
            print(f"Failed to upload video or extract ID. Response: {response}")
            return None
    except HttpError as e:
        print(f"HttpError during YouTube upload: {e}")
        if e.resp.status == 401: # Unauthorized
             print("ERROR: YouTube API authentication failed (401). Check your credentials and ensure the YouTube Data API v3 is enabled and you have the right scopes (including youtube.upload). You might need to delete token.json and re-authenticate.")
        elif e.resp.status == 403: # Forbidden
             print("ERROR: YouTube API request forbidden (403). This could be due to quota limits or incorrect API key/permissions.")
        return None
    except Exception as e:
        print(f"An unexpected error occurred during YouTube upload: {e}")
        traceback.print_exc()
        return None

# Slash command to scrape links and create a playlist
async def channel_name_autocomplete(ctx: discord.AutocompleteContext):
    channels = ctx.interaction.guild.text_channels
    threads = []
    for channel in channels:
        threads.extend(channel.threads)
    all_channels = list(channels) + threads
    return [
        channel.name
        for channel in all_channels
        if ctx.value.lower() in channel.name.lower()
    ][:25]

@client.slash_command(
    name="scrape",
    description="Scrape YouTube/Twitter links from a channel and create a playlist."
)
async def scrape(
    interaction: discord.Interaction,
    channel_name: str = discord.Option(
        description="Select the channel to scrape links from",
        autocomplete=channel_name_autocomplete
),
    
    date: DateType = discord.Option(
        default=None,
        description="Optional: pick a specific JST date to scrape (YYYY-MM-DD)",
        required=False
    )
):
    
    print(f"\n--- Scrape command initiated by {interaction.user} (ID: {interaction.user.id}) ---")
    print(f"Guild: '{interaction.guild.name}' (ID: {interaction.guild.id})")
    print(f"Target Channel: '{channel_name}'")
    print(f"Timestamp: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print("---")

    await interaction.response.defer() # Defer interaction early

    # Create a temporary directory for downloads
    temp_download_dir = tempfile.mkdtemp(prefix="discord_bot_downloads_")
    print(f"Created temporary download directory: {temp_download_dir}")

    try:
        target_channel = discord.utils.get(interaction.guild.text_channels, name=channel_name)

        # If not found as a text channel, try finding it as a thread
        if not target_channel:
            for channel in interaction.guild.text_channels:
                try:
                    thread_list = channel.threads
                    thread_match = discord.utils.get(thread_list, name=channel_name)
                    if thread_match:
                        target_channel = thread_match
                        break
                except Exception as e:
                    print(f"Error checking threads for channel {channel.name}: {e}")


        print(f"Target channel object found: {target_channel.name} (ID: {target_channel.id})")

        jst = timezone(timedelta(hours=9))

        if date:
            try:
                jst_extract_date = datetime.strptime(date, "%Y-%m-%d").date()
            except Exception:
                await interaction.followup.send("Invalid date format. Please use YYYY-MM-DD.")
                return
        else:
            jst_extract_date = datetime.now(jst).date() - timedelta(days=1)  # Default to yesterday if no date provided

        jst_start_of_day = datetime.combine(jst_extract_date, datetime.min.time(), jst)
        jst_end_of_day = jst_start_of_day + timedelta(days=1)
        title_date = jst_extract_date.strftime("%Y-%m-%d")

        print(f"Scraping messages from {jst_start_of_day.strftime('%Y-%m-%d %H:%M:%S %Z')} to {jst_end_of_day.strftime('%Y-%m-%d %H:%M:%S %Z')}")


        # Store links as dicts: {'url': str, 'type': 'youtube' | 'twitter', 'original_message': discord.Message}
        collected_links_info = []
        youtube_link_pattern = r'https?:\/\/(www\.)?(youtube\.com\/(watch\?v=|embed\/|v\/|shorts\/)|youtu\.be\/)[a-zA-Z0-9_-]{11}'
        twitter_link_pattern = r'https?:\/\/(www\.)?(twitter\.com|x\.com|fxtwitter\.com|vxtwitter\.com)\/[a-zA-Z0-9_]+\/status\/[0-9]+'

        async for msg in target_channel.history(limit=1000, after=jst_start_of_day, before=jst_end_of_day):
            if msg.author != client.user and 'http' in msg.content:
                for word in msg.content.split():
                    if re.match(youtube_link_pattern, word):
                        collected_links_info.append({'url': word, 'type': 'youtube', 'message_author': msg.author.name})
                    elif re.match(twitter_link_pattern, word):
                        collected_links_info.append({'url': word, 'type': 'twitter', 'message_author': msg.author.name})
        
        # Process unique URLs, keeping first occurrence's type if a URL is somehow classified differently (unlikely here)
        unique_urls = {}
        for link_info in collected_links_info:
            if link_info['url'] not in unique_urls:
                unique_urls[link_info['url']] = link_info
        
        links_to_process = sorted(list(unique_urls.values()), key=lambda x: x['url']) # Sort for consistent processing order

        if len(collected_links_info) != len(links_to_process):
            print(f"Removed {len(collected_links_info) - len(links_to_process)} duplicate links from initial scrape. Processing {len(links_to_process)} unique links.")

        if links_to_process:
            print(f"\nCollected unique link items: {links_to_process}\n")

            # MODIFIED SCOPES
            SCOPES = ['https://www.googleapis.com/auth/youtube.force-ssl', 'https://www.googleapis.com/auth/youtube.upload']

            def get_authenticated_service():
                creds = None
                if os.path.exists('token.json'):
                    creds = Credentials.from_authorized_user_file('token.json', SCOPES)
                if not creds or not creds.valid:
                    if creds and creds.expired and creds.refresh_token:
                        try:
                            creds.refresh(Request())
                        except Exception as e:
                            print(f"Error refreshing token: {e}. Need to re-authenticate.")
                            # Potentially delete token.json here if refresh consistently fails due to scope changes
                            # or instruct user to do so.
                            if os.path.exists('token.json') and 'invalid_grant' in str(e).lower(): # A common error if scopes changed
                                print("Attempting to delete token.json due to invalid_grant on refresh, please re-run to authorize.")
                                os.remove('token.json')
                            creds = None # Force re-auth
                    if not creds: # Either refresh failed or no token existed
                        flow = InstalledAppFlow.from_client_secrets_file(
                            'client_secret.json', SCOPES)
                        creds = flow.run_local_server(port=0)
                    with open('token.json', 'w') as token_file:
                        token_file.write(creds.to_json())
                return build('youtube', 'v3', credentials=creds)

            youtube = get_authenticated_service()
            print("YouTube service authenticated.")

            def extract_youtube_ids(link): # Renamed for clarity
                video_match = re.search(r"(?:youtube\.com\/(?:[^\/]+\/.+\/|(?:v|e(?:mbed)?)\/|shorts\/|.*[?&]v=)|youtu\.be\/)([a-zA-Z0-9_-]{11})", link)
                playlist_match = re.search(r"list=([0-9A-Za-z_-]+)", link)
                vid = video_match.group(1) if video_match else None
                pid = playlist_match.group(1) if playlist_match else None
                return {"video_id": vid, "playlist_id": pid}
            
            channel_name = channel_name.replace(" (playlist in pinned)", "")

            playlist_title = f"{channel_name} {title_date}"
            playlist_description = f"Playlist from {interaction.guild.name}'s #{channel_name} on {title_date}. Includes YouTube links and uploaded Twitter media."
            print(f"Attempting to create playlist with title: '{playlist_title}'")

            playlist_request_body = {
                "snippet": {
                    "title": playlist_title,
                    "description": playlist_description,
                    "tags": ["Discord", "YouTube", "Playlist", "Twitter"],
                    "defaultLanguage": "en"
                },
                "status": {"privacyStatus": "unlisted"}
            }
            playlist_request_obj = youtube.playlists().insert(part="snippet,status", body=playlist_request_body)
            playlist_response = await execute_with_retry(playlist_request_obj) # Assuming execute_with_retry is defined
            playlist_id = playlist_response["id"]
            print(f"Playlist created successfully. ID: {playlist_id}")

            added_video_ids = set()
            invalid_links_details = [] 

            for link_idx, link_info in enumerate(links_to_process):
                link = link_info['url']
                link_type = link_info['type']
                message_author = link_info.get('message_author', 'Unknown User')
                print(f"Processing link {link_idx + 1}/{len(links_to_process)} ({link_type}): {link}")

                video_id_to_add = None # This will be the YouTube ID to add to the playlist

                if link_type == 'youtube':
                    ids = extract_youtube_ids(link)
                    video_id_to_add = ids["video_id"] # Prioritize direct video ID
                    source_playlist_id = ids["playlist_id"]

                    if video_id_to_add: # It's a direct YouTube video link (or has a v= parameter)
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
                                print(f"  Successfully added YouTube video ID: {video_id_to_add}")
                            except HttpError as e:
                                print(f"  Failed to add YouTube video ID {video_id_to_add} after retries: {e}")
                                invalid_links_details.append({'link': link, 'id': video_id_to_add, 'type': 'youtube_video', 'reason': str(e)})
                            except Exception as e:
                                print(f"  An unexpected error occurred for YouTube video ID {video_id_to_add}: {e}")
                                invalid_links_details.append({'link': link, 'id': video_id_to_add, 'type': 'youtube_video', 'reason': f"Unexpected: {str(e)}"})
                        else:
                            print(f"  Skipped duplicate YouTube video ID: {video_id_to_add}")
                    
                    elif source_playlist_id: # It's a YouTube playlist link without a specific video_id in the main part
                        print(f"  Processing as YouTube playlist link. Source Playlist ID: {source_playlist_id}")
                        try:
                            playlist_items_list_request = youtube.playlistItems().list(
                                part="snippet", playlistId=source_playlist_id, maxResults=50 
                            )
                            playlist_items_response = await execute_with_retry(playlist_items_list_request)
                            videos_from_playlist_count = 0
                            for item_idx, item in enumerate(playlist_items_response.get("items", [])):
                                video_id_from_playlist = item["snippet"]["resourceId"]["videoId"]
                                print(f"    Item {item_idx + 1}: Video ID from source YouTube playlist: {video_id_from_playlist}")
                                if video_id_from_playlist not in added_video_ids:
                                    try:
                                        # ... (add to target playlist)
                                        playlist_item_request_body = {
                                            "snippet": {
                                                "playlistId": playlist_id,
                                                "resourceId": {"kind": "youtube#video", "videoId": video_id_from_playlist}
                                            }
                                        }
                                        pi_req_obj = youtube.playlistItems().insert(part="snippet", body=playlist_item_request_body)
                                        await execute_with_retry(pi_req_obj)
                                        added_video_ids.add(video_id_from_playlist)
                                        videos_from_playlist_count +=1
                                        print(f"      Successfully added video ID: {video_id_from_playlist} (from source playlist {source_playlist_id})")
                                    except HttpError as e:
                                        print(f"      Failed to add video ID {video_id_from_playlist} from source YT playlist {source_playlist_id}: {e}")
                                        invalid_links_details.append({'link': link, 'id': video_id_from_playlist, 'type': 'youtube_playlist_item', 'reason': f"From YT playlist {source_playlist_id}: {str(e)}"})
                                    except Exception as e:
                                        print(f"      Unexpected error for video ID {video_id_from_playlist} from YT playlist {source_playlist_id}: {e}")
                                        invalid_links_details.append({'link': link, 'id': video_id_from_playlist, 'type': 'youtube_playlist_item', 'reason': f"From YT playlist {source_playlist_id}, Unexpected: {str(e)}"})

                                else:
                                    print(f"    Skipped duplicate video ID: {video_id_from_playlist} (from source YT playlist {source_playlist_id})")
                            if videos_from_playlist_count > 0:
                                print(f"  Added {videos_from_playlist_count} new videos from YouTube playlist {source_playlist_id}.")
                            else:
                                print(f"  No new videos added from YouTube playlist {source_playlist_id}.")
                        except HttpError as e:
                            print(f"  Failed to retrieve videos from source YouTube playlist {source_playlist_id}: {e}")
                            invalid_links_details.append({'link': link, 'id': source_playlist_id, 'type': 'youtube_playlist', 'reason': f"Failed to list items: {str(e)}"})
                        except Exception as e:
                            print(f"  Unexpected error processing source YouTube playlist {source_playlist_id}: {e}")
                            invalid_links_details.append({'link': link, 'id': source_playlist_id, 'type': 'youtube_playlist', 'reason': f"Unexpected while listing: {str(e)}"})
                    else: # Neither video_id nor source_playlist_id for a YouTube link
                         print(f"  Invalid or unhandled YouTube link format: {link}")
                         invalid_links_details.append({'link': link, 'id': 'N/A', 'type': 'youtube_malformed', 'reason': 'Invalid YouTube link format or no ID extracted.'})


                elif link_type == 'twitter':

                    if 'fxtwitter.com' in link:
                        link = link.replace('fxtwitter.com', 'x.com')
                        print(f"---Normalized fxtwitter link to: {link}")
                    elif 'vxtwitter.com' in link:
                        link = link.replace('vxtwitter.com', 'x.com')
                        print(f"---Normalized vxtwitter link to: {link}")
                    downloaded_file_path = None
                    try:
                        downloaded_file_path = await download_twitter_media(link, temp_download_dir)
                        if downloaded_file_path:
                            yt_title = f"Twitter Media from {message_author} ({title_date}) - {os.path.basename(downloaded_file_path).split('.')[0]}"
                            yt_description = f"Media downloaded from Twitter: {link}\n(Original link: {link_info['url'] if link_info['url'] != link else 'same as processed'})\nShared by: {message_author} on {title_date} in Discord channel #{channel_name}."
                            
                            uploaded_youtube_id = await upload_video_to_youtube(youtube, downloaded_file_path, yt_title, yt_description)
                            
                            if uploaded_youtube_id:
                                video_id_to_add = uploaded_youtube_id # This ID will be added to the playlist
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
                                        print(f"  Successfully uploaded Twitter media and added to playlist. New YouTube ID: {video_id_to_add}")
                                    except HttpError as e:
                                        print(f"  Failed to add uploaded Twitter media (YT ID: {video_id_to_add}) to playlist: {e}")
                                        invalid_links_details.append({'link': link, 'id': video_id_to_add, 'type': 'twitter_upload_playlist_add_fail', 'reason': f"Failed to add to playlist: {str(e)}"})
                                    except Exception as e:
                                        print(f"  Unexpected error adding uploaded Twitter media (YT ID: {video_id_to_add}) to playlist: {e}")
                                        invalid_links_details.append({'link': link, 'id': video_id_to_add, 'type': 'twitter_upload_playlist_add_fail_unexpected', 'reason': f"Unexpected: {str(e)}"})

                                else:
                                    # This case should be rare if uploads are unique, but good for safety
                                    print(f"  Skipped duplicate YouTube video ID (from Twitter upload): {video_id_to_add}")
                            else:
                                print(f"  Failed to upload Twitter media from {link} to YouTube.")
                                invalid_links_details.append({'link': link, 'id': 'N/A', 'type': 'twitter_upload_fail', 'reason': 'YouTube upload returned no ID.'})
                        else:
                            print(f"  Failed to download Twitter media from {link}.")
                            invalid_links_details.append({'link': link, 'id': 'N/A', 'type': 'twitter_download_fail', 'reason': 'yt-dlp download failed or returned no file.'})
                    except Exception as e:
                        print(f"  Error processing Twitter link {link}: {e}")
                        traceback.print_exc()
                        invalid_links_details.append({'link': link, 'id': 'N/A', 'type': 'twitter_processing_error', 'reason': str(e)})
                    finally:
                        if downloaded_file_path and os.path.exists(downloaded_file_path):
                            try:
                                os.remove(downloaded_file_path)
                                print(f"  Cleaned up temporary file: {downloaded_file_path}")
                            except Exception as e_clean:
                                print(f"  Error cleaning up temporary file {downloaded_file_path}: {e_clean}")
                
                else: # Should not happen if types are 'youtube' or 'twitter'
                    print(f"  Unhandled link type '{link_type}' for link: {link}")
                    invalid_links_details.append({'link': link, 'id': 'N/A', 'type': 'unknown_type', 'reason': f'Unhandled link type: {link_type}'})


            # Construct playlist URL (Note: googleusercontent.com URLs are not standard public URLs)
            # A more standard URL is: https://www.youtube.com/playlist?list=PLAYLIST_ID
            playlist_url = f"https://www.youtube.com/playlist?list={playlist_id}"
            print(f"--- Scrape command finished ---")
            print(f"Playlist created: {playlist_url}")
            if invalid_links_details:
                print("--- Invalid/Failed Links/Items ---")
                for item in invalid_links_details:
                    print(f"  Type: {item['type']}, Link/ID: {item.get('link', 'N/A')}/{item.get('id', 'N/A')}, Reason: {item['reason']}")
                print("---")


            followup_message = (
                f"**Playlist Title**: {playlist_title}\n"
                # f"**Description**: {playlist_description}\n" # Can be long
                f"**Playlist Link**: <{playlist_url}>\n" # Enclose in < > to prevent Discord embed sometimes
                f"Added {len(added_video_ids)} unique videos/media items to the playlist."
            )
            if invalid_links_details:
                num_failed = len(invalid_links_details)
                followup_message += f"\nCould not process {num_failed} items/links (see bot logs for details)."

            await interaction.followup.send(followup_message)
        else:
            print("No YouTube or Twitter links found in the specified channel for the given period.")
            await interaction.followup.send("No YouTube or Twitter links found in the past day to create a playlist.")

    except HttpError as e:
        print(f"A Google API HttpError occurred after all retries: {e.resp.status} - {e.content}")
        traceback.print_exc()
        err_content = e.content.decode('utf-8') if e.content else str(e)
        if "quotaExceeded" in err_content or "usageLimits" in err_content:
             await interaction.followup.send("An API error occurred: YouTube API quota likely exceeded. Please check the Google Cloud Console and try again later.")
        elif e.resp.status == 401:
             await interaction.followup.send("An API error occurred: Authentication failed. The bot might need to be re-authorized. Try deleting `token.json` and running the command again.")
        else:
            await interaction.followup.send(f"An API error occurred while processing your request after several retries. Status: {e.resp.status}. Please check the bot logs and try again later.")
    except Exception as e:
        print(f"An unexpected error occurred in the scrape command: {e}")
        traceback.print_exc()
        await interaction.followup.send("An unexpected error occurred. Please check the bot logs.")
    finally:
        # Clean up the temporary directory
        if os.path.exists(temp_download_dir):
            try:
                # shutil.rmtree(temp_download_dir) # Use shutil for directory removal
                # For simplicity if shutil is not imported, os.remove files then os.rmdir
                for item_name in os.listdir(temp_download_dir):
                    item_path = os.path.join(temp_download_dir, item_name)
                    os.remove(item_path)
                os.rmdir(temp_download_dir)
                print(f"Successfully removed temporary download directory: {temp_download_dir}")
            except Exception as e_clean_dir:
                print(f"Error cleaning up temporary directory {temp_download_dir}: {e_clean_dir}")


async def channel_name_autocomplete(ctx: discord.AutocompleteContext):
    channels = ctx.interaction.guild.text_channels
    threads = []
    for channel in channels:
        threads.extend(channel.threads)
    all_channels = list(channels) + threads
    return [
        channel.name
        for channel in all_channels
        if ctx.value.lower() in channel.name.lower()
    ][:25]

# Add asyncio import for to_thread
import asyncio

# Get DISCORD_TOKEN from environment variable for security
load_dotenv()  # Load environment variables from .env file
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
if not DISCORD_TOKEN:
    raise ValueError("DISCORD_BOT_TOKEN environment variable not set.")

client.run(DISCORD_TOKEN)