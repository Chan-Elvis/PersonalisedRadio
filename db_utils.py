import sqlite3
import datetime

def get_liked_articles():
    conn = sqlite3.connect('user_profiles.db')
    c = conn.cursor()
    c.execute("""
        SELECT uuid FROM articles
        WHERE feedback IS NULL OR feedback = 'like'
        ORDER BY timestamp_fetched DESC LIMIT 15
    """)
    liked = [row[0] for row in c.fetchall()]
    conn.close()
    return liked

def get_disliked_articles():
    conn = sqlite3.connect('user_profiles.db')
    c = conn.cursor()
    c.execute("SELECT uuid FROM articles WHERE feedback = 'dislike'")
    disliked = [row[0] for row in c.fetchall()]
    conn.close()
    return disliked

def get_liked_songs():
    conn = sqlite3.connect('user_profiles.db')
    c = conn.cursor()
    c.execute("SELECT title, artist FROM songs WHERE feedback = 'like'")
    liked = c.fetchall()
    conn.close()
    return liked

def get_disliked_songs():
    conn = sqlite3.connect('user_profiles.db')
    c = conn.cursor()
    c.execute("SELECT title, artist FROM songs WHERE feedback = 'dislike'")
    disliked = c.fetchall()
    conn.close()
    return disliked

def store_song(track, source):
    conn = sqlite3.connect("user_profiles.db")
    c = conn.cursor()
    timestamp = datetime.datetime.utcnow().isoformat()
    try:
        c.execute("""
            INSERT OR IGNORE INTO songs (title, artist, url, source, timestamp_fetched)
            VALUES (?, ?, ?, ?, ?)
        """, (track['title'], track['artist'], track['url'], source, timestamp))
    except Exception as e:
        print(f"Failed to store song: {e}")
    conn.commit()
    conn.close()