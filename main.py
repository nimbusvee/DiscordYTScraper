import discord
from discord.ext import commands, tasks
from discord import app_commands
# from discord.app_commands import Choice

from datetime import datetime, timezone, timedelta
from datetime import date as DateType

from dotenv import load_dotenv
import os
import asyncio
import tempfile
import pandas as pd

# --- Google API --- #
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
import gspread

# --- Retry --- #
from googleapiclient.errors import HttpError
import time, random

# --- Twitter --- #
import uuid, subprocess
import traceback

# -- YouTube Upload --- #
from googleapiclient.http import MediaFileUpload

# -- Scrape Functionality --- #
from typing import Optional
import re

intents = discord.Intents.default()
intents.message_content = True

GUILD_ID = discord.Object(id=843153441621803028)

class MyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await self.tree.sync()
        bot.tree.clear_commands(guild=GUILD_ID)
        await bot.tree.sync(guild=GUILD_ID)

    
    async def on_ready(self):
        if not scheduled_scrape.is_running():
            scheduled_scrape.start()
        print(f"Logged in as {self.user} (ID: {self.user.id})")
        print('------')
        
    async def on_message(self, message):
        if message.author == self.user:
            return
        if message.content.startswith("-ls"):
            print(f'Message from {message.author} in server {message.guild.name}: {message.content}')
            await message.channel.send("please use /scrape command")

        
bot = MyBot()
    
# =========================================================================== #
#                                                                             #       
#                                                                             #       
#                                                                             #       
#                                                                             #       
# =========================================================================== #

# =========================== UTILITY DEFINITIONS =========================== #

# -- Scheduled Task Helper ---
JST = timezone(timedelta(hours=9))

TARGET_CHANNELS = []

SCHEDULED_SERVER_ID = 843153441621803028 # hachi-hive

TARGET_CHANNEL_IDS = [
    866072626241994772, # music
    867403789934002216 # vtubers-music
]

LOG_CHANNEl_ID = 925698743225942018 # bot-spam

# Daily schedule → 12:00 AM JST
SCRAPE_HOUR = 0
SCRAPE_MINUTE = 5

def seconds_until_target(hour: int, minute: int):
    """Returns seconds until the next scheduled JST time."""
    now = datetime.now(JST)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


@tasks.loop(hours=24)
async def scheduled_scrape():
    print(f"Scheduled scrape task triggered at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S %Z')}")
    wait_seconds = seconds_until_target(SCRAPE_HOUR, SCRAPE_MINUTE)
    print(f"Waiting {wait_seconds} seconds until next scheduled scrape time ({SCRAPE_HOUR:02}:{SCRAPE_MINUTE:02} JST)")
    await asyncio.sleep(wait_seconds)
    
    HACHI_HIVE = bot.get_guild(SCHEDULED_SERVER_ID)

    for channel_id in TARGET_CHANNEL_IDS:
        channel = bot.get_channel(channel_id).name
        print(f"Starting scheduled scrape for channel: {channel}")
        if not channel:
            print(f"Channel {channel} not found in guild.")
            continue

        try:
            followup_message = await run_scrape(None, channel, is_scheduled=True)
            await bot.get_channel(LOG_CHANNEl_ID).send(followup_message)
        except Exception as e:
            await bot.get_channel(LOG_CHANNEl_ID).send(f"An error occurred during scheduled scrape for channel {channel.name}: {e}")




# --- Retry Logic Helper Function ---
async def execute_with_retry(request, max_retries=5, initial_backoff=1.0, max_backoff=16.0):
    """Executes a request with exponential backoff retry logic."""
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
        
# Helper for retrying individual requests (used in sequential mode)
async def execute_with_retry_async(request_func, max_retries=5, initial_backoff=1.0):
    backoff = initial_backoff
    for attempt in range(max_retries + 1):
        try:
            # We run the blocking execute() in a thread to not block the Discord bot
            return await asyncio.to_thread(request_func().execute)
        except HttpError as e:
            # 409 Conflict/Service Unavailable are common playlist errors
            if e.resp.status in [409, 500, 502, 503, 504]:
                if attempt == max_retries:
                    raise e
                
                sleep_time = backoff + random.uniform(0, 1)
                print(f"   ⚠️ API Error {e.resp.status}. Retrying in {sleep_time:.2f}s...")
                await asyncio.sleep(sleep_time)
                backoff *= 2
            else:
                raise e

# =========================== COMMAND DEFINITIONS =========================== #


# 🔹 AUTOCOMPLETE FUNCTION
async def channel_autocomplete(interaction: discord.Interaction, current: str):
    channels = interaction.guild.text_channels
    threads = []
    for channel in channels:
        threads.extend(channel.threads)
        
    all_channels = list(channels) + threads
    return [
        app_commands.Choice(name=ch.name, value=ch.name)
        for ch in all_channels
        if current.lower() in ch.name.lower()
    ][:25]


# 🔹 SCRAPE FUNCTION
async def run_scrape(interaction, channel_name: str, date: Optional[str] = None, is_scheduled=False):
    if not is_scheduled and interaction:
        operator = interaction.user
        guild = interaction.guild
        all_channels = guild.text_channels
    else:
        operator = "Scheduled Task"
        guild = bot.get_guild(SCHEDULED_SERVER_ID)
        all_channels = guild.text_channels

    threads = []
    for ch in all_channels:
        threads.extend(ch.threads)

    all_possible = all_channels + threads
    
    print(f"\n--- Scrape command initiated by {operator}")
    print(f"Guild: '{guild.name}' (ID: {guild.id})")
    print(f"Target Channel: '{channel_name}'")
    print(f"Timestamp: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print("---")

         
    # Create a temporary directory for downloads
    temp_download_dir = tempfile.mkdtemp(prefix="discord_bot_downloads_")
    print(f"Created temporary download directory: {temp_download_dir}")
        
    try:
        target_channel = discord.utils.get(all_channels, name=channel_name)
        # If not found as a text channel, try finding it as a thread
        if not target_channel:
            for channel in guild.text_channels:
                try:
                    thread_list = channel.threads
                    thread_match = discord.utils.get(thread_list, name=channel_name)
                    if thread_match:
                        target_channel = thread_match
                        break
                except Exception as e:
                    print(f"Error checking threads for channel {channel.name}: {e}")

        print(f"Scraping links from channel: {target_channel.name} (ID: {target_channel.id})")


        if date:
            try:
                jst_extract_date = datetime.strptime(date, "%Y-%m-%d").date()
            except Exception:
                await interaction.followup.send("Invalid date format. Please use YYYY-MM-DD.")
                return
        else:
            jst_extract_date = datetime.now(JST).date() - timedelta(days=1)  # Default to yesterday if no date provided

        jst_start_of_day = datetime.combine(jst_extract_date, datetime.min.time(), JST)
        jst_end_of_day = jst_start_of_day + timedelta(days=1)
        title_date = jst_extract_date.strftime("%Y-%m-%d")
        
        # --- NEW: Calculate Day Name and Day Number ---
        playlist_day_str = jst_extract_date.strftime('%A') # e.g. "Monday"
        playlist_day_num = jst_extract_date.isoweekday() # 1 = Monday, 7 = Sunday

        print(f"Scraping messages from {jst_start_of_day.strftime('%Y-%m-%d %H:%M:%S %Z')} to {jst_end_of_day.strftime('%Y-%m-%d %H:%M:%S %Z')}")


        # Store links as dicts: {'url': str, 'type': 'youtube' | 'twitter', 'original_message': discord.Message}
        collected_links_info = []
        youtube_link_pattern = r'https?:\/\/(www\.)?(youtube\.com\/(watch\?v=|embed\/|v\/|shorts\/)|youtu\.be\/)[a-zA-Z0-9_-]{11}'
        twitter_link_pattern = r'https?:\/\/(www\.)?(twitter\.com|x\.com|fxtwitter\.com|vxtwitter\.com)\/[a-zA-Z0-9_]+\/status\/[0-9]+'

        async for msg in target_channel.history(limit=1000, after=jst_start_of_day, before=jst_end_of_day):
            if msg.author != bot.user and 'http' in msg.content:
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

        if not links_to_process:
            followup_message = "No YouTube or Twitter links found in the specified channel for the given period."
            return followup_message
        else:
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
            print(f"Channel name for playlist: {channel_name}")

            playlist_title = f"{channel_name} {title_date}"
            playlist_description = f"Playlist from {guild.name}'s #{channel_name} on {title_date}. Includes YouTube links and uploaded Twitter media."
            print(f"Attempting to create playlist with title: '{playlist_title}'")

            playlist_request_body = {
                "snippet": {
                    "title": playlist_title,
                    "description": playlist_description,
                    "tags": ["Discord", "YouTube", "Playlist", "Twitter"],
                    "defaultLanguage": "en"
                },
                "status": {"privacyStatus": "public"}
            }
            playlist_request_obj = youtube.playlists().insert(part="snippet,status", body=playlist_request_body)
            playlist_response = await execute_with_retry(playlist_request_obj) # Assuming execute_with_retry is defined
            playlist_id = playlist_response["id"]
            print(f"Playlist created successfully. ID: {playlist_id}")

            video_ids_to_process = set()
            invalid_links_details = [] 
            print(f"--- Processing {len(links_to_process)} raw links... ---")

            for link_idx, link_info in enumerate(links_to_process):
                
                link = link_info['url']
                link_type = link_info['type']
                
                message_author = link_info.get('message_author', 'Unknown User')
                print(f"Processing link {link_idx + 1}/{len(links_to_process)} ({link_type}): {link}")

                if link_type == 'youtube':
                    # 1. Extract direct Video ID
                    ids = extract_youtube_ids(link) # Ensure you have this helper function defined
                    if ids.get("video_id"):
                        video_ids_to_process.add(ids["video_id"])
                        
                    # 2. Handle Source Playlists (Expand them)
                    elif ids.get("playlist_id"):
                        src_pid = ids["playlist_id"]
                        print(f"  Expanding source playlist: {src_pid}")
                        try:
                            # Note: Fetching list items only costs 1 unit per page! Cheap.
                            pl_req = youtube.playlistItems().list(part="contentDetails", playlistId=src_pid, maxResults=50)
                            pl_res = pl_req.execute()
                            for item in pl_res.get("items", []):
                                video_ids_to_process.add(item["contentDetails"]["videoId"])
                        except Exception as e:
                            print(f"  Failed to expand playlist {src_pid}: {e}")


                elif link_type == 'twitter':
                    
                    # 3. Handle Twitter (Download -> Upload -> Get ID)
                    # We CANNOT batch this part easily because it involves file uploads
                    try:
                        if 'fxtwitter.com' in link or 'vxtwitter.com' in link or 'fixupx.com' in link:
                            link = link.replace('fxtwitter.com', 'x.com').replace('vxtwitter.com', 'x.com').replace('fixupx.com', 'x.com')
                        
                        fpath = await download_twitter_media(link, temp_download_dir)
                        if fpath:
                            yt_title = f"Twitter Media from {link_info['message_author']} ({title_date})"
                            # Uploading costs 1600 units! Be careful.
                            new_vid_id = await upload_video_to_youtube(youtube, fpath, yt_title, f"Source: {link}")
                            if new_vid_id:
                                video_ids_to_process.add(new_vid_id)
                            if os.path.exists(fpath): 
                                os.remove(fpath)
                    except Exception as e:
                        print(f"  Error handling Twitter link {link}: {e}")

            # ==============================================================================
            # 🐌 SEQUENTIAL INSERTION PHASE (Safe & Reliable)
            # ==============================================================================
            
            final_video_list = list(video_ids_to_process)
            success_count = 0
            
            if final_video_list:
                print(f"\nStarting SEQUENTIAL insertion of {len(final_video_list)} videos...")
                print(f"This will take approximately {len(final_video_list) * 1.5} seconds to ensure stability.")
                
                for idx, vid_id in enumerate(final_video_list):
                    try:
                        print(f"[{idx+1}/{len(final_video_list)}] Adding {vid_id}...", end="", flush=True)
                        
                        # Helper lambda to create the request object for our retry function
                        req_func = lambda: youtube.playlistItems().insert(
                            part="snippet",
                            body={
                                "snippet": {
                                    "playlistId": playlist_id,
                                    "resourceId": {"kind": "youtube#video", "videoId": vid_id}
                                }
                            }
                        )
                        
                        # Execute with retry logic (handles 409/500/503 automatically)
                        await execute_with_retry_async(req_func)
                        
                        print(" ✅ Success")
                        success_count += 1
                        
                        # IMPORTANT: Delay between successful inserts to prevent playlist lock
                        await asyncio.sleep(0.8) 
                        
                    except Exception as e:
                        print(f" ❌ Failed: {e}")
                        # Even if it fails, we sleep slightly to let API cool down
                        await asyncio.sleep(1)
                    
            # ==============================================================================
            # 📊 GOOGLE SHEETS EXPORT PHASE
            # ==============================================================================
            
            if final_video_list:
                print(f"Fetching details for {len(final_video_list)} videos for Sheets...")
                
                # 1. Setup Google Sheets Client
                try:
                    gc = gspread.service_account(filename='service_account.json')
                    sh = gc.open("HACHI HIVE Playlists") 
                    worksheet = sh.sheet1
                except Exception as e:
                    print(f"⚠️ Sheets Auth Error: {e}. Make sure service_account.json exists and Drive API is enabled.")
                    return f"Playlist Created, but Sheets Error: {e}"

                rows_to_append = []
                
                # 2. Batch Fetch Video Details (50 at a time)
                for i in range(0, len(final_video_list), 50):
                    batch_chunk = final_video_list[i:i+50]
                    try:
                        vid_res = youtube.videos().list(
                            part="snippet", 
                            id=",".join(batch_chunk)
                        ).execute()
                        
                        for item in vid_res.get("items", []):
                            snip = item.get("snippet", {})
                            
                            rows_to_append.append([
                                playlist_title,                 
                                channel_name,             
                                title_date,
                                playlist_day_str,
                                playlist_day_num,             
                                playlist_id,                    
                                item.get("id"),                 
                                snip.get("title"),              
                                snip.get("channelTitle"),       
                                snip.get("channelId"),          
                                snip.get("publishedAt", "") 
                            ])
                    except Exception as e:
                        print(f"  Error fetching video details for Sheets batch {i}: {e}")

                # 3. Write to Sheet
                if rows_to_append:
                    try:
                        if not worksheet.get_values('A1'): 
                            HEADERS = ["Playlist Title", "Discord Channel", "Playlist Date", "Playlist Day", "Playlist Day Number", "Playlist ID", "Video ID", "Video Title", "Channel Name", "Channel ID", "Upload Date"]
                            worksheet.append_row(HEADERS)
                        
                        # UPDATED: Use value_input_option='USER_ENTERED' to force date parsing
                        worksheet.append_rows(rows_to_append, value_input_option='USER_ENTERED')
                        print(f"✅ Successfully added {len(rows_to_append)} rows to Google Sheets.")
                    except Exception as e:
                        print(f"❌ Error writing to Google Sheets: {e}")
            
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
                f"Added {len(final_video_list)} unique videos/media items to the playlist."
            )
            
            if invalid_links_details:
                num_failed = len(invalid_links_details)
                followup_message += f"\nCould not process {num_failed} items/links (see bot logs for details)."
                
            return followup_message
                
    except HttpError as e:
        print(f"A Google API HttpError occurred after all retries: {e.resp.status} - {e.content}")
        traceback.print_exc()
        err_content = e.content.decode('utf-8') if e.content else str(e)
        if "quotaExceeded" in err_content or "usageLimits" in err_content:
            followup_message = ("An API error occurred: YouTube API quota likely exceeded. Please check the Google Cloud Console and try again later.")

        elif e.resp.status == 401:
            followup_message = ("An API error occurred: Authentication failed. The bot might need to be re-authorized. Try deleting `token.json` and running the command again.")
        else:
            followup_message = (f"An API error occurred while processing your request after several retries. Status: {e.resp.status}. Please check the bot logs and try again later.")

        return followup_message
    
    except Exception as e:
        print(f"An unexpected error occurred in the scrape command: {e}")
        traceback.print_exc()
        followup_message = ("An unexpected error occurred. Please check the bot logs.")
        return followup_message
    
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
    
@bot.tree.command(
    name="scrape",
    description="Scrape YouTube/Twitter links from a channel and create a playlist."
)
@app_commands.describe(
    channel_name="Channel or thread name",
    date="Optional: pick a specific JST date to scrape (YYYY-MM-DD)",
    )
@app_commands.autocomplete(channel_name=channel_autocomplete)

async def interaction_scrape(
    interaction: discord.Interaction,
    channel_name: str,
    date: Optional[str] = None
):
    
    await interaction.response.defer()
    
    async def task_wrapper():
        try:
            message = await run_scrape(interaction, channel_name, date)
            await interaction.followup.send(message)
        except Exception as e:
            await interaction.followup.send(f"⚠️ Scrape failed: {e}")

    # run in parallel
    asyncio.create_task(task_wrapper())
            
    

    
# Get DISCORD_TOKEN from environment variable for security
load_dotenv()  # Load environment variables from .env file
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
if not DISCORD_TOKEN:
    raise ValueError("DISCORD_BOT_TOKEN environment variable not set.")

bot.run(DISCORD_TOKEN)
