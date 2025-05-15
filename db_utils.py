import sqlite3

def get_liked_articles():
    conn = sqlite3.connect('user_profiles.db')
    c = conn.cursor()
    c.execute("SELECT uuid FROM covered_articles WHERE feedback = 'like'")
    liked = [row[0] for row in c.fetchall()]
    conn.close()
    return liked

def get_disliked_articles():
    conn = sqlite3.connect('user_profiles.db')
    c = conn.cursor()
    c.execute("SELECT uuid FROM covered_articles WHERE feedback = 'dislike'")
    disliked = [row[0] for row in c.fetchall()]
    conn.close()
    return disliked

def get_liked_songs():
    conn = sqlite3.connect('user_profiles.db')
    c = conn.cursor()
    c.execute("SELECT song_title, artist FROM played_songs WHERE feedback = 'like'")
    liked = c.fetchall()
    conn.close()
    return liked

def get_disliked_songs():
    conn = sqlite3.connect('user_profiles.db')
    c = conn.cursor()
    c.execute("SELECT song_title, artist FROM played_songs WHERE feedback = 'dislike'")
    disliked = c.fetchall()
    conn.close()
    return disliked
