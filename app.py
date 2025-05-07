from flask import Flask, render_template, request, redirect, flash
import sqlite3
import json
import subprocess
import os
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

NEWS_API_KEY = os.getenv("NEWS_API_KEY")
MUSIC_API_KEY = os.getenv("MUSIC_API_KEY")
SECRET_KEY = os.getenv("SECRET_KEY")

app = Flask(__name__)
app.secret_key = SECRET_KEY

# ✅ Local Gemini artist validator
def validate_artist_with_llm(artist_name):
    model = genai.GenerativeModel('gemini-2.0-flash-lite')
    prompt = (
        f"Is '{artist_name}' the name of a real, known music artist or band? "
        "If yes, return the corrected name only. If no, return 'invalid'. "
        "No explanation, just the word or name."
    )
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
            location TEXT
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
        music_tastes_list = [m.strip() for m in music_tastes.split(',')]
    else:
        topics_list = []
        music_tastes_list = []
        favorite_artists = ""
        news_mood = ""
        location = ""

    return render_template('index.html',
                           topics=topics_list,
                           music_tastes=music_tastes_list,
                           favorite_artists=favorite_artists,
                           news_mood=news_mood,
                           location=location)

@app.route('/save', methods=['POST'])
def save():
    model = genai.GenerativeModel('gemini-2.0-flash-lite')

    raw_topics = [t.strip() for t in request.form['topics'].split(',') if t.strip()]
    raw_music_tastes = [g.strip() for g in request.form['music_tastes'].split(',') if g.strip()]
    raw_artists = [a.strip() for a in request.form['favorite_artists'].split(',') if a.strip()]
    news_mood = request.form['news_mood']
    location = request.form['location']

    corrections_made = []
    all_valid = True

    def correct_list_with_llm(prompt_items, category_label):
        corrected = []
        local_corrections = []
        prompt = (
            f"For each of these {category_label}: {', '.join(prompt_items)}.\n"
            "If the item is already valid, return it exactly as given, no changes.\n"
            "Only correct if obviously misspelled.\n"
            "If invalid, nonsense, or unrelated, return 'invalid'.\n"
            "Respond as a comma-separated list, same length and order, no explanation."
        )

        try:
            response = model.generate_content(prompt)
            if response and hasattr(response, 'text'):
                corrected_list = [t.strip() for t in response.text.strip().split(',')]
                if len(corrected_list) < len(prompt_items):
                    flash(f"⚠️ LLM returned fewer {category_label} than expected. Processing only matched items.", "warning")
                for i in range(min(len(prompt_items), len(corrected_list))):
                    orig = prompt_items[i]
                    new = corrected_list[i]
                    if new.lower() == "invalid":
                        flash(f"{category_label[:-1].capitalize()} '{orig}' is invalid and was removed.", "warning")
                        nonlocal all_valid
                        all_valid = False
                    else:
                        if orig.lower() != new.lower():
                            local_corrections.append(f"{category_label[:-1].capitalize()} '{orig}' → '{new}'")
                        corrected.append(new)
                return corrected, local_corrections
        except Exception as e:
            flash(f"Error validating {category_label}: {e}", "error")
        return prompt_items, []

    # ✅ Process topics
    corrected_topics, topic_corrections = correct_list_with_llm(raw_topics, "topics")
    corrections_made.extend(topic_corrections)

    # ✅ Process music genres
    corrected_music_tastes, genre_corrections = correct_list_with_llm(raw_music_tastes, "music genres")
    corrections_made.extend(genre_corrections)

    # ✅ Process artists (individual checks)
    validated_artists = []
    for artist in raw_artists:
        corrected_artist = validate_artist_with_llm(artist)
        if corrected_artist:
            if corrected_artist.lower() != artist.lower():
                corrections_made.append(f"Artist '{artist}' → '{corrected_artist}'")
            validated_artists.append(corrected_artist)
        else:
            flash(f"Artist '{artist}' is invalid and was removed.", "warning")
            all_valid = False

    if not all_valid:
        flash("Some invalid entries were removed. Please review and resubmit.", "warning")
        return render_template('index.html',
                               topics=corrected_topics,
                               music_tastes=corrected_music_tastes,
                               favorite_artists=', '.join(validated_artists),
                               location=location,
                               news_mood=news_mood)

    if corrections_made:
        flash("Corrections made: " + "; ".join(corrections_made), "info")
    else:
        flash("All entries were valid. Preferences saved successfully!", "success")

    topics_str = ",".join(corrected_topics)
    music_tastes_str = ",".join(corrected_music_tastes)
    favorite_artists_str = ",".join(validated_artists)

    conn = sqlite3.connect('user_profiles.db')
    c = conn.cursor()
    c.execute('INSERT INTO profiles (topics, music_tastes, favorite_artists, news_mood, location) VALUES (?, ?, ?, ?, ?)',
              (topics_str, music_tastes_str, favorite_artists_str, news_mood, location))
    conn.commit()
    conn.close()

    return redirect('/radio')

@app.route('/radio')
def radio():
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
        music_tastes_list = [m.strip() for m in music_tastes.split(',')]
        favorite_artists_list = [a.strip() for a in favorite_artists.split(',')]

        grouped_news = {}
        for topic in topics_list:
            topic_lower = topic.lower()
            if topic_lower in news_data.get("aggregated_news", {}):
                grouped_news[topic] = news_data["aggregated_news"][topic_lower]

        top_stories = news_data.get("top_stories", [])

        return render_template('radio.html',
                               grouped_news=grouped_news,
                               top_stories=top_stories,
                               music_tastes=music_tastes_list,
                               favorite_artists=favorite_artists_list,
                               topics=topics_list,
                               news_mood=news_mood,
                               location=location)
    else:
        return "No user profile found. Please set up your profile first."

@app.route('/refresh_news')
def refresh_news():
    subprocess.run(["python", "FetchNews.py"])
    return redirect('/radio')

if __name__ == '__main__':
    init_db()
    app.run(debug=True)
