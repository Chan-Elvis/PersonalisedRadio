import requests
import os
from dotenv import load_dotenv
import sqlite3

load_dotenv()
LASTFM_API_KEY = os.getenv("LASTFM_API_KEY")

import sqlite3

def get_similar_tracks(title, artist):
    url = f"http://ws.audioscrobbler.com/2.0/?method=track.getsimilar&artist={artist}&track={title}&api_key={LASTFM_API_KEY}&format=json"
    response = requests.get(url)
    
    # Fetch previously played song titles
    conn = sqlite3.connect("user_profiles.db", timeout=5.0)
    c = conn.cursor()
    c.execute("SELECT title, artist FROM songs WHERE feedback = 'like'")
    seen_titles = set(row[0] for row in c.fetchall())
    conn.close()

    if response.status_code == 200:
        try:
            data = response.json()
            tracks = data['similartracks']['track']
            filtered_tracks = [
                {'artist': t['artist']['name'], 'title': t['name'], 'url': t['url']}
                for t in tracks if t['name'] not in seen_titles
            ]
            return filtered_tracks[:3]
        except:
            return []
    return []

