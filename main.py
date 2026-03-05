import discord
from discord.ext import commands
from discord import app_commands
import yt_dlp
import asyncio
import os
from dotenv import load_dotenv
from collections import deque

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    "cookiefile": None,
}

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn -af volume=0.5",
}

music_queues = {}
now_playing  = {}

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree


async def get_youtube_info(url: str):
    loop = asyncio.get_event_loop()
    ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)
    try:
        data = await loop.run_in_executor(
            None,
            lambda: ytdl.extract_info(url, download=False)
        )
        if not data:
            return None
        if "entries" in data:
            data = data["entries"][0]
        return {
            "title": data.get("title", "Canción sin título"),
            "url":   data.get("url") or data.get("webpage_url"),
        }
    except Exception as e:
        print(f"[ERROR yt-dlp] {e}")
        return None


async def play_next(guild_id: int, voice_client):
    queue = music_queues.get(guild_id)
    if not queue or len(queue) == 0:
        now_playing.pop(guild_id, None)
        await asyncio.sleep(300)
        if voice_client.is_connected() and not voice_client.is_playing():
            await voice_client.disconnect()
        return

    song = queue.popleft()
    now_playing[guild_id] = song

    info = await get_youtube_info(song["url"])
    if not info:
        await play_next(guild_id, voice_client)
        return

    audio_url = info["url"]

    def after_playing(error):
        if error:
            print(f"[ERROR reproducción] {error}")
        asyncio.run_coroutine_threadsafe(
            play_next(guild_id, voice_client),
            bot.loop
        )

    source = discord.FFmpegPCMAudio(audio_url, **FFMPEG_OPTIONS)
    voice_client.play(source, after=after_playing)


@tree.command(name="play", description="Reproduce música de YouTube")
@app_commands.describe(url="URL de YouTube o nombre de canción")
async def play(interaction: discord.Interaction, url: str):
    await interaction.response.defer(thinking=True)

    if not interaction.user.voice:
        await interaction.followup.send("❌ Debes estar en un canal de voz primero.")
        return

    channel = interaction.user.voice.channel
    guild   = interaction.guild

    voice_client = guild.voice_client
    if voice_client is None:
        voice_client = await channel.connect()
    elif voice_client.channel != channel:
        await voice_client.move_to(channel)

    await interaction.followup.send("🔍 Buscando canción...")
    info = await get_youtube_info(url)

    if not info:
        await interaction.edit_original_response(
            content="❌ No pude encontrar esa canción. Verifica la URL."
        )
        return

    song = {
        "title":     info["title"],
        "url":       url,
        "requester": interaction.user.display_name,
    }

    if guild.id not in music_queues:
        music_queues[guild.id] = deque()

    if voice_client.is_playing() or voice_client.is_paused():
        music_queues[guild.id].append(song)
        pos = len(music_queues[guild.id])
        await interaction.edit_original_response(
            content=f"📋 Agregada a la cola (#{pos}):\n🎵 **{song['title']}**"
        )
    else:
        music_queues[guild.id] = deque()
        music_queues[guild.id].append(song)
        await play_next(guild.id, voice_client)
        await interaction.edit_original_response(
            content=f"▶️ Reproduciendo:\n🎵 **{song['title']}**\nPedido por: {song['requester']}"
        )


@tree.command(name="skip", description="Salta a la siguiente canción")
async def skip(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    if not voice_client or not voice_client.is_playing():
        await interaction.response.send_message("❌ No hay nada reproduciéndose.")
        return
    voice_client.stop()
    await interaction.response.send_message("⏭️ Canción saltada.")


@tree.command(name="stop", description="Para la música y limpia la cola")
async def stop(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    voice_client = interaction.guild.voice_client
    if not voice_client:
        await interaction.response.send_message("❌ El bot no está en ningún canal.")
        return
    music_queues[guild_id] = deque()
    now_playing.pop(guild_id, None)
    voice_client.stop()
    await interaction.response.send_message("⏹️ Música detenida. Cola vaciada.")


@tree.command(name="leave", description="Expulsa al bot del canal de voz")
async def leave(interaction: discord.Interaction):
    voice_client = interaction.guild.voice_client
    if not voice_client:
        await interaction.response.send_message("❌ No estoy en ningún canal de voz.")
        return
    music_queues.pop(interaction.guild.id, None)
    now_playing.pop(interaction.guild.id, None)
    await voice_client.disconnect()
    await interaction.response.send_message("👋 ¡Hasta luego!")


@tree.command(name="queue", description="Muestra la lista de canciones")
async def queue(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    np = now_playing.get(guild_id)
    q  = music_queues.get(guild_id, deque())

    if not np and not q:
        await interaction.response.send_message("📋 La cola está vacía.")
        return

    msg = ""
    if np:
        msg += f"▶️ **Ahora:**\n🎵 {np['title']} — {np['requester']}\n\n"
    if q:
        msg += "📋 **Cola:**\n"
        for i, song in enumerate(q, 1):
            msg += f"`{i}.` {song['title']} — {song['requester']}\n"
            if i >= 10:
                msg += f"... y {len(q)-10} más\n"
                break

    await interaction.response.send_message(msg)


@bot.event
async def on_ready():
    print(f"✅ Bot conectado como: {bot.user.name}")
    print(f"📡 Servidores: {len(bot.guilds)}")
    try:
        synced = await tree.sync()
        print(f"🔄 Comandos sincronizados: {len(synced)}")
    except Exception as e:
        print(f"[ERROR sync] {e}")


if __name__ == "__main__":
    if not TOKEN:
        print("❌ ERROR: No se encontró DISCORD_TOKEN en .env")
        exit(1)
    bot.run(TOKEN)