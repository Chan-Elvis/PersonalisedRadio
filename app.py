from flask import Flask, render_template, request, redirect, flash, jsonify
import sqlite3
import json
import subprocess
import os
import requests
import google.generativeai as genai
from dotenv import load_dotenv
import random
import datetime
from similar_songs import get_similar_tracks
from FetchNews import fetch_similar_articles
from db_utils import get_liked_articles, get_disliked_articles, get_liked_songs, get_disliked_songs, mark_song_similar_fetched, mark_article_similar_fetched

import threading
import time
import json
from queue import Queue

from similar_songs import get_similar_tracks
from FetchNews import fetch_similar_articles
from db_utils import store_song

from FetchNews import fetch_full_article

from gtts import gTTS
import uuid        
import glob
import re

import pyttsx3
import subprocess


tts_lock = threading.Lock()

load_dotenv()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
genai.configure(api_key=GOOGLE_API_KEY)

SECRET_KEY = os.getenv("SECRET_KEY")
LASTFM_API_KEY = os.getenv("LASTFM_API_KEY")

app = Flask(__name__)
app.secret_key = SECRET_KEY

model = genai.GenerativeModel('gemini-2.0-flash')
model_longform = genai.GenerativeModel('gemini-2.0-flash-lite')    # for longer radio scripts

def configure_sqlite(max_retries=5, delay=1.0):
    for attempt in range(max_retries):
        try:
            conn = sqlite3.connect("user_profiles.db", timeout=5.0)
            c = conn.cursor()
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA busy_timeout=5000")
            conn.commit()
            conn.close()
            print("✅ SQLite configured successfully.")
            return
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower():
                print(f"⚠️ SQLite locked on attempt {attempt+1}/{max_retries}, retrying in {delay}s...")
                time.sleep(delay)
            else:
                raise
    raise RuntimeError("❌ Failed to configure SQLite after retries.")


configure_sqlite()


ALLOWED_GENRES = [
    'rock', 'pop', 'jazz', 'blues', 'hip-hop', 'rap', 'indie', 'electronic',
    'metal', 'country', 'classical', 'folk', 'punk', 'dance', 'house',
    'soul', 'reggae', 'k-pop', 'r&b'
]

ALLOWED_CATEGORIES = [
    'general', 'science', 'sports', 'business', 'health',
    'entertainment', 'tech', 'politics', 'food', 'travel'
]

STYLE_SAMPLES = {
    "positive": (
        "Welcome back to your happy place on the airwaves! Here, the tunes are good, the vibes are better, "
        "and we always find a silver lining in the news."
    ),
    "neutral": (
        "Here’s your midday news and music update. We bring you facts and insights"
        "to keep your day moving."
    ),
    "serious": (
        "This is your trusted news source. Our goal is to inform you with clarity and respect, "
        "no fluff, no filler, just the stories that matter."
    ),
    "funny": (
        "Alright folks, buckle up! We’ve got news with a side of chuckles, and music to keep your spirits high."
    )
}

STYLE_PRIMING_TEMPLATE = """
You are a radio host. Here's how you usually talk:

"{sample_style}"

Now, using the same tone and style, generate a new segment based on this:

{context_prompt}
"""

import shutil

def is_valid_wav(filepath):
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", filepath],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=2
        )
        duration = float(result.stdout.decode().strip())
        return duration > 0.5
    except Exception as e:
        print(f"🔍 ffprobe check failed: {e}")
        return False


def clear_audio_directory():
    audio_dir = os.path.join(app.root_path, "static", "audio")
    if os.path.exists(audio_dir):
        for filename in os.listdir(audio_dir):
            file_path = os.path.join(audio_dir, filename)
            try:
                if os.path.isfile(file_path):
                    os.remove(file_path)
            except Exception as e:
                print(f"⚠️ Failed to delete {file_path}: {e}")



def store_liked_segment(segment_text, mood):
    conn = sqlite3.connect('user_profiles.db')
    c = conn.cursor()
    c.execute("INSERT INTO tone_examples (mood, segment) VALUES (?, ?)", (mood, segment_text))
    conn.commit()
    conn.close()


def clean_and_validate_topic(topic):
    prompt = f"""
    Return only one of the following:
- The corrected spelling of the input word (no quotes).
- "REJECT" if the input word is inappropriate, offensive, or doesn't make sense.

Input: {topic}
Output:"""
    try:
        response = model.generate_content(prompt)
        if response and hasattr(response, 'text'):
            result = response.text.strip(' "\'.')
            if result.upper() == "REJECT":
                return None
            return result
    except Exception as e:
        flash(f"Error processing topic '{topic}': {e}", "error")
    return None



def check_spelling(word):
    prompt = f"Correct the spelling of the word '{word}'. If already correct, return as-is. No explanation."
    try:
        response = model.generate_content(prompt)
        if response and hasattr(response, 'text'):
            return response.text.strip(' "\'.')
    except Exception as e:
        flash(f"Error spellchecking '{word}': {e}", "error")
    return word

def is_safe_input(text):
    prompt = f"""
    Is the following input free of hate speech, slurs, profanity, or other offensive content? Reply only with 'yes' or 'no'.

    Input: {text}
    """
    try:
        response = model.generate_content(prompt)
        if response and hasattr(response, 'text'):
            return response.text.strip().lower().startswith("yes")
    except Exception as e:
        flash(f"Content safety check failed: {e}", "error")
    return False

def validate_artist_with_llm(artist_name):
    prompt = f"""
Return only one of the following:
- The corrected name of a real music artist or band (no quotes).
- "INVALID" if the input is not a valid artist.

Input: {artist_name}
Output:"""
    try:
        response = model.generate_content(prompt)
        if response and hasattr(response, 'text'):
            result = response.text.strip().strip('"\'. ')
            if result.upper() == "INVALID":
                return None
            return result
    except Exception as e:
        flash(f"Error validating artist '{artist_name}': {e}", "error")
    return None



def get_recommended_tracks(artists, genres):
    tracks = []
    for artist in artists:
        url = f"http://ws.audioscrobbler.com/2.0/?method=artist.gettoptracks&artist={artist}&api_key={LASTFM_API_KEY}&format=json"
        response = requests.get(url)
        if response.status_code == 200:
            top_tracks = response.json().get('toptracks', {}).get('track', [])[:3]
            for track in top_tracks:
                t = {'artist': artist, 'title': track['name'], 'url': track['url']}
                tracks.append(t)
                store_song(t, source=f"artist:{artist}")
    for genre in genres:
        url = f"http://ws.audioscrobbler.com/2.0/?method=tag.gettoptracks&tag={genre}&api_key={LASTFM_API_KEY}&format=json"
        response = requests.get(url)
        if response.status_code == 200:
            top_tracks = response.json().get('tracks', {}).get('track', [])[:3]
            for track in top_tracks:
                t = {'artist': track['artist']['name'], 'title': track['name'], 'url': track['url']}
                tracks.append(t)
                store_song(t, source=f"genre:{genre}")
    return tracks[:20]

def get_trending_tracks():
    url = f"http://ws.audioscrobbler.com/2.0/?method=chart.gettoptracks&api_key={LASTFM_API_KEY}&format=json"
    response = requests.get(url)
    tracks = []
    if response.status_code == 200:
        for t in response.json()['tracks']['track'][:5]:
            track = {'artist': t['artist']['name'], 'title': t['name'], 'url': t['url']}
            tracks.append(track)
            store_song(track, source="trending")
    return tracks

def get_throwback_tracks():
    throwback_tags = ['70s', '80s', '90s', 'classic rock', 'oldies']
    selected_tag = random.choice(throwback_tags)
    url = f"http://ws.audioscrobbler.com/2.0/?method=tag.gettoptracks&tag={selected_tag}&api_key={LASTFM_API_KEY}&format=json"
    response = requests.get(url)
    tracks = []
    if response.status_code == 200:
        data = response.json()
        for track in data.get('tracks', {}).get('track', [])[:5]:
            t = {
                'artist': track['artist']['name'],
                'title': track['name'],
                'url': track['url']
            }
            tracks.append(t)
            store_song(t, source=f"throwback:{selected_tag}")
    return {'tag': selected_tag, 'tracks': tracks}


def get_new_artist_recommendations(favorite_artists):
    recommendations = []
    seen_artists = set()

    for artist in favorite_artists:
        url = f"http://ws.audioscrobbler.com/2.0/?method=artist.getsimilar&artist={artist}&api_key={LASTFM_API_KEY}&format=json"
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            similar_artists = [a['name'] for a in data.get('similarartists', {}).get('artist', [])][:3]
            for similar_artist in similar_artists:
                if similar_artist not in seen_artists:
                    seen_artists.add(similar_artist)
                    track_url = f"http://ws.audioscrobbler.com/2.0/?method=artist.gettoptracks&artist={similar_artist}&api_key={LASTFM_API_KEY}&format=json"
                    track_resp = requests.get(track_url)
                    top_tracks = []
                    if track_resp.status_code == 200:
                        tracks = track_resp.json().get('toptracks', {}).get('track', [])[:3]
                        for track in tracks:
                            t = {'title': track['name'], 'url': track['url']}
                            top_tracks.append(t)
                            store_song({'artist': similar_artist, 'title': track['name'], 'url': track['url']}, source=f"similar_to:{artist}")
                    recommendations.append({'artist': similar_artist, 'tracks': top_tracks})
    return recommendations

def get_similar_songs_from_liked():
    liked_songs = get_liked_songs()  # list of (title, artist)
    all_similar = []
    for title, artist in liked_songs:
        similar = get_similar_tracks(title, artist)
        for track in similar:
            all_similar.append(track)
            store_song(track, source=f"similar_to:{artist}:{title}")  # ✅ Save to DB
    return all_similar

def get_unused_articles(limit=10, source_filter=None, exclude_sources=None):
    conn = sqlite3.connect("user_profiles.db", timeout=5.0)
    c = conn.cursor()

    query = "SELECT uuid, title, content, url, published_at, source FROM articles WHERE used IS NULL OR used = 0"
    params = []

    if source_filter:
        query += " AND source = ?"
        params.append(source_filter)
    elif exclude_sources:
        placeholders = ",".join("?" for _ in exclude_sources)
        query += f" AND source NOT IN ({placeholders})"
        params.extend(exclude_sources)

    query += " ORDER BY timestamp_fetched DESC LIMIT ?"
    params.append(limit)

    c.execute(query, params)
    rows = c.fetchall()
    conn.close()

    return [
        {
            "uuid": row[0],
            "title": row[1],
            "content": row[2],
            "url": row[3],
            "published_at": row[4],
            "source": row[5]
        }
        for row in rows
    ]


def get_unused_songs(limit=10):
    conn = sqlite3.connect("user_profiles.db", timeout=5.0)
    c = conn.cursor()
    c.execute("SELECT title, artist, url FROM songs WHERE used = 0 LIMIT ?", (limit,))
    rows = c.fetchall()
    conn.close()
    return [{'title': r[0], 'artist': r[1], 'url': r[2]} for r in rows]

def create_batches(items, batch_size):
    return [items[i:i + batch_size] for i in range(0, len(items), batch_size)]

import sqlite3

def get_style_examples(mood, limit=5, db_path="your_database.db"):
    """Fetch the most recent tone examples for a given mood."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT segment FROM tone_examples
        WHERE mood = ?
        ORDER BY created_at DESC
        LIMIT ?
    """, (mood, limit))

    results = [row[0] for row in cursor.fetchall()]
    conn.close()
    return results


def build_prompt(news_batch, music_batch, news_mood):
    # 1. Get the predefined style tone
    sample_style = STYLE_SAMPLES.get(news_mood, STYLE_SAMPLES["neutral"])

    # 2. Retrieve recent liked tone examples (limit to 5)
    examples = get_style_examples(news_mood, limit=5)

    def truncate(segment, max_chars=1000):
        return segment[:max_chars].strip() + ("…" if len(segment) > max_chars else "")

    examples_text = "\n\n".join(
        f"Example {i+1}:\n{truncate(ex)}" for i, ex in enumerate(examples)
    )

    # 3. Interleave: 2 songs, then 1 news — until both lists are exhausted
    interleaved_items = []
    song_idx = 0
    news_idx = 0
    while song_idx < len(music_batch) or news_idx < len(news_batch):
        # Add up to 2 songs
        for _ in range(2):
            if song_idx < len(music_batch):
                m = music_batch[song_idx]
                interleaved_items.append(f"- SONG: {m['artist']} — {m['title']}")
                song_idx += 1
        # Add 1 news
        if news_idx < len(news_batch):
            n = news_batch[news_idx]
            interleaved_items.append(f"- NEWS: {n['title']}: {n['content']}")
            news_idx += 1

    interleaved_text = "\n".join(interleaved_items)

    # 4. Prompt with interleaved flow
    context_prompt = f"""{examples_text}

You're hosting a ~3-minute radio segment that alternates between 2 songs and 1 news story.

Content:
{interleaved_text}

Make it flow naturally like a live host: include transitions, small talk, reactions, and rhythm.
"""

    # 5. Final prompt construction
    full_prompt = STYLE_PRIMING_TEMPLATE.format(
        sample_style=sample_style,
        context_prompt=context_prompt.strip()
    )

    return full_prompt






def generate_radio_script(news_batches, music_batches, news_mood):
    final_script = ""
    for i in range(len(news_batches)):
        news_batch = news_batches[i]
        music_batch = music_batches[i] if i < len(music_batches) else []
        prompt = build_prompt(news_batch, music_batch, news_mood)
        response = model_longform.generate_content(prompt)
        script_text = response.text.strip() if hasattr(response, 'text') else ''
        final_script += script_text + "\n\n"
    return final_script

def mark_articles_and_songs_used(news_batch, music_batch):
    conn = sqlite3.connect("user_profiles.db", timeout=5.0)
    c = conn.cursor()
    for article in news_batch:
        c.execute("UPDATE articles SET used = 1 WHERE uuid = ?", (article.get('uuid'),))
    for song in music_batch:
        c.execute("UPDATE songs SET used = 1 WHERE title = ? AND artist = ?", (song['title'], song['artist']))
    conn.commit()
    conn.close()


def init_db():
    conn = sqlite3.connect('user_profiles.db')
    c = conn.cursor()

    # Drop legacy tables if they exist
    c.execute("DROP TABLE IF EXISTS covered_articles")
    c.execute("DROP TABLE IF EXISTS played_songs")

    # Create profiles table
    c.execute("""
        CREATE TABLE IF NOT EXISTS profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topics TEXT, 
            music_tastes TEXT, 
            favorite_artists TEXT,
            news_mood TEXT, 
            location TEXT, 
            music_pref TEXT,
            station_name TEXT, 
            host_name TEXT,
            news_categories TEXT       
        )
    """)

    # Create articles table
    c.execute("""
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uuid TEXT UNIQUE,
            title TEXT,
            content TEXT,
            url TEXT,
            published_at TEXT,
            source TEXT,
            used INTEGER DEFAULT 0,
            feedback TEXT,
            timestamp_fetched TEXT
        )
    """)

    # Create songs table
    c.execute("""
        CREATE TABLE IF NOT EXISTS songs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            artist TEXT,
            url TEXT,
            source TEXT,
            used INTEGER DEFAULT 0,
            feedback TEXT,
            timestamp_fetched TEXT
        )
    """)

    # Add indexes for performance and deduplication
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_articles_uuid ON articles(uuid)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_articles_feedback ON articles(feedback)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_articles_used ON articles(used)")

    c.execute("CREATE INDEX IF NOT EXISTS idx_songs_artist_title ON songs(artist, title)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_songs_feedback ON songs(feedback)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_songs_used ON songs(used)")

    conn.commit()
    conn.close()

def load_profile_context():
    conn = sqlite3.connect('user_profiles.db')
    c = conn.cursor()
    c.execute('SELECT * FROM profiles ORDER BY id DESC LIMIT 1')
    user = c.fetchone()
    conn.close()

    if user:
        # Pad in case fewer than 10 columns
        user = list(user) + [None] * (10 - len(user))
        _, topics, music_tastes, favorite_artists, news_mood, location, music_pref, station_name, host_name, news_categories = user

        topics_list = [t.strip() for t in topics.split(',') if t.strip()]
        genres_list = [g.strip() for g in music_tastes.split(',') if g.strip()]
        artists = favorite_artists
        music_pref_list = music_pref.split(',') if music_pref else []
        selected_categories = news_categories.split(',') if news_categories else []

        return {
            "topics": ', '.join(topics_list),
            "music_tastes": genres_list,
            "favorite_artists": artists,
            "news_mood": news_mood,
            "location": location,
            "station_name": station_name,
            "host_name": host_name,
            "music_pref": music_pref_list,
            "selected_categories": selected_categories,
            "allowed_genres": ALLOWED_GENRES,
            "allowed_categories": ALLOWED_CATEGORIES,
        }
    else:
        return {
            "topics": "",
            "music_tastes": [],
            "favorite_artists": "",
            "news_mood": "",
            "location": "",
            "station_name": "",
            "host_name": "",
            "music_pref": [],
            "selected_categories": [],
            "allowed_genres": ALLOWED_GENRES,
            "allowed_categories": ALLOWED_CATEGORIES,
        }



@app.route('/')
def index():
    conn = sqlite3.connect('user_profiles.db')
    c = conn.cursor()
    c.execute('SELECT * FROM profiles ORDER BY id DESC LIMIT 1')
    user = c.fetchone()
    conn.close()
    if user:
        user = list(user) + [None] * (9 - len(user))
        _, topics, music_tastes, favorite_artists, news_mood, location, music_pref, station_name, host_name, news_categories = user
        topics_list = [t.strip() for t in topics.split(',')]
        genres_list = [g.strip() for g in music_tastes.split(',')]
        artists_list = [a.strip() for a in favorite_artists.split(',')]
        pref_list = music_pref.split(',') if music_pref else []
        return render_template('index.html',
                       topics=', '.join(topics_list),
                       music_tastes=genres_list,
                       favorite_artists=', '.join(artists_list),
                       news_mood=news_mood,
                       location=location,
                       station_name=station_name,
                       host_name=host_name,
                       music_pref=pref_list,
                       allowed_genres=ALLOWED_GENRES,
                       allowed_categories=ALLOWED_CATEGORIES,
                        selected_categories=news_categories.split(',') if news_categories else []
                        )

    else:
        return render_template('index.html',
                       topics='',
                       music_tastes=[],
                       favorite_artists='',
                       news_mood='',
                       location='',
                       station_name='',
                       host_name='',
                       music_pref=[],
                       allowed_genres=ALLOWED_GENRES,
                       allowed_categories=ALLOWED_CATEGORIES,
                       selected_categories=[])


@app.route('/save', methods=['POST'])
def save():
    raw_topics_list = [t.strip() for t in request.form['topics'].split(',') if t.strip()]
    raw_genres_list = request.form.getlist('music_tastes')
    raw_artists_list = [a.strip() for a in request.form['favorite_artists'].split(',') if a.strip()]
    news_mood = request.form['news_mood']
    location = request.form['location']
    station_name = request.form['station_name']
    host_name = request.form['host_name']
    music_pref_list = request.form.getlist('music_pref')
    music_pref = ','.join(music_pref_list)
    news_categories_list = request.form.getlist('news_categories')  # Get selected categories from form
    news_categories = ','.join(news_categories_list)  # Convert to comma-separated string


    corrected_topics = []
    for t in raw_topics_list:
        cleaned = clean_and_validate_topic(t)
        if cleaned:
            corrected_topics.append(cleaned)
        else:
            flash(f"Topic '{t}' was removed due to being inappropriate or unclear.", "warning")


    def detect_topic_changes(original_list, corrected_list):
        return [(orig.strip(), corr.strip()) for orig, corr in zip(original_list, corrected_list) if orig.strip().lower() != corr.strip().lower()]

    topic_changes = detect_topic_changes(raw_topics_list, corrected_topics)
    topic_corrections = [f"'{orig}' → '{corr}'" for orig, corr in topic_changes]



    corrected_genres = raw_genres_list


    corrected_artists, valid_artists, artist_corrections = [], True, []
    for artist in raw_artists_list:
        validated = validate_artist_with_llm(artist)
        if validated:
            if validated.lower() != artist.lower():
                artist_corrections.append(f"Artist '{artist}' → '{validated}'")
            corrected_artists.append(validated)
        else:
            flash(f"Artist '{artist}' is invalid and was removed.", "warning")
            valid_artists = False

    if not valid_artists:
        flash("Some invalid artists were removed. Please review and resubmit.", "warning")
        return render_template('index.html',
                               topics=', '.join(corrected_topics),
                               music_tastes=corrected_genres,
                               favorite_artists=', '.join(corrected_artists),
                               news_mood=news_mood,
                               location=location,
                               station_name=station_name,
                               host_name=host_name,
                               music_pref=music_pref_list,
                               allowed_genres=ALLOWED_GENRES,
                               allowed_categories=ALLOWED_CATEGORIES,
                               selected_categories=news_categories_list)

    messages = []
    if topic_corrections:
        messages.append("Topic corrections: " + "; ".join(topic_corrections))
    if artist_corrections:
        messages.append("Artist corrections: " + "; ".join(artist_corrections))

    if messages:
        flash("Corrections made: " + " | ".join(messages), "info")
    else:
        flash("Preferences saved successfully!", "success")


    conn = sqlite3.connect('user_profiles.db')
    c = conn.cursor()
    c.execute('INSERT INTO profiles (topics, music_tastes, favorite_artists, news_mood, location, music_pref, station_name, host_name, news_categories) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
          (",".join(corrected_topics),
           ",".join(corrected_genres),
           ",".join(corrected_artists),
           news_mood,
           location,
           music_pref,
           station_name,
           host_name,
           news_categories))  # ✅ New field here

    conn.commit()
    conn.close()

    ensure_minimum_content()
    return redirect('/radio')



@app.route('/radio')
def radio():
    ensure_minimum_content()
    with open('aggregated_news.json', 'r') as f:
        news_data = json.load(f)
    conn = sqlite3.connect('user_profiles.db')
    c = conn.cursor()
    c.execute('SELECT * FROM profiles ORDER BY id DESC LIMIT 1')
    user = c.fetchone()
    conn.close()

    if user:
        _, topics, music_tastes, favorite_artists, news_mood, location, music_pref, station_name, host_name, news_categories = user
        topics_list = [t.strip() for t in topics.split(',')]
        genres_list = [g.strip() for g in music_tastes.split(',')]
        artists_list = [a.strip() for a in favorite_artists.split(',')]
        news_categories_list = [c.strip() for c in news_categories.split(',')] if news_categories else []

        top_tracks_playlist = get_recommended_tracks(artists_list, genres_list)
        trending_playlist = get_trending_tracks()
        throwback_playlist = get_throwback_tracks()
        new_artist_playlist = get_new_artist_recommendations(artists_list)

        # Instead of re-fetching similar content:
        # Just read already-fetched similar content
        conn = sqlite3.connect("user_profiles.db", timeout=5.0)
        c = conn.cursor()

        c.execute("SELECT uuid, title, content, url, published_at, source FROM articles WHERE source LIKE 'similar_to:%' AND used = 0 ORDER BY timestamp_fetched DESC LIMIT 10")
        similar_articles = [{
            "uuid": row[0], "title": row[1], "content": row[2],
            "url": row[3], "published_at": row[4], "source": row[5]
        } for row in c.fetchall()]

        c.execute("SELECT title, artist, source FROM songs WHERE source LIKE 'similar_to:%' AND used = 0 ORDER BY timestamp_fetched DESC LIMIT 10")
        similar_songs = [{
            "title": row[0], "artist": row[1], "source": row[2]
        } for row in c.fetchall()]

        conn.close()

        grouped_news = {}

        # Add keyword-based news
        for topic in topics_list:
            key = topic.lower()
            grouped_news[key] = news_data.get('aggregated_news', {}).get(key, [])

        # Add category-based news
        for category in news_categories_list:
            key = category.lower()
            grouped_news[key] = news_data.get('aggregated_news', {}).get(key, [])



        news_categories_list = user[9].split(',') if user[9] else []
        news_categories = user[9] if len(user) > 9 else ""

        # 🧠 Fetch liked/disliked articles and songs
        conn = sqlite3.connect('user_profiles.db')
        c = conn.cursor()

        # Fetch feedback on articles
        c.execute("SELECT title, feedback FROM articles WHERE feedback IS NOT NULL")
        news_feedback = c.fetchall()
        # Fetch feedback on songs
        c.execute("SELECT title, artist, feedback FROM songs WHERE feedback IS NOT NULL")
        song_feedback = c.fetchall()

        conn.close()


        return render_template('radio.html',
                       grouped_news=grouped_news,
                       top_stories=news_data.get('top_stories', []),
                       music_tastes=genres_list,
                       favorite_artists=artists_list,
                       topics=topics_list,
                       news_mood=news_mood,
                       location=location,
                       station_name=station_name,
                       host_name=host_name,
                       top_tracks_playlist=top_tracks_playlist,
                       trending_playlist=trending_playlist,
                       throwback_playlist=throwback_playlist,
                       new_artist_playlist=new_artist_playlist, 
                       selected_categories=news_categories_list,
                        allowed_categories=ALLOWED_CATEGORIES,
                        news_feedback=news_feedback,
                        song_feedback=song_feedback,
                        similar_articles=similar_articles,
                        similar_songs=similar_songs)
    return "No profile found."

@app.route('/refresh_news')
def refresh_news():
    subprocess.run(["python", "FetchNews.py"])
    return redirect('/radio')




@app.route('/show_script')
def show_script():
    try:
        with open('radio_script.txt', 'r') as f:
            script = f.read()
    except FileNotFoundError:
        script = "No script has been generated yet. Please click 'Generate Radio Host Script' first."
    return render_template('show_script.html', script=script)

@app.route('/feedback/song', methods=['POST'])
def feedback_song():
    title = request.form['title']
    artist = request.form['artist']
    feedback = request.form['feedback']

    conn = sqlite3.connect('user_profiles.db')
    c = conn.cursor()
    c.execute("""
        UPDATE songs
        SET feedback = ?
        WHERE rowid = (
            SELECT rowid FROM songs
            WHERE title = ? AND artist = ?
            ORDER BY timestamp_fetched DESC
            LIMIT 1
        )
    """, (feedback, title, artist))
    conn.commit()
    conn.close()

    flash(f"You {feedback}d the song '{title}' by {artist}.", "info")

    return redirect('/show_script')


@app.route('/feedback/news', methods=['POST'])
def feedback_news():
    title = request.form['title']
    feedback = request.form['feedback']

    conn = sqlite3.connect('user_profiles.db')
    c = conn.cursor()
    c.execute("UPDATE articles SET feedback = ? WHERE title = ?", (feedback, title))
    conn.commit()
    conn.close()

    flash(f"You {feedback}d the news article '{title}'.", "info")

    if feedback == "like":
        # Fetch UUID and region
        conn = sqlite3.connect('user_profiles.db')
        c = conn.cursor()
        c.execute("SELECT uuid, source FROM articles WHERE title = ? ORDER BY timestamp_fetched DESC LIMIT 1", (title,))
        row = c.fetchone()
        conn.close()

    return redirect('/show_script')

def similarity_worker():
    BATCH_INTERVAL = 60  # run every 60 seconds
    BATCH_SIZE = 10
    while True:
        time.sleep(BATCH_INTERVAL)

        song_batch = [{'title': t, 'artist': a} for t, a in get_liked_songs()]
        article_batch = [{'uuid': u, 'region': r} for u, r in get_liked_articles()]

        for s in song_batch:
            print(f"🔁 Fetching similar songs for: {s['title']} by {s['artist']}")
            similar_tracks = get_similar_tracks(s['title'], s['artist'])
            for track in similar_tracks:
                store_song(track, source=f"similar_to:{s['artist']}:{s['title']}")
            mark_song_similar_fetched(s['title'], s['artist'])

        for a in article_batch:
            print(f"📰 Fetching similar articles for UUID: {a['uuid']}")
            fetch_similar_articles([a['uuid']], a['region'])
            mark_article_similar_fetched(a['uuid'])


from flask import Response, stream_with_context
def ensure_minimum_content():
            articles_left = len(get_unused_articles(limit=15))  # buffer
            songs_left = len(get_unused_songs(limit=15))

            if articles_left < 10:
                print("📡 Fetching more articles based on liked + neutral feedback...")
                subprocess.run(["python", "FetchNews.py"])  # already does similar fetching

            if songs_left < 10:
                print("🎵 Not enough songs — fetching similar songs from liked")


@app.route('/generate_script_stream')
def generate_script_stream():
    # ✅ Clear previous script chunks BEFORE streaming starts
    conn = sqlite3.connect('user_profiles.db')
    c = conn.cursor()
    c.execute("DELETE FROM script_chunks")
    conn.commit()
    conn.close()

    # ✅ Clear all audio files before starting new script generation
    audio_dir = os.path.join(app.root_path, "static", "audio")
    os.makedirs(audio_dir, exist_ok=True)

    clear_audio_directory()


    def stream():
        import time
        conn = sqlite3.connect('user_profiles.db')
        c = conn.cursor()
        c.execute('SELECT station_name, host_name, news_mood FROM profiles ORDER BY id DESC LIMIT 1')
        row = c.fetchone()
        conn.close()

        station_name = row[0] if row else "Your Radio Station"
        host_name = row[1] if row else "Your Host"
        news_mood = row[2] if row else "neutral"


        WORDS_PER_MINUTE = 150
        current_time_min = 0.0

        ensure_minimum_content()



        top_stories = get_unused_articles(limit=10, source_filter="top")
        personalized_stories = get_unused_articles(limit=10, exclude_sources=["top"])

        # Prevent overlap by UUID
        seen_uuids = {a['uuid'] for a in top_stories}
        personalized_stories = [a for a in personalized_stories if a['uuid'] not in seen_uuids]


        # Collect initial tracks
        trending_tracks = get_trending_tracks()
        trending_set = {(t['artist'], t['title']) for t in trending_tracks}

        throwback_raw = get_throwback_tracks().get('tracks', [])
        throwback_tracks = [t for t in throwback_raw if (t['artist'], t['title']) not in trending_set]
        throwback_set = trending_set.union({(t['artist'], t['title']) for t in throwback_tracks})

        new_artist_recs_raw = get_new_artist_recommendations([])
        new_artist_recs = []
        for rec in new_artist_recs_raw:
            filtered_tracks = [track for track in rec['tracks'] if (rec['artist'], track['title']) not in throwback_set]
            if filtered_tracks:
                new_artist_recs.append({
                    'artist': rec['artist'],
                    'top_tracks': filtered_tracks
                })


        if not top_stories:
            top_stories = get_unused_articles(limit=5)
        if not personalized_stories:
            personalized_stories = get_unused_articles(limit=5)
        if not trending_tracks:
            trending_tracks = get_unused_songs(limit=5)
        if not throwback_tracks:
            throwback_tracks = get_unused_songs(limit=5)
        if not new_artist_recs:
            fallback_songs = get_unused_songs(limit=3)
            new_artist_recs = [{
                'artist': song['artist'],
                'top_tracks': [{'name': song['title'], 'url': song['url'], 'duration': '180000'}]
            } for song in fallback_songs]

        max_length = max(len(top_stories), len(personalized_stories), len(trending_tracks), len(throwback_tracks), len(new_artist_recs))
        combined = []
        used_articles, used_songs = [], []

        for i in range(max_length):
            if i < len(top_stories):
                combined.append(('news', top_stories[i]))
            if i < len(personalized_stories):
                combined.append(('news', personalized_stories[i]))
            if i < len(trending_tracks):
                combined.append(('song', trending_tracks[i]))
            if i < len(throwback_tracks):
                combined.append(('song', throwback_tracks[i]))
            if i < len(new_artist_recs):
                artist = new_artist_recs[i]
                if artist.get('top_tracks'):
                    combined.append(('song', {
                        'artist': artist['artist'],
                        'title': artist['top_tracks'][0]['name'],
                        'url': artist['top_tracks'][0]['url'],
                        'duration': artist['top_tracks'][0].get('duration', '180000')
                    }))

        for item_type, item in combined:
            total_seconds = int(current_time_min * 60)
            minutes = total_seconds // 60
            seconds = total_seconds % 60
            timestamp_str = f"[{minutes:02d}:{seconds:02d}]"

            if item_type == 'news':
                used_articles.append(item)
                full_text = fetch_full_article(item['url']) or item.get('content', '')
                sample_style = STYLE_SAMPLES.get(news_mood, STYLE_SAMPLES["neutral"])
                context_prompt = f"""You are {host_name}, the host of {station_name}.
                You're reading a real article and presenting it on radio.

                Title: {item['title']}
                Full article:
                {full_text}

                Generate a spoken radio segment for your listeners."""
                prompt = STYLE_PRIMING_TEMPLATE.format(sample_style=sample_style, context_prompt=context_prompt.strip())
         
                try:
                    print("🧠 Calling model_longform for article:", item['title'])
                    response = model_longform.generate_content(prompt)
                    script_text = response.text.strip()
                    print("✅ Received response from LLM:", script_text[:80])
                except Exception as e:
                    script_text = f"[ERROR: Gemini API failed — {e}]"

                # ✅ Save script chunk to DB
                conn = sqlite3.connect('user_profiles.db')
                c = conn.cursor()
                c.execute("SELECT COUNT(*) FROM script_chunks WHERE title = ? AND script = ?", (item['title'], script_text))
                if c.fetchone()[0] == 0:
                    c.execute("""
                        INSERT INTO script_chunks (item_type, title, artist, script)
                        VALUES (?, ?, ?, ?)
                    """, ('news', item['title'], '', script_text))

                conn.commit()
                conn.close()


                word_count = len(script_text.split())
                duration_min = word_count / WORDS_PER_MINUTE
                feedback_form = f"""
                    <div><strong>📰 Article:</strong> {item['title']}</div>
                    <form method='POST' action='/feedback/news' style='display:inline;' onsubmit="submitFeedback(event, '/feedback/news')">
                        <input type='hidden' name='uuid' value="{item['uuid']}">
                        <input type='hidden' name='title' value="{item['title']}">
                        <input type='hidden' name='feedback' value="like">
                        <button type='submit'>👍 Like</button>
                    </form>
                    <form method='POST' action='/feedback/news' style='display:inline;' onsubmit="submitFeedback(event, '/feedback/news')">
                        <input type='hidden' name='uuid' value="{item['uuid']}">
                        <input type='hidden' name='title' value="{item['title']}">
                        <input type='hidden' name='feedback' value="dislike">
                        <button type='submit'>👎 Dislike</button>
                    </form>
                """


                yield f"<div class='script-chunk'>{timestamp_str} {script_text}<br>{feedback_form}</div>\n\n"
                current_time_min += duration_min

            elif item_type == 'song':
                used_songs.append(item)
                duration_ms = item.get('duration', '180000')
                duration_min = int(duration_ms) / 60000 if duration_ms else 3

                intro_prompt = f"""You are {host_name}, the host of {station_name}. Introduce this artist or song in 1-2 lines:\nArtist: {item['artist']}\nSong: {item['title']}\nBe enthusiastic and conversational."""
                try:
                    print("🧠 Calling model for song:", item['title'])
                    intro_response = model.generate_content(intro_prompt)
                    intro_text = intro_response.text.strip()
                    print("✅ Received song intro from LLM:", intro_text[:80])
                except Exception as e:
                    intro_text = f"[ERROR: Gemini API failed — {e}]"

                # ✅ Save script chunk to DB
                conn = sqlite3.connect('user_profiles.db')
                c = conn.cursor()
                c.execute("SELECT COUNT(*) FROM script_chunks WHERE title = ? AND script = ?", (item['title'], intro_text))
                if c.fetchone()[0] == 0:
                    c.execute("""
                        INSERT INTO script_chunks (item_type, title, artist, script)
                        VALUES (?, ?, ?, ?)
                    """, ('song', item['title'], item['artist'], intro_text))

                conn.commit()
                conn.close()


                intro_word_count = len(intro_text.split())
                intro_duration_min = intro_word_count / WORDS_PER_MINUTE

                song_marker = f"<span style='color: #ff6600; font-weight: bold;'>🎵 NOW PLAYING: {item['artist']} — {item['title']}</span>"

                feedback_form = f"""
                    <div><strong>🎵 Song:</strong> {item['artist']} — {item['title']}</div>
                    <form method='POST' action='/feedback/song' style='display:inline;' onsubmit="submitFeedback(event, '/feedback/song')">
                        <input type='hidden' name='title' value="{item['title']}">
                        <input type='hidden' name='artist' value="{item['artist']}">
                        <input type='hidden' name='feedback' value="like">
                        <button type='submit'>👍 Like</button>
                    </form>
                    <form method='POST' action='/feedback/song' style='display:inline;' onsubmit="submitFeedback(event, '/feedback/song')">
                        <input type='hidden' name='title' value="{item['title']}">
                        <input type='hidden' name='artist' value="{item['artist']}">
                        <input type='hidden' name='feedback' value="dislike">
                        <button type='submit'>👎 Dislike</button>
                    </form>
                """



                yield f"<div class='script-chunk'>{timestamp_str} {intro_text}<br>{feedback_form}</div>\n\n"
                current_time_min += intro_duration_min

                total_seconds = int(current_time_min * 60)
                minutes = total_seconds // 60
                seconds = total_seconds % 60
                song_timestamp = f"[{minutes:02d}:{seconds:02d}]"

                yield f"<div class='script-chunk'>{song_timestamp} {song_marker}</div>\n\n"
                current_time_min += duration_min

        mark_articles_and_songs_used(used_articles, used_songs)

    return Response(stream_with_context(stream()), content_type='text/html')


@app.route('/show_stream_script')
def show_stream_script():
    return render_template('stream_script.html')

@app.route('/dual_stream_view')
def dual_stream_view():
    return render_template('dual_stream_view.html')

@app.route('/user_script_stream')
def user_script_stream():
    return render_template('user_script_stream.html')

@app.route('/next_tts_chunk')
def next_tts_chunk():
    # Step 1: Fetch next unused script chunk
    conn = sqlite3.connect('user_profiles.db')
    c = conn.cursor()
    c.execute("""
        SELECT id, item_type, title, artist, script 
        FROM script_chunks 
        WHERE used = 0 
        ORDER BY id 
        LIMIT 1
    """)
    row = c.fetchone()

    if not row:
        conn.close()
        return jsonify({"text": "No content.", "audio_url": ""})

    chunk_id, item_type, title, artist, script = row
    cleaned_script = re.sub(r'\*\*.*?\*\*', '', script).strip()

    if not cleaned_script or len(cleaned_script.split()) < 2:
        print(f"⚠️ Skipping invalid script chunk: {repr(cleaned_script)}")
        conn.close()
        return jsonify({"text": "Skipped: empty or invalid script.", "audio_url": ""})

    # Step 2: Set file paths
    wav_filename = f"{uuid.uuid4().hex}.wav"
    fixed_wav_filename = wav_filename.replace(".wav", "_fixed.wav")
    mp3_filename = wav_filename.replace(".wav", ".mp3")

    wav_filepath = os.path.join(app.root_path, "static", "audio", wav_filename)
    fixed_wav_filepath = os.path.join(app.root_path, "static", "audio", fixed_wav_filename)
    mp3_filepath = os.path.join(app.root_path, "static", "audio", mp3_filename)

    os.makedirs(os.path.dirname(wav_filepath), exist_ok=True)

    # Step 3: Generate WAV using pyttsx3 with thread lock
    if not hasattr(app, 'tts_lock'):
        app.tts_lock = threading.Lock()

    try:
        with app.tts_lock:
            print(f"🗣️ Generating WAV with pyttsx3 for chunk {chunk_id}")
            engine = pyttsx3.init()
            engine.setProperty('rate', 180)
            engine.save_to_file(cleaned_script, wav_filepath)
            engine.runAndWait()
            time.sleep(0.5)  # ⏳ Allow disk flush
            print(f"✅ WAV saved: {wav_filepath}")

        # Ensure file is written and non-empty
        for attempt in range(15):
            if os.path.exists(wav_filepath) and os.path.getsize(wav_filepath) > 10000:
                if is_valid_wav(wav_filepath):
                    break
            time.sleep(0.2)
        else:
            raise Exception("WAV file too small or invalid")


    except Exception as e:
        print(f"❌ pyttsx3 failed or WAV incomplete: {e}")
        conn.close()
        return jsonify({"text": "TTS generation failed.", "audio_url": ""})

    print(f"🧪 WAV file size: {os.path.getsize(wav_filepath)} bytes")

    # Step 4: Fix WAV format using ffmpeg
    try:
        print(f"🛠️ Re-encoding WAV for compatibility: {fixed_wav_filepath}")
        subprocess.run(
            ["ffmpeg", "-y", "-i", wav_filepath, "-ar", "22050", "-ac", "1", "-sample_fmt", "s16", fixed_wav_filepath],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    except Exception as e:
        print(f"❌ Failed to fix WAV: {e}")
        conn.close()
        return jsonify({"text": "WAV fix failed", "audio_url": ""})

    # Step 5: Convert fixed WAV to MP3
    try:
        print(f"🎛️ Converting to MP3: {mp3_filename}")
        subprocess.run(
            ["ffmpeg", "-y", "-i", fixed_wav_filepath, "-codec:a", "libmp3lame", "-qscale:a", "5", mp3_filepath],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        print(f"✅ MP3 created: {mp3_filepath}")
        os.remove(wav_filepath)
        os.remove(fixed_wav_filepath)
    except Exception as e:
        print(f"❌ ffmpeg conversion failed: {e}")
        conn.close()
        return jsonify({"text": "MP3 conversion failed.", "audio_url": ""})

    # Step 6: Update DB and return result
    c.execute("UPDATE script_chunks SET used = 1, audio_file = ? WHERE id = ?", (mp3_filename, chunk_id))
    conn.commit()
    conn.close()

    return jsonify({
        "text": script,
        "title": title,
        "artist": artist,
        "audio_url": f"/static/audio/{mp3_filename}"
    })









@app.route('/script_ready')
def script_ready():
    print("📦 Checking if any script chunks are ready...")

    conn = sqlite3.connect('user_profiles.db')
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM script_chunks WHERE used = 0")
    count_unused = c.fetchone()[0]
    conn.close()

    print(f"🔍 {count_unused} unused script chunks found.")
    return jsonify({"ready": count_unused > 0})


@app.route("/user_main", methods=["GET", "POST"])
def user_main():
    return render_template("user_main.html", **load_profile_context())

@app.route("/user_radio")
def user_radio():
    return render_template("user_radio.html", **load_profile_context())

@app.route('/user_save', methods=['POST'])
def user_save_preferences():
    station_name = request.form.get("station_name", "")
    host_name = request.form.get("host_name", "")
    news_categories = request.form.getlist("news_categories")
    raw_topics_list = [t.strip() for t in request.form.get("topics", "").split(',') if t.strip()]
    raw_genres_list = request.form.getlist("music_tastes")
    raw_artists_list = [a.strip() for a in request.form.get("favorite_artists", "").split(',') if a.strip()]
    music_pref_list = request.form.getlist("music_pref")
    location = request.form.get("location", "")
    news_mood = request.form.get("news_mood", "")

    # 🧠 Spellcheck and filter topics
    corrected_topics = []
    for t in raw_topics_list:
        cleaned = clean_and_validate_topic(t)
        if cleaned:
            corrected_topics.append(cleaned)
        else:
            flash(f"Topic '{t}' was removed due to being inappropriate or unclear.", "warning")

    def detect_topic_changes(original_list, corrected_list):
        return [(orig.strip(), corr.strip()) for orig, corr in zip(original_list, corrected_list) if orig.strip().lower() != corr.strip().lower()]

    topic_changes = detect_topic_changes(raw_topics_list, corrected_topics)
    topic_corrections = [f"'{orig}' → '{corr}'" for orig, corr in topic_changes]

    # 🧠 Validate artists
    corrected_artists, valid_artists, artist_corrections = [], True, []
    for artist in raw_artists_list:
        validated = validate_artist_with_llm(artist)
        if validated:
            if validated.lower() != artist.lower():
                artist_corrections.append(f"Artist '{artist}' → '{validated}'")
            corrected_artists.append(validated)
        else:
            flash(f"Artist '{artist}' is invalid and was removed.", "warning")
            valid_artists = False

    if not valid_artists:
        flash("Some invalid artists were removed. Please review and resubmit.", "warning")
        return redirect('/user_main')

    messages = []
    if topic_corrections:
        messages.append("Topic corrections: " + "; ".join(topic_corrections))
    if artist_corrections:
        messages.append("Artist corrections: " + "; ".join(artist_corrections))

    if messages:
        flash("Corrections made: " + " | ".join(messages), "info")
    else:
        flash("Preferences saved successfully!", "success")

    # Save into database
    conn = sqlite3.connect('user_profiles.db')
    c = conn.cursor()
    c.execute("""
        INSERT INTO profiles 
        (station_name, host_name, news_categories, topics, music_tastes, favorite_artists, music_pref, location, news_mood)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        station_name,
        host_name,
        ",".join(news_categories),
        ",".join(corrected_topics),
        ",".join(raw_genres_list),
        ",".join(corrected_artists),
        ",".join(music_pref_list),
        location,
        news_mood
    ))
    conn.commit()
    conn.close()

    return redirect('/user_radio')


@app.route('/like_script_chunk', methods=['POST'])
def like_script_chunk():
    data = request.get_json()
    chunk_title = data.get("chunk_title")

    conn = sqlite3.connect('user_profiles.db')
    c = conn.cursor()

    # Find the latest script chunk matching the title
    c.execute("SELECT id, chunk_text FROM script_chunks WHERE title = ? ORDER BY id DESC LIMIT 1", (chunk_title,))
    row = c.fetchone()
    if row:
        chunk_id, chunk_text = row
        c.execute("UPDATE script_chunks SET liked = 1 WHERE id = ?", (chunk_id,))
        c.execute("SELECT news_mood FROM profiles ORDER BY id DESC LIMIT 1")
        mood_row = c.fetchone()
        if mood_row:
            store_liked_segment(chunk_text, mood_row[0])
        conn.commit()

    conn.close()
    return jsonify({"status": "success"})


if __name__ == "__main__":
    init_db()  # ✅ Create tables + indexes

    def delayed_start_worker():
        time.sleep(3)
        similarity_worker()

    def delayed_sqlite_config():
        time.sleep(3)
        configure_sqlite()

    threading.Thread(target=delayed_start_worker, daemon=True).start()
    threading.Thread(target=delayed_sqlite_config, daemon=True).start()

    app.run(debug=True)

