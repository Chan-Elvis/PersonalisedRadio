from flask import Flask, render_template, request, redirect, flash
import sqlite3
import json
import subprocess
import os
import requests
import google.generativeai as genai
from dotenv import load_dotenv
import random
import datetime

load_dotenv()
SECRET_KEY = os.getenv("SECRET_KEY")
LASTFM_API_KEY = os.getenv("LASTFM_API_KEY")

app = Flask(__name__)
app.secret_key = SECRET_KEY
model = genai.GenerativeModel('gemini-2.0-flash')
model_longform = genai.GenerativeModel('gemini-2.0-flash-lite')    # for longer radio scripts

ALLOWED_GENRES = [
    'rock', 'pop', 'jazz', 'blues', 'hip-hop', 'rap', 'indie', 'electronic',
    'metal', 'country', 'classical', 'folk', 'punk', 'dance', 'house',
    'soul', 'reggae', 'k-pop', 'r&b'
]

ALLOWED_CATEGORIES = [
    'general', 'science', 'sports', 'business', 'health',
    'entertainment', 'tech', 'politics', 'food', 'travel'
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

def get_trending_tracks():
    url = f"http://ws.audioscrobbler.com/2.0/?method=chart.gettoptracks&api_key={LASTFM_API_KEY}&format=json"
    response = requests.get(url)
    if response.status_code == 200:
        tracks = response.json()['tracks']['track']
        return [{'artist': t['artist']['name'], 'title': t['name'], 'url': t['url']} for t in tracks[:5]]
    return []

def get_throwback_tracks():
    throwback_tags = ['70s', '80s', '90s', 'classic rock', 'oldies']
    selected_tag = random.choice(throwback_tags)
    url = f"http://ws.audioscrobbler.com/2.0/?method=tag.gettoptracks&tag={selected_tag}&api_key={LASTFM_API_KEY}&format=json"
    response = requests.get(url)
    tracks = []
    if response.status_code == 200:
        data = response.json()
        top_tracks = data.get('tracks', {}).get('track', [])[:5]
        for track in top_tracks:
            tracks.append({'artist': track['artist']['name'], 'title': track['name'], 'url': track['url']})
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
                    # Get top tracks for this similar artist
                    track_url = f"http://ws.audioscrobbler.com/2.0/?method=artist.gettoptracks&artist={similar_artist}&api_key={LASTFM_API_KEY}&format=json"
                    track_resp = requests.get(track_url)
                    top_tracks = []
                    if track_resp.status_code == 200:
                        track_data = track_resp.json()
                        tracks = track_data.get('toptracks', {}).get('track', [])[:3]
                        for track in tracks:
                            top_tracks.append({'title': track['name'], 'url': track['url']})
                    recommendations.append({'artist': similar_artist, 'tracks': top_tracks})
    return recommendations

def filter_unused_articles(news_data):
    conn = sqlite3.connect('user_profiles.db')
    c = conn.cursor()
    c.execute('SELECT article_title FROM covered_articles')
    covered = set([row[0] for row in c.fetchall()])
    conn.close()
    return [article for article in news_data if article['title'] not in covered]

def filter_unused_songs(songs):
    conn = sqlite3.connect('user_profiles.db')
    c = conn.cursor()
    c.execute('SELECT song_title FROM played_songs')
    played = set([row[0] for row in c.fetchall()])
    conn.close()
    return [song for song in songs if song['title'] not in played]

def create_batches(items, batch_size):
    return [items[i:i + batch_size] for i in range(0, len(items), batch_size)]

def build_prompt(news_batch, music_batch):
    news_text = "\n".join([f"- {n['title']}: {n['content']}" for n in news_batch])
    music_text = "\n".join([f"- {m['artist']} — {m['title']}" for m in music_batch])
    prompt = f"""
You are a lively radio host.
Create a ~3-minute radio script segment introducing:
News:
{news_text}

Songs:
{music_text}

Make it engaging, fun, with smooth transitions, comments, and occasional humor.
"""
    return prompt

def generate_radio_script(news_batches, music_batches):
    final_script = ""
    for i in range(len(news_batches)):
        news_batch = news_batches[i]
        music_batch = music_batches[i] if i < len(music_batches) else []
        prompt = build_prompt(news_batch, music_batch)
        response = model_longform.generate_content(prompt)
        script_text = response.text.strip() if hasattr(response, 'text') else ''
        final_script += script_text + "\n\n"
    return final_script

def mark_articles_and_songs_used(news_batch, music_batch):
    timestamp = datetime.datetime.now().isoformat()
    conn = sqlite3.connect('user_profiles.db')
    c = conn.cursor()
    for n in news_batch:
        c.execute('INSERT INTO covered_articles (article_title, url, timestamp) VALUES (?, ?, ?)', 
                  (n['title'], n['url'], timestamp))
    for m in music_batch:
        c.execute('INSERT INTO played_songs (song_title, artist, timestamp) VALUES (?, ?, ?)', 
                  (m['title'], m['artist'], timestamp))
    conn.commit()
    conn.close()


def init_db():
    conn = sqlite3.connect('user_profiles.db')
    c = conn.cursor()
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


    corrected_topics = [check_spelling(t).strip() for t in raw_topics_list]

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

    subprocess.run(["python", "FetchNews.py"])
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
        _, topics, music_tastes, favorite_artists, news_mood, location, music_pref, station_name, host_name, news_categories = user
        topics_list = [t.strip() for t in topics.split(',')]
        genres_list = [g.strip() for g in music_tastes.split(',')]
        artists_list = [a.strip() for a in favorite_artists.split(',')]
        news_categories_list = [c.strip() for c in news_categories.split(',')] if news_categories else []

        top_tracks_playlist = get_recommended_tracks(artists_list, genres_list)
        trending_playlist = get_trending_tracks()
        throwback_playlist = get_throwback_tracks()
        new_artist_playlist = get_new_artist_recommendations(artists_list)

        grouped_news = {}

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
                        allowed_categories=ALLOWED_CATEGORIES)

    return "No profile found."

@app.route('/refresh_news')
def refresh_news():
    subprocess.run(["python", "FetchNews.py"])
    return redirect('/radio')

@app.route('/generate_script')
def generate_script():
    conn = sqlite3.connect('user_profiles.db')
    c = conn.cursor()
    c.execute('SELECT station_name, host_name FROM profiles ORDER BY id DESC LIMIT 1')
    row = c.fetchone()
    conn.close()
    station_name = row[0] if row else "Your Radio Station"
    host_name = row[1] if row else "Your Host"

    try:
        with open('aggregated_news.json', 'r') as f:
            news_data = json.load(f)
        top_stories = news_data.get('top_stories', [])
        aggregated_news = news_data.get('aggregated_news', {})
    except Exception as e:
        print(f"Error loading news: {e}")
        top_stories, aggregated_news = [], {}

    personalized_stories = []
    for topic, articles in aggregated_news.items():
        for article in articles:
            personalized_stories.append({
                'title': f"[{topic}] {article['title']}",
                'content': article['content'],
                'url': article['url']
            })

    trending_tracks = get_trending_tracks()
    throwback_tracks = get_throwback_tracks().get('tracks', [])
    new_artist_recs = get_new_artist_recommendations([])

    if not top_stories:
        top_stories = [{'title': 'Sample Top Story', 'content': 'Sample top story content'}]
    if not personalized_stories:
        personalized_stories = [{'title': 'Sample Personalized Story', 'content': 'Sample content'}]
    if not trending_tracks:
        trending_tracks = [{'artist': 'Sample Trending Artist', 'title': 'Sample Song', 'url': '#', 'duration': '180000'}]
    if not throwback_tracks:
        throwback_tracks = [{'artist': 'Sample Throwback Artist', 'title': 'Sample Song', 'url': '#', 'duration': '180000'}]
    if not new_artist_recs:
        new_artist_recs = [{'artist': 'Sample New Artist', 'top_tracks': [{'name': 'Sample Song', 'url': '#', 'duration': '180000'}]}]

    max_length = max(len(top_stories), len(personalized_stories),
                     len(trending_tracks), len(throwback_tracks), len(new_artist_recs))
    combined = []

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

    WORDS_PER_MINUTE = 150
    final_script = ""
    current_time_min = 0.0

    for item_type, item in combined:
        total_seconds = int(current_time_min * 60)
        minutes = total_seconds // 60
        seconds = total_seconds % 60
        timestamp_str = f"[{minutes:02d}:{seconds:02d}]"

        if item_type == 'news':
            prompt = f"""You are {host_name}, the host of {station_name}. Announce this news headline:
News: {item['title']} - {item.get('content', '')}
Be conversational and engaging, suitable for live radio."""
            try:
                response = model_longform.generate_content(prompt)
                script_text = response.text.strip() if hasattr(response, 'text') else ''
            except Exception as e:
                print(f"Gemini API error: {e}")
                script_text = f"[ERROR: Gemini API failed — {e}]"

            word_count = len(script_text.split())
            duration_min = word_count / WORDS_PER_MINUTE
            final_script += f"{timestamp_str} {script_text}\n\n"
            current_time_min += duration_min

        elif item_type == 'song':
            duration_ms = item.get('duration', '180000')
            try:
                duration_min = int(duration_ms) / 60000 if duration_ms else 3
            except:
                duration_min = 3

            # ✨ LLM artist/song intro
            intro_prompt = f"""You are {host_name}, the host of {station_name}. Give a 1-2 sentence fun introduction to this artist or song before it plays:
Artist: {item['artist']}
Song: {item['title']}
Be enthusiastic, light, and conversational."""

            try:
                intro_response = model.generate_content(intro_prompt)
                intro_text = intro_response.text.strip() if hasattr(intro_response, 'text') else ''
            except Exception as e:
                print(f"Gemini API error: {e}")
                intro_text = f"[ERROR: Gemini API failed on intro — {e}]"

            intro_word_count = len(intro_text.split())
            intro_duration_min = intro_word_count / WORDS_PER_MINUTE

            song_marker = f'<span style="color: #ff6600; font-weight: bold;">🎵 NOW PLAYING: {item["artist"]} — {item["title"]} 🎵</span>'

            final_script += f"{timestamp_str} {intro_text}\n\n"
            current_time_min += intro_duration_min

            # Recalculate timestamp after intro
            total_seconds = int(current_time_min * 60)
            minutes = total_seconds // 60
            seconds = total_seconds % 60
            song_timestamp = f"[{minutes:02d}:{seconds:02d}]"

            final_script += f"{song_timestamp} {song_marker}\n\n"
            current_time_min += duration_min

    try:
        with open('radio_script.txt', 'w') as f:
            f.write(final_script)
        print("✅ Script saved to radio_script.txt")
    except Exception as e:
        print(f"Error saving script: {e}")

    return redirect('/show_script')







@app.route('/show_script')
def show_script():
    try:
        with open('radio_script.txt', 'r') as f:
            script = f.read()
    except FileNotFoundError:
        script = "No script has been generated yet. Please click 'Generate Radio Host Script' first."
    return render_template('show_script.html', script=script)


if __name__ == '__main__':
    init_db()
    app.run(debug=True)
