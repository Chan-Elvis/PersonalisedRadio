import sqlite3
import datetime

def get_liked_articles():
    conn = sqlite3.connect('user_profiles.db')
    c = conn.cursor()
    c.execute("""
        SELECT uuid, source FROM articles
        WHERE feedback = 'like' AND (similar_fetched IS NULL OR similar_fetched = 0)
    """)
    liked = c.fetchall()
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
    c.execute("""
        SELECT title, artist FROM songs
        WHERE feedback = 'like' AND (similar_fetched IS NULL OR similar_fetched = 0)
    """)
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
    conn = sqlite3.connect("user_profiles.db", timeout=5.0)
    c = conn.cursor()

    title = track['title']
    artist = track['artist']
    url = track['url']
    now = datetime.datetime.utcnow()

    c.execute("""
        SELECT used, timestamp_fetched FROM songs
        WHERE title = ? AND artist = ?
    """, (title, artist))
    row = c.fetchone()

    if row:
        used, fetched_str = row
        fetched_at = datetime.datetime.fromisoformat(fetched_str)
        age_seconds = (now - fetched_at).total_seconds()

        if used == 1:
            if age_seconds < 3600:  # less than 1 hour
                print(f"⏳ Skipping {title} by {artist} (played recently)")
                conn.close()
                return
            else:
                # ✅ More than an hour ago — mark as reusable
                print(f"♻️ Re-marking {title} by {artist} as unused (was used >1hr ago)")
                c.execute("""
                    UPDATE songs
                    SET used = 0, timestamp_fetched = ?, source = ?
                    WHERE title = ? AND artist = ?
                """, (now.isoformat(), source, title, artist))
        else:
            # ✅ Exists and unused — do nothing
            print(f"✅ Song {title} by {artist} already in DB and unused.")
    else:
        # ✅ Not in DB — insert new song
        print(f"🎵 Adding new song: {title} by {artist}")
        c.execute("""
            INSERT INTO songs (title, artist, url, source, used, timestamp_fetched)
            VALUES (?, ?, ?, ?, 0, ?)
        """, (title, artist, url, source, now.isoformat()))

    conn.commit()
    conn.close()


def mark_song_similar_fetched(title, artist):
    conn = sqlite3.connect("user_profiles.db", timeout=5.0)
    c = conn.cursor()
    c.execute("""
        UPDATE songs SET similar_fetched = 1
        WHERE title = ? AND artist = ?
    """, (title, artist))
    conn.commit()
    conn.close()

def mark_article_similar_fetched(uuid):
    conn = sqlite3.connect("user_profiles.db", timeout=5.0)
    c = conn.cursor()
    c.execute("UPDATE articles SET similar_fetched = 1 WHERE uuid = ?", (uuid,))
    conn.commit()
    conn.close()
