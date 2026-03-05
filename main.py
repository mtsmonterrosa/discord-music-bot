import os
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
import yt_dlp

load_dotenv()
TOKEN = os.getenv("TOKEN")

INTENTS = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=INTENTS)

YTDL_OPTS = {
    "format": "bestaudio/best",
    "noplaylist": False,
    "quiet": True,
    "default_search": "ytsearch",
    "extract_flat": False,
    "source_address": "0.0.0.0",
}

FFMPEG_BEFORE_OPTS = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
FFMPEG_OPTS = "-vn"

ytdl = yt_dlp.YoutubeDL(YTDL_OPTS)

class Track:
    def __init__(self, title: str, webpage_url: str, stream_url: str):
        self.title = title
        self.webpage_url = webpage_url
        self.stream_url = stream_url

class GuildPlayer:
    def __init__(self):
        self.queue: asyncio.Queue[Track] = asyncio.Queue()
        self.current: Track | None = None
        self.lock = asyncio.Lock()

players: dict[int, GuildPlayer] = {}

def get_player(guild_id: int) -> GuildPlayer:
    if guild_id not in players:
        players[guild_id] = GuildPlayer()
    return players[guild_id]

async def ensure_voice(interaction: discord.Interaction) -> discord.VoiceClient:
    if not interaction.user or not isinstance(interaction.user, discord.Member):
        raise app_commands.CheckFailure("No pude detectar tu usuario.")
    if not interaction.user.voice or not interaction.user.voice.channel:
        raise app_commands.CheckFailure("Tenés que estar en un canal de voz.")

    vc = interaction.guild.voice_client if interaction.guild else None
    channel = interaction.user.voice.channel

    if vc and vc.is_connected():
        if vc.channel != channel:
            await vc.move_to(channel)
        return vc

    return await channel.connect()

def make_source(track: Track) -> discord.FFmpegPCMAudio:
    return discord.FFmpegPCMAudio(
        track.stream_url,
        before_options=FFMPEG_BEFORE_OPTS,
        options=FFMPEG_OPTS,
    )

async def extract_tracks(query: str) -> list[Track]:
    """
    Acepta:
    - URL de video
    - URL de playlist
    - texto de búsqueda (YouTube)
    """
    def _extract():
        return ytdl.extract_info(query, download=False)

    info = await asyncio.to_thread(_extract)

    tracks: list[Track] = []

    # Si es búsqueda: ytsearch devuelve entries
    if "entries" in info and isinstance(info["entries"], list):
        # Playlist o búsqueda
        entries = [e for e in info["entries"] if e]  # filtra None

        # Si es búsqueda, suele traer 1° resultado; si querés más, podés usar ytsearch5:
        # (pero para “fácil”, dejamos 1 o playlist completa)
        for e in entries:
            # Algunas playlists devuelven items “flat” sin url de stream; re-extraemos
            if "url" in e and "webpage_url" in e and e.get("is_live") is not None:
                pass

            # Asegura extracción completa del item
            if e.get("_type") in ("url", "url_transparent") or e.get("webpage_url"):
                item_url = e.get("webpage_url") or e.get("url")
            else:
                item_url = e.get("url")

            if not item_url:
                continue

            def _extract_item():
                return ytdl.extract_info(item_url, download=False)

            item = e
            # Si el item está “incompleto”, extraemos de nuevo
            if not item.get("url") or not item.get("title"):
                item = asyncio.run(_extract_item())  # fallback (raro en hosting)
            # Mejor: re-extraer siempre para obtener stream real
            item = await asyncio.to_thread(_extract_item)

            title = item.get("title") or "Sin título"
            webpage = item.get("webpage_url") or item_url
            stream = item.get("url")
            if stream:
                tracks.append(Track(title, webpage, stream))

        # Si era búsqueda y vinieron muchos, normalmente querés solo el primero.
        # Detectamos búsqueda típica (sin playlist_id) y recortamos a 1 para no spamear.
        if info.get("_type") == "playlist" and info.get("playlist_id") is None and len(tracks) > 1:
            tracks = tracks[:1]

        return tracks

    # Caso: video directo
    title = info.get("title") or "Sin título"
    webpage = info.get("webpage_url") or query
    stream = info.get("url")
    if stream:
        tracks.append(Track(title, webpage, stream))
    return tracks

async def start_player_loop(guild: discord.Guild):
    """Asegura un loop de reproducción por guild."""
    player = get_player(guild.id)

    async with player.lock:
        # Si ya hay algo sonando o el bot no está conectado, salimos
        vc = guild.voice_client
        if not vc or not vc.is_connected():
            return
        if vc.is_playing() or vc.is_paused():
            return

        while True:
            try:
                track = await asyncio.wait_for(player.queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                # si la cola está vacía, cortamos el loop
                if player.queue.empty():
                    player.current = None
                    return
                continue

            player.current = track
            source = make_source(track)

            done = asyncio.Event()

            def after_play(err):
                done.set()

            vc.play(source, after=after_play)
            await done.wait()

            # Si se desconectó mientras sonaba
            if not guild.voice_client or not guild.voice_client.is_connected():
                player.current = None
                return

@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"✅ Bot online como {bot.user} | Slash sync: {len(synced)} comandos")
    except Exception as e:
        print("⚠️ Error sync comandos:", e)

@bot.tree.command(name="play", description="Reproduce una canción por URL o búsqueda (YouTube) y la agrega a la cola.")
@app_commands.describe(query="Pegá una URL o escribí una búsqueda (ej: 'duki givinchu') o playlist")
async def play(interaction: discord.Interaction, query: str):
    await interaction.response.defer(thinking=True)

    vc = await ensure_voice(interaction)
    player = get_player(interaction.guild_id)

    tracks = await extract_tracks(query)
    if not tracks:
        await interaction.followup.send("No encontré nada para reproducir.")
        return

    # Encola todo (playlist o 1 tema)
    for t in tracks:
        await player.queue.put(t)

    # Mensaje
    if len(tracks) == 1:
        t = tracks[0]
        await interaction.followup.send(f"➕ En cola: **{t.title}**\n{t.webpage_url}")
    else:
        await interaction.followup.send(f"➕ Playlist agregada: **{len(tracks)}** temas a la cola.")

    # Arranca reproducción si no estaba sonando
    await start_player_loop(interaction.guild)

@bot.tree.command(name="skip", description="Salta la canción actual.")
async def skip(interaction: discord.Interaction):
    vc = interaction.guild.voice_client if interaction.guild else None
    if not vc or not vc.is_connected():
        await interaction.response.send_message("No estoy conectado a voz.", ephemeral=True)
        return
    if not vc.is_playing() and not vc.is_paused():
        await interaction.response.send_message("No hay nada reproduciéndose.", ephemeral=True)
        return

    vc.stop()
    await interaction.response.send_message("⏭️ Skip.")

    # intenta seguir con lo que sigue
    await start_player_loop(interaction.guild)

@bot.tree.command(name="pause", description="Pausa la reproducción.")
async def pause(interaction: discord.Interaction):
    vc = interaction.guild.voice_client if interaction.guild else None
    if not vc or not vc.is_connected():
        await interaction.response.send_message("No estoy conectado a voz.", ephemeral=True)
        return
    if vc.is_playing():
        vc.pause()
        await interaction.response.send_message("⏸️ Pausado.")
    else:
        await interaction.response.send_message("No hay nada reproduciéndose.", ephemeral=True)

@bot.tree.command(name="resume", description="Reanuda la reproducción.")
async def resume(interaction: discord.Interaction):
    vc = interaction.guild.voice_client if interaction.guild else None
    if not vc or not vc.is_connected():
        await interaction.response.send_message("No estoy conectado a voz.", ephemeral=True)
        return
    if vc.is_paused():
        vc.resume()
        await interaction.response.send_message("▶️ Reanudado.")
    else:
        await interaction.response.send_message("No está en pausa.", ephemeral=True)

@bot.tree.command(name="queue", description="Muestra la cola de reproducción.")
async def queue_cmd(interaction: discord.Interaction):
    player = get_player(interaction.guild_id)
    items = list(player.queue._queue)  # snapshot rápido

    lines = []
    if player.current:
        lines.append(f"🎶 Ahora: **{player.current.title}**")
    else:
        lines.append("🎶 Ahora: *(nada)*")

    if not items:
        lines.append("\nCola vacía.")
        await interaction.response.send_message("\n".join(lines))
        return

    lines.append("\n📜 Próximos:")
    for i, t in enumerate(items[:10], start=1):
        lines.append(f"{i}. {t.title}")

    if len(items) > 10:
        lines.append(f"... y {len(items)-10} más.")

    await interaction.response.send_message("\n".join(lines))

@bot.tree.command(name="stop", description="Detiene todo, limpia la cola y desconecta el bot.")
async def stop(interaction: discord.Interaction):
    vc = interaction.guild.voice_client if interaction.guild else None
    player = get_player(interaction.guild_id)

    # limpia cola
    while not player.queue.empty():
        try:
            player.queue.get_nowait()
        except asyncio.QueueEmpty:
            break
    player.current = None

    if vc and vc.is_connected():
        if vc.is_playing() or vc.is_paused():
            vc.stop()
        await vc.disconnect()

    await interaction.response.send_message("⏹️ Detenido, cola limpiada y desconectado.")

if not TOKEN:
    raise RuntimeError("Falta TOKEN. Ponelo en .env o en Variables del hosting.")

bot.run(TOKEN)