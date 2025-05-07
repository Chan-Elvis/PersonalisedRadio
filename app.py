from flask import Flask, render_template, request, redirect, flash
import sqlite3
import json
import subprocess
import os
import requests
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()
SECRET_KEY = os.getenv("SECRET_KEY")
LASTFM_API_KEY = os.getenv("LASTFM_API_KEY")

app = Flask(__name__)
app.secret_key = SECRET_KEY
model = genai.GenerativeModel('gemini-2.0-flash')


ALLOWED_GENRES = [
    'rock', 'pop', 'jazz', 'blues', 'hip-hop', 'rap', 'indie', 'electronic',
    'metal', 'country', 'classical', 'folk', 'punk', 'dance', 'house',
    'soul', 'reggae', 'k-pop', 'r&b'
]

def check_spelling(word):
    prompt = f"Correct the spelling of the word '{word}'. If already correct, return as-is. No explanation."
    try:
        response = model.generate_content(prompt)
        if response and hasattr(response, 'text'):
            return response.text.strip(' "\'.')
    except Exception as e:
        flash(f"Error spellchecking '{word}': {e}", "error")
    return word

def validate_artist_with_llm(artist_name):
    prompt = f"Is '{artist_name}' a real music artist or band? If yes, return corrected name. If no, return 'invalid'. No explanation."
    try:
        response = model.generate_content(prompt)
        if response and hasattr(response, 'text'):
            result = response.text.strip()
            if result.lower() == 'invalid':
                return None
            return result.strip(' "\'.')
    except Exception as e:
        flash(f"Error validating artist '{artist_name}': {e}", "error")
    return None

def get_recommended_tracks(artists, genres):
    tracks = []
    for artist in artists:
        url = f"http://ws.audioscrobbler.com/2.0/?method=artist.gettoptracks&artist={artist}&api_key={LASTFM_API_KEY}&format=json"
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            top_tracks = data.get('toptracks', {}).get('track', [])[:3]
            for track in top_tracks:
                tracks.append({'artist': artist, 'title': track['name'], 'url': track['url']})

    for genre in genres:
        url = f"http://ws.audioscrobbler.com/2.0/?method=tag.gettoptracks&tag={genre}&api_key={LASTFM_API_KEY}&format=json"
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            top_tracks = data.get('tracks', {}).get('track', [])[:3]
            for track in top_tracks:
                tracks.append({'artist': track['artist']['name'], 'title': track['name'], 'url': track['url']})

    return tracks[:20]

def init_db():
    conn = sqlite3.connect('user_profiles.db')
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topics TEXT, music_tastes TEXT, favorite_artists TEXT, news_mood TEXT, location TEXT
        )
    """)
    conn.commit()
    conn.close()

@app.route('/')
def index():
    conn = sqlite3.connect('user_profiles.db')
    c = conn.cursor()
    c.execute('SELECT * FROM profiles ORDER BY id DESC LIMIT 1')
    user = c.fetchone()
    conn.close()
    if user:
        _, topics, music_tastes, favorite_artists, news_mood, location = user
        topics_list = [t.strip() for t in topics.split(',')]
        genres_list = [g.strip() for g in music_tastes.split(',')]
        artists_list = [a.strip() for a in favorite_artists.split(',')]
        return render_template('index.html',
                               topics=', '.join(topics_list),
                               music_tastes=genres_list,
                               favorite_artists=', '.join(artists_list),
                               news_mood=news_mood,
                               location=location,
                               allowed_genres=ALLOWED_GENRES)
    else:
        return render_template('index.html',
                               topics='',
                               music_tastes=[],
                               favorite_artists='',
                               news_mood='',
                               location='',
                               allowed_genres=ALLOWED_GENRES)

@app.route('/save', methods=['POST'])
def save():
    raw_topics_list = [t.strip() for t in request.form['topics'].split(',') if t.strip()]
    raw_genres_list = request.form.getlist('music_tastes')
    raw_artists_list = [a.strip() for a in request.form['favorite_artists'].split(',') if a.strip()]
    news_mood = request.form['news_mood']
    location = request.form['location']

    corrected_topics = [check_spelling(t) for t in raw_topics_list]
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
                               allowed_genres=ALLOWED_GENRES)

    if artist_corrections:
        flash("Corrections made: " + "; ".join(artist_corrections), "info")
    else:
        flash("Preferences saved successfully!", "success")

    conn = sqlite3.connect('user_profiles.db')
    c = conn.cursor()
    c.execute('INSERT INTO profiles (topics, music_tastes, favorite_artists, news_mood, location) VALUES (?, ?, ?, ?, ?)',
              (",".join(corrected_topics),
               ",".join(corrected_genres),
               ",".join(corrected_artists),
               news_mood,
               location))
    conn.commit()
    conn.close()

    return redirect('/radio')

@app.route('/radio')
def radio():
    subprocess.run(["python", "FetchNews.py"])
    with open('aggregated_news.json', 'r') as f:
        news_data = json.load(f)
    conn = sqlite3.connect('user_profiles.db')
    c = conn.cursor()
    c.execute('SELECT * FROM profiles ORDER BY id DESC LIMIT 1')
    user = c.fetchone()
    conn.close()
    if user:
        _, topics, music_tastes, favorite_artists, news_mood, location = user
        topics_list = [t.strip() for t in topics.split(',')]
        genres_list = [g.strip() for g in music_tastes.split(',')]
        artists_list = [a.strip() for a in favorite_artists.split(',')]

        grouped_news = {topic: news_data.get('aggregated_news', {}).get(topic.lower(), [])
                        for topic in topics_list}
        recommended_tracks = get_recommended_tracks(artists_list, genres_list)

        return render_template('radio.html',
                               grouped_news=grouped_news,
                               top_stories=news_data.get('top_stories', []),
                               music_tastes=genres_list,
                               favorite_artists=artists_list,
                               topics=topics_list,
                               news_mood=news_mood,
                               location=location,
                               recommended_tracks=recommended_tracks)
    return "No profile found."

@app.route('/refresh_news')
def refresh_news():
    subprocess.run(["python", "FetchNews.py"])
    return redirect('/radio')

if __name__ == '__main__':
    init_db()
    app.run(debug=True)

