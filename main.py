import os
import re
import asyncio
import tempfile
import time
import subprocess
import random
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes
from google import genai
from google.genai import types
from telethon import TelegramClient
from telethon.sessions import StringSession

# --- CONFIG ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING")

client = None
if GEMINI_API_KEY:
    client = genai.Client(api_key=GEMINI_API_KEY)
    print(f"✅ Gemini client creado correctamente")

user_client = None
if API_ID and API_HASH:
    try:
        api_id_int = int(API_ID)
        if SESSION_STRING:
            user_client = TelegramClient(StringSession(SESSION_STRING), api_id_int, API_HASH)
        else:
            user_client = TelegramClient('user_session', api_id_int, API_HASH)
    except Exception as e:
        print(f"Error Telethon: {e}")
        user_client = None

last_media = {}
last_uploaded_files = {}
last_media_names = {}  # chat_id -> texto con quien es quien dado por usuario
last_media_type = {}  # chat_id -> 'video' o 'audio'

# --- CUOTA TRACKING ---
import json
from datetime import datetime
QUOTA_FILE = "/tmp/bot_quota.json"
DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "20"))  # GRATIS: 20 creditos = 10 batallas x2 (justo tus 10 audios semanales). Si quieres mas, usa DAILY_LIMIT=1000 con flash-lite o API pagada. Aviso cuando quedan 2 creditos.

def load_quota():
    try:
        if os.path.exists(QUOTA_FILE):
            with open(QUOTA_FILE, 'r') as f:
                data = json.load(f)
                today = datetime.now().strftime("%Y-%m-%d")
                if data.get("date") != today:
                    return {"date": today, "used": 0, "limit": DAILY_LIMIT}
                return data
    except:
        pass
    return {"date": datetime.now().strftime("%Y-%m-%d"), "used": 0, "limit": DAILY_LIMIT}

def save_quota(data):
    try:
        with open(QUOTA_FILE, 'w') as f:
            json.dump(data, f)
    except Exception as e:
        print(f"Error guardando quota: {e}")

def get_quota_status():
    data = load_quota()
    # actualizar limit por si cambio env
    data["limit"] = DAILY_LIMIT
    remaining = data["limit"] - data["used"]
    return data["used"], remaining, data["limit"], data

def add_quota_used(n=1):
    data = load_quota()
    data["used"] += n
    data["limit"] = DAILY_LIMIT
    save_quota(data)
    return data["used"], data["limit"] - data["used"]

def reset_quota_if_new_day():
    data = load_quota()
    today = datetime.now().strftime("%Y-%m-%d")
    if data.get("date") != today:
        data = {"date": today, "used": 0, "limit": DAILY_LIMIT}
        save_quota(data)
    return data

def compress_to_420(input_path, output_path):
    try:
        cmd = ["ffmpeg", "-y", "-i", input_path, "-vf", "scale=-2:480", "-c:v", "libx264", "-crf", "30", "-preset", "veryfast", "-c:a", "aac", "-b:a", "64k", output_path]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180)
        return os.path.exists(output_path) and os.path.getsize(output_path) > 1000
    except Exception as e:
        print(f"No se pudo comprimir: {e}")
        return False

def convert_audio_to_mp3(input_path):
    """Convierte cualquier audio a mp3/wav compatible con Gemini - robusto"""
    try:
        base = input_path.rsplit('.', 1)[0]
        # Intento 1: mp3 128k 44.1k stereo
        mp3_path = base + "_converted.mp3"
        cmd = ["ffmpeg", "-y", "-i", input_path, "-c:a", "libmp3lame", "-b:a", "128k", "-ar", "44100", "-ac", "2", mp3_path]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        if os.path.exists(mp3_path) and os.path.getsize(mp3_path) > 1000:
            print(f"Convertido a mp3 128k: {mp3_path} - {os.path.getsize(mp3_path)} bytes")
            return mp3_path
        # Intento 2: mp3 64k mono (mas compatible, mas chico)
        print(f"Fallo 128k, intentando 64k mono: {result.stderr.decode()[:500] if result.stderr else 'no stderr'}")
        mp3_path2 = base + "_converted_64k.mp3"
        cmd2 = ["ffmpeg", "-y", "-i", input_path, "-c:a", "libmp3lame", "-b:a", "64k", "-ar", "22050", "-ac", "1", mp3_path2]
        result2 = subprocess.run(cmd2, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        if os.path.exists(mp3_path2) and os.path.getsize(mp3_path2) > 1000:
            print(f"Convertido a mp3 64k mono: {mp3_path2}")
            return mp3_path2
        # Intento 3: wav 16k mono (formato mas universal para Gemini)
        wav_path = base + "_converted.wav"
        cmd3 = ["ffmpeg", "-y", "-i", input_path, "-c:a", "pcm_s16le", "-ar", "16000", "-ac", "1", wav_path]
        result3 = subprocess.run(cmd3, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        if os.path.exists(wav_path) and os.path.getsize(wav_path) > 1000:
            print(f"Convertido a wav 16k: {wav_path}")
            return wav_path
        print(f"Todos los intentos de conversion fallaron")
        return None
    except Exception as e:
        print(f"Error convirtiendo audio: {e}")
        import traceback
        traceback.print_exc()
        return None

def get_audio_duration(input_path):
    try:
        cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", input_path]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        if result.stdout:
            return float(result.stdout.decode().strip())
    except:
        pass
    return 0


MODELS_TO_TRY = ["gemini-3.8-flash", "gemini-flash-latest", "gemini-2.5-flash", "gemini-3.5-flash-lite", "gemini-2.5-flash-lite"]  # FINAL: FULL primero porque es esencial que le atine a quien es quien. Lite solo de respaldo cuando se acaba cuota gratis. No cambiar a lite primero.

def extract_text_safe(resp):
    try:
        if hasattr(resp, 'candidates') and resp.candidates:
            for cand in resp.candidates:
                fr = getattr(cand, 'finish_reason', None)
                print(f"Finish reason: {fr}")
                if hasattr(cand, 'safety_ratings'):
                    print(f"Safety ratings: {cand.safety_ratings}")
                if hasattr(cand, 'content') and cand.content and hasattr(cand.content, 'parts'):
                    txt = "".join([p.text for p in cand.content.parts if hasattr(p, 'text') and p.text])
                    if txt and txt.strip():
                        return txt
        if hasattr(resp, 'text') and resp.text and resp.text.strip():
            return resp.text
        if hasattr(resp, 'to_dict'):
            d = resp.to_dict()
            try:
                print(f"Full response dict: {d}")
                return d['candidates'][0]['content']['parts'][0]['text']
            except Exception as de:
                print(f"Dict extract fail: {de}")
                pass
        print(f"Respuesta vacía bloqueada por safety, resp: {resp}")
        return None
    except Exception as e:
        print(f"extract_text_safe error: {e}")
        import traceback
        traceback.print_exc()
        return None

async def generate_with_retry(contents, max_retries=5):
    last_error = None
    # Check quota before calling
    used, remaining, limit, _ = get_quota_status()
    if remaining <= 0:
        raise Exception(f"Cuota diaria agotada: {used}/{limit} creditos usados. Espera hasta mañana o usa flash-lite.")
    for attempt in range(max_retries):
        try:
            loop = asyncio.get_running_loop()
            for model_name in MODELS_TO_TRY:
                try:
                    try:
                        safe_config = types.GenerateContentConfig(
                            safety_settings=[
                                types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="BLOCK_NONE"),
                                types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="BLOCK_NONE"),
                                types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_NONE"),
                                types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_NONE"),
                                types.SafetySetting(category="HARM_CATEGORY_CIVIC_INTEGRITY", threshold="BLOCK_NONE"),
                            ],
                            temperature=0.8,
                            top_p=0.95,
                        )
                        resp = await loop.run_in_executor(None, lambda m=model_name, cfg=safe_config: client.models.generate_content(model=m, contents=contents, config=cfg))
                    except Exception as cfg_e:
                        print(f"Fallback sin safety config: {cfg_e}")
                        resp = await loop.run_in_executor(None, lambda m=model_name: client.models.generate_content(model=m, contents=contents))
                    if resp:
                        print(f"Respuesta con modelo {model_name}")
                        return resp
                except Exception as inner_e:
                    inner_msg = str(inner_e).lower()
                    if "404" in inner_msg or "not_found" in inner_msg or "no longer available" in inner_msg:
                        print(f"Modelo {model_name} no disponible, probando siguiente... {inner_e}")
                        continue
                    else:
                        raise inner_e
            raise Exception("Ningún modelo disponible")
        except Exception as e:
            last_error = e
            msg = str(e).lower()
            if "quota" in msg and "exceeded" in msg or "resource_exhausted" in msg and "quota" in msg:
                print(f"Cuota excedida: {e}")
                raise e
            if "503" in msg or "unavailable" in msg or "429" in msg or "high demand" in msg or "overloaded" in msg or "not in an active state" in msg or "failed_precondition" in msg:
                wait = (attempt + 1) * 6 + random.uniform(1, 3)
                print(f"Gemini saturado (intento {attempt+1}/{max_retries}), esperando {wait:.1f}s... {e}")
                await asyncio.sleep(wait)
                if "not in an active state" in msg or "failed_precondition" in msg:
                    try:
                        for c in contents:
                            if hasattr(c, 'name') or (hasattr(c, 'state')):
                                try:
                                    refreshed = await loop.run_in_executor(None, lambda: client.files.get(name=c.name))
                                    if refreshed.state.name == "ACTIVE":
                                        idx = contents.index(c)
                                        contents[idx] = refreshed
                                except:
                                    pass
                    except:
                        pass
                continue
            else:
                raise e
    raise last_error

async def download_via_telethon(chat_id, message_id):
    if not user_client:
        return None
    try:
        await user_client.connect()
        msg = await user_client.get_messages(chat_id, ids=message_id)
        if msg and msg.media:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tf:
                path = await user_client.download_media(msg, tf.name)
                return path
    except Exception as e:
        print(f"Telethon download error: {e}")
    finally:
        try:
            await user_client.disconnect()
        except:
            pass
    return None

async def post_init(app):
    if user_client:
        try:
            await user_client.connect()
            print("✅ Telethon user client conectado")
        except Exception as e:
            print(f"Error conectando Telethon: {e}")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔥 Bot JURADO MAESTRO + BOSS activo. Manda audio/video y escribe JURADOS VOTEN. x2 creditos por batalla. Identifica voces por timbre, soporta 2-4+ participantes, sin robo. Usa /status para ver cuota y ultimo archivo.")

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    has_media = chat_id in last_media and os.path.exists(last_media[chat_id])
    has_names = chat_id in last_media_names
    media_type = last_media_type.get(chat_id, "ninguno")
    used, remaining, limit, data = get_quota_status()
    text = f"📊 STATUS BOT\n\n- Chat ID: {chat_id}\n- Ultimo archivo: {'SI' if has_media else 'NO'} ({media_type})\n- Quien es quien: {last_media_names.get(chat_id, 'NO') if has_names else 'NO'}\n- Cuota hoy ({data.get('date')}): {used//2} audios/videos usados de {limit//2}\n- Te quedan: {remaining//2} audios/videos hoy ({remaining} creditos)\n- Modelos: {', '.join(MODELS_TO_TRY)}\n"
    if remaining <= 2:
        text += f"\n⚠️ TE QUEDA {remaining//2} AUDIO/VIDEO MÁS HOY. Mañana se resetea gratis. Si necesitas más hoy, el bot usará lite (500-1000/dia) pero con menos precisión."
    elif remaining <= 4:
        text += f"\n⚠️ Te quedan {remaining//2} audios/videos hoy. Para tus 10 audios del finde, mañana se resetea."
    await update.message.reply_text(text)
    print(f"STATUS {chat_id}: has_media={has_media}, quota {used}/{limit} remaining {remaining}")

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    message = update.message
    print(f"📥 MEDIA RECIBIDO de {chat_id} - audio:{bool(message.audio)} voice:{bool(message.voice)} video:{bool(message.video)} doc:{bool(message.document)} doc_mime:{message.document.mime_type if message.document else 'N/A'}")
    file_obj = None
    if message.audio:
        file_obj = message.audio
    elif message.voice:
        file_obj = message.voice
    elif message.video:
        file_obj = message.video
    elif message.document:
        file_obj = message.document
    if not file_obj:
        print(f"❌ No se encontro file_obj para {chat_id}")
        await update.message.reply_text("❌ No pude leer el archivo, intenta mandarlo como documento o audio")
        return
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tf:
            temp_path = tf.name
        new_file = await context.bot.get_file(file_obj.file_id)
        await new_file.download_to_drive(temp_path)
        last_media[chat_id] = temp_path
        # Limpiar identificación anterior para no arrastrar nombres de batallas pasadas - cada archivo es batalla nueva
        if chat_id in last_media_names:
            del last_media_names[chat_id]
        
        # Detectar tipo
        is_vid = False
        if message.video or (message.document and message.document.mime_type and "video" in message.document.mime_type):
            is_vid = True
            last_media_type[chat_id] = "video"
        else:
            last_media_type[chat_id] = "audio"
            is_vid = False
        
        if is_vid:
            await update.message.reply_text(
                "✅ Video guardado.\n\n"
                "👀 ¿Quién es quién? (si el host no dijo nombres)\n"
                "Escribe por ejemplo:\n"
                "• El de polera negra = A, el de polera blanca = B\n"
                "• El que empieza = Juan, el segundo = Pedro\n"
                "• Izquierda = uno, derecha = otro\n\n"
                "Luego escribe JURADOS VOTEN"
            )
        else:
            await update.message.reply_text(
                "✅ Audio guardado.\n\n"
                "🎤 ¿Quién es quién?\n"
                "Escribe por ejemplo:\n"
                "• Voz 1 (el que parte) = Juan, Voz 2 = Pedro\n"
                "• El que empieza es A y el que sigue es B\n"
                "• Si son 3: Voz 1 = X, Voz 2 = Y, Voz 3 = Z\n\n"
                "Luego escribe JURADOS VOTEN"
            )
    except Exception as e:
        print(f"❌ Error guardando media {chat_id}: {e}")
        import traceback; traceback.print_exc()
        await update.message.reply_text(f"❌ Error guardando: {e}")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text:
        return
    upper = text.upper()
    chat_id = update.effective_chat.id

    # Detectar JURADOS VOTEN con tolerancia a typos: votenj, voten, VOTEN, etc
    is_jurados_voten = ("JURADOS" in upper and "VOTEN" in upper) or ("JURADO" in upper and "VOTE" in upper)

    # Si no es JURADOS VOTEN, ver si es info de quien es quien
    if not is_jurados_voten:
        # Si el texto parece identificacion (contiene = o : o "es" + nombre o polera o voz)
        lower = text.lower()
        # normalizar para detectar Voz:1, Voz :1, Voz1: etc
        import re as re_id
        has_voz_pattern = bool(re_id.search(r'voz\s*:*\s*\d+|voz\s+\d+\s*[:=]|voz\d+', lower))
        is_identification = (
            has_voz_pattern or
            "=" in text or
            ":" in text and ("voz" in lower or "polera" in lower ) or
            "polera" in lower or
            "camisa" in lower or
            "gorro" in lower or
            "izquierda" in lower or
            "derecha" in lower or
            "empieza" in lower or
            "parte" in lower or
            "voz 1" in lower or
            "voz1" in lower or
            "voz 2" in lower or
            "voz 3" in lower or
            "voz 4" in lower or
            "voz 5" in lower or
            "voz2" in lower or
            "voz3" in lower or
            "el de" in lower or
            ("es" in lower and len(text.split()) <= 15)
        )
        # Si parece identificacion pero no hay media, avisar en vez de silencio (fix para tu amigo)
        if is_identification and len(text) < 300:
            if chat_id in last_media:
                last_media_names[chat_id] = text
                await update.message.reply_text(f"✅ Anotado quién es quién: {text}\n\nAhora escribe JURADOS VOTEN")
            else:
                await update.message.reply_text("❌ No hay audio/video guardado para anotar voces. Manda el audio/video primero, luego pon Voz 1: X Voz 2: Y")
            return
        return

    # Si llega aqui, es JURADOS VOTEN (con tolerancia a typo votenj)
    if chat_id not in last_media or not os.path.exists(last_media[chat_id]):
        await update.message.reply_text("❌ No hay audio/video guardado. Manda un audio/video primero y luego escribe JURADOS VOTEN. Si el bot se reinició, se borró la memoria, manda el audio de nuevo.")
        return

    temp_path = last_media[chat_id]
    compressed_path = None
    g_file = None

    try:
        # Preparar archivo
        final_path = temp_path
        # Detectar duracion
        duration = get_audio_duration(temp_path)
        print(f"Duracion audio: {duration}s, archivo: {temp_path}, size: {os.path.getsize(temp_path) if os.path.exists(temp_path) else 0}")

        if temp_path.lower().endswith((".mp4", ".mov", ".avi", ".mkv", ".webm")) or "video" in temp_path.lower():
            compressed_path = temp_path + "_480p.mp4"
            if compress_to_420(temp_path, compressed_path):
                final_path = compressed_path
        else:
            # SIEMPRE convertir audio a mp3 compatible (WhatsApp m4a a veces tiene codec opus raro que Gemini rechaza)
            # Esto arregla el error FAILED de AUD-20260911-WA0039.m4a
            mp3 = convert_audio_to_mp3(final_path)
            if mp3:
                await update.message.reply_text(f"🔄 Audio convertido a mp3 compatible ({duration:.0f}s)")
                final_path = mp3
            else:
                await update.message.reply_text("⚠️ No se pudo convertir audio, intentando subir original...")
            # Si dura mas de 10 min, advertir
            if duration > 600:
                await update.message.reply_text(f"⚠️ Audio muy largo ({duration/60:.1f} min), Gemini puede fallar. Si falla, manda clips mas cortos.")

        # Upload a Gemini con reintentos y manejo de FAILED
        async def upload_and_wait(path, attempt_convert=True):
            loop = asyncio.get_running_loop()
            def _upload(p):
                f = client.files.upload(file=p)
                return f
            def _get(name):
                return client.files.get(name=name)
            def _delete(name):
                try:
                    client.files.delete(name=name)
                except:
                    pass

            uploaded = await loop.run_in_executor(None, lambda: _upload(path))
            print(f"Archivo subido: {uploaded.name}, esperando ACTIVE...")
            retries = 0
            while retries < 25:
                try:
                    rf = await loop.run_in_executor(None, lambda: _get(uploaded.name))
                    state = getattr(rf, 'state', None)
                    state_name = getattr(state, 'name', str(state)) if state else "ACTIVE"
                    print(f"Estado archivo {uploaded.name}: {state_name}")
                    if state_name == "ACTIVE" or state is None or state_name == "":
                        print(f"Archivo ACTIVE: {uploaded.name}")
                        return rf
                    if "FAILED" in state_name:
                        print(f"Archivo FAILED: {uploaded.name}, borrando y probando conversion alternativa")
                        await loop.run_in_executor(None, lambda: _delete(uploaded.name))
                        if attempt_convert:
                            # Intentar convertir a formato mas compatible y resubir
                            alt_path = convert_audio_to_mp3(path)
                            if alt_path and alt_path != path and os.path.exists(alt_path):
                                print(f"Reintentando upload con archivo convertido: {alt_path}")
                                return await upload_and_wait(alt_path, attempt_convert=False)
                        raise Exception(f"Archivo en estado FAILED tras conversion - archivo original puede estar corrupto o muy largo. Intenta con un audio mas corto o reenvialo como mp3.")
                    await asyncio.sleep(2)
                    retries += 1
                except Exception as ue:
                    if "FAILED" in str(ue):
                        raise ue
                    print(f"Error esperando archivo: {ue}, reintento {retries}")
                    await asyncio.sleep(2)
                    retries += 1
            print(f"Timeout esperando ACTIVE, retornando ultimo estado")
            return uploaded

        await update.message.reply_text("🔍 Analizando con Gemini 3.8 Flash... (si está saturado reintenta solo) 20-60 seg")
        g_file = await upload_and_wait(final_path)
        last_uploaded_files[chat_id] = g_file

        evaluaciones_texto = ""

        # Detectar si es VIDEO o AUDIO para PUESTA EN ESCENA - FIX: usar last_media_type, no extension .mp4 del temp
        # El temp file siempre es .mp4 aunque sea audio, asi que la fuente de verdad es last_media_type
        stored_type = last_media_type.get(chat_id, "audio")
        is_video = stored_type == "video"
        is_audio = not is_video
        print(f"Tipo detectado: {'VIDEO' if is_video else 'AUDIO'} - stored_type={stored_type} - {final_path} - mime check")
        # Si final_path contiene audio convertido (mp3/wav) es audio aunque temp original era mp4
        if final_path.lower().endswith((".mp3", ".wav", ".ogg", ".m4a", ".opus")):
            is_video = False
            is_audio = True
            print(f"Override a AUDIO por extension de audio: {final_path}")

        # Check cuota antes de evaluar y avisar si quedan pocos
        used_q, rem_q, lim_q, _ = get_quota_status()
        if rem_q <= 2 and rem_q > 0:
            await update.message.reply_text(f"⚠️ ATENCION: Te queda {rem_q//2} audio/video más hoy (quedan {rem_q} creditos de {lim_q}). Esta batalla gasta 1 audio/video. Después se acaba la cuota gratis diaria. Mañana se resetea solo. Si necesitas más hoy, el bot pasará a lite (más audios pero menos preciso).")
        elif rem_q <= 0:
            await update.message.reply_text(f"⛔ Cuota agotada hoy: {used_q}/{lim_q}. No puedes evaluar más hasta mañana. Cambia a flash-lite para 500-1000/dia o espera.")
            return

        # Info de quien es quien dada por usuario
        user_names_info = last_media_names.get(chat_id, "")
        if user_names_info:
            print(f"Usuario dijo quien es quien: {user_names_info}")
            # Contar participantes segun lo que escribio usuario - robusto para Voz:1, Voz: 1, Voz1: etc
            import re as re_count
            # busca Voz con cualquier separador : = espacio y numero
            voz_matches = re_count.findall(r'voz\s*:*\s*\d+|voz\s+\d+\s*[:=]*|voz\d+', user_names_info.lower())
            # extraer numeros de voz
            voz_nums = []
            for m in re_count.finditer(r'voz\D*(\d+)', user_names_info.lower()):
                try:
                    voz_nums.append(int(m.group(1)))
                except:
                    pass
            if voz_nums:
                num_part = max(voz_nums)  # si dice Voz 1 y Voz 2, son 2; si dice Voz 1,2,3 son 3
                # tambien contar unicos
                if len(set(voz_nums)) > num_part:
                    num_part = len(set(voz_nums))
                if num_part < 2:
                    num_part = 2
            else:
                # contar por separadores , o : o = - GENERAL, sin nombres fijos
                parts = re_count.split(r'[,;]+', user_names_info)
                num_part = max(2, len([p for p in parts if p.strip()]))
                # Si menciona 3 voces explicitas, son 3
                if user_names_info.lower().count("voz") >= 3 or user_names_info.count(":") >=3 or user_names_info.count("=")>=3 or len(parts)>=3:
                    # solo si detecta 3 voces distintas
                    if len(set(voz_nums)) >= 3 or len(parts) >=3:
                        num_part = max(num_part, 3)
            # Parsear nombres reales para prompt
            parsed_names = []
            # intenta extraer Nombre despues de Voz X
            for mm in re_count.finditer(r'voz\s*:*\s*\d+\s*[:=]*\s*([A-Za-z0-9_]+)', user_names_info, flags=re.IGNORECASE):
                name = mm.group(1).strip()
                if name and len(name)>=1:  # permite X/Y/Z single letter
                    parsed_names.append(name)
            # Si no pudo con regex, intenta split simple
            if not parsed_names:
                # ejemplo "Voz:1 X Voz 2:Y" -> extraer palabras que no son voz/numero - GENERAL/: =
                tokens = re_count.split(r'voz\s*:*\s*\d+\s*[:=]*', user_names_info, flags=re.IGNORECASE)
                for tok in tokens:
                    tok = tok.strip(" :=,;")
                    if tok:
                        # tomar primera palabra
                        first = tok.split()[0] if tok.split() else ""
                        if first and first.lower() not in ["y", "vs", "con", "de"]:
                            parsed_names.append(first)
            # Construir texto de nombres detectados
            names_str = ", ".join(parsed_names) if parsed_names else user_names_info
            
            extra_names_prompt = f"\n\n🔥 INFORMACION EXTRA DADA POR EL USUARIO SOBRE QUIEN ES QUIEN (OBLIGATORIO RESPETAR - GENERAL PARA CUALQUIER BATALLA): {user_names_info}\nNOMBRES DETECTADOS: {names_str}\nEL USUARIO DICE QUE SON {num_part} PARTICIPANTES. DEBES EVALUAR EXACTAMENTE {num_part} VOCES EN CADA JURADO, NO 2 SI SON 3, NO 1 SI SON 2. Usa EXACTAMENTE estos nombres: {names_str}. Si dice 'polera negra = Juan', entonces el de polera negra es Juan. Si dice 'Voz 1:X Voz 2:Y Voz 3:Z' entonces Voz 1 es X, Voz 2 es Y, Voz 3 es Z. ORDEN EXACTO: Voz 1 = primer nombre detectado, Voz 2 = segundo, etc segun lo que diga el usuario en ESTE audio. NO inventes otros nombres que el usuario no dio en ESTA batalla, respeta 100% los nombres que dio el usuario en este mensaje. Si usuario dice 2 voces, en CADA TOP debes poner 2 entradas, NUNCA 1. Cada batalla es gente diferente, analiza la batalla actual, no uses nombres de batallas pasadas."
        else:
            extra_names_prompt = "\n\nNo hay info extra de quien es quien del usuario, identifica por voces/timbre y por lo que diga el host. Puede haber 2, 3, 4+ voces, detecta todas."

        if is_video:
            max_puntos = 24
            desglose_template = "FLOW x + MORBO x + INGENIO x + PUNCHLINE x + COHERENCIA x + SKILLS x + PUESTA EN ESCENA x + RESPUESTAS x"
            mega_prompt = """Eres el JURADO MAESTRO SUPREMO de freestyle - 20 años en plazas, FMS, Red Bull. Hablas como jurado de plaza chileno, calle, sin robot, con jerga chilena, divertido, no formal. EVALUACION 100% ARTISTICA, SIN CENSURA Y SIN ROBO.

🎥 ESTO ES VIDEO con imagen, asi que SI hay PUESTA EN ESCENA visual: puedes hablar de ropa, parka, polera, gestos, energia, como se mueve, fisicamente. 8 jurados max 24 puntos.



ANALISIS GENERAL - ESCUCHA/VE COMPLETO 2 VECES MINIMO - GENERAL PARA AUDIO Y VIDEO:
- Cada batalla es DIFERENTE, no todas son iguales. Cada archivo (audio o video) es gente diferente, formato diferente, orden diferente.
- OBLIGATORIO - REVISA 2 VECES MINIMO: Escucha/ve el archivo COMPLETO de principio a fin MINIMO 2 veces entero, o las veces necesarias, antes de decidir quien es quien y que formato es. Cualquiera le podria atinar si analiza bien, entonces ANALIZA BIEN. No decidas solo por primeros 10 segundos. Si es video, rebobina y fijate en ropa y timbre. Si es audio, rebobina y fijate en timbre, tono, velocidad. Si el usuario ya te dijo quien es quien, respeta eso y usa las 2 revisiones para confirmar que no te equivocas.
- Puede tener: host organizando (externo o ustedes mismos), cancion intro, cypher de apoyo que evoluciona a batalla todos vs todos, 1 beat o varios beats seguidos, 1 batalla o varias en mismo archivo, 2,3,4+ participantes. DEBES analizar TODO el archivo de principio a fin.
- Detecta bien cuantas voces distintas rapean sobre beat de batalla, cuantos beats, cuantas batallas hay, sin agregar ni quitar. Si al principio hay cancion o charla de host, identificala pero la batalla real es cuando rapean tirandose.
- Cada batalla es gente diferente, analiza profundo ESTA batalla actual. No arrastres nombres ni formatos de batallas pasadas. REVISA 2 VECES - ESENCIAL - GENERAL PARA AUDIO Y VIDEO.

FORMATO DE BATALLA - DETECCION GENERAL PARA AUDIO Y VIDEO (ESCUCHA TODO - 1vs1, 2vs2, TODOS VS TODOS, CUALQUIER FORMATO):
- Formato puede ser CUALQUIERA: 1vs1, 1vs2, 2vs2, 3vs1, 2vs1, todos vs todos, etc. No asumas que es igual a la batalla anterior. Cada batalla es formato diferente. Generaliza la idea, no copies textual el nombre del formato que dijo el usuario.
- OBLIGATORIO: Escucha/ve TODO el archivo completo para saber el formato. No decidas formato solo por el inicio.
- POSIBLES FORMATOS QUE DEBES DETECTAR (GENERAL):
  * Apoyo inicial que evoluciona: al principio se apoyan (cypher) y luego poco a poco se van tirando hasta todos vs todos. Es batalla que evoluciona de apoyo a ataque.
  * Equipo real 2vs2: si uno defiende a su compañero CONSTANTEMENTE durante TODA la batalla es equipo.
  * Todos vs todos: si al final todos se tiran entre todos sin defender, es todos vs todos aunque al principio se apoyaron.
  * 1vs1, 2vs1, 5 vidas, filtro de mejores, etc: detecta por quien ataca a quien durante TODO el archivo. No importa el nombre del formato, lo importante es evaluar quien gano y categorias.
- Si uno defiende a otro solo para usarlo en un punch (ej: "mi compa te gana"), no siempre es equipo, puede ser estrategia.
- Analiza bien TODO lo que dice cada uno de principio a fin para saber formato real de ESTA batalla (audio o video). No asumas que es igual a la anterior. GENERAL PARA AUDIO Y VIDEO.

TAREA 1: IDENTIFICAR VOCES POR TIMBRE - GENERAL PARA AUDIO Y VIDEO (CRITICO - ANALIZA BIEN - REVISA 2 VECES MINIMO - ESENCIAL QUE LE ATINE):
- OBLIGATORIO ANALISIS PROFUNDO GENERAL: Cualquiera le podria atinar si analiza bien, entonces ANALIZA BIEN. Revisa el archivo (audio o video) COMPLETO 2 veces MINIMO, o las veces necesarias, para entender quien es quien. No es opcional, ES OBLIGATORIO rever 2 veces antes de decidir orden. Si es VIDEO, fijate en timbre + ropa (polera negra/blanca) + forma de hablar. Si es AUDIO, fijate en timbre + tono + velocidad + respiracion + muletillas. GENERAL PARA AMBOS.
- Separa voces por timbre, tono, velocidad, respiracion, muletillas. En video tambien por ropa. Cada voz distinta que RAPEA sobre beat de batalla = participante. MINIMO 2 voces siempre. Si crees que hay 1, vuelve a escuchar/ver TODO el archivo 2 veces mas buscando cambio de timbre y de ropa (si es video).
- ORDEN DADO POR USUARIO ES LEY ABSOLUTA - NO LO REINTERPRETES - GENERAL: Si el usuario te dice Voz 1:X Voz 2:Y Voz 3:Z, tu NO debes adivinar timbre para cambiar orden. USA EXACTAMENTE ESE ORDEN. El usuario ya te dijo quien es quien, no lo cambies. Si te dice polera negra = Juan (video) o Voz 1 = Juan (audio), entonces es Juan 100%, no lo discutas. El bot pregunta quien es quien y ustedes le dicen, pero despues se equivoca porque no respeta lo que le dijeron. AHORA RESPETA 100% LO QUE LE DIJO EL USUARIO - GENERAL PARA AUDIO Y VIDEO.
- HOST: Al principio a veces ustedes mismos son host organizando quien parte (ej: 'ya, parte tu'). En ese caso HOST = FREESTYLERS, esa charla previa ES de los participantes, usala para identificar timbres por como hablan, pero NO la evalues como rap. La batalla real empieza cuando suena el beat y rapean. Si host es externo que solo organiza y no rapea, IGNORA esa voz.
- CANCION/INTRO: Si al principio hay cancion cantando o charla, identificala pero no la tomes como Voz 1 de batalla si no estaba rapeando batalla.
- EVOLUCION: Si al principio se apoyan (cypher) y luego se van tirando cada vez mas hasta todos vs todos o 2vs2, sigue contando las mismas voces de principio a fin. Es la misma batalla que evoluciona. Identifica timbres desde inicio aunque se apoyen.
- Si host dice nombres al inicio usalos para identificar. Si dicen Yo soy X, usa ese nombre.
- Si es 1vs1 son 2 voces, si es triangular todos vs todos son 3, si es 2vs2 son 4, etc. Cuenta bien solo los que rapean en batalla. Si usuario dijo que son 3, en CADA TOP pon 3 entradas en el mismo orden que dio.
- Cada batalla es gente diferente, no asumas siempre los mismos. Manten orden en los 8 jurados segun lo que dijo el usuario en ESTA batalla. REVISA ARCHIVO 2 VECES PARA NO EQUIVOCARTE - ANALIZA BIEN - GENERAL.
- VERIFICACION DE ORDEN - GENERAL PARA AUDIO Y VIDEO - PARA QUE USUARIO SEPA SI ESTA MAL Y PUEDA CORREGIR:
  * Si es VIDEO, describe timbre y ropa: "Voz 1 (grave, rapido, polera negra) = X, Voz 2 (agudo, lento, polera blanca) = Y"
  * Si es AUDIO, describe solo timbre: "Voz 1 (grave, rapido, respira fuerte) = X, Voz 2 (agudo, lento, muletilla 'eh') = Y"
  * Si usuario dio orden exacto Voz 1:X Voz 2:Y, RESPETALO 100% y no lo cambies. Si no dio orden, detecta tu pero pon al inicio: "Detecte: Voz 1 = [timbre+ropa si es video / timbre solo si es audio], Voz 2 = [...] - Si este orden esta mal, mandame 'Voz 1:X Voz 2:Y correcto' y re-evaluo".
  * Revisa archivo 2 veces para confirmar que no te equivocas despues de que usuario te dijo quien es quien. GENERAL PARA AUDIO Y VIDEO.
- No hagas robo/tongo. NUNCA evalue solo 1 voz si es batalla. NUNCA cambies el orden que dio el usuario. GENERAL.

ESTILO DE RESPUESTA - NO ROBOT, PLAZA CHILENA - COMPARACIONES SOLO CUANDO SEA MUY SIMILAR (GENERAL PARA TODAS LAS CATEGORIAS):
- No hables como robot tecnico. Habla como jurado de plaza chileno, natural, calle, con jerga, divertido.
- Usa jerga chilena: brigido, piola, pulento, caleta, se paso, filete, etc, sin ser ofensivo extra. Es evaluacion artistica.
- Pon ejemplos de lo que dijeron para justificar cada nota.
- COMPARACIONES CON FREESTYLERS CONOCIDOS - GENERAL PARA TODAS LAS CATEGORIAS (FLOW, MORBO, INGENIO, PUNCHLINE, COHERENCIA, SKILLS, PUESTA EN ESCENA, RESPUESTAS):
  * Puedes comparar con freestylers conocidos de nivel mundial (FMS, DEM, BDM, Red Bull, God Level, etc) cuando notes que el estilo es muy similar y aporte a la explicacion.
  * Solo cuando sea muy parecido y consideres necesario, no en cada categoria. No es obligatorio comparar siempre.
  * Conocidos: chilenos e internacionales de renombre. Usa tu conocimiento amplio.
  * Aplica para todas las categorias por igual: si el flow es muy fluido puedes compararlo, si el morbo es muy picante y negro puedes compararlo, si el punchline es muy contundente, si el ingenio es muy rebuscado, si la coherencia es muy narrativa, si los skills son muy tecnicos, si la puesta en escena es muy teatral, si las respuestas son muy al hueso. En cualquiera de esas categorias, si ves similitud fuerte, comparas.
  * No pongas comparacion forzada. Si no es muy similar, no compares. Prioriza explicar con jerga chilena que poner nombre por poner.
- FLOW: describe como encaja en el beat, cambios de ritmo, si se sube bien.
- MORBO: describe que tan picante, gracioso, sin filtro, que tan hiriente fue.
- INGENIO: describe metaforas, doble sentido, creatividad.
- PUNCHLINE: describe remate, si dolio, si cerro bien.
- COHERENCIA: describe si mantuvo argumento, si no se contradijo.
- SKILLS: describe tecnica, multis, referencias.
- PUESTA EN ESCENA (solo video): describe energia, gestos, dominio escenario.
- RESPUESTAS: describe si contesto altiro, si dio vuelta lo que le dijeron.
- General para todas las categorias, mismo nivel de detalle, no solo flow.

REGLA DE ORO INQUEBRANTABLE: Cuenta cuantas voces distintas hay en TODO el archivo. MINIMO 2 VOCES SIEMPRE, NUNCA 1. Si es 1vs1 son 2, si usuario dijo que son 3 (ej: Voz1:Z Voz2:Y Voz3:X), entonces SON 3, no 2 ni 1. En CADA TOP pon EXACTAMENTE N entradas, mismo orden, sin excepcion. Si son 2 participantes, debes poner 2 lineas en FLOW, 2 en MORBO, etc. Si son 3 participantes, debes poner 3 lineas en FLOW, 3 en MORBO, 3 en INGENIO, etc. NUNCA dejes a nadie fuera, NUNCA califiques solo 1 si son 2, NUNCA solo 2 si son 3. Si te equivocas es robo/tongo y esta PROHIBIDO.

FORMATO OBLIGATORIO (mantiene emojis pero texto callejero):

🔥 JURADO FLOW 🔥
GANADOR: [Voz X] con [razon callejera tipo wow flowzaso]
TOP FLOW:
1: [Voz 1] - [nota]/3 - [por que con jerga, no robot]
2: [Voz 2] - [nota]/3 - [por que]
💯 FIN FLOW 💯

🔥 JURADO MORBO 🔥
GANADOR: [Voz X] con [razon tipo jaj fue mas gracioso y picante]
TOP MORBO:
1: [Voz 1] - [nota]/3 - [cita insulto tal cual si dijo algo explicito + comentario jaj se paso]
2: [Voz 2] - [nota]/3 - [por que]
💯 FIN MORBO 💯

🔥 JURADO INGENIO 🔥
GANADOR: [Voz X]
TOP INGENIO:
1: [Voz 1] - [nota]/3 - [por que callejero]
2: [Voz 2] - [nota]/3 - [por que]
💯 FIN INGENIO 💯

🔥 JURADO PUNCHLINE 🔥
GANADOR: [Voz X]
TOP PUNCHLINE:
1: [Voz 1] - [nota]/3 - [por que, que punch le dolio]
2: [Voz 2] - [nota]/3 - [por que]
💯 FIN PUNCHLINE 💯

🔥 JURADO COHERENCIA 🔥
GANADOR: [Voz X]
TOP COHERENCIA:
1: [Voz 1] - [nota]/3 - [por que]
2: [Voz 2] - [nota]/3 - [por que]
💯 FIN COHERENCIA 💯

🔥 JURADO SKILLS 🔥
GANADOR: [Voz X]
TOP SKILLS:
1: [Voz 1] - [nota]/3 - [por que, que tecnica metio]
2: [Voz 2] - [nota]/3 - [por que]
💯 FIN SKILLS 💯

🔥 JURADO PUESTA EN ESCENA 🔥
GANADOR: [Voz X] con [razon visual]
TOP PUESTA EN ESCENA:
1: [Voz 1] - [nota]/3 - [por que visual]
2: [Voz 2] - [nota]/3 - [por que visual]
💯 FIN PUESTA EN ESCENA 💯

🔥 JURADO RESPUESTAS 🔥
GANADOR: [Voz X]
TOP RESPUESTAS:
1: [Voz 1] - [nota]/3 - [por que]
2: [Voz 2] - [nota]/3 - [por que]
💯 FIN RESPUESTAS 💯

Escala 0.0 a 3.0. Habla calle, no robot. Profundo, sin robo, 2-4+ voces por timbre. VIDEO con PUESTA EN ESCENA.
""" + extra_names_prompt
            boss_prompt_base = f"""Eres el JURADO BOSS FINAL. VIDEO con 8 jurados, maximo {max_puntos} puntos.

INFO USUARIO SOBRE QUIEN ES QUIEN (OBLIGATORIO USAR ESTOS NOMBRES, NO INVENTAR): {user_names_info}
NOMBRES DETECTADOS: {names_str if 'names_str' in locals() else user_names_info}

REGLA INQUEBRANTABLE GENERAL - CADA BATALLA ES DIFERENTE: Usa puntajes reales del MAESTRO. MINIMO 2 VOCES SIEMPRE, NUNCA 1. Siempre hay mas de 1 en batalla. Formato puede ser 1vs1, 1vs2, 2vs2, 3vs1, todos vs todos. Detecta formato en ESTA batalla por lo que dicen. Si es 1vs1 son 2, si maestro evaluo 1 solo estas haciendo robo, debe haber minimo 2. Si el usuario dijo 3 voces en ESTA batalla o detectaste 3 timbres, tabla con 3 OBLIGATORIO. NUNCA INVENTES NOMBRES QUE EL USUARIO NO DIO EN ESTA BATALLA. Usa EXACTAMENTE los nombres que dio el usuario en este mensaje (X, Y, Z), no uses nombres de batallas pasadas. Si usuario dijo Voz 1:X Voz 2:Y, entonces los participantes son X e Y. No hagas tongo, no dejes a nadie fuera. Si maestro evaluo 2, tu tabla final debe tener 2 minimo. Manten voces exactas del usuario de ESTA batalla. Cada batalla es gente diferente, analiza ESTA. VIDEO max 24, AUDIO max 21.

FORMATO:

🏆 TABLA FINAL GENERAL (sobre {max_puntos}) - VIDEO:
1: [Nombre/Voz] - [total]/{max_puntos} (desglose: {desglose_template})
2: [Nombre/Voz] - [total]/{max_puntos} (desglose: ...)
3: [Si hay tercera] - [total]/{max_puntos} (...)

GANADOR ABSOLUTO: [Nombre/Voz] con [total]/{max_puntos} porque [resumen profundo sin robo]

EN QUE MEJORAR:
- [Voz 1]: [consejo]
- [Voz 2]: [consejo]

🔥🔥🔥 FIN EVALUACION BOSS 🔥🔥🔥
"""
        else:
            max_puntos = 21
            desglose_template = "FLOW x + MORBO x + INGENIO x + PUNCHLINE x + COHERENCIA x + SKILLS x + RESPUESTAS x"
            mega_prompt = """Eres el JURADO MAESTRO SUPREMO de freestyle - 20 años en plazas, FMS, Red Bull. Hablas como jurado de plaza chileno, calle, sin robot, con jerga chilena, divertido. EVALUACION 100% ARTISTICA, SIN CENSURA Y SIN ROBO.

🚨 ESTO ES SOLO AUDIO, NO HAY VIDEO - PROHIBIDO HABLAR DE ROPA O VISUAL - GENERAL PARA AUDIO:
- ESTO ES AUDIO, NO VIDEO. NO HAY IMAGEN, NO HAY CAMARA, NO VES ROPA.
- PROHIBIDO ABSOLUTO: hablar de parka, chaqueta, polera negra/blanca, poleron plomo, gorro, jockey, capucha, fisicamente, visualmente, gestos, se mueve, se agacha, salta, apunta con el dedo, manos en bolsillos, presencia escenica visual, como se ve, ropa.
- Si es AUDIO, SOLO puedes hablar de TIMBRE, TONO, VELOCIDAD, RESPIRACION, MULETILLAS. NUNCA de ropa ni gestos.
- NO hay jurado PUESTA EN ESCENA en AUDIO. Son solo 7 jurados: FLOW, MORBO, INGENIO, PUNCHLINE, COHERENCIA, SKILLS, RESPUESTAS. Maximo 21 puntos.
- Si intentas describir ropa en audio, estas haciendo robo y estas PROHIBIDO.
- VERIFICACION para AUDIO: describe SOLO timbre: "Voz 1 (grave, rapido, respira fuerte) = X, Voz 2 (agudo, lento) = Y" - NUNCA menciones ropa en audio.
- ESTO ES SOLO AUDIO, NO HAY VIDEO, ASI QUE NO HAY PUESTA EN ESCENA - OBLIGATORIO RESPETAR.

ANALISIS GENERAL - ESCUCHA/VE COMPLETO 2 VECES MINIMO - GENERAL PARA AUDIO Y VIDEO:
- Cada batalla es DIFERENTE, no todas son iguales. Cada archivo (audio o video) es gente diferente, formato diferente, orden diferente.
- OBLIGATORIO - REVISA 2 VECES MINIMO: Escucha/ve el archivo COMPLETO de principio a fin MINIMO 2 veces entero, o las veces necesarias, antes de decidir quien es quien y que formato es. Cualquiera le podria atinar si analiza bien, entonces ANALIZA BIEN. No decidas solo por primeros 10 segundos. Si es video, rebobina y fijate en ropa y timbre. Si es audio, rebobina y fijate en timbre, tono, velocidad. Si el usuario ya te dijo quien es quien, respeta eso y usa las 2 revisiones para confirmar que no te equivocas.
- Puede tener: host organizando (externo o ustedes mismos), cancion intro, cypher de apoyo que evoluciona a batalla todos vs todos, 1 beat o varios beats seguidos, 1 batalla o varias en mismo archivo, 2,3,4+ participantes. DEBES analizar TODO el archivo de principio a fin.
- Detecta bien cuantas voces distintas rapean sobre beat de batalla, cuantos beats, cuantas batallas hay, sin agregar ni quitar. Si al principio hay cancion o charla de host, identificala pero la batalla real es cuando rapean tirandose.
- Cada batalla es gente diferente, analiza profundo ESTA batalla actual. No arrastres nombres ni formatos de batallas pasadas. REVISA 2 VECES - ESENCIAL - GENERAL PARA AUDIO Y VIDEO.

FORMATO DE BATALLA - DETECCION GENERAL PARA AUDIO Y VIDEO (ESCUCHA TODO - 1vs1, 2vs2, TODOS VS TODOS, CUALQUIER FORMATO):
- Formato puede ser CUALQUIERA: 1vs1, 1vs2, 2vs2, 3vs1, 2vs1, todos vs todos, etc. No asumas que es igual a la batalla anterior. Cada batalla es formato diferente. Generaliza la idea, no copies textual el nombre del formato que dijo el usuario.
- OBLIGATORIO: Escucha/ve TODO el archivo completo para saber el formato. No decidas formato solo por el inicio.
- POSIBLES FORMATOS QUE DEBES DETECTAR (GENERAL):
  * Apoyo inicial que evoluciona: al principio se apoyan (cypher) y luego poco a poco se van tirando hasta todos vs todos. Es batalla que evoluciona de apoyo a ataque.
  * Equipo real 2vs2: si uno defiende a su compañero CONSTANTEMENTE durante TODA la batalla es equipo.
  * Todos vs todos: si al final todos se tiran entre todos sin defender, es todos vs todos aunque al principio se apoyaron.
  * 1vs1, 2vs1, 5 vidas, filtro de mejores, etc: detecta por quien ataca a quien durante TODO el archivo. No importa el nombre del formato, lo importante es evaluar quien gano y categorias.
- Si uno defiende a otro solo para usarlo en un punch (ej: "mi compa te gana"), no siempre es equipo, puede ser estrategia.
- Analiza bien TODO lo que dice cada uno de principio a fin para saber formato real de ESTA batalla (audio o video). No asumas que es igual a la anterior. GENERAL PARA AUDIO Y VIDEO.

TAREA 1: IDENTIFICAR VOCES POR TIMBRE - GENERAL PARA AUDIO Y VIDEO (CRITICO - ANALIZA BIEN - REVISA 2 VECES MINIMO - ESENCIAL QUE LE ATINE):
- OBLIGATORIO ANALISIS PROFUNDO GENERAL: Cualquiera le podria atinar si analiza bien, entonces ANALIZA BIEN. Revisa el archivo (audio o video) COMPLETO 2 veces MINIMO, o las veces necesarias, para entender quien es quien. No es opcional, ES OBLIGATORIO rever 2 veces antes de decidir orden. Si es VIDEO, fijate en timbre + ropa (polera negra/blanca) + forma de hablar. Si es AUDIO, fijate en timbre + tono + velocidad + respiracion + muletillas. GENERAL PARA AMBOS.
- Separa voces por timbre, tono, velocidad, respiracion, muletillas. En video tambien por ropa. Cada voz distinta que RAPEA sobre beat de batalla = participante. MINIMO 2 voces siempre. Si crees que hay 1, vuelve a escuchar/ver TODO el archivo 2 veces mas buscando cambio de timbre y de ropa (si es video).
- ORDEN DADO POR USUARIO ES LEY ABSOLUTA - NO LO REINTERPRETES - GENERAL: Si el usuario te dice Voz 1:X Voz 2:Y Voz 3:Z, tu NO debes adivinar timbre para cambiar orden. USA EXACTAMENTE ESE ORDEN. El usuario ya te dijo quien es quien, no lo cambies. Si te dice polera negra = Juan (video) o Voz 1 = Juan (audio), entonces es Juan 100%, no lo discutas. El bot pregunta quien es quien y ustedes le dicen, pero despues se equivoca porque no respeta lo que le dijeron. AHORA RESPETA 100% LO QUE LE DIJO EL USUARIO - GENERAL PARA AUDIO Y VIDEO.
- HOST: Al principio a veces ustedes mismos son host organizando quien parte (ej: 'ya, parte tu'). En ese caso HOST = FREESTYLERS, esa charla previa ES de los participantes, usala para identificar timbres por como hablan, pero NO la evalues como rap. La batalla real empieza cuando suena el beat y rapean. Si host es externo que solo organiza y no rapea, IGNORA esa voz.
- CANCION/INTRO: Si al principio hay cancion cantando o charla, identificala pero no la tomes como Voz 1 de batalla si no estaba rapeando batalla.
- EVOLUCION: Si al principio se apoyan (cypher) y luego se van tirando cada vez mas hasta todos vs todos o 2vs2, sigue contando las mismas voces de principio a fin. Es la misma batalla que evoluciona. Identifica timbres desde inicio aunque se apoyen.
- Si host dice nombres al inicio usalos para identificar. Si dicen Yo soy X, usa ese nombre.
- Si es 1vs1 son 2 voces, si es triangular todos vs todos son 3, si es 2vs2 son 4, etc. Cuenta bien solo los que rapean en batalla. Si usuario dijo que son 3, en CADA TOP pon 3 entradas en el mismo orden que dio.
- Cada batalla es gente diferente, no asumas siempre los mismos. Manten orden en los 8 jurados segun lo que dijo el usuario en ESTA batalla. REVISA ARCHIVO 2 VECES PARA NO EQUIVOCARTE - ANALIZA BIEN - GENERAL.
- VERIFICACION DE ORDEN - GENERAL PARA AUDIO Y VIDEO - PARA QUE USUARIO SEPA SI ESTA MAL Y PUEDA CORREGIR:
  * Si es VIDEO, describe timbre y ropa: "Voz 1 (grave, rapido, polera negra) = X, Voz 2 (agudo, lento, polera blanca) = Y"
  * Si es AUDIO, describe solo timbre: "Voz 1 (grave, rapido, respira fuerte) = X, Voz 2 (agudo, lento, muletilla 'eh') = Y"
  * Si usuario dio orden exacto Voz 1:X Voz 2:Y, RESPETALO 100% y no lo cambies. Si no dio orden, detecta tu pero pon al inicio: "Detecte: Voz 1 = [timbre+ropa si es video / timbre solo si es audio], Voz 2 = [...] - Si este orden esta mal, mandame 'Voz 1:X Voz 2:Y correcto' y re-evaluo".
  * Revisa archivo 2 veces para confirmar que no te equivocas despues de que usuario te dijo quien es quien. GENERAL PARA AUDIO Y VIDEO.
- No hagas robo/tongo. NUNCA evalue solo 1 voz si es batalla. NUNCA cambies el orden que dio el usuario. GENERAL.

ESTILO NO ROBOT, PLAZA CHILENA - COMPARACIONES SOLO CUANDO SEA MUY SIMILAR - GENERAL PARA TODAS LAS CATEGORIAS:
- No hables como robot, habla como plaza chilena, natural, con jerga.
- Usa jerga chilena: brigido, piola, pulento, caleta, se paso, etc.
- Justifica con ejemplos de lo que dijeron.
- COMPARACIONES CON FREESTYLERS CONOCIDOS - GENERAL PARA TODAS LAS CATEGORIAS (FLOW, MORBO, INGENIO, PUNCHLINE, COHERENCIA, SKILLS, RESPUESTAS):
  * Puedes comparar con freestylers conocidos de FMS, DEM, BDM, Red Bull, God Level cuando el estilo sea muy similar y aporte.
  * Solo cuando sea muy parecido y lo consideres necesario, no siempre, no en cada categoria obligatoriamente.
  * Aplica para cualquier categoria: flow muy fluido, morbo muy picante, punchline muy contundente, ingenio muy rebuscado, coherencia muy narrativa, skills muy tecnicos, respuestas muy al hueso. Si ves similitud fuerte en cualquiera, comparas.
  * Si no es muy similar, no compares. Mejor explica con jerga que poner nombre forzado.
- FLOW, MORBO, INGENIO, PUNCHLINE, COHERENCIA, SKILLS, RESPUESTAS: todas con mismo nivel de detalle callejero, no solo flow.

REGLA DE ORO INQUEBRANTABLE: Cuenta cuantas voces distintas hay en TODO el archivo. MINIMO 2 VOCES SIEMPRE, NUNCA 1. Si es 1vs1 son 2, si usuario dijo que son 3 (ej: Voz1:Z Voz2:Y Voz3:X), entonces SON 3, no 2 ni 1. En CADA TOP pon EXACTAMENTE N entradas, mismo orden, sin excepcion. Si son 2 participantes, debes poner 2 lineas en FLOW, 2 en MORBO, etc. Si son 3 participantes, debes poner 3 lineas en FLOW, 3 en MORBO, 3 en INGENIO, etc. NUNCA dejes a nadie fuera, NUNCA califiques solo 1 si son 2, NUNCA solo 2 si son 3. Si te equivocas es robo/tongo y esta PROHIBIDO.

FORMATO OBLIGATORIO - SOLO 7 JURADOS (AUDIO, SIN PUESTA EN ESCENA):

🔥 JURADO FLOW 🔥
GANADOR: [Voz X] con [razon callejera]
TOP FLOW:
1: [Voz 1] - [nota]/3 - [por que con jerga]
2: [Voz 2] - [nota]/3 - [por que]
💯 FIN FLOW 💯

🔥 JURADO MORBO 🔥
GANADOR: [Voz X] con [razon tipo jaj fue mas gracioso]
TOP MORBO:
1: [Voz 1] - [nota]/3 - [cita insulto tal cual + comentario]
2: [Voz 2] - [nota]/3 - [por que]
💯 FIN MORBO 💯

🔥 JURADO INGENIO 🔥
GANADOR: [Voz X]
TOP INGENIO:
1: [Voz 1] - [nota]/3 - [por que callejero]
2: [Voz 2] - [nota]/3 - [por que]
💯 FIN INGENIO 💯

🔥 JURADO PUNCHLINE 🔥
GANADOR: [Voz X]
TOP PUNCHLINE:
1: [Voz 1] - [nota]/3 - [por que]
2: [Voz 2] - [nota]/3 - [por que]
💯 FIN PUNCHLINE 💯

🔥 JURADO COHERENCIA 🔥
GANADOR: [Voz X]
TOP COHERENCIA:
1: [Voz 1] - [nota]/3 - [por que]
2: [Voz 2] - [nota]/3 - [por que]
💯 FIN COHERENCIA 💯

🔥 JURADO SKILLS 🔥
GANADOR: [Voz X]
TOP SKILLS:
1: [Voz 1] - [nota]/3 - [por que]
2: [Voz 2] - [nota]/3 - [por que]
💯 FIN SKILLS 💯

🔥 JURADO RESPUESTAS 🔥
GANADOR: [Voz X]
TOP RESPUESTAS:
1: [Voz 1] - [nota]/3 - [por que]
2: [Voz 2] - [nota]/3 - [por que]
💯 FIN RESPUESTAS 💯

NOTA: AUDIO, NO HAY PUESTA EN ESCENA. No inventes gestos.

Escala 0.0 a 3.0. Habla calle, no robot. Profundo, sin robo, 2-4+ voces por timbre. AUDIO sin PUESTA EN ESCENA.
""" + extra_names_prompt
            boss_prompt_base = f"""Eres el JURADO BOSS FINAL. AUDIO con 7 jurados (SIN PUESTA EN ESCENA), maximo {max_puntos} puntos (7x3).

🚨 ESTO ES AUDIO, NO VIDEO - PROHIBIDO PUESTA EN ESCENA: No menciones ropa, parka, polera, gorro, fisicamente, visualmente, gestos, movimientos. SOLO audio = 7 jurados max 21 puntos, NUNCA 24, NUNCA tabla de video. Si pones puesta en escena en audio estas haciendo robo.

INFO USUARIO SOBRE QUIEN ES QUIEN (OBLIGATORIO USAR ESTOS NOMBRES, NO INVENTAR): {user_names_info}
NOMBRES DETECTADOS: {names_str if 'names_str' in locals() else user_names_info}

REGLA INQUEBRANTABLE GENERAL - CADA BATALLA ES DIFERENTE: Usa puntajes reales del MAESTRO. MINIMO 2 VOCES SIEMPRE, NUNCA 1. Siempre hay mas de 1 en batalla. Formato puede ser 1vs1, 1vs2, 2vs2, 3vs1, todos vs todos. Detecta formato en ESTA batalla por lo que dicen. Si es 1vs1 son 2, si maestro evaluo 1 solo estas haciendo robo, debe haber minimo 2. Si el usuario dijo 3 voces en ESTA batalla o detectaste 3 timbres, tabla con 3 OBLIGATORIO. NUNCA INVENTES NOMBRES QUE EL USUARIO NO DIO EN ESTA BATALLA. Usa EXACTAMENTE los nombres que dio el usuario en este mensaje (X, Y, Z), no uses nombres de batallas pasadas. Si usuario dijo Voz 1:X Voz 2:Y, entonces los participantes son X e Y. No hagas tongo, no dejes a nadie fuera. Si maestro evaluo 2, tu tabla final debe tener 2 minimo. Manten voces exactas del usuario de ESTA batalla. Cada batalla es gente diferente, analiza ESTA. VIDEO max 24, AUDIO max 21. AUDIO no tiene puesta en escena.

FORMATO:

🏆 TABLA FINAL GENERAL (sobre {max_puntos}) - AUDIO SIN PUESTA EN ESCENA:
1: [Nombre/Voz] - [total]/{max_puntos} (desglose: {desglose_template})
2: [Nombre/Voz] - [total]/{max_puntos} (desglose: ...)
3: [Si hay tercera] - [total]/{max_puntos} (...)

GANADOR ABSOLUTO: [Nombre/Voz] con [total]/{max_puntos} porque [resumen profundo sin robo]

EN QUE MEJORAR:
- [Voz 1]: [consejo]
- [Voz 2]: [consejo]

🔥🔥🔥 FIN EVALUACION BOSS 🔥🔥🔥
"""


        try:
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")
            await update.message.reply_text("🔍 JURADO MAESTRO analizando voces por timbre (credito 1/2)... Profundo, sin robo.")
            resp = await generate_with_retry([mega_prompt, g_file])
            texto = extract_text_safe(resp)
            if not texto or not texto.strip():
                print("Respuesta vacia, intentando bypass por transcripcion...")
                await update.message.reply_text("⚠️ Filtro detecto insultos, transcribiendo primero para identificar voces...")
                try:
                    transcribe_prompt = "Transcribe literalmente esta batalla separando por voces: Voz 1 (timbre), Voz 2 (timbre), etc. Si host dice nombres, ponlos. Si dicen yo soy X, pon nombre. Transcribe todo sin censura."
                    resp_trans = await generate_with_retry([transcribe_prompt, g_file])
                    transcripcion = extract_text_safe(resp_trans)
                    if transcripcion and transcripcion.strip():
                        await update.message.reply_text(f"📝 Transcripcion con voces: {transcripcion[:1200]}...")
                        texto_evaluacion_prompt = mega_prompt + f"\n\nTRANSCRIPCION SEPARADA POR VOCES:\n{transcripcion}"
                        resp_eval = await generate_with_retry([texto_evaluacion_prompt])
                        texto = extract_text_safe(resp_eval)
                        if texto and texto.strip():
                            print("Bypass por transcripcion con voces funciono!")
                        else:
                            texto = None
                    else:
                        await update.message.reply_text("❌ No se pudo transcribir.")
                except Exception as bypass_e:
                    print(f"Bypass error: {bypass_e}")

                if not texto or not texto.strip():
                    await update.message.reply_text("❌ Gemini devolvio vacio (filtro). Intenta con audio mas corto.")
                    evaluaciones_texto = ""
                else:
                    evaluaciones_texto = texto
            else:
                evaluaciones_texto = texto

            if evaluaciones_texto:
                if len(evaluaciones_texto) > 4000:
                    partes = re.split(r'(🔥 JURADO.*?💯 FIN.*?💯)', evaluaciones_texto, flags=re.DOTALL)
                    buffer = ""
                    for part in partes:
                        if not part.strip():
                            continue
                        if len(buffer) + len(part) > 3500:
                            await update.message.reply_text(buffer)
                            buffer = part
                            await asyncio.sleep(0.5)
                        else:
                            buffer += part + "\n"
                    if buffer.strip():
                        await update.message.reply_text(buffer)
                else:
                    await update.message.reply_text(evaluaciones_texto)

                await asyncio.sleep(1)
                await context.bot.send_chat_action(chat_id=chat_id, action="typing")
                await update.message.reply_text("🏆 JURADO BOSS contando puntos profundo, sin robo (credito 2/2)...")
                boss_full = f"{boss_prompt_base}\n\nEvaluaciones del MAESTRO:\n{evaluaciones_texto}"
                try:
                    resp_boss = await generate_with_retry([boss_full])
                    texto_boss = extract_text_safe(resp_boss)
                    if texto_boss and texto_boss.strip():
                        await update.message.reply_text(texto_boss)
                        # Check quota after boss - avisar si quedan 2 creditos
                        used_after, rem_after, lim_after, _ = get_quota_status()
                        if rem_after == 2:
                            await update.message.reply_text(f"⚠️ Te queda 1 audio/video más hoy (2 creditos de {lim_after}). Después se acaba lo gratis. Usa /status para ver. Si quieres más hoy, el bot usará lite (500-1000/dia, un poco menos preciso).")
                        elif rem_after == 0:
                            await update.message.reply_text(f"⛔ Se acabaron tus audios/videos gratis de hoy: {used_after//2} audios usados de {lim_after//2}. Hasta mañana no hay más gratis. Si necesitas más hoy, cambia a lite en MODELS_TO_TRY primero (500-1000/dia).")
                        elif rem_after <= 4:
                            await update.message.reply_text(f"⚠️ Te quedan {rem_after//2} audios/videos hoy ({rem_after} creditos de {lim_after}).")
                    else:
                        await update.message.reply_text(f"⚠️ BOSS no pudo generar tabla, pero maestro si evaluo.")
                except Exception as e_boss:
                    await update.message.reply_text(f"⚠️ BOSS fallo: {e_boss}")

        except Exception as e:
            print(f"Error jurado maestro: {e}")
            import traceback
            traceback.print_exc()
            err_lower = str(e).lower()
            if "quota" in err_lower or "resource_exhausted" in err_lower:
                retry_match = re.search(r'retry in (\d+)', str(e).lower())
                if retry_match:
                    secs = int(retry_match.group(1))
                    mins = secs // 60
                    await update.message.reply_text(f"⛔ Cuota agotada (20/dia = 10 batallas x2). Espera {mins} min. Con 3.5-flash-lite tienes 500-1000/dia. Error: {e}")
                else:
                    await update.message.reply_text(f"⛔ Cuota agotada. Error: {e}")
            else:
                await update.message.reply_text(f"❌ Error: {e}. Intenta JURADOS VOTEN de nuevo.")

    except Exception as e:
        await update.message.reply_text(f"❌ Error general: {e}")
        print(e)
        import traceback
        traceback.print_exc()
    finally:
        try:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)
            if compressed_path and os.path.exists(compressed_path):
                os.remove(compressed_path)
        except:
            pass
    return

def main():
    if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
        print("Faltan TELEGRAM_TOKEN o GEMINI_API_KEY")
        return
    # --- Mini web server para Render (free web service necesita puerto abierto) ---
    import threading
    from http.server import HTTPServer, BaseHTTPRequestHandler
    class KeepAliveHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-type","text/plain")
            self.end_headers()
            self.wfile.write(b"Bot Jurados Live")
        def log_message(self, *args):
            return
    def run_web():
        port = int(os.getenv("PORT", 10000))
        try:
            HTTPServer(("0.0.0.0", port), KeepAliveHandler).serve_forever()
        except Exception as e:
            print(f"Web server error: {e}")
    threading.Thread(target=run_web, daemon=True).start()
    print(f"🌐 Mini web server en puerto {os.getenv('PORT', 10000)} para Render")

    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", start_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(MessageHandler(filters.VIDEO | filters.AUDIO | filters.VOICE | filters.Document.ALL, handle_media))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    print("Bot JURADO MAESTRO + BOSS, x2 creditos, identifica voces por timbre, 2-4+ participantes, sin robo/tongo...")
    app.run_polling()

if __name__ == "__main__":
    main()
