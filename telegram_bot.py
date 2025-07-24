import os
import requests
import tempfile
from urllib.parse import urlparse
from pathlib import Path
import mimetypes
import asyncio
import aiohttp
import aiofiles
import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

# Bot configuration
BOT_TOKEN = "7234599644:AAEvEgD2BSkevUNp29eQI-DuyaNfc7menkc"  # Replace with your bot token
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB limit (Telegram's limit)

class FileDownloadBot:
    def __init__(self, token):
        self.token = token
        self.app = Application.builder().token(token).build()
        self.active_downloads = {}  # Track active downloads for cancellation
        self.setup_handlers()
    
    def setup_handlers(self):
        """Set up command and message handlers"""
        self.app.add_handler(CommandHandler("start", self.start_command))
        self.app.add_handler(CommandHandler("help", self.help_command))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_url))
        self.app.add_handler(CallbackQueryHandler(self.handle_callback))
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        welcome_text = """
🤖 **File Download Bot**

Send me any download link and I'll download the file and send it back to you!

**Features:**
✅ Visual download progress
✅ Cancel downloads anytime
✅ Files up to 50MB
✅ Multiple file formats

**Usage:**
Just send me a URL like:
`https://example.com/file.pdf`

Type /help for more information.
        """
        await update.message.reply_text(welcome_text, parse_mode='Markdown')
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        help_text = """
**How to use this bot:**

1. Send me any direct download link
2. Watch the progress bar as I download
3. Cancel anytime using the ❌ button
4. Get your file delivered to Telegram

**New Features:**
🔄 **Progress Visualization** - See download/upload progress
❌ **Cancel Function** - Stop downloads anytime
⚡ **Speed Display** - See download speed in real-time

**Limitations:**
- Maximum file size: 50MB
- Only direct download links work
- Some websites may block bot downloads

**Examples:**
✅ `https://example.com/document.pdf`
✅ `https://example.com/image.jpg`
✅ `https://example.com/video.mp4`

❌ `https://drive.google.com/...` (not direct)
❌ `https://dropbox.com/...` (not direct)
        """
        await update.message.reply_text(help_text, parse_mode='Markdown')
    
    def get_progress_bar(self, percentage, length=20):
        """Generate a visual progress bar"""
        filled = int(length * percentage / 100)
        bar = "█" * filled + "░" * (length - filled)
        return f"[{bar}] {percentage:.1f}%"
    
    def format_size(self, bytes_size):
        """Format bytes to human readable format"""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if bytes_size < 1024.0:
                return f"{bytes_size:.1f} {unit}"
            bytes_size /= 1024.0
        return f"{bytes_size:.1f} TB"
    
    def format_speed(self, bytes_per_second):
        """Format speed to human readable format"""
        return f"{self.format_size(bytes_per_second)}/s"
    
    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle button callbacks (cancel button)"""
        query = update.callback_query
        await query.answer()
        
        if query.data.startswith("cancel_"):
            download_id = query.data.replace("cancel_", "")
            
            if download_id in self.active_downloads:
                self.active_downloads[download_id]["cancelled"] = True
                await query.edit_message_text("❌ **Download Cancelled**\n\nThe download has been stopped.")
            else:
                await query.edit_message_text("❌ **Download Not Found**\n\nThis download may have already completed or expired.")
    
    async def handle_url(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle URL messages and download files"""
        url = update.message.text.strip()
        
        # Basic URL validation
        if not self.is_valid_url(url):
            await update.message.reply_text(
                "❌ Please send a valid URL starting with http:// or https://"
            )
            return
        
        # Generate unique download ID
        download_id = f"{update.message.chat_id}_{int(time.time())}"
        
        # Create cancel button
        keyboard = [[InlineKeyboardButton("❌ Cancel Download", callback_data=f"cancel_{download_id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Send initial response with cancel button
        status_msg = await update.message.reply_text(
            "🔍 **Checking file...**\n\nPlease wait while I analyze the download link.",
            parse_mode='Markdown',
            reply_markup=reply_markup
        )
        
        # Initialize download tracking
        self.active_downloads[download_id] = {
            "cancelled": False,
            "status_msg": status_msg,
            "chat_id": update.message.chat_id
        }
        
        try:
            # Download the file with progress tracking
            result = await self.download_file_with_progress(
                url, download_id, status_msg, reply_markup
            )
            
            # Check if download was successful
            if result is None:
                return  # Download was cancelled or failed
            
            file_path, filename, file_size = result
            
            # Check if cancelled
            if self.active_downloads[download_id]["cancelled"]:
                if os.path.exists(file_path):
                    os.unlink(file_path)
                return
            
            if file_size > MAX_FILE_SIZE:
                await status_msg.edit_text(
                    f"❌ **File Too Large**\n\n"
                    f"File size: {self.format_size(file_size)}\n"
                    f"Maximum allowed: {self.format_size(MAX_FILE_SIZE)}",
                    parse_mode='Markdown'
                )
                if os.path.exists(file_path):
                    os.unlink(file_path)
                return
            
            # Start upload with progress
            await self.upload_file_with_progress(
                update, file_path, filename, url, download_id, status_msg, reply_markup
            )
            
        except asyncio.TimeoutError:
            # Handle timeout specifically - file might still be downloading
            if download_id in self.active_downloads:
                try:
                    await status_msg.edit_text(
                        "⚠️ **Connection Timeout**\n\n"
                        "The download is taking longer than expected.\n"
                        "Please wait, I'm still trying to complete it...",
                        parse_mode='Markdown'
                    )
                except:
                    pass
        except Exception as e:
            if download_id in self.active_downloads and not self.active_downloads[download_id]["cancelled"]:
                # Check if file was actually downloaded despite the error
                temp_files = [f for f in os.listdir(tempfile.gettempdir()) if f.endswith('_' + str(download_id))]
                if temp_files:
                    # File exists, try to upload it anyway
                    try:
                        file_path = os.path.join(tempfile.gettempdir(), temp_files[0])
                        filename = temp_files[0].split('_', 1)[1] if '_' in temp_files[0] else 'download'
                        
                        await self.upload_file_with_progress(
                            update, file_path, filename, url, download_id, status_msg, reply_markup
                        )
                        return
                    except:
                        pass
                
                error_msg = f"❌ **Download Failed**\n\nError: {str(e)}"
                try:
                    await status_msg.edit_text(error_msg, parse_mode='Markdown')
                except:
                    pass
        
        finally:
            # Clean up tracking
            if download_id in self.active_downloads:
                del self.active_downloads[download_id]
    
    async def download_file_with_progress(self, url, download_id, status_msg, reply_markup):
        """Download file with visual progress updates"""
        temp_path = None
        try:
            timeout = aiohttp.ClientTimeout(total=300, connect=30)  # 5 min total, 30s connect
            async with aiohttp.ClientSession(timeout=timeout) as session:
                # Get file info first
                async with session.head(url) as response:
                    if response.status != 200:
                        raise Exception(f"Server returned status {response.status}")
                    
                    file_size = int(response.headers.get('content-length', 0))
                    if file_size > MAX_FILE_SIZE:
                        raise Exception(f"File too large: {self.format_size(file_size)}")
                    
                    filename = self.get_filename_from_response(response, url)
                
                # Start download
                async with session.get(url) as response:
                    if response.status != 200:
                        raise Exception(f"Download failed with status {response.status}")
                    
                    # Create temporary file with unique name
                    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=f"_{download_id}")
                    temp_path = temp_file.name
                    temp_file.close()
                    
                    downloaded_size = 0
                    start_time = time.time()
                    last_update = 0
                    
                    async with aiofiles.open(temp_path, 'wb') as f:
                        async for chunk in response.content.iter_chunked(8192):
                            # Check if cancelled
                            if self.active_downloads.get(download_id, {}).get("cancelled", False):
                                return None
                            
                            await f.write(chunk)
                            downloaded_size += len(chunk)
                            
                            # Update progress every 1 second (reduced frequency to avoid timeout)
                            current_time = time.time()
                            if current_time - last_update >= 1.0:
                                try:
                                    if file_size > 0:
                                        percentage = (downloaded_size / file_size) * 100
                                        progress_bar = self.get_progress_bar(percentage)
                                        
                                        # Calculate speed
                                        elapsed_time = current_time - start_time
                                        speed = downloaded_size / elapsed_time if elapsed_time > 0 else 0
                                        
                                        status_text = (
                                            f"⬇️ **Downloading: {filename}**\n\n"
                                            f"{progress_bar}\n\n"
                                            f"📊 {self.format_size(downloaded_size)} / {self.format_size(file_size)}\n"
                                            f"🚀 Speed: {self.format_speed(speed)}\n"
                                            f"⏱️ {percentage:.1f}% complete"
                                        )
                                    else:
                                        # Unknown file size
                                        elapsed_time = current_time - start_time
                                        speed = downloaded_size / elapsed_time if elapsed_time > 0 else 0
                                        
                                        status_text = (
                                            f"⬇️ **Downloading: {filename}**\n\n"
                                            f"📊 Downloaded: {self.format_size(downloaded_size)}\n"
                                            f"🚀 Speed: {self.format_speed(speed)}\n"
                                            f"⏱️ Downloading..."
                                        )
                                    
                                    await status_msg.edit_text(
                                        status_text,
                                        parse_mode='Markdown',
                                        reply_markup=reply_markup
                                    )
                                except Exception as e:
                                    # Ignore message editing errors but continue download
                                    pass
                                
                                last_update = current_time
                    
                    return temp_path, filename, downloaded_size
                    
        except Exception as e:
            # Clean up temp file if download failed
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except:
                    pass
            raise e
    
    async def upload_file_with_progress(self, update, file_path, filename, url, download_id, status_msg, reply_markup):
        """Upload file with progress indication"""
        try:
            # Show upload status
            await status_msg.edit_text(
                f"📤 **Uploading: {filename}**\n\n"
                f"⏳ Preparing upload to Telegram...\n"
                f"📁 File size: {self.format_size(os.path.getsize(file_path))}",
                parse_mode='Markdown'
            )
            
            # Send the file
            with open(file_path, 'rb') as f:
                await update.message.reply_document(
                    document=f,
                    filename=filename,
                    caption=f"✅ **Download Complete!**\n\n📎 {filename}\n🔗 {self.shorten_url(url)}",
                    parse_mode='Markdown'
                )
            
            # Clean up
            os.unlink(file_path)
            
            # Final success message
            await status_msg.edit_text(
                f"✅ **Upload Complete!**\n\n"
                f"📎 File: {filename}\n"
                f"📁 Size: {self.format_size(os.path.getsize(file_path) if os.path.exists(file_path) else 0)}\n"
                f"🚀 Successfully delivered!",
                parse_mode='Markdown'
            )
            
        finally:
            # Clean up file if it still exists
            if os.path.exists(file_path):
                os.unlink(file_path)
    
    def shorten_url(self, url, max_length=50):
        """Shorten URL for display"""
        if len(url) <= max_length:
            return url
        
        # Parse URL to get domain and path
        parsed = urlparse(url)
        domain = parsed.netloc
        path = parsed.path
        
        # If domain + "..." is still too long, truncate domain too
        if len(domain) > max_length - 10:
            domain = domain[:max_length-10] + "..."
            return f"{domain}/..."
        
        # Calculate remaining space for path
        remaining = max_length - len(domain) - 4  # 4 for "..." and "/"
        
        if remaining > 0 and path:
            if len(path) > remaining:
                path = path[:remaining] + "..."
            return f"{domain}{path}"
        else:
            return f"{domain}/..."
    
    def is_valid_url(self, url):
        """Check if URL is valid"""
        try:
            result = urlparse(url)
            return all([result.scheme, result.netloc]) and result.scheme in ['http', 'https']
        except:
            return False
    
    def get_filename_from_response(self, response, url):
        """Extract filename from response or URL"""
        # Try to get filename from Content-Disposition header
        cd = response.headers.get('content-disposition', '')
        if 'filename=' in cd:
            filename = cd.split('filename=')[1].strip('"\'')
            return filename
        
        # Try to get filename from URL
        parsed_url = urlparse(url)
        filename = os.path.basename(parsed_url.path)
        
        if filename and '.' in filename:
            return filename
        
        # Try to guess extension from content-type
        content_type = response.headers.get('content-type', '')
        if content_type:
            ext = mimetypes.guess_extension(content_type.split(';')[0])
            if ext:
                return f"download{ext}"
        
        return "download"
    
    def run(self):
        """Start the bot"""
        print("🤖 Starting Enhanced File Download Bot...")
        print("✨ Features: Progress bars, Cancel functionality, Speed display")
        print("Press Ctrl+C to stop")
        self.app.run_polling()

def main():
    """Main function"""
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ Please set your bot token in the BOT_TOKEN variable")
        print("Get your token from @BotFather on Telegram")
        return
    
    bot = FileDownloadBot(BOT_TOKEN)
    bot.run()

if __name__ == "__main__":
    main()