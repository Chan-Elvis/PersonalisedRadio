from flask import Flask, render_template, request, redirect, flash
import sqlite3
import json
import random
import subprocess
from FetchNews import check_spelling 

import os
from dotenv import load_dotenv

load_dotenv()

NEWS_API_KEY = os.getenv("NEWS_API_KEY")
MUSIC_API_KEY = os.getenv("MUSIC_API_KEY")
SECRET_KEY = os.getenv("SECRET_KEY")

app = Flask(__name__)
app.secret_key = SECRET_KEY

def init_db():
    conn = sqlite3.connect('user_profiles.db')
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topics TEXT,
            music_tastes TEXT,
            favorite_artists TEXT,
            news_mood TEXT
        )
    """)
    conn.commit()
    conn.close()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/save', methods=['POST'])
def save():
    topics = request.form.getlist('topics')
    music_tastes = request.form.getlist('music_tastes')
    favorite_artists = request.form['favorite_artists']
    news_mood = request.form['news_mood']
    location = request.form['location']  # NEW

    corrected_topics = [check_spelling(topic.strip()) for topic in topics]
    corrected_music_tastes = [check_spelling(genre.strip()) for genre in music_tastes]

    topics_str = ",".join(corrected_topics)
    music_tastes_str = ",".join(corrected_music_tastes)

    conn = sqlite3.connect('user_profiles.db')
    c = conn.cursor()
    c.execute('INSERT INTO profiles (topics, music_tastes, favorite_artists, news_mood, location) VALUES (?, ?, ?, ?, ?)', 
              (topics_str, music_tastes_str, favorite_artists, news_mood, location))
    conn.commit()
    conn.close()

    flash(f"Saved! Corrected Topics: {topics_str} | Corrected Music Tastes: {music_tastes_str}")

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

        # Build grouped personalized news
        grouped_news = {}
        for topic in topics_list:
            topic_lower = topic.lower()
            if topic_lower in news_data.get("aggregated_news", {}):
                grouped_news[topic] = news_data["aggregated_news"][topic_lower]

        # Fetch top stories
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
        return "No user profile found. Please setup your profile first."

@app.route('/refresh_news')
def refresh_news():
    subprocess.run(["python", "FetchNews.py"])
    return redirect('/radio')

if __name__ == '__main__':
    init_db()
    app.run(debug=True)