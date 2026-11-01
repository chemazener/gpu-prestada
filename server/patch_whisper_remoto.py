#!/usr/bin/env python3
"""Whisper (CT 101): si la 3080 no da turno enseguida, transcribir en un PC de GPU Prestada (31-oct-2026).
El audio se pasa a WAV 16 kHz mono (whisper.cpp) y va por el proxy de Ollama (/_nodes/svc/whisper/…).
/transcribe: 5 s de espera por la 3080; si no, PC prestado; si tampoco, la espera de siempre.
/lyrics: sin GPU, PC prestado (large-v3-turbo) antes que la CPU.
Uso: python3 patch_whisper_remoto.py /opt/whisper-service/server.py"""
import sys

p = sys.argv[1]
s = open(p, encoding="utf-8").read()
if "_remote_whisper" in s:
    sys.exit("ya parcheado")


def rep(a, b):
    global s
    assert s.count(a) == 1, a[:80]
    s = s.replace(a, b)


rep('''def get_model():''', '''# ---- GPU Prestada: transcribir en un PC prestado cuando la 3080 está ocupada (31-oct-2026) ----
import subprocess
import httpx
NODES_SVC = os.environ.get('NODES_SVC', 'http://127.0.0.1:11434/_nodes/svc/whisper')
_LANGS = {'spanish': 'es', 'english': 'en', 'french': 'fr', 'german': 'de', 'italian': 'it', 'portuguese': 'pt',
          'catalan': 'ca', 'galician': 'gl', 'basque': 'eu', 'russian': 'ru', 'chinese': 'zh', 'japanese': 'ja',
          'arabic': 'ar', 'dutch': 'nl', 'polish': 'pl', 'romanian': 'ro', 'ukrainian': 'uk', 'korean': 'ko'}


def _remote_whisper(model, path, language=''):
    """Transcribe en un PC prestado. Devuelve dict (segments, language, prob, duration, node) o None."""
    wav = path + '.16k.wav'
    try:
        subprocess.run(['ffmpeg', '-nostdin', '-loglevel', 'error', '-y', '-i', path, '-ar', '16000', '-ac', '1',
                        '-c:a', 'pcm_s16le', wav], check=True, timeout=300)
        with open(wav, 'rb') as f:
            r = httpx.post(f'{NODES_SVC}/{model}/inference', timeout=900,
                           files={'file': ('audio.wav', f, 'audio/wav')},
                           data={'response_format': 'verbose_json', 'language': language or 'auto', 'temperature': '0'})
        if r.status_code != 200:
            print(f'whisper remoto ({model}): HTTP {r.status_code} {r.text[:120]}', flush=True)
            return None
        d = r.json()
        lang = d.get('detected_language') or d.get('language') or ''
        lang = _LANGS.get(str(lang).lower(), str(lang).lower()[:5])
        segs = [{'start': round(float(x.get('start', 0)), 2), 'end': round(float(x.get('end', 0)), 2),
                 'text': str(x.get('text', '')).strip()} for x in d.get('segments') or []]
        node = r.headers.get('x-served-by', '?')
        print(f'whisper remoto ({model}) en {node}: {len(segs)} segmentos, idioma {lang}', flush=True)
        return {'segments': segs, 'language': lang, 'node': node,
                'prob': float(d.get('detected_language_probability') or 1.0), 'duration': float(d.get('duration') or 0)}
    except Exception as e:
        print(f'whisper remoto ({model}) falló: {e}', flush=True)
        return None
    finally:
        try:
            os.unlink(wav)
        except Exception:
            pass


def get_model():''')

rep('''        tok = _lease(1 if _model is not None else 1500)
        try:''', '''        # 3080 primero, pero esperando poco: si está ocupada (p. ej. un vídeo), a un PC prestado
        tok, quick = None, False
        try:
            tok, quick = _b_acquire("whisper", need_mb=1 if _model is not None else 1500, timeout_s=5).get("token"), True
        except (urllib.error.URLError, OSError, ValueError) as e:
            if not (isinstance(e, urllib.error.HTTPError) and e.code == 503):
                quick = True   # broker caído: como siempre (fail-open en _lease)
        if not quick:
            rem = await asyncio.to_thread(_remote_whisper, 'small', tmp.name)
            if rem is not None:
                segments = [x for x in rem['segments'] if x['text']]
                lang = rem['language']
                translated = translate_segments(segments, lang) if lang != 'es' and segments else None
                return JSONResponse({'status': 'done', 'language': lang, 'language_probability': round(rem['prob'], 2),
                                     'segments': segments, 'translated': translated,
                                     'duration_seconds': round(time.time() - t0, 1), 'served_by': rem['node']})
            tok = _lease(1 if _model is not None else 1500)
        elif tok is None:
            tok = _lease(1 if _model is not None else 1500)
        try:''')

rep('''        try:
            with _model_lock:
                if tok and _lyr_model is None:''', '''        if not tok:   # sin la 3080: antes que la CPU (lenta), un PC prestado con large-v3-turbo
            rem = await asyncio.to_thread(_remote_whisper, 'turbo', tmp.name, language)
            if rem is not None:
                dur = rem['duration'] or (rem['segments'][-1]['end'] if rem['segments'] else 0)
                segs = []
                for x in rem['segments']:
                    t = x['text']
                    if not t or any(a in t.lower() for a in _ALUCINA):
                        continue
                    if dur and x['start'] >= dur - 0.5:
                        break
                    segs.append({'start': x['start'], 'end': round(min(x['end'], dur), 2) if dur else x['end'], 'text': t})
                return JSONResponse({'status': 'done', 'language': rem['language'], 'duration': round(dur, 2),
                                     'segments': segs, 'seconds': round(time.time() - t0, 1), 'device': 'prestada:' + rem['node']})
        try:
            with _model_lock:
                if tok and _lyr_model is None:''')
if "import asyncio" not in s:
    s = s.replace("import threading\n", "import threading\nimport asyncio\n", 1)
open(p, "w", encoding="utf-8").write(s)
print("parcheado")
