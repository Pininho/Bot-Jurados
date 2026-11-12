
import os
import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import discord

# Mini web server pa Render Web Service - si no abre puerto, Render lo mata con Port scan timeout
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header('Content-Type','text/plain'); self.end_headers(); self.wfile.write(b'JURADOS BATALLA Discord SOLO VIDEO - OK')
    def log_message(self, *a): pass

def start_web():
    port=int(os.getenv('PORT','10000'))
    try:
        ThreadingHTTPServer(('0.0.0.0',port),H).serve_forever()
    except Exception as e:
        print(f'web server error {e}')

threading.Thread(target=start_web,daemon=True).start()
print(f'Mini web server en puerto {os.getenv("PORT","10000")}')
from discord.ext import commands
from google import genai

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# DISCORD SOLO VIDEO - 100% CERTEZA SIN CONFUSION - Decision usuario 2026-11-11
# Discord = solo video (mp4, mov, mkv, webm). Mejor calidad, se ve ropa y puesta en escena, sin Telethon.
# Telegram = solo audio. Si llega AUDIO a Discord, avisa y NO lo analiza: "Audio va por Telegram"
# TL = audio 100% certeza con confirmacion, Discord = video 100% certeza con confirmacion, mismo sistema.

MODELS_TO_TRY = ["gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-3-flash-preview"]

JURADO_PROMPT = """
Eres JURADO COMPLETO de batallas freestyle, 8 jurados, 0.0-3.0.
FLOW: cadencia, delivery, swing, como caes en beat, entonacion, pausas, variaciones velocidad, doble tempo, triple tempo, metralleta, flow entrecortado, cambio de flow, cambio de registro.
Scratch Vocal: imitar DJ wika-wika skr-skr, como Nobody 12-33s Nobody vs Teorema. Prolijo y a tiempo sube Flow.
Beatbox sin beat: hacer beat con boca bum-pa-ts, ser bateria. Si hace beatbox + scratch + rapear mezclado, es Modo DJ / Scratch Rap, sube Flow.
One-two: mantener rimando todas las vocales y silabas.
SKILL: metrica, estructuras, pareados, rimas multisilabicas, Encadenado EntendiENDO EN DOminar (ENDO -> EN DO), Calambur, Wordplay, Retruecano, Aliteracion, Paranomasia, Antitesis, Simil, Metafora, Hiperbole, Hiperbaton, esquema.
Si compleja bien ejecutada 2.2-3.0, si compleja mala 1.5, si basica perfecta puede ganar a compleja mala.
PUNCHLINE: negativo (ataca, recuerda imperfecto) y positivo (enaltece, superioridad), directo, personal, con referencia, con doble sentido, barra que hace gritar.
COHERENCIA: conexion ideas, desarrollo argumentos, no contradecirse.
INGENIO: ideas nuevas, no repetir, salir con algo que nadie esperaba.
PUESTA EN ESCENA: soltura, confianza, fuerza, decision, lenguaje corporal, teatralidad, contacto visual, energia, presencia, no mostrar nervios. En video SI se evalua con lo que se VE.
RESPUESTAS: respuesta directa con punchline, al tiro o se demora. Sin respuesta = 0.5-1.0 aunque tenga buen flow.
MORBO / HUMOR: Berretin / frases morbosas, humor negro, doble sentido, tallas, picardia, insultos como arte, sin censura.
REGLAS: Rango 0.0-3.0, no generico 2.0-2.3. No castigar ni premiar por ser nino, chico, alto, flaco, gordo. Solo rap. Si nino rapea mejor que adulto que no rima, nino va mas arriba.

FIX CONFUSION + 100% CERTEZA SIN EQUIVOCARSE - OBLIGATORIO:
1. TRANSCRIBE PRIMERO, SIN ATRIBUIR: [00:12-00:18 Voz A: texto literal]. Si no se escucha claro, escribe [NO SE ESCUCHA CLARO - ruido / se pisan] y NO inventes frase.
2. ATRIBUCION CON 3 ANCLAS: (a) Orden formulario "Quien parte y quien sigue" = orden real, NO lo cambies. (b) Ropa formulario = lo que ves en video (ej: MONARKH poleron Kappa Blanco jeans azules mochila, VATRA nino pequeno poleron negro short-buzo azul, MCLOVIN poleron vino jeans azules, EXTRA poleron negro short rojo). (c) Timbre + boca moviendose en video. Si 2 anclas no coinciden, marca ATRIBUCION DUDOSA y no uses esa cita para decidir ganador.
3. PROHIBIDO INVENTAR CITA: Si no puedes transcribir exacto, NO inventes cita generica como "vengo con la estructura..." o "yo me quedo parado tranquilo...". Escribe "No se pudo transcribir exacto" y evalua con lo que SI escuchaste.
4. CONFIANZA POR CITA: ALTA / MEDIA / BAJA. Si BAJA, NO la uses para dar ganador.
5. SI TE CONFUNDES, DILO Y PREGUNTA, NO VOTES: "Posible confusion entre X y Y en 00:XX, no estoy seguro, ¿quien dijo esto?" Mejor decir eso que atribuir mal y tomar mal al ganador.
6. CONFIRMACION ANTES DE VOTAR: Primero manda transcripcion y pregunta "¿Estoy en lo correcto? Si me equivoque en quien dijo que, corrigeme con: Anotado quien es quien: Voz A = VATRA...". Solo cuando usuario confirma, vota. Asi atribucion es 100% porque la valido el humano que estuvo ahi.
7. CADA PUNTAJE CON 1 CITA ALTA: No des 2.2-2.6 generico a todos sin justificar. Cada puntaje debe tener 1 cita ALTA que lo justifique. Si no hay cita ALTA, baja el puntaje o dice que no puede justificarlo.
"""

client_genai = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
pending = {}

async def transcribir_con_gemini(video_path, participantes_text):
    # PASO 1: Solo transcripcion, temperature baja, sin evaluar
    prompt = JURADO_PROMPT + f"\n\nPARTICIPANTES:\n{participantes_text}\n\nPASO 1 SOLO TRANSCRIPCION: Transcribe literal con timestamps, sin evaluar, sin puntaje. Formato [00:12-00:18 Voz A: texto literal]. Si no se escucha claro, escribe [NO SE ESCUCHA CLARO]. NO inventes. NO atribuyas puntaje todavia."
    for modelo in MODELS_TO_TRY[:1]:
        try:
            uploaded = client_genai.files.upload(file=video_path)
            resp = client_genai.models.generate_content(model=modelo, contents=[prompt, uploaded])
            return resp.text, modelo
        except Exception as e:
            print(f"Transcripcion fallo {modelo}: {e}")
            await asyncio.sleep(2)
            continue
    return None, None

async def evaluar_con_gemini(video_path, participantes_text, transcripcion_confirmada):
    prompt = JURADO_PROMPT + f"\n\nPARTICIPANTES:\n{participantes_text}\n\nTRANSCRIPCION CONFIRMADA POR USUARIO (100% CERTEZA):\n{transcripcion_confirmada}\n\nPASO 2 EVALUAR: Con esta transcripcion confirmada, evalua TODO: Flow, Skill, Punchline, Coherencia, Ingenio, Puesta en Escena, Respuestas, Morbo. Da TOP de 8 jurados + tabla final 0.0-3.0. Cita solo si confianza ALTA."
    for modelo in MODELS_TO_TRY[:2]:
        try:
            uploaded = client_genai.files.upload(file=video_path)
            resp = client_genai.models.generate_content(model=modelo, contents=[prompt, uploaded])
            return resp.text, modelo
        except Exception as e:
            print(f"Evaluacion fallo {modelo}: {e}")
            await asyncio.sleep(2)
            continue
    return None, None

@bot.event
async def on_ready():
    print(f"JURADOS BATALLA Discord SOLO VIDEO 100% listo como {bot.user}")

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    if message.attachments:
        for att in message.attachments:
            name = att.filename.lower()
            # AUDIO en Discord -> AVISA y NO procesa
            if any(name.endswith(x) for x in [".mp3", ".ogg", ".wav", ".m4a", ".opus"]):
                await message.channel.send(f"{message.author.mention} 🎤 **Audio va por Telegram, aqui solo video.**\nEn Discord solo analizamos VIDEO (mp4, mov, mkv, webm) para ver bien ropa y puesta en escena.\nManda el audio en el TL y te voto alla.")
                return
            if any(name.endswith(x) for x in [".mp4", ".mov", ".mkv", ".webm"]):
                path = f"/tmp/{att.filename}"
                await att.save(path)
                pending[message.channel.id] = {"video_path": path}
                await message.channel.send(
                    f"✅ Video guardado de {message.author.mention} - SOLO VIDEO en Discord\n\n"
                    f"📝 **FORMULARIO - Copia, llena y manda:**\n"
                    f"```\nParticipantes en orden:\n1: MONARKH - poleron Kappa Blanco jeans azules mochila\n2: VATRA - nino pequeno poleron negro short-buzo azul\n3: MCLOVIN - poleron vino jeans azules\n4: EXTRA - poleron negro short rojo\nQuien parte: MONARKH, VATRA, MCLOVIN, EXTRA\n```\n"
                    f"Despues escribe `TRANSCRIBIR` para transcribir primero, y cuando confirmes, `JURADOS VOTEN`."
                )
                return
    if message.content.strip().lower().startswith("participantes"):
        if message.channel.id in pending:
            pending[message.channel.id]["participantes"] = message.content
            await message.channel.send("✅ Participantes guardados. Escribe `TRANSCRIBIR` para transcribir primero con 100% certeza.")
            return
    # PASO 1: Transcribir
    if message.content.strip().upper() in ["TRANSCRIBIR", "!TRANSCRIBIR"]:
        data = pending.get(message.channel.id)
        if not data:
            await message.channel.send("❌ No hay video pendiente. Manda video primero (aqui solo video, audio va por Telegram).")
            return
        await message.channel.send("🔍 Transcribiendo video con gemini-3.8-flash... sin evaluar todavia, solo transcripcion...")
        participantes = data.get("participantes", "Participantes en orden del video, identifica por voz + ropa + orden.")
        texto, modelo = await transcribir_con_gemini(data["video_path"], participantes)
        if texto:
            pending[message.channel.id]["transcripcion"] = texto
            for i in range(0, len(texto), 1900):
                await message.channel.send(texto[i:i+1900])
            await message.channel.send("✅ Transcripcion lista. **¿Estoy en lo correcto?** Si me equivoque en quien dijo que, corrigeme con: `Anotado quien es quien: Voz A = VATRA...` y despues escribe `JURADOS VOTEN`.")
        else:
            await message.channel.send("❌ 503. Espera 1 min y escribe `TRANSCRIBIR` de nuevo.")
        return
    # Correccion del usuario
    if message.content.strip().lower().startswith("anotado quien es quien"):
        if message.channel.id in pending:
            pending[message.channel.id]["correccion"] = message.content
            await message.channel.send("✅ Correccion guardada. Ahora escribe `JURADOS VOTEN` para votar con 100% certeza.")
            return
    # PASO 2: Votar solo con transcripcion confirmada
    if message.content.strip().upper() in ["JURADOS VOTEN", "!VOTEN", "!JURADOS"]:
        data = pending.get(message.channel.id)
        if not data:
            await message.channel.send("❌ No hay video pendiente. Manda video primero.")
            return
        if "transcripcion" not in data:
            await message.channel.send("⚠️ Primero escribe `TRANSCRIBIR` para transcribir y confirmar, asi votamos con 100% certeza sin confusion.")
            return
        await message.channel.send("🔍 Evaluando con transcripcion confirmada, gemini-3.8-flash...")
        participantes = data.get("participantes", "Participantes en orden del video.")
        trans = data.get("transcripcion", "") + "\nCorreccion usuario: " + data.get("correccion", "Sin correccion, usuario confirmo.")
        texto, modelo = await evaluar_con_gemini(data["video_path"], participantes, trans)
        if texto:
            for i in range(0, len(texto), 1900):
                await message.channel.send(texto[i:i+1900])
            await message.channel.send(f"✅ Votado con {modelo} - Discord SOLO VIDEO 100% certeza")
        else:
            await message.channel.send("❌ 503 en los 2 modelos. Espera 1 min y escribe `JURADOS VOTEN` de nuevo.")
        return
    await bot.process_commands(message)

@bot.command()
async def jurados(ctx):
    await ctx.send("🎤 **JURADOS BATALLA - Discord SOLO VIDEO 100%**\nManda video, escribe `TRANSCRIBIR`, confirma, escribe `JURADOS VOTEN` y voto con 8 jurados, 0.0-3.0, sin inventar, sin confusion.\n🎤 Audio va por Telegram.")

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
