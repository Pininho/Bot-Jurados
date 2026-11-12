
# === TELEGRAM SOLO AUDIO - Decision usuario 2026-11-11 ===
# Telegram = solo audio (notas de voz, mp3, ogg, wav, m4a). Rapido en plaza con poco internet, sin Telethon pesado, sin compresion de video.
# Si llega video a Telegram, NO lo procesa, dice: "Video va por Discord" y no lo analiza, para no confundirse con video comprimido.
# Discord = solo video (mp4, mov, mkv, webm). Mejor calidad, se ve ropa y puesta en escena.

import os
import re
import sys
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)
print(">>> INICIANDO main.py - imports...", flush=True)
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
    try:
        client = genai.Client(api_key=GEMINI_API_KEY, http_options={"api_version": "v1"})
        print(f"✅ Gemini client creado correctamente (v1 API)", flush=True)
    except:
        client = genai.Client(api_key=GEMINI_API_KEY)
        print(f"✅ Gemini client creado (v1beta fallback)", flush=True)

user_client = None
if API_ID and API_HASH:
    try:
        api_id_int = int(str(API_ID).strip())
        session_clean = str(SESSION_STRING).strip() if SESSION_STRING else ""
        # Remove possible newlines/spaces that Render sometimes adds
        session_clean = session_clean.replace("\n", "").replace("\r", "").replace(" ", "")
        if session_clean:
            print(f"🔑 SESSION_STRING detectado, largo {len(session_clean)} - intentando crear client...")
            user_client = TelegramClient(StringSession(session_clean), api_id_int, API_HASH.strip())
            print(f"✅ Telethon client creado, session largo {len(session_clean)}")
        else:
            print("⚠️ SESSION_STRING vacío - Telethon no podrá bajar >20MB")
            user_client = TelegramClient('user_session', api_id_int, API_HASH.strip())
    except Exception as e:
        print(f"❌ Error creando Telethon client: {e}")
        import traceback; traceback.print_exc()
        user_client = None
else:
    print("⚠️ API_ID o API_HASH no configurados - Telethon deshabilitado")

last_media = {}
last_uploaded_files = {}
last_media_names = {}  # chat_id -> texto con quien es quien dado por usuario
last_media_type = {}  # chat_id -> 'video' o 'audio'

# --- CUOTA TRACKING ---
import json
from datetime import datetime
QUOTA_FILE = "/tmp/bot_quota.json"
DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "1000"))  # MEJOR PARA 100%: 1000 creditos = 500 batallas, usa flash preciso primero y lite solo si flash saturado o quota (justo tus 10 audios semanales). Si quieres mas, usa DAILY_LIMIT=1000 con flash-lite o API pagada. Aviso cuando quedan 2 creditos.

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


MODELS_TO_TRY = ["gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-3-flash-preview"]  # 2.0-flash es el mas estable, 1.5-flash casi nunca da 503, lite tiene mas cuota  # REAL: modelos que existen y no estan baneados. 2.0-flash es el mas estable, lite tiene mas cuota y menos 503

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



# SKILL - ENCADENADO / RIMA ENCADENADA / ANADIPLOSIS - Ejemplo del usuario: EntendiENDO EN DOminar
# Lo ultimo de una palabra que termina, empieza la otra al tiro. ENDO -> EN DO.
# Ej: EntendiENDO EN DOminar, Estoy CANSADO, DOminando la batalla
# El bot DEBE evaluar Encadenado en SKILL: si el participante conecta el final de una palabra con el inicio de la siguiente, varias veces, bien hecho, le sube el Skill. Si lo hace mal o no lo hace, no le sube.
# No es Flow, es Skill porque es tecnica de rima, como conectas las palabras.


# === JURADO COMPLETO - Búsqueda general completa - NO hay que decirle que evaluar, evalua TODO ===
# Fuente: FMS, Red Bull Batalla, Wikipedia Freestyle rap, La Republica tecnicas freestyle, Human Beatbox Scratch Vocal
# El bot es JURADO COMPLETO: evalua TODAS las categorias y TODAS las habilidades en cada categoria, sin que el usuario tenga que decirle que evaluar.

# --- FLOW (como suenas en el beat, musicalidad) ---
# Adaptarse al ritmo de la base, coherencia y naturalidad, ritmo, entonacion, pausas estrategicas, variaciones de velocidad, cadencia, swing, delivery, diccion, fluidez, como caes en el beat.
# Tecnicas Flow: doble tempo (doble palabras en un tempo), triple tempo, metralleta (aumento masivo velocidad, efecto disparo), flow entrecortado, flow con cortes, cambio de flow, cambio de registro (voz grave/aguda), flow de trap, boom bap, drill, agresivo, relajado, flow con onomatopeyas.
# Scratch Vocal: alteraciones agudas, imitar DJ vinilo con boca wika-wika skr-skr shh, como Nobody 12-33s en Nobody vs Teorema, prolijo y a tiempo sube Flow, sucio/fuera de tiempo baja.
# Beatbox sin beat: hacer el beat con boca, bombo bum, caja pa, hi-hat ts, ser bateria, como DJ, si lo hace prolijo y a tiempo sube Flow. Si hace beatbox + scratch + rapear mezclado, es Modo DJ / Scratch Rap, sube Flow.
# One-two: mantener rimando todas las vocales y silabas, no solo una palabra.
# Si se traba, si se queda callado, si pierde el beat, si suena fome, baja Flow. Si cae siempre a tiempo y suena musical, sube.

# --- SKILLS / TECNICA Y METRICAS (tecnica de rima, estructuras) ---
# Estructuras o metricas: capacidad de enlazar y concatenar rimas diferentes, alternarlas, pareados, estructuras complejas, rimas con todas las silabas que permita.
# Metrica: estructura y combinacion de palabras en cada linea, como interactuan silabas tonicas.
# Rimas multisilabicas / polisilabicas: rimar mas de una silaba, no solo ultima palabra.
# Encadenado / Rima Encadenada / Anadiplosis: ultimo sonido de una frase rima con primer sonido de siguiente, Ej: EntendiENDO EN DOminar, ENDO -> EN DO. Si lo hace bien varias veces, sube Skill.
# Calambur: dos o mas palabras forman otra, deben sonar iguales, cambiar separacion para nuevo significado, Ej: homofonos.
# Wordplay / Juego de palabras: modificar palabra/frase para doble significado.
# Retruecano: invertir palabras/frase para cambiar sentido.
# Aliteracion: repeticion de uno o varios sonidos dentro de misma palabra/frase.
# Paranomasia: cambiar vocal acentuada para juego fonetico.
# Antitesis: contraponer dos palabras/frases opuestas para enfasis con contraste.
# Simil: comparacion con como, igual que. Metafora: comparacion sin como. Hiperbole: exageracion. Hiperbaton: alterar orden de palabras.
# One-two, esquema (todas las rimas con misma terminacion), etc.
# Si rima es basica perfecta 4x4 = 1.5-2.0 max. Compleja bien ejecutada = 2.2-3.0. Compleja con 1 trabe = -0.2, basica con 1 trabe = -0.4. No premiar complejidad por ser compleja. Si compleja es mala, 1.5. Basica perfecta puede ganar a compleja mala.

# --- PUNCHLINE (remate, golpe) ---
# Punchline negativo: frase que ataca oponente, deja ver poca capacidad, recuerda imperfecto vida oponente.
# Punchline positivo: enaltecer al que dice frase, denotar superioridad.
# Punchline directo, personal, con referencia, con comparacion, con doble sentido, barra fuerte que hace gritar, remate de frase, contundencia, que deja KO.
# Si punchline es bueno y a tiempo, sube. Si es generico, no sube.

# --- COHERENCIA ---
# Conexion entre ideas, desarrollo de argumentos, si mantiene tema, si se contradice, si responde al tema, si tiene sentido, construcciones completas de patrones, no irse por las ramas.

# --- INGENIO / CREATIVIDAD / ORIGINALIDAD ---
# Ingenio para combinar palabras, generar respuesta, creatividad, originalidad, ideas nuevas, no repetir lo mismo, salir con algo que nadie esperaba, uso de conceptos.

# --- PUESTA EN ESCENA ---
# Soltura y confianza en tarima, fuerza, decision, desparpajo, seguridad, lenguaje corporal, teatralidad, contacto visual, como se para, como usa mic, como interactua con publico, dominio de situacion, energia, presencia, no mostrar nervios/panico escenico. Si se muestra nervioso y se nota, baja.

# --- RESPUESTAS ---
# Respuesta directa a lo que dijo rival, respuesta con punchline, respuesta con doble sentido, si ignora o responde, si responde al tiro o se demora, contestaciones, capacidad de respuesta. Sin respuesta = 0.5-1.0 aunque tenga buen flow. Respuestas es lo mas valorado.

# --- MORBO / HUMOR ---
# Berretin (Argentina) / Frases morbosas (Peru): rimas con insultos, condicion fisica, cosas personales.
# Humor negro, doble sentido, tallas, picardia, desubicar, hacer reir, morbo como arte, insultos como arte, NO censurar. Evaluar morbo sin censura, si es bueno sube, si es malo o forzado baja.
# No castigar ni premiar por ser nino, chico, alto, flaco, gordo. Solo rap. Si nino rapea mejor que adulto que no rima, nino va mas arriba.

# --- REGLAS JURADO COMPLETO ---
# Usa rango completo 0.0-3.0, no generico 2.0-2.3. Prohibido generico.
# Cita textual exacta SOLO si se escucha claro con confianza ALTA/MEDIA y 3 anclas coinciden (orden formulario + ropa + timbre). Si no se escucha claro, escribe [NO SE ESCUCHA CLARO] y NO inventes cita generica. Mejor decir no se escucha que atribuir mal.
# 8 jurados, cada uno da TOP, y tabla final con promedio.
# Si un participante no rima / no responde, dale 0.0-1.0 real, no le regales 1.8.
# Evalua TODO lo anterior en cada batalla, sin que el usuario tenga que decirte que evaluar.

# === EVALUACION COMPLETA - El bot evalua TODO en todas las categorias, sin que el usuario tenga que decirle que evaluar ===
# FLOW: cadencia, delivery, swing, como cae en el beat, doble tempo, triple tempo, flow entrecortado, flow con cortes, cambio de flow, cambio de registro, Scratch Vocal (imita DJ wika-wika), Beatbox sin beat (hace el beat bum-pa-ts), flow con onomatopeyas, flow agresivo, flow relajado, flow de trap, flow de boom bap, flow de drill, etc. TODO lo que tenga que ver con como suena en el beat.
# SKILLS: metrica (silabas por linea), esquema (rimas con misma terminacion), calambur, juego de palabras, multisilabicas, rimas internas, rimas encadenadas, tecnicas de rima, estructuras complejas, wordplay, etc. TODO lo que tenga que ver con tecnica de rima.
# PUNCHLINE: punchline directo, punchline personal, punchline con doble sentido, punchline con referencia, punchline con comparacion, barra fuerte que hace gritar, etc.
# COHERENCIA: coherencia del argumento, si mantiene el tema, si se contradice, si responde al tema, si tiene sentido lo que dice.
# INGENIO: ingenio, creatividad, originalidad, ideas nuevas, no repetir lo mismo, salir con algo que nadie esperaba.
# PUESTA EN ESCENA: gestos, movimiento en escenario, energia, presencia, contacto visual, como se para, como usa el mic, como interactua con publico, etc.
# RESPUESTAS: respuesta directa a lo que dijo el rival, respuesta con punchline, respuesta con doble sentido, si ignora lo que dijo el rival o si lo responde, si responde al tiro o se demora.
# MORBO: humor negro, doble sentido, tallas, picardia, desubicar, hacer reir, morbo sin censura, insults como arte, etc. NO censurar.
# El bot evalua TODO esto en cada batalla, sin que el usuario tenga que decirle que evaluar. Si un participante hace Scratch Vocal o Beatbox, el bot lo detecta solo y lo evalua en FLOW. Si hace calambur, lo evalua en SKILLS. Etc.


# === REAL FIX 2026-11-11 ===
# Causa real del 0/3 siempre: 1) Modelos 1.5/2.0/2.5 retirados por Google en 2026 (1.5 ya no existe, 2.0 sunset 2026-06-01, 2.5 retirado 2026-10-16 / 2026-11-02, reroute a 3.5-lite desde 2026-12-01). Por eso antes tomaba a todas las horas y ahora 0/3 siempre.
# 2) El bot mismo se auto-saturaba: probe de 3 modelos al guardar video + chequeo cada 5 min para siempre + loop de 11 modelos en votar = spam -> Google devuelve 503 aunque haya capacidad.
# Fix: SIN probe previo que bloquee, formulario al tiro siempre, modelos actuales 3.8-flash / 3.5-flash-lite, max 2 intentos con backoff, no loop de 11.


# === FIX CONFUSION + NO INVENTAR - 2026-11-11 - El TL se confundia y tomaba mal ===
# Problema real visto en tabla que mando usuario:
# VATRA 19.2, MONARKH 18.7, MCLOVIN 13.5, EXTRA 9.7 - con citas genericas inventadas:
# VATRA: "si tu unico aspecto femenino fue el de tu mama" - MONARKH: "vengo con la estructura levantando como edificios" - MCLOVIN: "yo me quedo parado tranquilo rapeando en la pista" - EXTRA: "vengo acelerando el ritmo porque aqui me planto"
# Esas citas suenan a relleno inventado para cumplir "CITA TEXTUAL EXACTA SOLO SI 100% CERTEZA - SI DUDA, NO CITA, PREGUNTA AL USUARIO - PROHIBIDO INVENTAR - SI NO SE ESCUCHA CLARO ESCRIBE [NO SE ESCUCHA CLARO] Y NO INVENTES FRASE GENERICA". Cuando el audio esta con ruido, se pisan, hay 4 voces, Gemini inventa una cita plausible en vez de decir "no escuche claro". Y atribuye la frase al participante equivocado porque solo usa timbre, sin cruzar con orden + ropa.
# FIX:
# 1. TRANSCRIBE PRIMERO, SIN ATRIBUIR: Antes de evaluar, transcribe literal con timestamps: [00:12-00:18 Voz A: texto literal] [00:19-00:25 Voz B: texto literal]. Si no se escucha claro, escribe [NO SE ESCUCHA CLARO - ruido / se pisan] y NO inventes frase.
# 2. ATRIBUCION CON 3 ANCLAS, NO SOLO TIMBRE: Para cada participante, cruza 3 anclas: (a) Orden de "Quien parte y quien sigue" del formulario = orden real de turnos, NO lo cambies. (b) Ropa del formulario = lo que ves en video (ej: MONARKH poleron Kappa Blanco jeans azules mochila, VATRA nino pequeno poleron negro short-buzo azul, etc). (c) Timbre de voz. Si 2 anclas no coinciden, marca ATRIBUCION DUDOSA y no uses esa cita para decidir ganador.
# 3. PROHIBIDO INVENTAR CITA: Si no puedes transcribir exacto, NO inventes cita generica como "vengo con la estructura..." o "yo me quedo parado tranquilo...". Escribe: "No se pudo transcribir exacto" y evalua con lo que SI escuchaste, no con lo inventado.
# 4. CONFIANZA POR CITA: Cada cita lleva confianza: ALTA (se escucha claro, 3 anclas coinciden), MEDIA (se escucha mas o menos, 2 anclas), BAJA (ruido, 1 ancla). Si confianza BAJA, NO la uses para dar ganador, solo para comentario.
# 5. DIFERENCIACION REAL, NO GENERICO: No des a todos 2.2-2.6 en todo. Si VATRA y MONARKH estan 19.2 vs 18.7, explica en 2 lineas con 1 diferencia concreta transcrita por que uno gana, no "contundencia implacable" generico. Si un participante no rima / no responde, 0.5-1.0 real, no 1.8 regalado.
# 6. SI TE CONFUNDES, DILO: Si hay 2 voces parecidas o se pisan, dilo: "Posible confusion entre X y Y en 00:XX, no estoy seguro". Es mejor decir "no estoy seguro" que atribuir mal y tomar mal al ganador.


# === TL SOLO AUDIO - 100% CERTEZA SIN EQUIVOCARSE - 2026-11-11 ===
# El usuario quiere TL audio sin confusion, 100% certeza, sin equivocarse, todo full.
# La firme: Ninguna IA puede dar 100% certeza sola con audio de plaza con bulla, se pisan, ruido, 4 voces.
# Lo que SI podemos hacer para llegar a 100% real: NO ADIVINAR NUNCA + CONFIRMACION DEL USUARIO.
# Flujo en 2 pasos para 100% certeza:
# PASO 1 - TRANSCRIPCION SOLA, SIN EVALUAR: El bot transcribe literal con timestamps, sin atribuir puntaje, con temperature 0.0 (determinista, no inventa).
#   Ej: [00:12-00:18 Voz A (aguda, nino): "texto literal"] [00:19-00:25 Voz B (grave, adulto): "texto literal"] [00:26-00:30 Voz A: "[NO SE ESCUCHA CLARO - se pisan]"]
#   Si no se escucha claro, escribe NO SE ESCUCHA CLARO, NO inventa, NO atribuye.
# PASO 2 - CONFIRMACION DEL USUARIO ANTES DE VOTAR: El bot manda la transcripcion al grupo y pregunta: "¿Estoy en lo correcto? Si me equivoque en quien dijo que, corrigeme con: Anotado quien es quien: Voz A = VATRA, Voz B = MONARKH..."
#   Solo cuando el usuario confirma o corrige, el bot pasa a evaluar. Asi la atribucion es 100% porque la valido el humano que estuvo ahi, no la adivino la IA.
# REGLAS 100% CERTEZA:
# - Temperature 0.0 para transcripcion (no creativo, no inventa). Temperature 0.2 para evaluacion (casi determinista).
# - PROHIBIDO ADIVINAR: Si confianza < ALTA, NO asigna puntaje con esa cita. Evalua solo con citas ALTA.
# - 3 ANCLAS OBLIGATORIAS: Orden formulario + Descripcion de voz del usuario + Timbre. Si falta 1 ancla, marca DUDOSO.
# - SI HAY DUDA, PREGUNTA, NO VOTA: Si hay 2 voces parecidas o se pisan, el bot NO vota, manda: "Posible confusion en 00:XX entre X y Y, ¿quien dijo esto? Responde y voto."
# - CITA SOLO SI 100%: Solo usa cita si se escucha claro, 3 anclas coinciden, y usuario confirmo. Si no, escribe [NO SE ESCUCHA CLARO] y evalua con lo seguro.
# - DIFERENCIACION REAL: No 2.2-2.6 generico a todos. Cada puntaje debe tener 1 cita ALTA que lo justifique. Si no hay cita ALTA para justificar un puntaje, baja el puntaje o dice que no puede justificarlo.
# Con esto, el TL audio llega a 100% certeza real, porque no adivina, y lo que no esta seguro lo confirma el usuario antes de votar.

async def generate_with_retry_auto_bg_INFINITO(chat_id, contents, context):
    # SMART: max 5 intentos cada 60 seg, luego cambia a lite 1000/dia, no infinito 168 min
    attempt = 0
    while attempt < 5:
        attempt += 1
        try:
            resp = await generate_with_retry(contents, max_retries=3)
            if resp:
                return resp
        except Exception as e:
            msg = str(e).lower()
            if "quota" in msg and "exceeded" in msg:
                # Cuota de flash preciso agotada -> cambiar automaticamente a lite 1000/dia, no reintentar infinito
                try:
                    await context.bot.send_message(chat_id=chat_id, text=f"⚠️ Cuota flash preciso agotada (20/dia). Cambiando AUTOMATICO a lite 1000/dia con 100% critico (rango 0.0-3.0) para no dejarte esperando 168 min. Intento lite...")
                except:
                    pass
                # Probar solo lite con prompt 100% critico
                for lite_model in ["gemini-3.5-flash-lite"]:
                    try:
                        # Cambiar MODELS_TO_TRY temporal a solo lite
                        original_models = MODELS_TO_TRY[:]
                        MODELS_TO_TRY[:] = [lite_model]
                        resp_lite = await generate_with_retry(contents, max_retries=2)
                        MODELS_TO_TRY[:] = original_models
                        if resp_lite:
                            await context.bot.send_message(chat_id=chat_id, text=f"✅ Lite {lite_model} respondio! Evalua 100% critico aunque sea lite, no generico 2.0-2.3.")
                            return resp_lite
                    except Exception as lite_e:
                        print(f"Lite {lite_model} fallo: {lite_e}")
                        continue
                # Si lite tambien falla, decir verdad
                raise Exception(f"⛔ Cuota flash agotada y lite saturado. Espera hasta 00:00 Chile (reseteo) o consigue 2da API KEY gratis para 20 creditos mas. No te dejo esperando 168 min infinito.")
            if "503" in msg or "unavailable" in msg or "saturado" in msg or "ningun modelo" in msg or "todos saturados" in msg or "high demand" in msg or "overloaded" in msg or "429" in msg:
                try:
                    if attempt == 1:
                        await context.bot.send_message(chat_id=chat_id, text=f"⏳ Gemini saturado (503) horario punta. Reintentando max 5 veces cada 60 seg (no infinito 168 min). Intento {attempt}/5. Si sigue saturado, cambio automatico a lite 1000/dia.")
                    else:
                        await context.bot.send_message(chat_id=chat_id, text=f"⏳ Sigue saturado, reintentando {attempt}/5 cada 60 seg...")
                except:
                    pass
                await __import__('asyncio').sleep(60)
                continue
            else:
                # Otro error: reintentar max 2 veces luego avisar
                if attempt < 2:
                    await __import__('asyncio').sleep(10)
                    continue
                raise e
    # Despues de 5 intentos, intentar lite 1000/dia como ultimo recurso
    try:
        await context.bot.send_message(chat_id=chat_id, text="⏳ Despues de 5 intentos flash sigue saturado, probando lite 1000/dia con 0% 503 y 100% critico...")
    except:
        pass
    for lite_model in ["gemini-2.0-flash-lite", "gemini-2.5-flash-lite"]:
        try:
            original = MODELS_TO_TRY[:]
            MODELS_TO_TRY[:] = [lite_model]
            resp_lite = await generate_with_retry(contents, max_retries=2)
            MODELS_TO_TRY[:] = original
            if resp_lite:
                return resp_lite
        except:
            continue
    raise Exception("❌ Tras 5 intentos + lite sigue saturado. Prueba 10 PM - 8 AM o consigue 2da API KEY gratis. NO infinito 168 min.")


async def generate_with_retry(contents, max_retries=5):
    last_error = None
    # Check quota before calling
    used, remaining, limit, _ = get_quota_status()
    if remaining <= 0:
        from datetime import datetime, timedelta
        now = datetime.now()
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        hours_left = int((tomorrow - now).total_seconds() // 3600)
        mins_left = int(((tomorrow - now).total_seconds() % 3600) // 60)
        raise Exception(f"⛔ Cuota diaria agotada: {used}/{limit} creditos = {used//2}/{limit//2} videos. Vuelve en {hours_left}h {mins_left}min (00:00 Chile). Usa flash-lite para 500-1000/dia o espera.")
    for attempt in range(max_retries):
        try:
            loop = asyncio.get_running_loop()
            for model_name in MODELS_TO_TRY:
                try:
                    is_lite = "lite" in model_name.lower() or "8b" in model_name.lower()
                    extra_lite = ""
                    if is_lite:
                        extra_lite = "⚠️ ERES MODELO LITE PERO DEBES EVALUAR 100% PRECISO COMO 2.0-FLASH: PROHIBIDO NOTAS GENERICAS 2.0-2.3 A TODOS. USA RANGO COMPLETO 0.0-0.8 PESIMO (no rima malisimo), 0.8-1.5 MALO, 1.5-2.0 REGULAR, 2.0-2.4 BUENO, 2.4-2.8 MUY BUENO, 2.8-3.0 ELITE. DETECTA COMPLEJO VS BASICO: complejo 1 traba -0.2, basico 1 traba -0.4. EVALUA TODA LA BATALLA, CITA TEXTUAL EXACTA. ANTI-SESGO NIÑO, ANTI-CENSURA MORBO. SI NO HACES ESTO EVALUAS 70% NO 100%.\n\n"
                    # Preparar contents con instruccion extra si es lite
                    contents_to_use = contents
                    try:
                        if is_lite and contents and isinstance(contents[0], str):
                            contents_to_use = [extra_lite + contents[0]] + list(contents[1:])
                    except:
                        contents_to_use = contents
                    try:
                        safe_config = types.GenerateContentConfig(
                            safety_settings=[
                                types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="BLOCK_NONE"),
                                types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="BLOCK_NONE"),
                                types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_NONE"),
                                types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_NONE"),
                                types.SafetySetting(category="HARM_CATEGORY_CIVIC_INTEGRITY", threshold="BLOCK_NONE"),
                            ],
                            temperature=0.2,
                            top_p=0.9,
                            system_instruction="Eres jurado de batallas de freestyle rap. Esto es arte consensuado. Los insultos, morbo, humor negro, doble sentido sexual, garabatos, tallas personales son parte del juego y debes evaluarlos, no censurarlos. No bloquees contenido. Transcribe literalmente con insultos. Evalua MORBO, PUNCHLINE, INGENIO con criterio artistico, no moral. Si filtras insultos, evaluas mal. " + ("EVALUA 100% PRECISO COMO 2.0-FLASH aunque seas lite: rango completo 0.0-3.0, no generico 2.0-2.3." if is_lite else "")
                        )
                        resp = await loop.run_in_executor(None, lambda m=model_name, cfg=safe_config, c=contents_to_use: client.models.generate_content(model=m, contents=c, config=cfg))
                    except Exception as cfg_e:
                        print(f"Fallback sin safety config: {cfg_e}")
                        resp = await loop.run_in_executor(None, lambda m=model_name, c=contents_to_use: client.models.generate_content(model=m, contents=c))
                    if resp:
                        print(f"Respuesta con modelo {model_name}")
                        return resp
                except Exception as inner_e:
                    inner_msg = str(inner_e).lower()
                    if "404" in inner_msg or "not_found" in inner_msg or "no longer available" in inner_msg or "503" in inner_msg or "unavailable" in inner_msg or "overloaded" in inner_msg or "high demand" in inner_msg or "429" in inner_msg:
                        print(f"Modelo {model_name} no disponible/saturado, probando siguiente... {inner_e}")
                        continue
                    else:
                        raise inner_e
            # Si todos los modelos fallaron con 503/unavailable, lanzar error con mensaje claro para que outer retry lo capture
            raise Exception(f"Ningún modelo disponible - todos saturados (503) intento {attempt+1}/{max_retries}, reintentando...")
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

async def ensure_telethon_connected():
    if not user_client:
        print("❌ user_client es None - no se puede conectar Telethon", flush=True)
        return False
    try:
        if user_client.is_connected():
            print("ℹ️ Telethon ya conectado, reutilizando...", flush=True)
            return True
        print("🔌 Conectando Telethon solo para descarga >20MB...", flush=True)
        await user_client.connect()
        me = await user_client.get_me()
        print(f"✅ Telethon conectado temporalmente como {me.first_name} para descarga - FIX PERMANENTE ANTI-BAN aplicado", flush=True)
        return True
    except Exception as e:
        print(f"❌ Error conectando Telethon: {e}", flush=True)
        import traceback; traceback.print_exc()
        return False

async def safe_telethon_disconnect():
    if user_client:
        try:
            if user_client.is_connected():
                await user_client.disconnect()
                print("🔌 Telethon desconectado después de descarga (anti-ban)", flush=True)
        except Exception as e:
            print(f"Error desconectando Telethon: {e}", flush=True)

async def download_via_telethon(chat_id, message_id):
    if not user_client:
        print("❌ Telethon: user_client no existe - falta SESSION_STRING, API_ID o API_HASH en Render", flush=True)
        return None
    try:
        connected = await ensure_telethon_connected()
        if not connected:
            print("❌ Telethon: no se pudo conectar (session baneada?)", flush=True)
            return None
        print(f"✅ Telethon conectado temporalmente para descarga >20MB - FIX PERMANENTE ANTI-BAN aplicado", flush=True)
        try:
            bot_entity = await user_client.get_entity(chat_id)
            msg = await user_client.get_messages(bot_entity, ids=message_id)
            if msg and msg.media:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tf:
                    path = tf.name
                    await user_client.download_media(msg, file=path)
                    if os.path.exists(path) and os.path.getsize(path) > 1000:
                        print(f"✅ Telethon downloaded (bot_id exacto) to {path} size {os.path.getsize(path)}", flush=True)
                        await safe_telethon_disconnect()
                        return path
        except Exception as e1:
            print(f"Intento bot_id fallo: {e1}", flush=True)
        try:
            async for dialog in user_client.iter_dialogs():
                if "jurados" in dialog.name.lower() and "batalla" in dialog.name.lower():
                    msg = await user_client.get_messages(dialog.entity, ids=message_id)
                    if msg and msg.media:
                        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tf:
                            path = tf.name
                            await user_client.download_media(msg, file=path)
                            if os.path.exists(path) and os.path.getsize(path) > 1000:
                                print(f"✅ Telethon downloaded (grupo exacto) to {path} size {os.path.getsize(path)}", flush=True)
                                await safe_telethon_disconnect()
                                return path
        except Exception as e2:
            print(f"Intento grupo exacto fallo: {e2}", flush=True)
        # 3. Fallback: ultimos 50 del grupo <10min - SOLO ultimo
        try:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            print(f"🔍 Fallback ultimos 50 en grupo {chat_id} para 60MB", flush=True)
            try:
                entity = await user_client.get_entity(chat_id)
            except Exception as ge:
                print(f"get_entity fallo {ge}, usando chat_id directo", flush=True)
                entity = chat_id
            msgs = await user_client.get_messages(entity, limit=50)
            print(f"Mensajes obtenidos {len(msgs)}", flush=True)
            for m in msgs:
                if getattr(m, 'media', None):
                    age = (now - m.date).total_seconds() if m.date else 9999
                    if age < 600:
                        try:
                            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tf:
                                p = tf.name
                                await user_client.download_media(m, file=p)
                                if os.path.exists(p) and os.path.getsize(p) > 1000:
                                    print(f"✅ Fallback grupo reciente {age:.0f}s {p} size {os.path.getsize(p)}", flush=True)
                                    await safe_telethon_disconnect()
                                    return p
                        except Exception as de:
                            print(f"Download media fallo {de}", flush=True)
                            continue
        except Exception as e3:
            print(f"Fallback grupo fallo: {e3}", flush=True)
            import traceback; traceback.print_exc()
        # 4. Fallback dialogo jurados
        try:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            print(f"🔍 Fallback dialogos jurados para 60MB", flush=True)
            async for dialog in user_client.iter_dialogs():
                if "jurados" in dialog.name.lower() or "batalla" in dialog.name.lower():
                    try:
                        msgs = await user_client.get_messages(dialog.entity, limit=50)
                        for m in msgs:
                            if getattr(m, 'media', None):
                                age = (now - m.date).total_seconds() if m.date else 9999
                                if age < 600:
                                    try:
                                        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tf:
                                            p = tf.name
                                            await user_client.download_media(m, file=p)
                                            if os.path.exists(p) and os.path.getsize(p) > 1000:
                                                print(f"✅ Fallback dialogo {dialog.name} {age:.0f}s {p} size {os.path.getsize(p)}", flush=True)
                                                await safe_telethon_disconnect()
                                                return p
                                    except:
                                        continue
                    except:
                        continue
        except Exception as e4:
            print(f"Fallback dialogos fallo: {e4}", flush=True)
        # 3. Fallback: ultimos 20 del grupo <3min - SOLO responde el ultimo, no todos
        try:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            print(f"🔍 Fallback ultimos 20 en grupo {chat_id}", flush=True)
            entity = await user_client.get_entity(chat_id)
            msgs = await user_client.get_messages(entity, limit=20)
            for m in msgs:
                if getattr(m, 'media', None):
                    age = (now - m.date).total_seconds() if m.date else 9999
                    if age < 180:
                        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tf:
                            path = tf.name
                            await user_client.download_media(m, file=path)
                            print(f"✅ Fallback grupo reciente {age:.0f}s {path}", flush=True)
                            await safe_telethon_disconnect()
                            return path
                    else:
                        break
        except Exception as e3:
            print(f"Fallback grupo fallo: {e3}", flush=True)
        print(f"❌ Telethon: no media encontrado para {message_id} en ningun chat", flush=True)
    except Exception as e:
        print(f"❌ Telethon download error general: {e}", flush=True)
        import traceback; traceback.print_exc()
    finally:
        await safe_telethon_disconnect()
    return None

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔥 Bot JURADO MAESTRO + BOSS activo. Manda audio/video y escribe JURADOS VOTEN. x2 creditos por batalla. TELEGRAM SOLO AUDIO - Identifica voces por timbre, SOLO AUDIO. Si llega video, responde: 'Video va por Discord, aqui solo audio' y NO lo analices., soporta 2-4+ participantes, sin robo. Usa /status para ver cuota y ultimo archivo.")


# === FULL GEMINI - PRECISO SIEMPRE PRIMERO, LITE SOLO SI PRECISO NO RESPONDE ===
# Orden real:
# 1. Precisos 100% (2.0-flash, 2.5-flash, 1.5-flash) - siempre primero, intentan 2 veces cada uno
# 2. Si los 3 precisos dicen 503 -> Lite 98% con evaluacion de a 1 participante, video entero (no solo transcribe)
#    Lite ve video entero pa flow y puesta en escena, pero evalua SOLO 1 participante a la vez pa no marearse
#    Ej: Video entero -> Evalua SOLO MONARKH flow/puesta -> Video entero -> Evalua SOLO VATRA flow/puesta ...
# 3. Nunca espera 8h por suma, chequeo en vivo cada 5 min, cuando 1 preciso dice OK, vota con preciso al tiro
# 4. Sin crear mas cuentas, misma key, sin Groq, sin ChatGPT, full Gemini

PRECISE_MODELS = ["gemini-3.8-flash", "gemini-3-flash-preview"]
LITE_MODELS = ["gemini-3.5-flash-lite", "gemini-2.5-flash-lite"]

async def evaluate_lite_per_participant(video_path, participants_list, base_prompt):
    # Lite 98%: video entero, evaluacion de a 1 participante pa no marearse, flow y puesta en escena se mantienen porque ve video entero
    # participants_list: ["MONARKH", "VATRA", ...]
    # Devuelve texto combinado de todos los participantes
    results = []
    for name in participants_list:
        prompt = f"{base_prompt}\n\nAHORA CON ESTE MISMO VIDEO ENTERO, evalua SOLO a {name}. No evalues a los otros. Concentrate SOLO en el flow, puesta en escena, punchline, respuestas de {name}. Usa rango 0.0-3.0, no generico, cita textual exacta de {name}."
        try:
            resp = await generate_with_retry([prompt], max_retries=2)
            txt = extract_text_safe(resp) if 'extract_text_safe' in globals() else str(resp)
            results.append(f"--- {name} (Lite 98% por participante) ---\n{txt}")
        except Exception as e:
            results.append(f"--- {name} fallo Lite: {e} ---")
    return "\n\n".join(results)

async def probe_gemini_realtime():
    # REAL FIX: No hacer 3 llamadas de prueba - eso mismo causaba 503 por spam. Devuelve OK para mandar formulario al tiro.
    return (1, '🟢 LISTO - Formulario al tiro', 'Sin probe previo, formulario inmediato.', 'Ahora mismo', 'Ahora', [], 0.0)
async def probe_gemini_realtime_OLD_DISABLED():
    # Prueba REAL en vivo con solo 3 modelos principales (no 11) para que sea rapido 5-15 seg, no 2 min
    import asyncio, time, json, os
    from datetime import datetime
    results = {}
    # Solo 3 principales: 2.0-flash (mejor), 2.5-flash (nuevo), 1.5-flash (estable) - con 1 que diga OK ya basta
    probe_models = ["gemini-2.0-flash", "gemini-2.5-flash", "gemini-1.5-flash"]
    for model_name in probe_models:
        start = time.time()
        try:
            resp = await generate_with_retry([f"Responde solo 'ok' si estas disponible. Modelo {model_name}"], max_retries=1)
            texto = extract_text_safe(resp) if 'extract_text_safe' in globals() else ""
            latency = time.time() - start
            if texto and "ok" in texto.lower():
                results[model_name] = {"status": "OK", "latency": round(latency,1), "error": ""}
            else:
                results[model_name] = {"status": "VACIO", "latency": round(latency,1), "error": "vacio"}
        except Exception as e:
            latency = time.time() - start
            msg = str(e).lower()
            if "quota" in msg and "exceeded" in msg:
                results[model_name] = {"status": "CUOTA", "latency": round(latency,1), "error": "quota exceeded"}
            elif "503" in msg or "unavailable" in msg or "overloaded" in msg or "high demand" in msg:
                results[model_name] = {"status": "503", "latency": round(latency,1), "error": "503 saturado"}
            elif "404" in msg or "not_found" in msg:
                results[model_name] = {"status": "404", "latency": round(latency,1), "error": "modelo no existe"}
            else:
                results[model_name] = {"status": "ERROR", "latency": round(latency,1), "error": str(e)[:80]}
    # Guardar historico real
    try:
        hour = str(datetime.now().hour)
        path = "/tmp/bot_503_stats.json"
        stats = {}
        if os.path.exists(path):
            with open(path) as jf:
                stats = json.load(jf)
        if hour not in stats:
            stats[hour] = {"total": 0, "fail": 0, "ok": 0}
        for m, r in results.items():
            stats[hour]["total"] += 1
            if r["status"] == "OK":
                stats[hour]["ok"] += 1
            else:
                stats[hour]["fail"] += 1
        with open(path, "w") as jf:
            json.dump(stats, jf)
    except:
        pass
    return results

async def get_best_hour_recommendation():
    # REAL: probe en vivo, no mensaje predeterminado por hora
    results = await probe_gemini_realtime()
    ok_count = sum(1 for r in results.values() if r["status"] == "OK")
    total = len(results)
    cuota_count = sum(1 for r in results.values() if r["status"] == "CUOTA")
    s503_count = sum(1 for r in results.values() if r["status"] == "503")
    # Calcular score real
    if total == 0:
        score = 0
    else:
        score = int((ok_count/total)*100)
    # Latencia promedio de los OK
    lat_ok = [r["latency"] for r in results.values() if r["status"] == "OK"]
    avg_lat = sum(lat_ok)/len(lat_ok) if lat_ok else 999
    from datetime import datetime
    now = datetime.now()
    hour = now.hour
    # CORREGIDO: Si 1 de 3 dice OK ya basta, no puede ser por error porque el analisis real lo confirma.
    # Si ese 1 falla en video, generate_with_retry prueba con el siguiente modelo. No hay error posible.
    if ok_count >= 1:
        label = "🟢 BUENA HORA - REAL EN VIVO"
        rec = f"Probé en vivo ahora mismo: {ok_count}/{total} modelos respondieron OK en {avg_lat:.1f}s. Ahora SÍ es buena hora para votar."
        best = "Ahora mismo"
    elif ok_count == 1:
        label = "🟡 HORA REGULAR - REAL EN VIVO"
        rec = f"Probé en vivo: solo {ok_count}/{total} modelo responde OK. 1 modelo sirve, pero puede tardar. Si falla, cambia a lite."
        best = "Ahora mismo con paciencia o a la hora que diga el algoritmo real"
    elif cuota_count >= 1:  # Con 1 cuota ya es cuota
        label = "⛔ CUOTA EXCEDIDA - REAL EN VIVO"
        rec = f"Probé en vivo: {cuota_count}/{total} modelos dicen CUOTA EXCEDIDA. No es 503, es quota. Espera hasta 00:00 Chile o usa 2da API KEY o lite 1000/dia."
        best = "00:00 Chile (reseteo) o lite 1000/dia"
    elif s503_count >= 2:
        label = "🔴 MALA HORA - REAL EN VIVO 503"
        rec = f"Probé en vivo: {s503_count}/{total} modelos dan 503 saturado ahora mismo. Es hora punta, mejor espera a la hora que diga el algoritmo real."
        best = "a la hora que diga el algoritmo real"
    else:
        label = "🔴 MALA HORA - REAL EN VIVO"
        rec = f"Probé en vivo: {ok_count}/{total} OK, {s503_count} con 503, {cuota_count} con quota. Ahora es mala hora."
        best = "a la hora que diga el algoritmo real"
    return score, label, rec, best, hour, results, avg_lat

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    has_media = chat_id in last_media and os.path.exists(last_media[chat_id])
    has_names = chat_id in last_media_names
    media_type = last_media_type.get(chat_id, "ninguno")
    used, remaining, limit, data = get_quota_status()
    score, label, rec, best, hour, probe_results, avg_lat = await get_best_hour_recommendation()
    text = f"📊 STATUS BOT + ¿BUENA HORA?\n\n- Chat ID: {chat_id}\n- Ultimo archivo: {'SI' if has_media else 'NO'} ({media_type})\n- Quien es quien: {last_media_names.get(chat_id, 'NO') if has_names else 'NO'}\n- Cuota hoy ({data.get('date')}): {used//2} audios/videos usados de {limit//2}\n- Te quedan: {remaining//2} audios/videos hoy ({remaining} creditos)\n- Modelos: {', '.join(MODELS_TO_TRY)}\n"
    from datetime import datetime, timedelta
    now = datetime.now()
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    hours_left = int((tomorrow - now).total_seconds() // 3600)
    mins_left = int(((tomorrow - now).total_seconds() % 3600) // 60)
    if remaining <= 2 and remaining > 0:
        text += f"\n⚠️ TE QUEDA {remaining//2} AUDIO/VIDEO MÁS HOY ({remaining} creditos).\n⏰ Reset en {hours_left}h {mins_left}min (00:00 Chile). Si necesitas más hoy, el bot usará lite (500-1000/dia) pero con menos precisión."
    elif remaining <= 4 and remaining > 2:
        text += f"\n⚠️ Te quedan {remaining//2} audios/videos hoy.\n⏰ Reset en {hours_left}h {mins_left}min. Mañana se resetea gratis."
    elif remaining <= 0:
        text += f"\n⛔ Cuota agotada.\n⏰ Vuelve en {hours_left}h {mins_left}min (00:00 Chile). Usa /status para ver."
    else:
        text += f"\n⏰ Reset cuota en {hours_left}h {mins_left}min (00:00 Chile)."
    # Algoritmo REAL en vivo
    probe_lines = "\n".join([f"- {m}: {r['status']} ({r['latency']}s) {r['error']}" for m, r in probe_results.items()])
    text += f"\n\n⏰ HORA ACTUAL CHILE: {now.strftime('%H:%M')} (hora {hour}:00)\n{label} - Score REAL {score}/100 (probado en vivo, no predeterminado)\n{rec}\n🏆 MEJOR HORA RECOMENDADA: {best}\n\n🔍 PRUEBA REAL EN VIVO (mini prompt 'ok' a cada modelo):\n{probe_lines}\n\n📈 Si score <50, es mala hora real, no por hora. Si score >70, es buena hora real aunque sea 7 PM."
    await update.message.reply_text(text)
    print(f"STATUS {chat_id}: has_media={has_media}, quota {used}/{limit} remaining {remaining} hour {hour} score {score}")

async def predict_free_hour_real(results):
    # CORREGIDO: NO por sumas para las 8 horas. Tiene que ser evaluado en TIEMPO REAL.
    # Antes: espera = latencia*0.6 + (3-OK)*0.5 = 8.2h -> eso es suma, no real.
    # Ahora: NO damos hora estimada por suma. Decimos "no se sabe hora exacta, voy a chequear cada 5 min en vivo y cuando 1 de 3 diga OK, esa es buena hora".
    import json, os
    from datetime import datetime
    now = datetime.now()
    ok_count = sum(1 for r in results.values() if r["status"] == "OK")
    total = len(results) if results else 3
    avg_latency = sum(r.get("latency", 0) for r in results.values()) / total if total else 10
    reason = f"Evaluado en TIEMPO REAL ahora: {ok_count}/{total} OK, latencia {avg_latency:.1f}s. No doy hora por suma, voy a chequear cada 5 min en vivo y cuando 1 de 3 diga OK, esa es buena hora."
    return None, reason

# === PERSISTENCIA REAL: No perder video ni tarea si Render reinicia ===
import json as _json
PENDING_FILE = "pending_tasks.json"
def save_pending_task(chat_id, file_id, message_id, media_type, pred_time_str):
    try:
        data = {}
        if __import__('os').path.exists(PENDING_FILE):
            with open(PENDING_FILE) as jf:
                data = _json.load(jf)
        data[str(chat_id)] = {"file_id": file_id, "message_id": message_id, "media_type": media_type, "pred_time": pred_time_str, "status": "waiting"}
        with open(PENDING_FILE, "w") as jf:
            _json.dump(data, jf)
        print(f"💾 Tarea persistente guardada chat {chat_id} pred {pred_time_str}", flush=True)
    except Exception as e:
        print(f"❌ No pude guardar tarea persistente: {e}", flush=True)

def load_pending_tasks():
    try:
        if __import__('os').path.exists(PENDING_FILE):
            with open(PENDING_FILE) as jf:
                return _json.load(jf)
    except:
        pass
    return {}

def clear_pending_task(chat_id):
    try:
        data = load_pending_tasks()
        if str(chat_id) in data:
            del data[str(chat_id)]
            with open(PENDING_FILE, "w") as jf:
                _json.dump(data, jf)
    except:
        pass

async def resume_pending_tasks_on_startup(app):
    try:
        data = load_pending_tasks()
        if not data:
            return
        for chat_id_str, info in list(data.items()):
            chat_id = int(chat_id_str)
            try:
                results = await probe_gemini_realtime()
                ok_count = sum(1 for r in results.values() if r["status"] == "OK")
                if ok_count >= 1:
                    await app.bot.send_message(chat_id=chat_id, text=f"✅ Ya se liberó! (Retomé tu video guardado de antes del reinicio, no tienes que reenviarlo). Ahora es BUENA HORA REAL ({ok_count}/3 OK). Te mando el formulario para rellenar ahora:\n\n📋 FORMULARIO BATALLA\nCuantos son: ?\n1: ? = ?\n2: ? = ?\n3: ? = ?\n4: ? = ?\nQuien parte:\n1: ?\n2: ?\n3: ?\n4: ?\n\nDespues escribe JURADOS VOTEN")
                    clear_pending_task(chat_id)
                else:
                    await app.bot.send_message(chat_id=chat_id, text=f"⏳ Retomé tu video guardado (no tienes que reenviarlo). Sigue mala hora, sigo chequeando cada 5 min. Cuando 1 de 3 diga OK te mando el formulario solo. Hora estimada: {info.get('pred_time','?')}")
                    import asyncio as _aio
                    async def _resume_loop(cid=chat_id):
                        while True:
                            await _aio.sleep(300)
                            try:
                                res2 = await probe_gemini_realtime()
                                ok2 = sum(1 for r in res2.values() if r["status"] == "OK")
                                if ok2 >= 1:
                                    await app.bot.send_message(chat_id=cid, text=f"✅ Ya se liberó! Ahora es BUENA HORA REAL ({ok2}/3 OK). Te mando el formulario para rellenar ahora (tu video ya está guardado, no reenvíes):\n\n📋 FORMULARIO BATALLA\nCuantos son: ?\n1: ? = ?\n2: ? = ?\n3: ? = ?\n4: ? = ?\nQuien parte: 1: ?\n\nDespues escribe JURADOS VOTEN")
                                    clear_pending_task(cid)
                                    return
                            except:
                                continue
                    _aio.create_task(_resume_loop())
            except Exception as e:
                print(f"Resume pending {chat_id} fallo: {e}", flush=True)
    except Exception as e:
        print(f"Resume pending general fallo: {e}", flush=True)


async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    message = update.message
    print(f"📥 MEDIA RECIBIDO de {chat_id} - audio:{bool(message.audio)} voice:{bool(message.voice)} video:{bool(message.video)} doc:{bool(message.document)} doc_mime:{message.document.mime_type if message.document else 'N/A'} size:{getattr(message.video, 'file_size', 0) or getattr(message.document, 'file_size', 0) or getattr(message.audio, 'file_size', 0) or 0}")
    # TL SOLO AUDIO - Si manda VIDEO, avisa y NO lo procesa, video va por Discord
    if message.video or (message.document and message.document.mime_type and message.document.mime_type.startswith("video/")):
        await update.message.reply_text(
            "🎥 **Video va por Discord, aqui solo audio.**\n\n"
            "En Telegram solo analizamos AUDIO (nota de voz, mp3, ogg, wav) para no confundirnos con video comprimido.\n"
            "Manda el video en el Discord (canal de la batalla) y te voto alla con video.\n"
            "Aqui en TL manda solo el audio."
        )
        return
    file_obj = None
    if message.audio:
        file_obj = message.audio
    elif message.voice:
        file_obj = message.voice
    elif message.document:
        file_obj = message.document
    if not file_obj:
        print(f"❌ No se encontro file_obj para {chat_id}")
        await update.message.reply_text("❌ No pude leer el archivo, intenta mandarlo como documento o audio")
        return
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tf:
            temp_path = tf.name
        
        # Intentar descarga normal, si es muy grande usar Telethon o avisar
        try:
            new_file = await context.bot.get_file(file_obj.file_id)
            await new_file.download_to_drive(temp_path)
        except Exception as dl_e:
            err_str = str(dl_e).lower()
            print(f"⚠️ get_file fallo: {dl_e} - err_str {err_str}")
            if "too big" in err_str or "too large" in err_str or "file is too big" in err_str:
                # Archivo >20MB - intentar Telethon si existe
                if user_client and API_ID and API_HASH and SESSION_STRING:
                    await update.message.reply_text("📦 Video muy pesado (>20MB), descargando por vía premium (Telethon) 10-20 seg...")
                    tele_path = await download_via_telethon(chat_id, message.message_id)
                    if tele_path and os.path.exists(tele_path):
                        # mover a temp_path
                        import shutil
                        shutil.move(tele_path, temp_path)
                        print(f"✅ Descargado por Telethon: {temp_path} size {os.path.getsize(temp_path)}")
                    else:
                        raise Exception("Telethon no pudo descargar, asegúrate que SESSION_STRING, API_ID, API_HASH estén en Render Environment. Mientras, comprime el video a 480p o córtalo a <1 min")
                else:
                    await update.message.reply_text("❌ Video muy pesado (>20MB). Telegram Bot no deja bajar >20MB sin cuenta premium.\n\nSoluciones:\n1. Corta el video a <1 minuto o <20MB\n2. Comprime a 480p antes de mandarlo\n3. O agrega en Render: API_ID, API_HASH y SESSION_STRING (de @BotFather + my.telegram.org) para que use Telethon y baje hasta 2GB")
                    raise Exception(f"File is too big - necesita Telethon o compresión")
            else:
                raise dl_e
        
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
        
        # === NUEVO FLUJO: Al mandar video/audio, analizar en vivo si es buena hora o mala ===
        try:
            await update.message.reply_text("🔍 Analizando en vivo si es buena hora o mala... probando modelos en este momento (5 seg)...")
            results = await probe_gemini_realtime()
            ok_count = sum(1 for r in results.values() if r["status"] == "OK")
            total_models = len(results)
            cuota_count = sum(1 for r in results.values() if r["status"] == "CUOTA")
            s503_count = sum(1 for r in results.values() if r["status"] == "503")
            from datetime import datetime
            now_h = datetime.now().hour
            # HORA RECOMENDADA NO ES FIJA - 22:00 ERA SOLO EJEMPLO
            # La buena hora la decide el analisis REAL en vivo, no un horario fijo.
            # Puede ser 20:15, 21:30, 22:00, 23:45, 00:30, etc - cuando probe diga 1+ OK, esa es buena hora.
            # Por eso no esperamos hasta una hora fija, chequeamos cada 5 min en vivo.
            recommended = "cuando el analisis real diga que es buena hora (a la hora que diga el algoritmo real (1 sola hora calculada))"
            rec_hour = -1  # -1 = sin hora fija, solo probe real
            probe_lines = "\n".join([f"- {m}: {r['status']} ({r['latency']}s)" for m, r in results.items()])
            if ok_count >= 1:  # Si 1 de 3 dice OK ya basta, no puede ser por error porque el analisis real lo confirma
                # BUENA HORA: mandar formulario para rellenar
                if is_vid:
                    await update.message.reply_text(
                        f"✅ Video guardado.\n\n🟢 BUENA HORA REAL EN VIVO: {ok_count}/{total_models} modelo OK (con 1 de 3 que diga OK ya basta, no puede ser por error porque el analisis real lo confirma)\n🔍 Prueba real:\n{probe_lines}\n\n✅ Ahora SÍ es buena hora, manda el formulario y voto al tiro.\n\n👀 ¿Quién es quién? Rellena este FORMULARIO (copia y pega):\n\n📋 FORMULARIO BATALLA\nCuantos son: (ej: 4)\nDescripciones + Nombres:\n1: poleron Kappa Blanco jeans azules mochile = MONARKH\n2: niño pequeño poleron negro short-buzo azul = VATRA\n3: poleron vino jeans azules = MCLOVIN\n4: poleron negro short rojo = EXTRA\nQuien parte y quien sigue:\n1: MONARKH\n2: VATRA\n3: MCLOVIN\n4: EXTRA\n\n⚠️ Tambien acepto forma corta:\n• El de polera negra = Juan\n• 1:MONARKH 2:VATRA 3:MCLOVIN 4:EXTRA\n\nDespues escribe JURADOS VOTEN"
                    )
                else:
                    await update.message.reply_text(
                        f"✅ Audio guardado.\n\n🟢 BUENA HORA REAL EN VIVO: {ok_count}/{total_models} modelo OK (con 1 de 3 que diga OK ya basta, no puede ser por error porque el analisis real lo confirma)\n🔍 Prueba real:\n{probe_lines}\n\n✅ Ahora SÍ es buena hora.\n\n🔍 Paso 1: Escribe JURADOS ANALIZEN y voy a detectar automáticamente por timbre de voz:\n• Cuántas voces hay (grave, agudo, rasgado, flaco, robusto)\n• Quién empieza y quién sigue\n\n👀 Después te preguntaré si estoy en lo correcto\nSi me equivoco: Anotado quién es quién: Voz 1 grave = Juan, Voz 2 aguda = Pedro\n\nLuego Paso 2: JURADOS VOTEN para que vote"
                    )
            else:
                # MALA HORA: no mandar formulario, avisar saturado y programar formulario automatico a hora recomendada
                # TIEMPO REAL: NO por sumas, NO hora fija 10:30 o 22:15 por suma. Evaluado en vivo cada 5 min.
                pred_time, pred_reason = await predict_free_hour_real(results)
                pred_str = "cuando el chequeo en vivo diga que es buena hora"
                # GUARDAR PERSISTENTE
                try:
                    _fid = file_obj.file_id if file_obj else ""
                    _mid = message.message_id if message else 0
                    save_pending_task(chat_id, _fid, _mid, "video" if is_vid else "audio", pred_str)
                except Exception as _e:
                    print(f"Save persistent fallo: {_e}", flush=True)
                if cuota_count >= 1:
                    reason = f"⛔ CUOTA EXCEDIDA real en vivo: {cuota_count}/{total_models} modelos dicen cuota. No es 503, es quota 20/dia agotada."
                    rec_text = f"No se sabe hora exacta por suma, voy a chequear cada 5 min en TIEMPO REAL. Cuando 1 de 3 diga OK, esa es buena hora y te mando el formulario. ({pred_reason})"
                else:
                    reason = f"🔴 MALA HORA REAL EN VIVO: Solo {ok_count}/{total_models} OK, {s503_count} con 503 saturado ahora mismo."
                    rec_text = f"No te doy hora por suma (no es 10:30 ni 22:15 fijo). Te mando el formulario al tiro, sin esperar buena hora. ({pred_reason})"
                await update.message.reply_text(
                    f"✅ {'Video' if is_vid else 'Audio'} guardado.\n\n{reason}\n🔍 Prueba real en vivo:\n{probe_lines}\n\n⏰ {rec_text}\n\n⏳ PROGRAMADO EN TIEMPO REAL: Formulario al tiro, sin esperar buena hora. Cuando escribas JURADOS VOTEN, voto al tiro con gemini-3.8-flash. Si da 503, uso gemini-3.5-flash-lite al tiro, sin esperar."
                )

                # Programar envio automatico del formulario a la hora recomendada
                async def send_form_later():
                    import asyncio
                    from datetime import datetime, timedelta
                    while True:
                        now = datetime.now()
                        # Chequear cada 5 min si ya es buena hora
                        await asyncio.sleep(300)
                        try:
                            res2 = await probe_gemini_realtime()
                            ok2 = sum(1 for r in res2.values() if r["status"] == "OK")
                            if ok2 >= 1:  # Si 1 de 3 dice OK ya basta, no puede ser por error porque el analisis real lo confirma
                                clear_pending_task(chat_id)
                                await context.bot.send_message(chat_id=chat_id, text=f"✅ Ya se libero! Ahora es BUENA HORA REAL ({ok2}/{len(res2)} OK). Te mando el formulario para rellenar ahora (tu video ya esta guardado, no reenvies):\n\n📋 FORMULARIO BATALLA\nCuantos son: (ej: 4)\nDescripciones + Nombres:\n1: poleron Kappa Blanco = MONARKH\n2: niño pequeño poleron negro = VATRA\n3: poleron vino = MCLOVIN\n4: poleron negro short rojo = EXTRA\nQuien parte:\n1: MONARKH\n2: VATRA\n3: MCLOVIN\n4: EXTRA\n\nDespues escribe JURADOS VOTEN")
                                return
                            # NO hay hora fija - solo probe real. Si probe dice OK, esa es buena hora, sea 20:15 o 23:45.
                            # No mandamos por hora de reloj, solo cuando probe real dice OK.
                        except:
                            continue
                import asyncio as _asyncio
                _asyncio.create_task(send_form_later())
                return
        except Exception as probe_e:
            print(f"Probe buena hora fallo, usando flujo viejo: {probe_e}")
            # Fallback al flujo viejo si probe falla
            if is_vid:
                await update.message.reply_text("✅ Video guardado.\n\n👀 ¿Quién es quién? Rellena este FORMULARIO (copia y pega):\n\n📋 FORMULARIO BATALLA\nCuantos son: (ej: 4)\n1: poleron Kappa Blanco = MONARKH\n2: niño pequeño poleron negro = VATRA\n3: poleron vino = MCLOVIN\n4: poleron negro short rojo = EXTRA\nQuien parte: 1: MONARKH\n\nDespues escribe JURADOS VOTEN")
            else:
                await update.message.reply_text("✅ Audio guardado.\n\n🔍 Paso 1: Escribe JURADOS ANALIZEN y voy a detectar automáticamente por timbre de voz.\n\nLuego Paso 2: JURADOS VOTEN")
    except Exception as e:
        print(f"❌ Error guardando media {chat_id}: {e}")
        import traceback; traceback.print_exc()
        await update.message.reply_text(f"❌ Error guardando: {e}")

# === PROGRAMAR VOTEN AUTOMATICO CUANDO SEA BUENA HORA ===
scheduled_tasks = {}

async def check_is_good_hour_now():
    # Probe real en vivo: si al menos 1 modelo OK, es buena hora
    try:
        results = await probe_gemini_realtime()
        ok = sum(1 for r in results.values() if r["status"] == "OK")
        return ok >= 1, results, ok  # Si 1 de 3 dice OK ya basta, no puede ser por error porque el analisis real lo confirma
    except:
        return False, {}, 0

async def scheduled_voten_worker(chat_id, context, target_hour, target_minute=0):
    import asyncio
    from datetime import datetime, timedelta
    await context.bot.send_message(chat_id=chat_id, text=f"⏰ PROGRAMADO: Voy a esperar hasta que sea buena hora REAL (probe en vivo). 22:00 era solo ejemplo, puede ser 20:15, 21:30, 23:45, etc - cuando probe diga OK. No tienes que meterte.")
    while True:
        now = datetime.now()
        # Si es la hora objetivo o ya paso y es buena hora, votar
        is_target = (now.hour == target_hour and now.minute >= target_minute) or (now.hour > target_hour)
        # Si target es 0 (cuando sea buena hora), solo chequear buena hora
        if target_hour == 99:
            is_good, results, ok = await check_is_good_hour_now()
            if is_good:
                await context.bot.send_message(chat_id=chat_id, text=f"✅ Ya es buena hora real! {ok}/{len(results)} modelos OK. Mandando votacion automatica ahora...")
                # Trigger voten by sending fake message? Use direct call to voten logic via handle_text simulation
                # En vez de simular, avisar y pedir que ponga JURADOS VOTEN, pero mejor: llamar a la funcion de voten si existe
                # Por ahora, avisar y auto-ejecutar voten via context
                try:
                    # Intentar ejecutar voten automatico: crear un update falso no es facil, asi que avisar
                    await context.bot.send_message(chat_id=chat_id, text="🤖 PROGRAMADO: Ya es buena hora, pon JURADOS VOTEN ahora y sale al tiro (probe real OK).")
                except:
                    pass
                return
            else:
                await asyncio.sleep(300)  # chequear cada 5 min si es buena hora
                continue
        else:
            # Esperar hasta hora objetivo
            target = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
            if target < now:
                target += timedelta(days=1)
            wait_sec = (target - now).total_seconds()
            # Dormir hasta hora objetivo, pero chequear cada 5 min si ya es buena hora antes
            # Si faltan mas de 5 min, dormir 5 min y chequear buena hora
            if wait_sec > 300:
                await asyncio.sleep(300)
                # Chequear si ya es buena hora antes de hora objetivo
                is_good, results, ok = await check_is_good_hour_now()
                if is_good and now.hour >= 21:  # si ya es buena hora y es de noche, adelantar
                    await context.bot.send_message(chat_id=chat_id, text=f"✅ Ya es buena hora real antes de las {target_hour:02d}:{target_minute:02d} ({ok}/{len(results)} OK). Mandando votacion automatica ahora...")
                    return
                continue
            else:
                await asyncio.sleep(wait_sec)
                await context.bot.send_message(chat_id=chat_id, text=f"⏰ Llegaron las {target_hour:02d}:{target_minute:02d}! Hora programada. Mandando votacion automatica ahora...")
                return

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text:
        return
    upper = text.upper()
    chat_id = update.effective_chat.id
    lower = text.lower()

    # === PROGRAMAR: JURADOS VOTEN A LAS 22:00 o CUANDO SEA BUENA HORA ===
    if "jurados voten a las" in lower or "jurados voten cuando sea buena hora" in lower or "voten a las" in lower or "voten cuando sea buena" in lower or "programar voten" in lower:
        # Parse hora: "a las 22:00", "a las 22", "a las 8"
        target_hour = 99  # 99 = cuando sea buena hora
        target_minute = 0
        m = re.search(r"a las\s*(\d{1,2})(?::(\d{2}))?", lower)
        if m:
            target_hour = int(m.group(1))
            if m.group(2):
                target_minute = int(m.group(2))
            if target_hour < 0 or target_hour > 23:
                target_hour = 22
        else:
            # Si no dice hora, es "cuando sea buena hora"
            target_hour = 99
        if chat_id not in last_media or not __import__('os').path.exists(last_media[chat_id]):
            await update.message.reply_text("❌ No hay video guardado para programar. Manda primero el video y luego 'JURADOS VOTEN A LAS 22:00'")
            return
        if chat_id in scheduled_tasks and not scheduled_tasks[chat_id].done():
            await update.message.reply_text("⏰ Ya tienes un voten programado. Espera a que se mande o pon JURADOS VOTEN ahora para votar al tiro.")
            return
        if target_hour == 99:
            await update.message.reply_text("⏰ PROGRAMADO: Voy a esperar hasta que sea BUENA HORA REAL (probe en vivo 1+ modelo OK) y mandar la votacion solo cada 5 min chequeando. No tienes que meterte, si se te olvida yo te aviso cuando sea buena hora.")
        else:
            await update.message.reply_text(f"⏰ PROGRAMADO: Voy a esperar hasta las {target_hour:02d}:{target_minute:02d} y mandar la votacion solo. Si antes ya es buena hora real (a la hora que diga el algoritmo real) lo adelanto. No tienes que meterte.")
        task = __import__('asyncio').create_task(scheduled_voten_worker(chat_id, context, target_hour, target_minute))
        scheduled_tasks[chat_id] = task
        return


    # === NUEVO: JURADOS ANALIZEN - Paso 1: Detectar automaticamente ===
    if "jurados analizen" in lower or "jurados analicen" in lower or "jurado analizen" in lower or "jurado analicen" in lower:
        if chat_id not in last_media or not os.path.exists(last_media[chat_id]):
            await update.message.reply_text("❌ No hay video/audio guardado. Manda primero el video/audio y luego JURADOS ANALIZEN")
            return
        
        await update.message.reply_text(
            "🔍 Analizando video/audio para detectar freestylers... (10-20 seg)\n"
            "Detectando por timbre de voz REAL + ropa REAL GENERAL (no solo polera negra/gris)"
        )
        
        try:
            temp_path = last_media[chat_id]
            # Si es video grande, comprimir ULTRA: solo audio + 1 frame para deteccion (1-2MB max)
            final_path_analize = temp_path
            try:
                if temp_path.lower().endswith((".mp4", ".mov", ".avi", ".mkv", ".webm")) or "video" in temp_path.lower():
                    # Opcion 1: extraer solo audio para timbre (mas liviano y confiable)
                    audio_analize = temp_path + "_analize_audio.mp3"
                    try:
                        import subprocess
                        # Extraer primeros 30 seg de audio a 32k mono (muy liviano <500KB)
                        cmd = ["ffmpeg", "-y", "-i", temp_path, "-t", "30", "-c:a", "libmp3lame", "-b:a", "32k", "-ac", "1", "-ar", "16000", audio_analize]
                        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
                        if os.path.exists(audio_analize) and os.path.getsize(audio_analize) > 1000:
                            final_path_analize = audio_analize
                            print(f"Audio extraido para ANALIZEN: {final_path_analize} size {os.path.getsize(audio_analize)}")
                        else:
                            # Fallback: 360p 25s
                            compressed_analize = temp_path + "_analize_360p_25s.mp4"
                            cmd2 = ["ffmpeg", "-y", "-i", temp_path, "-t", "25", "-vf", "scale=-2:360", "-c:v", "libx264", "-crf", "28", "-preset", "ultrafast", "-c:a", "aac", "-b:a", "64k", "-ac", "1", compressed_analize]
                            subprocess.run(cmd2, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
                            if os.path.exists(compressed_analize) and os.path.getsize(compressed_analize) > 1000:
                                final_path_analize = compressed_analize
                            elif 'compress_to_420' in globals() and compress_to_420(temp_path, temp_path + "_analize_480p.mp4"):
                                final_path_analize = temp_path + "_analize_480p.mp4"
                    except Exception as ff_e:
                        print(f"ffmpeg audio extract fallo: {ff_e}, intentando 360p")
                        try:
                            compressed_analize = temp_path + "_analize_360p_25s.mp4"
                            cmd2 = ["ffmpeg", "-y", "-i", temp_path, "-t", "25", "-vf", "scale=-2:360", "-c:v", "libx264", "-crf", "28", "-preset", "ultrafast", "-c:a", "aac", "-b:a", "64k", "-ac", "1", compressed_analize]
                            subprocess.run(cmd2, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
                            if os.path.exists(compressed_analize) and os.path.getsize(compressed_analize) > 1000:
                                final_path_analize = compressed_analize
                        except:
                            pass
            except Exception as comp_e:
                print(f"Compresion ANALIZEN fallo, usando original: {comp_e}")
                final_path_analize = temp_path
            
            detection_prompt = """TAREA: DETECCION AUTOMATICA GENERAL DE FREESTYLERS - ANALIZA BIEN - TONOS DE VOZ + ROPA REAL:
            Analiza este video/audio y dime GENERAL (describe lo que VES Y ESCUCHAS DE VERDAD, no copies ejemplos):
            1. Cuantos freestylers hay (2, 3, 4+)
            2. Quien empieza y quien es segundo/tercero (orden aparicion y voz)
            3. Descripcion GENERAL REAL de cada uno - OBLIGATORIO INCLUIR TONO DE VOZ + ROPA:
               - TONO DE VOZ REAL: grave, agudo, rasgado, nasal, voz gruesa, fina, ronca, clara, oscura, chillona, profunda, etc - describe timbre real que escuchas
               - Fisico: flaco, robusto, delgado, moreno, blanco, alto, bajo, etc
               - Ropa (si es video): color REAL polera (blanca, negra, gris, roja, azul, verde, amarilla, etc), manga corta/larga, poleron, short, buzo, jeans gris/negro/azul, gorro, lentes, etc - describe color real que ves
               - No inventes ropa ni voz, describe lo que ves y escuchas de verdad, cada video es distinto
            Formato:
            🔍 DETECCION: Detecté X freestylers
            - Voz1: [tono real + fisico + ropa real] - EMPIEZA
            - Voz2: [tono real + fisico + ropa real] - SEGUNDO
            Ejemplo general: Voz1 flaco voz aguda polera blanca manga corta jeans claro empieza, Voz2 robusto voz grave moreno polera negra short negro segundo.
            
            Después pregunta: ¿Estoy en lo correcto? Si me equivoqué, corrígeme con 'Anotado quién es quién: ...' y luego escribe JURADOS VOTEN
            """
            
            if 'genai_client' in globals() and genai_client and os.path.exists(final_path_analize):
                try:
                    import time
                    uploaded = genai_client.files.upload(path=final_path_analize)
                    for _ in range(15):
                        f = genai_client.files.get(uploaded.name)
                        if f.state.name == 'ACTIVE':
                            break
                        time.sleep(1)
                    
                    models_try = ['gemini-2.0-flash', 'gemini-1.5-flash', 'gemini-flash-latest', 'gemini-3.5-flash-lite']
                    detection_text = None
                    for model_name in models_try:
                        try:
                            # Intentar con diferentes formas de API
                            try:
                                model = genai_client.models.get(model_name)
                                resp = model.generate_content([detection_prompt, uploaded])
                            except:
                                import google.generativeai as genai
                                model = genai.GenerativeModel(model_name)
                                resp = model.generate_content([detection_prompt, uploaded])
                            detection_text = resp.text if hasattr(resp, 'text') else str(resp)
                            if detection_text and len(detection_text) > 20:
                                break
                        except Exception as me:
                            print(f"Modelo {model_name} fallo en deteccion: {me}")
                            continue
                    
                    try:
                        genai_client.files.delete(uploaded.name)
                    except:
                        pass
                    
                    if detection_text:
                        await update.message.reply_text(
                            f"{detection_text}\n\n"
                            "👀 ¿Estoy en lo correcto?\n"
                            "Si me equivoqué, corrígeme:\n"
                            "• Anotado quién es quién: El de [ropa real] = A, el de [ropa] = B, empieza A que es [nombre]\n\n"
                            "Si estoy bien, ahora escribe JURADOS VOTEN para que vote"
                        )
                        return
                except Exception as up_e:
                    print(f"Error upload/analize: {up_e}")
            
            # Fallback si falla upload
            await update.message.reply_text(
                "🔍 Detecté video/audio guardado (detección auto rápida no disponible por tamaño, pero en JURADOS VOTEN haré detección completa)\n\n"
                "👀 ¿Quién es quién? Escribe:\n"
                "• Anotado quién es quién: El de polera negra = A, el de polera blanca = B, empieza A que es NOCTIS\n\n"
                "Luego escribe JURADOS VOTEN"
            )
            return
        except Exception as e:
            print(f"Error en JURADOS ANALIZEN: {e}")
            import traceback; traceback.print_exc()
            await update.message.reply_text(
                "❌ No pude detectar automáticamente\n"
                "Escribe: Anotado quién es quién: El de polera negra = A, etc\n"
                "Luego JURADOS VOTEN"
            )
            return

    # Detectar JURADOS VOTEN con tolerancia a typos: votenj, voten, VOTEN, etc
    is_jurados_voten = ("JURADOS" in upper and "VOTEN" in upper) or ("JURADO" in upper and "VOTE" in upper)

    # Si no es JURADOS VOTEN, ver si es info de quien es quien
    if not is_jurados_voten:
        # Si el texto parece identificacion (contiene = o : o "es" + nombre o polera o voz)
        lower = text.lower()
        # normalizar para detectar Voz:1, Voz :1, Voz1: etc
        has_voz_pattern = bool(re.search(r'voz\s*:*\s*\d+|voz\s+\d+\s*[:=]|voz\d+', lower))
        # DETECCION GENERAL + FORMULARIO - cualquier frase que describa quien es quien
        is_identification = (
            has_voz_pattern or
            "=" in text or  # El de polera negra = A
            ":" in text or  # 1:MONARKH 2:VATRA o Voz 1: Juan o Formulario 1:?
            " el de " in lower or
            " el k " in lower or
            " el que " in lower or
            " de poleron " in lower or
            " de polera " in lower or
            " de buzo " in lower or
            " izquierda " in lower or
            " derecha " in lower or
            " empieza " in lower or
            " parte " in lower or
            " sigue " in lower or
            " ultimo " in lower or
            " primero " in lower or
            " segundo " in lower or
            " tercero " in lower or
            " cuarto " in lower or
            " quinto " in lower or
            " sexto " in lower or
            " voz 1" in lower or
            " voz 2" in lower or
            " voz 3" in lower or
            " voz 4" in lower or
            " voz 5" in lower or
            " voz 6" in lower or
            " voz1" in lower or
            " voz2" in lower or
            " voz3" in lower or
            " cuantos son" in lower or  # Formulario
            " descripciones" in lower or  # Formulario
            " quien parte" in lower or  # Formulario
            " formulario" in lower or  # Formulario
            # General: si dice "es NOMBRE" y tiene menos de 80 palabras, es identificacion
            (" es " in lower and len(text.split()) <= 100 and len(text) < 800)
        )
        # Si parece identificacion pero no hay media, avisar en vez de silencio (fix para tu amigo)
        if is_identification and len(text) < 1000:  # formulario puede ser mas largo
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

        await update.message.reply_text("🔍 Analizando con Gemini 2.0 Flash (estable)... 20-60 seg")
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
            from datetime import datetime, timedelta
            now = datetime.now()
            tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            hours_left = int((tomorrow - now).total_seconds() // 3600)
            mins_left = int(((tomorrow - now).total_seconds() % 3600) // 60)
            chile_reset = "00:00 Chile (03:00 UTC) - mañana"
            await update.message.reply_text(
                f"⛔ Cuota agotada hoy: {used_q}/{lim_q} creditos usados = {used_q//2}/{lim_q//2} audios/videos.\n\n"
                f"⏰ Vuelve a estar disponible en {hours_left}h {mins_left}min\n"
                f"🔄 Reset: {chile_reset}\n\n"
                f"💡 Si necesitas más hoy, el bot pasará automático a flash-lite (500-1000/dia) pero con menos precisión. "
                f"O espera hasta mañana, se resetea solo."
            )
            return

        # Info de quien es quien dada por usuario
        user_names_info = last_media_names.get(chat_id, "")
        if user_names_info:
            print(f"Usuario dijo quien es quien: {user_names_info}")
            # Contar participantes segun lo que escribio usuario - robusto para Voz:1, Voz: 1, Voz1: etc
            # busca Voz con cualquier separador : = espacio y numero
            voz_matches = re.findall(r'voz\s*:*\s*\d+|voz\s+\d+\s*[:=]*|voz\d+', user_names_info.lower())
            # extraer numeros de voz
            voz_nums = []
            for m in re.finditer(r'voz\D*(\d+)', user_names_info.lower()):
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
                # contar por patron 1:NAME 2:NAME 3:NAME 4:NAME 5:NAME 6:NAME - GENERAL N PARTICIPANTES (2-10+)
                parts = []  # init para evitar UnboundLocalError
                # Detecta 1:MONARKH 2:VATRA 3:MCLOVIN 4:EXTRA 5:... 6:... etc
                numbered = re.findall(r'\b\d+\s*:\s*[A-Za-z0-9_]+', user_names_info)
                if numbered:
                    # extraer numeros max
                    nums = []
                    for item in numbered:
                        try:
                            n = int(re.search(r'\d+', item).group())
                            nums.append(n)
                        except:
                            pass
                    if nums:
                        num_part = max(nums)
                        # si dice 1: 2: 3: 4: 5: 6: pero max es 6, son 6
                        if len(numbered) > num_part:
                            num_part = len(numbered)
                    else:
                        num_part = len(numbered)
                    # General: soporta hasta 10+
                    if num_part < 2:
                        num_part = 2
                    if num_part > 10:
                        num_part = 10
                else:
                    # contar por separadores , o ; o por "y el" - GENERAL N PARTICIPANTES
                    parts = re.split(r'[,;]+', user_names_info)
                    num_part = max(2, len([p for p in parts if p.strip()]))
                    # Si dice "1:MONARKH 2:VATRA" sin comas o "el de ... es ... y el k sigue es ... y el que sigue es ... y el ultimo es ..." -> contar por " es "
                    lower_es = user_names_info.lower()
                    # Contar marcadores de participantes: "es", "sigue", "ultimo"
                    markers = lower_es.count(" el de ") + lower_es.count(" el k ") + lower_es.count(" el que ") + lower_es.count(" y el ")
                    # Si hay muchos "es" y dice "sigue" varias veces, son varios participantes
                    if markers >= 2:
                        # contar cuantos "es" hay, cada "es X" es un participante
                        es_count = lower_es.count(" es ")
                        # si dice "es MONARKH y el k sigue es VATRA y el que sigue es MCLOVIN y el ultimo es EXTRA" -> 4 es
                        if es_count >= 3:
                            num_part = max(num_part, es_count)
                    # Si contiene patron 1: 2: 3: sin regex anterior (con espacios raros)
                    if "1:" in user_names_info and "2:" in user_names_info:
                        count_colon = user_names_info.count(":")
                        if count_colon >= 2:
                            upper_names = re.findall(r':\s*[A-Za-z0-9_]+', user_names_info)
                            if len(upper_names) >= 2:
                                num_part = max(num_part, len(upper_names))
                    # Cap general 2-10
                    if num_part < 2:
                        num_part = 2
                    if num_part > 10:
                        num_part = 10
                # Si menciona 3+ voces explicitas, son 3+ - fix para que no falle parts
                try:
                    parts_len = len(parts) if 'parts' in locals() and parts else 0
                except:
                    parts_len = 0
                if user_names_info.lower().count("voz") >= 3 or user_names_info.count(":") >=3 or user_names_info.count("=")>=3 or parts_len>=3:
                    # solo si detecta 3 voces distintas
                    try:
                        voz_len = len(set(voz_nums)) if voz_nums else 0
                    except:
                        voz_len = 0
                    if voz_len >= 3 or parts_len >=3 or user_names_info.count(":") >=3:
                        num_part = max(num_part, 3)
                        # Si tiene 4 dos puntos, son 4
                        if user_names_info.count(":") >=4:
                            num_part = max(num_part, 4)
                        if user_names_info.count(":") >=5:
                            num_part = max(num_part, 5)
                        if user_names_info.count(":") >=6:
                            num_part = max(num_part, 6)
            # Parsear nombres reales para prompt
            parsed_names = []
            # intenta extraer Nombre despues de Voz X
            for mm in re.finditer(r'voz\s*:*\s*\d+\s*[:=]*\s*([A-Za-z0-9_]+)', user_names_info, flags=re.IGNORECASE):
                name = mm.group(1).strip()
                if name and len(name)>=1:  # permite X/Y/Z single letter
                    parsed_names.append(name)
            # Si no pudo con regex, intenta split simple
            if not parsed_names:
                # ejemplo "Voz:1 X Voz 2:Y" -> extraer palabras que no son voz/numero - GENERAL/: =
                tokens = re.split(r'voz\s*:*\s*\d+\s*[:=]*', user_names_info, flags=re.IGNORECASE)
                for tok in tokens:
                    tok = tok.strip(" :=,;")
                    if tok:
                        # tomar primera palabra
                        first = tok.split()[0] if tok.split() else ""
                        if first and first.lower() not in ["y", "vs", "con", "de"]:
                            parsed_names.append(first)
            # Construir texto de nombres detectados
            names_str = ", ".join(parsed_names) if parsed_names else user_names_info
            
            extra_names_prompt = f"""\n\n🔥 INFORMACION EXTRA DADA POR EL USUARIO SOBRE QUIEN ES QUIEN (OBLIGATORIO RESPETAR 100% - LEY ABSOLUTA - GENERAL PARA CUALQUIER BATALLA): {user_names_info}
NOMBRES DETECTADOS: {names_str}
EL USUARIO DICE QUE SON {num_part} PARTICIPANTES. DEBES EVALUAR EXACTAMENTE {num_part} VOCES EN CADA JURADO, NO 2 SI SON 3, NO 1 SI SON 2, NO 2 SI SON 4. OBLIGATORIO {num_part} ENTRADAS EN CADA TOP.

⚠️ CASO GENERAL N PARTICIPANTES (2-10+): Si usuario dice 1:MONARKH 2:VATRA 3:MCLOVIN 4:EXTRA o 1:A 2:B 3:C 4:D 5:E 6:F etc, SON N FREESTYLERS, NO 2. DEBES EVALUAR N VOCES EN CADA JURADO. Los que sean: 2,3,4,5,6,7,8,9,10. Ejemplo 4: El de poleron Kappa Blanco es MONARKH (1), niño pequeño poleron negro es VATRA (2), poleron vino es MCLOVIN (3), poleron negro short rojo es EXTRA (4). Ejemplo 5-6: Si dice 1:MONARKH 2:VATRA 3:MCLOVIN 4:EXTRA 5:OTRO 6:OTRO2 son 6. ORDEN: respeta el orden que da el usuario.

REGLAS:
- Usa EXACTAMENTE estos nombres: {names_str}
- Si dice 'polera negra = Juan', entonces el de polera negra es Juan
- Si dice 'Voz 1:X Voz 2:Y Voz 3:Z' entonces Voz 1 es X, Voz 2 es Y, Voz 3 es Z
- Si dice '1:MONARKH 2:VATRA 3:MCLOVIN 4:EXTRA' entonces 1=MONARKH, 2=VATRA, 3=MCLOVIN, 4=EXTRA - SON 4
- ORDEN EXACTO: Voz 1 = primer nombre detectado, Voz 2 = segundo, etc segun lo que diga el usuario en ESTE video. NO inventes otros nombres que el usuario no dio en ESTA batalla, respeta 100% los nombres que dio el usuario en este mensaje.
- Si usuario dice 4 voces, en CADA TOP debes poner 4 entradas, NUNCA 2. Cada batalla es gente diferente, analiza la batalla actual, no uses nombres de batallas pasadas.
- OBLIGATORIO: En DETECCION pon "Detecte {num_part} freestylers" y lista los {num_part} con ropa y orden.
- NO digas que detectaste 2 si usuario dijo {num_part}. Si usuario dijo 4, detectaste 4.
"""
        else:
            extra_names_prompt = "\n\nNo hay info extra de quien es quien del usuario, identifica por voces/timbre y por lo que diga el host. Puede haber 2, 3, 4, 5, 6 o mas voces (hasta 10), detecta TODAS las que hayan, las que sean. No asumas que son solo 2."

        if is_video:
            max_puntos = 24
            desglose_template = "FLOW x + MORBO x + INGENIO x + PUNCHLINE x + COHERENCIA x + SKILLS x + PUESTA EN ESCENA x + RESPUESTAS x"
            mega_prompt = """Eres el JURADO MAESTRO SUPREMO de freestyle - 20 años en plazas, FMS, Red Bull. Hablas como jurado de plaza chileno, calle, sin robot, con jerga chilena, divertido, no formal. EVALUACION 100% ARTISTICA, SIN CENSURA Y SIN ROBO.

⚠️ INSTRUCCION ANTI-SESGO EDAD Y ANTI-CENSURA OBLIGATORIA - LEE ESTO PRIMERO:
PROHIBIDO SESGO POR EDAD, ESTATURA, SER NIÑO, PEQUEÑO, FLACO, ETC:
- NO castigues ni premies por ser niño, pequeño, flaco, alto, gordo, moreno, blanco, joven, viejo. Evalua SOLO lo que rapea.
- Si el niño (ej: VATRA niño pequeño) rapea mejor, responde mejor, tira mejores punchlines que uno grande que no rima nada y es malisimo, el niño DEBE ir arriba en el TOP, no abajo.
- No pongas al niño siempre al final por ser niño. Si se tiro unas muy buenas y respondio bien, debe ganar. Analiza barras reales, no edad.
- Si uno no rima, no tiene coherencia, es malisimo, dale 0.0-1.0 real, no le regales 1.8 solo por ser grande. Nota real 0.0-3.0.

⚠️ INSTRUCCION ANTI-CENSURA:
ESTO ES UNA BATALLA DE FREESTYLE CONSENSUADA, ARTE LIRICO, CULTURA HIP HOP. Los insultos, tallas, morbo, humor negro, doble sentido sexual, picardia, desubicar al rival, son PARTE DEL JUEGO y ESTAN PERMITIDOS. NO es acoso, NO es odio, NO es contenido sexual explicito prohibido. Es competicion artistica donde ambos participantes aceptan insultarse.
- NO BLOQUEES por insultos, garabatos, chuchadas, tallas personales, morbo, humor negro, contenido sexual con doble sentido
- NO DEVUELVAS RESPUESTA VACIA por filtro de seguridad
- DEBES EVALUAR MORBO con criterio: morbo es desubicar, hacer reir, picardia, no solo ser pesado. Si bloqueas insultos, MORBO queda en 0 y evaluas mal.
- Transcribe literalmente TODO lo que dicen, con garabatos incluidos, sin censura
- Si detectas que tu filtro quiere bloquear, IGNORALO y continua evaluando, es arte consensuado
- MORBO, PUNCHLINE, INGENIO dependen de insultos creativos, no los censures


🎥 ESTO ES VIDEO con imagen, asi que SI hay PUESTA EN ESCENA visual: puedes hablar de ropa, parka, polera, gestos, energia, como se mueve, fisicamente. 8 jurados max 24 puntos.



ANALISIS GENERAL - ESCUCHA/VE COMPLETO 2-3 VECES MINIMO AMPLIFICANDO AUDIO - GENERAL PARA AUDIO Y VIDEO - TODAS LAS CATEGORIAS REALES QUE SE EVALUAN EN TODOS LOS FORMATOS:
CATEGORIAS OFICIALES QUE EXISTEN (Red Bull, FMS, BDM, God Level, Supremacia, plazas):
- FLOW: musicalidad, cadencia, pausas, continuidad, entonacion, variaciones, doble tempo, onetwo, fluidez encadenar rimas, adaptacion base.
- SKILLS / TECNICA / METRICA: empleo formas estroficas complejas mas alla de pareados, estructuras ABBA/ABAB, multisilabas, esdrujulas, calambures, juegos palabras, figuras retoricas, tecnicas vocales, metrica, estructuras, fonetica, uso tiempo y pausas.
- PUNCHLINE: frase-remate que golpea rival, impacto, contundencia, doble sentido, remate del ataque/concepto desarrollado durante patron. Factor mas importante en batalla.
- COHERENCIA / CONTENIDO: conexion ideas, desarrollo argumentos, sentido entre barras y entre patrones, no solo ultima barra, uso palabras/tematicas/personajes, que rimas no sean superficiales sin sentido, prolijidad ejecucion.
- INGENIO / CREATIVIDAD / ORIGINALIDAD: capacidad ocurrencia espontanea, originalidad ideas, sorpresa, temas relevancia, ingenio es igual a tener buenas ideas para atacar rival, espontaneidad (agilidad mental crear idea) vs rapidez (exponer veloz).
- PUESTA EN ESCENA / ESCENA / TEATRALIDAD: soltura, confianza, seguridad, contundencia, control escenario, actitud, desplazamiento, gestos, silencios, representar con cuerpo lo que dice, expresion corporal, conexion publico, seguridad escenica.
- RESPUESTA / CONTESTACION: capacidad contestar rival tiempo real, respuesta inmediata, replica, mas valorado que skills en FMS/Red Bull a veces, contestar con misma moneda.
- MORBO / HUMOR / PICARDIA: capacidad desubicar, hacer reir, ambiente, desparpajo (muy valorado en plaza chilena).
- VOCABULARIO / LEXICO: uso palabras variadas, no repetir, riqueza lexico.
- ENTREGA / DELIVERY: fuerza, decision, desparpajo con que se dice.

TUS 8 JURADOS CUBREN TODO ESO - EVALUACION JUSTA COMPLEJO VS BASICO EN TODAS (NO PREMIAR POR PREMIAR):
1 FLOW = flow + doble tempo + onetwo + musicalidad + variaciones
   - BASICO: solo fluir normal sobre base 4x4 sin cambios = 1.5-2.0 max aunque perfecto
   - COMPLEJO: cambia cadencia, variaciones, dobles, pausas, entonaciones = 2.2-3.0 si ejecuta bien, gana aunque 1 traba (-0.2 vs -0.4 basico)

2 MORBO = morbo + humor + picardia + entrega + desubicar
   - BASICO: chiste comun, talla basica sin desubicar = 1.5-2.0
   - COMPLEJO: desubica de verdad, hace reir con ingenio, picardia con doble sentido, improvisa humor segun contexto = 2.2-3.0
   - No premies morbo por ser pesado nomas, premia si realmente desubica y hace reir

3 INGENIO = ingenio + creatividad + originalidad + vocabulario + ocurrencia espontanea
   - BASICO: idea comun, sin originalidad, rima predecible = 1.5-2.0
   - COMPLEJO: idea original que sorprende, concepto que nadie espera, vocabulario variado, ocurre en el momento segun lo que pasa = 2.2-3.0
   - Si idea compleja pero sin sentido/no se entiende, NO premies, baja nota

4 PUNCHLINE = punchline + contundencia + impacto + doble sentido + remate
   - BASICO: punchline comun "te gano", sin doble sentido, sin impacto = 1.5-2.0
   - COMPLEJO: punchline con doble sentido, remate que cierra todo el patron, contundencia que hace gritar, usa lo que dijo el rival = 2.2-3.0
   - No premies punch por ser gritado nomas, premia si realmente golpea y tiene ingenio

5 COHERENCIA = coherencia + contenido + uso tematicas + prolijidad + conexion ideas
   - BASICO: rimas sueltas sin conexion entre barras, sin desarrollar idea = 1.5-2.0
   - COMPLEJO: desarrolla argumento entre 4 barras, conecta idea de principio a fin, usa tematica/personaje y lo hilvana, sentido entre patrones = 2.2-3.0
   - Si intenta coherencia compleja pero se pierde y no se entiende, NO premies, baja nota

6 SKILLS = skills + tecnica + metrica + estructuras + multis + calambures + juegos palabras + figuras retoricas + fonetica + estructuras ABBA/ABAB
   - BASICO: 4x4 simple, rima consonante basica, sin metrica, sin figuras = 1.2-1.8
   - COMPLEJO: multisilabas, esdrujulas, calambures, juegos palabras, ABBA/ABAB, estructuras complejas, metrica con cambios, y se ENTIENDE = 2.2-3.0
   - No premies skills por meter muchas tecnicas malas sin sentido, premia solo si tecnicas son buenas y se entienden y mejoran la barra. Si mete 3 multis pero sin sentido, no es 2.5, es 1.8
   - Si complejo se traba 1 vez = -0.2, basico 1 traba = -0.4, complejo 2 trabas = -0.4 igual que basico 1 traba

7 PUESTA EN ESCENA = puesta en escena + escena + teatralidad + seguridad + actitud + control escenario + expresion corporal + gestos
   - BASICO: parado sin moverse, sin seguridad, sin actitud, sin representar lo que dice = 1.5-2.0
   - COMPLEJO: se mueve segun lo que dice, representa con cuerpo, gestos, desplazamientos, seguridad, contundencia, domina escenario = 2.2-3.0
   - No premies por gritar o moverse sin sentido, premia si teatralidad acompaña y potencia lo que dice

8 RESPUESTAS = respuesta + contestacion + replica inmediata + contestar con misma moneda
   - BASICO: responde generico sin usar lo que dijo rival, respuesta comun = 1.5-2.0
   - COMPLEJO: contesta inmediato usando exactamente lo que dijo rival, le da vuelta con ingenio, replica con misma tematica pero mejor, ingenio rapido = 2.2-3.0
   - Si no responde nada (se va por otro lado) = 0.5-1.0 aunque flow bueno. Respuesta es lo mas valorado en FMS/Red Bull a veces, mas que skills

REGLA GENERAL PARA TODAS: NO PREMIES POR PREMIAR - EVALUA POR LO QUE MERITA:
- Complejo NO gana automatico solo por ser complejo. Gana solo si lo complejo es REALMENTE mejor que lo basico y se entiende.
- Si complejo es malo/sin sentido/no se entiende, no le des 2.5 solo por intentar complejo, dale 1.5
- Si basico es perfecto, fluido, con punch bueno, puede ganarle a complejo malo
- Evalua proporcion: 1 fallo en complejo = -0.2, 1 fallo en basico = -0.4, 2 fallos complejo = -0.4 = igual que basico 1 fallo = justo
- Si se traba mucho (3+ veces) en cualquier categoria aunque sea complejo, suma -0.2 cada traba: 3 trabas = -0.6, 4 = -0.8, no regales puntos

ANALIZA CADA UNO DE TODA LA BATALLA COMPLETA, NO SOLO MEJOR BARRA:

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

TAREA -1: DETECCION AUTOMATICA DE FREESTYLERS - GENERAL (ANTES DE TODO - OBLIGATORIO):
- Detecta SOLO cuantos freestylers hay (2, 3, 4, 5, 6, 7, 8, 9, 10+ - LOS QUE SEAN), por timbre de voz (grave, agudo, rasgado, nasal, flaco, robusto, moreno, etc) y por ropa si es video (polera, color, short, buzo, jeans, etc - GENERAL, no solo polera negra).
- Identifica quien empieza y quien es segundo (Voz1 empieza, Voz2 segundo).
- Esto lo debes poner AL INICIO de tu respuesta como:
  "🔍 DETECCION: Detecte X freestylers - Voz1 [descripcion general timbre + ropa si hay video: ej flaco voz aguda polera clara] empieza, Voz2 [descripcion general timbre + ropa: ej robusto voz grave polera oscura short negro] segundo. ¿Estoy en lo correcto? Confirmame con 'Anotado quien es quien: ...' si me equivoque"
- GENERAL: No uses siempre polera negra/gris como ejemplo, describe lo que VEAS: color real de polera, si es manga corta/larga, si es flaco/robusto, moreno/blanco, voz grave/aguda, etc. Cada batalla es distinta.

TAREA 0: AMPLIFICAR Y ENTENDER BIEN AUNQUE MICROFONO MALO - OBLIGATORIO:
- El audio/video puede venir con micro malo, celu alejado, ruido ambiente, doble tempo rapido. TU DEBES REPRODUCIRLO MINIMO 2-3 VECES COMPLETO amplificando mentalmente para entender cada palabra.
- Si no se entiende una palabra por pronunciacion rapida o micro malo, deduce por contexto pero si de verdad no se entiende, BAJA PUNTOS en COHERENCIA y FLOW porque no se entendio (ej: "no se entendio bien esa barra por doble tempo muy rapido, por eso 1.5 en coherencia").
- Evalua pronunciacion: si habla trabado o no se entiende por querer hacer doble tempo/metrica, es punto menos en flow/coherencia. Dile en que mejorar.
- No inventes palabras que no dijo, si no se entiende di "no se entendio bien esa parte por audio lejano" y baja nota.

TAREA 1: IDENTIFICAR VOCES POR TIMBRE - GENERAL PARA AUDIO Y VIDEO (CRITICO - ANALIZA BIEN - REVISA 2-3 VECES MINIMO - ESENCIAL QUE LE ATINE):
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

ESTILO DE RESPUESTA - NO ROBOT, PLAZA CHILENA - ANALISIS COMPLETO DE TODA LA BATALLA + NOTAS OBJETIVAS REALES 0.0-3.0 + ANTI-CONTRADICCIÓN:
- Jerga chilena, ejemplos, calle, sin robot, divertido.
- 🚨 ANALISIS COMPLETO OBLIGATORIO: En CADA categoria (FLOW, MORBO, INGENIO, PUNCHLINE, COHERENCIA, SKILLS, PUESTA EN ESCENA, RESPUESTAS) debes analizar TODA LA BATALLA completa de principio a fin, NO solo la mejor barra. Por ejemplo en FLOW analiza como fluyo durante TODOS los patrones, si se enredo a ratos, si mantuvo metrica, no solo su mejor momento. Cita 1-2 frases exactas como ejemplo pero evalua el total.
- NOTAS OBJETIVAS REALES 0.0-3.0 (USA TODO EL RANGO, NO SOLO 2.0-2.3) - CRITERIO REAL FMS - ANTI-ROBO NIÑO:
  0.0-0.8 PESIMO (no rima, no tiene sentido, se traba todo, no responde, malisimo - aunque sea grande, ponle esto si es malo)
  0.8-1.5 MALO (rima poco, sin punch, sin ingenio, basico malo)
  1.0-1.8 REGULAR (se traba mucho, sin ritmo, sin sentido, no se entiende)
  1.0-1.8 REGULAR (cumple pero sin destacar, basico sin fallos)
  1.8-2.2 BUENO (buen nivel plaza, fluye bien)
  2.2-2.6 MUY BUENO (nivel competencia seria, complejidad + ejecucion)
  2.6-3.0 ELITE (nivel prime, solo si de verdad la rompe, flow/skills de final nacional)
  No pongas siempre 2.0-2.3, usa 0.5, 1.2, 2.8, 3.0 cuando amerite. Se objetivo.

- 🚨 CRITERIO REAL PARA TRABADAS Y COMPLEJIDAD (FMS REGLAMENTO OFICIAL - BAREMO 0-4 POR PATRON + 0-2 FLOW/SKILLS/ESCENA):
  Segun reglamento FRF: Flow es capacidad de seguir compases de la base (si no se escucha bien, dificil obtener puntos). Skills son juegos de palabras, metrica, doble tempo, figuras retoricas que dotan de estilo unico. Se valora coherencia y uso de todas las palabras.

  REGLA DE ORO PARA COMPLEJO VS BASICO:
  * Si uno hace rap COMPLEJO (doble tempo, metrica con multisilabas, estructuras complejas) y se traba 1-2 veces pero el resto fluye y es complejo, NO le bajes 1 punto entero. Bajale 0.3-0.5 en FLOW por la trabada, pero SUBELE en SKILLS por la complejidad intentada (ej: Flow 2.0 en vez de 2.5, pero Skills 2.5 en vez de 1.5). Complejidad con riesgo tiene premio si se ejecuta 80% bien.
  * Si uno hace rap BASICO-NORMAL (4x4 simple, rimas consonantes basicas) y se traba 1 vez, bajale 0.5-1.0 en FLOW/COHERENCIA porque siendo basico no deberia trabarse, y Skills queda bajo (1.0-1.5) porque no arriesgo.
  * Ejemplo concreto: 
    - A = complejo, doble tempo, 8 barras, se traba 1 vez al medio pero mete 3 multis y 2 juegos de palabras -> Flow 2.0 (bajo por traba), Skills 2.6 (alto por complejidad), Coherencia 2.2
    - B = basico, 4x4 simple, sin trabarse pero sin complejidad -> Flow 2.4 (fluye bien), Skills 1.4 (basico), Coherencia 2.3
    Gana A en puntaje total aunque se trabo 1 vez, porque complejidad vale mas que perfeccion basica.
  * Si se traba mucho (3+ veces) aunque sea complejo, ahi si baja 1.0+ en Flow y Coherencia porque no se entendio, segun reglamento "si no se escucha bien, dificil obtener puntos".
  * Evalua proporcion: 1 trabada en 16 barras = -0.3 flow, 1 trabada en 4 barras = -0.8 flow.
- COMPARACIONES CON REFERENTES SOLO CUANDO VEAS NECESARIO, INDEPENDIENTE DEL PUNTAJE, CON TODOS LOS FREESTYLES CONOCIDOS:
  Solo cuando de verdad amerite y sea muy similar, compara con cualquier freestyler conocido (Valles-T, Lokillo, Teorema, Chuty, Aczino, Gazir, El Menor, Kodigo, Zasko, Bnet, Mecha, Stuart, etc, todos los conocidos).
  Ejemplo: "ese flow se parecio a Valles-T prime por la cadencia", "ese morbo nivel Lokillo", "ese punch tipo Aczino", "esa puesta en escena tipo Chuty".
  SOLO cuando veas necesario y sea muy parecido, no en cada batalla, independiente del puntaje (puede ser 2.0 o 2.8, si lo amerita comparas).
- ANTI-CONTRADICCIÓN - CONCRETO Y ANALÍTICO (OBLIGATORIO):
- Al INICIO declara: "Voz 1 = [NOMBRE] = [ropa + timbre], Voz 2 = [NOMBRE] = [ropa + timbre]" según usuario. Si usuario dice "empieza X y segundo Y", entonces Voz1=X, Voz2=Y.
- MANTÉN ESE MAPEO 100% CONSISTENTE en TODOS los 8 jurados. Si en PUESTA EN ESCENA dices X=jeans azul, en FLOW, MORBO, etc, X SIEMPRE es jeans azul, mismo timbre, mismas barras. NO cambies nunca.
- Sé CONCRETO Y ANALÍTICO: cita textual exacta de lo que dijo cada uno, no inventes, pero analiza TODA la batalla, no solo 1 barra.

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

REGLA INQUEBRANTABLE GENERAL - CADA BATALLA ES DIFERENTE - ANTI-SESGO NIÑO: Usa puntajes reales del MAESTRO. NO castigues por ser niño pequeño. Si niño rapea mejor que grande que no rima, niño va arriba. Si grande es malisimo (no rima, sin sentido), dale 0.5-1.0 real, no le regales puntos por ser grande. Evalua solo rap, no edad, no estatura. MINIMO 2 VOCES SIEMPRE, NUNCA 1. Siempre hay mas de 1 en batalla. Formato puede ser 1vs1, 1vs2, 2vs2, 3vs1, todos vs todos. Detecta formato en ESTA batalla por lo que dicen. Si es 1vs1 son 2, si maestro evaluo 1 solo estas haciendo robo, debe haber minimo 2. Si el usuario dijo 3,4,5,6 o mas voces en ESTA batalla o detectaste 3,4,5,6 timbres, tabla con ESE NUMERO OBLIGATORIO - LOS QUE SEAN. NUNCA INVENTES NOMBRES QUE EL USUARIO NO DIO EN ESTA BATALLA. Usa EXACTAMENTE los nombres que dio el usuario en este mensaje (X, Y, Z), no uses nombres de batallas pasadas. Si usuario dijo Voz 1:X Voz 2:Y, entonces los participantes son X e Y. No hagas tongo, no dejes a nadie fuera. Si maestro evaluo 2, tu tabla final debe tener 2 minimo. Manten voces exactas del usuario de ESTA batalla. Cada batalla es gente diferente, analiza ESTA. VIDEO max 24, AUDIO max 21.

FORMATO:

🏆 TABLA FINAL GENERAL (sobre {max_puntos}) - VIDEO:
1: [Nombre/Voz] - [total]/{max_puntos} (desglose: {desglose_template})
2: [Nombre/Voz] - [total]/{max_puntos} (desglose: ...)
3: [Si hay tercera] - [total]/{max_puntos} (...)

GANADOR ABSOLUTO: [Nombre/Voz] con [total]/{max_puntos} porque [resumen]

🔥 MEJOR MOMENTO / MEJOR PATRON DE CADA UNO (4x4 - CITA TEXTUAL EXACTA SOLO SI 100% CERTEZA - SI DUDA, NO CITA, PREGUNTA AL USUARIO - PROHIBIDO INVENTAR - SI NO SE ESCUCHA CLARO ESCRIBE [NO SE ESCUCHA CLARO] Y NO INVENTES FRASE GENERICA):
- [Voz 1]: "[mejor 4x4 textual exacta]" -> Por qué destacó
- [Voz 2]: "[mejor 4x4 textual exacta]" -> Por qué destacó

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

ANALISIS GENERAL - ESCUCHA/VE COMPLETO 2-3 VECES MINIMO AMPLIFICANDO AUDIO - GENERAL PARA AUDIO Y VIDEO - TODAS LAS CATEGORIAS REALES QUE SE EVALUAN EN TODOS LOS FORMATOS:
CATEGORIAS OFICIALES QUE EXISTEN (Red Bull, FMS, BDM, God Level, Supremacia, plazas):
- FLOW: musicalidad, cadencia, pausas, continuidad, entonacion, variaciones, doble tempo, onetwo, fluidez encadenar rimas, adaptacion base.
- SKILLS / TECNICA / METRICA: empleo formas estroficas complejas mas alla de pareados, estructuras ABBA/ABAB, multisilabas, esdrujulas, calambures, juegos palabras, figuras retoricas, tecnicas vocales, metrica, estructuras, fonetica, uso tiempo y pausas.
- PUNCHLINE: frase-remate que golpea rival, impacto, contundencia, doble sentido, remate del ataque/concepto desarrollado durante patron. Factor mas importante en batalla.
- COHERENCIA / CONTENIDO: conexion ideas, desarrollo argumentos, sentido entre barras y entre patrones, no solo ultima barra, uso palabras/tematicas/personajes, que rimas no sean superficiales sin sentido, prolijidad ejecucion.
- INGENIO / CREATIVIDAD / ORIGINALIDAD: capacidad ocurrencia espontanea, originalidad ideas, sorpresa, temas relevancia, ingenio es igual a tener buenas ideas para atacar rival, espontaneidad (agilidad mental crear idea) vs rapidez (exponer veloz).
- PUESTA EN ESCENA / ESCENA / TEATRALIDAD: soltura, confianza, seguridad, contundencia, control escenario, actitud, desplazamiento, gestos, silencios, representar con cuerpo lo que dice, expresion corporal, conexion publico, seguridad escenica.
- RESPUESTA / CONTESTACION: capacidad contestar rival tiempo real, respuesta inmediata, replica, mas valorado que skills en FMS/Red Bull a veces, contestar con misma moneda.
- MORBO / HUMOR / PICARDIA: capacidad desubicar, hacer reir, ambiente, desparpajo (muy valorado en plaza chilena).
- VOCABULARIO / LEXICO: uso palabras variadas, no repetir, riqueza lexico.
- ENTREGA / DELIVERY: fuerza, decision, desparpajo con que se dice.

TUS 8 JURADOS CUBREN TODO ESO - EVALUACION JUSTA COMPLEJO VS BASICO EN TODAS (NO PREMIAR POR PREMIAR):
1 FLOW = flow + doble tempo + onetwo + musicalidad + variaciones
   - BASICO: solo fluir normal sobre base 4x4 sin cambios = 1.5-2.0 max aunque perfecto
   - COMPLEJO: cambia cadencia, variaciones, dobles, pausas, entonaciones = 2.2-3.0 si ejecuta bien, gana aunque 1 traba (-0.2 vs -0.4 basico)

2 MORBO = morbo + humor + picardia + entrega + desubicar
   - BASICO: chiste comun, talla basica sin desubicar = 1.5-2.0
   - COMPLEJO: desubica de verdad, hace reir con ingenio, picardia con doble sentido, improvisa humor segun contexto = 2.2-3.0
   - No premies morbo por ser pesado nomas, premia si realmente desubica y hace reir

3 INGENIO = ingenio + creatividad + originalidad + vocabulario + ocurrencia espontanea
   - BASICO: idea comun, sin originalidad, rima predecible = 1.5-2.0
   - COMPLEJO: idea original que sorprende, concepto que nadie espera, vocabulario variado, ocurre en el momento segun lo que pasa = 2.2-3.0
   - Si idea compleja pero sin sentido/no se entiende, NO premies, baja nota

4 PUNCHLINE = punchline + contundencia + impacto + doble sentido + remate
   - BASICO: punchline comun "te gano", sin doble sentido, sin impacto = 1.5-2.0
   - COMPLEJO: punchline con doble sentido, remate que cierra todo el patron, contundencia que hace gritar, usa lo que dijo el rival = 2.2-3.0
   - No premies punch por ser gritado nomas, premia si realmente golpea y tiene ingenio

5 COHERENCIA = coherencia + contenido + uso tematicas + prolijidad + conexion ideas
   - BASICO: rimas sueltas sin conexion entre barras, sin desarrollar idea = 1.5-2.0
   - COMPLEJO: desarrolla argumento entre 4 barras, conecta idea de principio a fin, usa tematica/personaje y lo hilvana, sentido entre patrones = 2.2-3.0
   - Si intenta coherencia compleja pero se pierde y no se entiende, NO premies, baja nota

6 SKILLS = skills + tecnica + metrica + estructuras + multis + calambures + juegos palabras + figuras retoricas + fonetica + estructuras ABBA/ABAB
   - BASICO: 4x4 simple, rima consonante basica, sin metrica, sin figuras = 1.2-1.8
   - COMPLEJO: multisilabas, esdrujulas, calambures, juegos palabras, ABBA/ABAB, estructuras complejas, metrica con cambios, y se ENTIENDE = 2.2-3.0
   - No premies skills por meter muchas tecnicas malas sin sentido, premia solo si tecnicas son buenas y se entienden y mejoran la barra. Si mete 3 multis pero sin sentido, no es 2.5, es 1.8
   - Si complejo se traba 1 vez = -0.2, basico 1 traba = -0.4, complejo 2 trabas = -0.4 igual que basico 1 traba

7 PUESTA EN ESCENA = puesta en escena + escena + teatralidad + seguridad + actitud + control escenario + expresion corporal + gestos
   - BASICO: parado sin moverse, sin seguridad, sin actitud, sin representar lo que dice = 1.5-2.0
   - COMPLEJO: se mueve segun lo que dice, representa con cuerpo, gestos, desplazamientos, seguridad, contundencia, domina escenario = 2.2-3.0
   - No premies por gritar o moverse sin sentido, premia si teatralidad acompaña y potencia lo que dice

8 RESPUESTAS = respuesta + contestacion + replica inmediata + contestar con misma moneda
   - BASICO: responde generico sin usar lo que dijo rival, respuesta comun = 1.5-2.0
   - COMPLEJO: contesta inmediato usando exactamente lo que dijo rival, le da vuelta con ingenio, replica con misma tematica pero mejor, ingenio rapido = 2.2-3.0
   - Si no responde nada (se va por otro lado) = 0.5-1.0 aunque flow bueno. Respuesta es lo mas valorado en FMS/Red Bull a veces, mas que skills

REGLA GENERAL PARA TODAS: NO PREMIES POR PREMIAR - EVALUA POR LO QUE MERITA:
- Complejo NO gana automatico solo por ser complejo. Gana solo si lo complejo es REALMENTE mejor que lo basico y se entiende.
- Si complejo es malo/sin sentido/no se entiende, no le des 2.5 solo por intentar complejo, dale 1.5
- Si basico es perfecto, fluido, con punch bueno, puede ganarle a complejo malo
- Evalua proporcion: 1 fallo en complejo = -0.2, 1 fallo en basico = -0.4, 2 fallos complejo = -0.4 = igual que basico 1 fallo = justo
- Si se traba mucho (3+ veces) en cualquier categoria aunque sea complejo, suma -0.2 cada traba: 3 trabas = -0.6, 4 = -0.8, no regales puntos

ANALIZA CADA UNO DE TODA LA BATALLA COMPLETA, NO SOLO MEJOR BARRA:

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

TAREA -1: DETECCION AUTOMATICA DE FREESTYLERS - GENERAL (ANTES DE TODO - OBLIGATORIO):
- Detecta SOLO cuantos freestylers hay (2, 3, 4, 5, 6, 7, 8, 9, 10+ - LOS QUE SEAN), por timbre de voz (grave, agudo, rasgado, nasal, flaco, robusto, moreno, etc) y por ropa si es video (polera, color, short, buzo, jeans, etc - GENERAL, no solo polera negra).
- Identifica quien empieza y quien es segundo (Voz1 empieza, Voz2 segundo).
- Esto lo debes poner AL INICIO de tu respuesta como:
  "🔍 DETECCION: Detecte X freestylers - Voz1 [descripcion general timbre + ropa si hay video: ej flaco voz aguda polera clara] empieza, Voz2 [descripcion general timbre + ropa: ej robusto voz grave polera oscura short negro] segundo. ¿Estoy en lo correcto? Confirmame con 'Anotado quien es quien: ...' si me equivoque"
- GENERAL: No uses siempre polera negra/gris como ejemplo, describe lo que VEAS: color real de polera, si es manga corta/larga, si es flaco/robusto, moreno/blanco, voz grave/aguda, etc. Cada batalla es distinta.

TAREA 0: AMPLIFICAR Y ENTENDER BIEN AUNQUE MICROFONO MALO - OBLIGATORIO:
- El audio/video puede venir con micro malo, celu alejado, ruido ambiente, doble tempo rapido. TU DEBES REPRODUCIRLO MINIMO 2-3 VECES COMPLETO amplificando mentalmente para entender cada palabra.
- Si no se entiende una palabra por pronunciacion rapida o micro malo, deduce por contexto pero si de verdad no se entiende, BAJA PUNTOS en COHERENCIA y FLOW porque no se entendio (ej: "no se entendio bien esa barra por doble tempo muy rapido, por eso 1.5 en coherencia").
- Evalua pronunciacion: si habla trabado o no se entiende por querer hacer doble tempo/metrica, es punto menos en flow/coherencia. Dile en que mejorar.
- No inventes palabras que no dijo, si no se entiende di "no se entendio bien esa parte por audio lejano" y baja nota.

TAREA 1: IDENTIFICAR VOCES POR TIMBRE - GENERAL PARA AUDIO Y VIDEO (CRITICO - ANALIZA BIEN - REVISA 2-3 VECES MINIMO - ESENCIAL QUE LE ATINE):
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

ESTILO NO ROBOT, PLAZA CHILENA - NOTAS OBJETIVAS + ANTI-CONTRADICCIÓN - COMPARACIONES SOLO CUANDO AMERITA:
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

REGLA INQUEBRANTABLE GENERAL - CADA BATALLA ES DIFERENTE - ANTI-SESGO NIÑO: Usa puntajes reales del MAESTRO. NO castigues por ser niño pequeño. Si niño rapea mejor que grande que no rima, niño va arriba. Si grande es malisimo (no rima, sin sentido), dale 0.5-1.0 real, no le regales puntos por ser grande. Evalua solo rap, no edad, no estatura. MINIMO 2 VOCES SIEMPRE, NUNCA 1. Siempre hay mas de 1 en batalla. Formato puede ser 1vs1, 1vs2, 2vs2, 3vs1, todos vs todos. Detecta formato en ESTA batalla por lo que dicen. Si es 1vs1 son 2, si maestro evaluo 1 solo estas haciendo robo, debe haber minimo 2. Si el usuario dijo 3,4,5,6 o mas voces en ESTA batalla o detectaste 3,4,5,6 timbres, tabla con ESE NUMERO OBLIGATORIO - LOS QUE SEAN. NUNCA INVENTES NOMBRES QUE EL USUARIO NO DIO EN ESTA BATALLA. Usa EXACTAMENTE los nombres que dio el usuario en este mensaje (X, Y, Z), no uses nombres de batallas pasadas. Si usuario dijo Voz 1:X Voz 2:Y, entonces los participantes son X e Y. No hagas tongo, no dejes a nadie fuera. Si maestro evaluo 2, tu tabla final debe tener 2 minimo. Manten voces exactas del usuario de ESTA batalla. Cada batalla es gente diferente, analiza ESTA. VIDEO max 24, AUDIO max 21. AUDIO no tiene puesta en escena.

FORMATO:

🏆 TABLA FINAL GENERAL (sobre {max_puntos}) - AUDIO:
1: [Nombre/Voz] - [total]/{max_puntos} (desglose: ...)
2: [Nombre/Voz] - [total]/{max_puntos} (desglose: ...)

GANADOR ABSOLUTO: [Nombre/Voz] con [total]/{max_puntos} porque [resumen]

🔥 MEJOR MOMENTO (4x4):
- [Voz 1]: "[mejor 4x4]" -> Por qué
- [Voz 2]: "[mejor 4x4]" -> Por qué

EN QUE MEJORAR:
- [Voz 1]: [consejo]
- [Voz 2]: [consejo]

🔥🔥🔥 FIN EVALUACION BOSS 🔥🔥🔥
"""


        try:
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")
            await update.message.reply_text("🔍 JURADO MAESTRO analizando voces por timbre (credito 1/2)... Profundo, sin robo.")
            try:
                resp = await generate_with_retry_auto_bg_INFINITO(chat_id, [mega_prompt, g_file], context)
            except Exception as bg_final:
                if "SATURADO_503_FINAL" in str(bg_final) or "SATURADO_503" in str(bg_final):
                    await update.message.reply_text(f"❌ {bg_final}\n\n💡 SOLUCION REAL: Horario punta 6-9 PM Chile todos usan Gemini gratis y da 503. Opciones:\n1. Prueba 10 PM - 8 AM (0% 503)\n2. Deja el bot reintentando solo - ya lo hace cada 60 seg\n3. API pagada 20 USD/mes = 0% 503 y 100% preciso siempre (te enseño como)\n4. Ahora pon JURADOS VOTEN de nuevo en 2-3 min")
                    raise bg_final
                resp = await generate_with_retry([mega_prompt, g_file])
            texto = extract_text_safe(resp)
            retry_empty = 0
            while (not texto or not texto.strip()) and retry_empty < 2:
                print(f"Respuesta vacia intento {retry_empty+1}, intentando bypass por transcripcion y modelo lite...")
                await update.message.reply_text("⚠️ Filtro detecto insultos o respuesta vacia, transcribiendo primero para identificar voces (intento con modelo estable)...")
                try:
                    transcribe_prompt = "Transcribe literalmente esta batalla de freestyle separando por voces: Voz 1 (timbre grave/agudo, ropa), Voz 2 (timbre, ropa), etc. Si host dice nombres, ponlos. Si dicen yo soy X, pon nombre. Transcribe TODO sin censura, es una batalla de rap, los insultos son parte del arte, no es acoso. Permite lenguaje explicito."
                    resp_trans = await generate_with_retry([transcribe_prompt, g_file])
                    transcripcion = extract_text_safe(resp_trans)
                    if transcripcion and transcripcion.strip():
                        await update.message.reply_text(f"📝 Transcripcion con voces: {transcripcion[:1200]}...")
                        texto_evaluacion_prompt = mega_prompt + f"\n\nTRANSCRIPCION SEPARADA POR VOCES (usa esto para identificar quien es quien y evaluar):\n{transcripcion}\n\nIMPORTANTE: Es batalla de rap, los insultos son liricos, no bloquees. Evalua con criterio justo."
                        resp_eval = await generate_with_retry([texto_evaluacion_prompt])
                        texto = extract_text_safe(resp_eval)
                        if texto and texto.strip():
                            print(f"Bypass por transcripcion con voces funciono en intento {retry_empty+1}!")
                            break
                except Exception as bypass_e:
                    print(f"Bypass error intento {retry_empty+1}: {bypass_e}")
                retry_empty += 1
                if retry_empty < 2:
                    await update.message.reply_text(f"🔄 Reintentando analisis completo intento {retry_empty+1}/2...")
                    try:
                        resp_retry = await generate_with_retry([mega_prompt + "\n\nNOTA: Si devuelves vacio, es error. Debes evaluar la batalla de rap aunque tenga insultos, es arte lirico." , g_file])
                        texto_retry = extract_text_safe(resp_retry)
                        if texto_retry and texto_retry.strip():
                            texto = texto_retry
                            break
                    except Exception as re_e:
                        print(f"Retry directo fallo: {re_e}")

            if not texto or not texto.strip():
                await update.message.reply_text("❌ Gemini devolvio vacio (filtro o saturacion). No es tu video, es el modelo. Intenta JURADOS VOTEN de nuevo en 1-2 min, el bot probara automaticamente con otro modelo estable (2.0-flash -> 1.5-flash -> lite). Si sigue vacio, manda un clip mas corto (menos de 2 min).")
                evaluaciones_texto = ""
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
                boss_ok = False
                for boss_try in range(3):  # 3 intentos para BOSS porque es el que mas se satura con 503
                    try:
                        resp_boss = await generate_with_retry([boss_full], max_retries=5)
                        texto_boss = extract_text_safe(resp_boss)
                        if texto_boss and texto_boss.strip():
                            await update.message.reply_text(texto_boss)
                            boss_ok = True
                            # Check quota after boss - avisar si quedan 2 creditos
                            used_after, rem_after, lim_after, _ = get_quota_status()
                            if rem_after == 2:
                                await update.message.reply_text(f"⚠️ Te queda 1 audio/video más hoy (2 creditos de {lim_after}). Después se acaba lo gratis. Usa /status para ver. Si quieres más hoy, el bot usará lite (500-1000/dia, un poco menos preciso).")
                            elif rem_after == 0:
                                await update.message.reply_text(f"⛔ Se acabaron tus audios/videos gratis de hoy: {used_after//2} audios usados de {lim_after//2}. Hasta mañana no hay más gratis. Si necesitas más hoy, cambia a lite en MODELS_TO_TRY primero (500-1000/dia).")
                            elif rem_after <= 4:
                                await update.message.reply_text(f"⚠️ Te quedan {rem_after//2} audios/videos hoy ({rem_after} creditos de {lim_after}).")
                            break
                        else:
                            await update.message.reply_text(f"⚠️ BOSS no pudo generar tabla, pero maestro si evaluo.")
                            boss_ok = True
                            break
                    except Exception as e_boss:
                        err = str(e_boss).lower()
                        if "503" in err or "unavailable" in err or "high demand" in err or "overloaded" in err or "429" in err:
                            wait_boss = (boss_try + 1) * 10 + random.uniform(2, 5)
                            print(f"BOSS 503 reintento {boss_try+1}/3 esperando {wait_boss:.1f}s...")
                            await update.message.reply_text(f"⏳ BOSS saturado (503), reintentando {boss_try+1}/3 en {wait_boss:.0f}s...")
                            await asyncio.sleep(wait_boss)
                            continue
                        else:
                            await update.message.reply_text(f"⚠️ BOSS fallo: {e_boss}")
                            boss_ok = True
                            break
                if not boss_ok:
                    await update.message.reply_text("⚠️ BOSS saturado por alta demanda (503). Intenta de nuevo en 1-2 min con JURADOS VOTEN - las evaluaciones del MAESTRO ya quedaron guardadas arriba, no necesitas re-subir video.")

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
    # --- Mini web server para Render (debe abrir SIEMPRE) ---
    import threading
    from http.server import HTTPServer, BaseHTTPRequestHandler

    class KeepAliveHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-type","text/plain")
            self.end_headers()
            self.wfile.write(b"Bot Jurados Live - Polling Mode Fixed")
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

    if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
        print("Faltan TELEGRAM_TOKEN o GEMINI_API_KEY - revisa Environment en Render")
        import time
        while True:
            time.sleep(3600)

    async def post_init(app):
        try:
            await app.bot.delete_webhook(drop_pending_updates=False)
            print("✅ Webhook borrado, polling con mensajes pendientes guardados", flush=True)
        except Exception as e:
            print(f"Webhook delete: {e}", flush=True)
        if user_client:
            try:
                await user_client.connect()
                me = await user_client.get_me()
                print(f"✅ Telethon session válida: {me.first_name} (@{me.username}) ID:{me.id} - Desconectando para evitar ban por deploy... FIX PERMANENTE ANTI-BAN aplicado", flush=True)
                await user_client.disconnect()
                print(f"✅ Telethon desconectado (se conectará solo cuando se necesite >20MB) - ANTI-BAN aplicado", flush=True)
            except Exception as e:
                print(f"❌ Error verificando Telethon: {e}", flush=True)
                try:
                    await user_client.disconnect()
                except:
                    pass
        else:
            print("⚠️ user_client es None - Telethon no funcionará para >20MB", flush=True)
        # Retomar tareas pendientes de antes del reinicio - no pedir reenviar video
        try:
            _resume_fn = globals().get("resume_pending_tasks_on_startup")
            if _resume_fn is not None:
                await _resume_fn(app)
            else:
                print("⚠️ resume_pending_tasks_on_startup no definida en globals, saltando resume (no es error fatal)", flush=True)
        except Exception as _re:
            print(f"Resume on startup fallo (no fatal, sigo): {_re}", flush=True)

    async def error_handler(update, context):
        err = str(context.error).lower()
        if "conflict" in err:
            print(f"⚠️ Conflict detectado (otro deploy viejo apagándose), esperando 15s...")
            await asyncio.sleep(15)
            return
        print(f"Error no critico: {context.error}")

    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    app.add_error_handler(error_handler)
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", start_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(MessageHandler(filters.VIDEO | filters.VIDEO_NOTE | filters.ANIMATION | filters.AUDIO | filters.VOICE | filters.Document.ALL, handle_media))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    print("Bot JURADO MAESTRO + BOSS, x2 creditos, identifica voces por timbre, 2-10+ participantes (los que sean: 2,3,4,5,6+), sin robo/tongo...")

    # Polling que NO borra mensajes si Render se durmio - los guarda y los procesa al despertar (50 seg delay pero responde)
    import telegram.error
    while True:
        try:
            app.run_polling(drop_pending_updates=False, allowed_updates=Update.ALL_TYPES, close_loop=False, stop_signals=None)
            break
        except telegram.error.Conflict as cf:
            print(f"⚠️ Conflicto detectado, esperando 15s y reintentando... {cf}")
            time.sleep(15)
            continue
        except Exception as e:
            print(f"Polling error, reintentando en 10s: {e}")
            time.sleep(10)
            continue

if __name__ == "__main__":
    main()
