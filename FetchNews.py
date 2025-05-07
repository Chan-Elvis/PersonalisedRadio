import requests
import json
import time
from datetime import datetime, timezone, timedelta
import sqlite3
import os
from dotenv import load_dotenv

load_dotenv()
THENEWS_API_KEY = os.getenv("THENEWS_API_KEY")

def fetch_news(api_url, params):
    response = requests.get(api_url, params=params)
    if response.status_code == 200:
        return response.json()
    else:
        print(f"Error fetching data: {response.status_code}")
        return None

def get_previous_month():
    return (datetime.now(timezone.utc).replace(day=1) - timedelta(days=1)).strftime('%Y-%m')

def suggest_related_keywords(keyword):
    # Keep this minimal or connect to Gemini if needed later
    return [keyword, f"{keyword} news", f"latest {keyword}"]

def fetch_top_stories(region):
    url = "https://api.thenewsapi.com/v1/news/top"
    params = {"api_token": THENEWS_API_KEY, "locale": region, "language": "en", "published_after": get_previous_month()}
    return fetch_news(url, params)

def fetch_global_stories():
    return fetch_top_stories("xx")  # "xx" means international/global headlines in TheNewsAPI


def process_news():
    conn = sqlite3.connect('user_profiles.db')
    c = conn.cursor()
    c.execute('SELECT * FROM profiles ORDER BY id DESC LIMIT 1')
    user = c.fetchone()
    conn.close()

    if not user:
        print("No user profile found.")
        return

    _, topics, _, _, _, location = user
    topics_list = [t.strip() for t in topics.split(',')]
    region = location or 'us'

    aggregated_news = {}
    top_stories_data = fetch_top_stories(region)
    top_stories = [{"title": a.get("title", ""), "content": a.get("snippet", ""), "url": a.get("url", ""),
                    "published_at": a.get("published_at", datetime.now(timezone.utc).isoformat())}
                   for a in top_stories_data.get("data", [])] if top_stories_data else []

    for topic in topics_list:
        params = {"api_token": THENEWS_API_KEY, "locale": region, "language": "en",
                  "published_after": get_previous_month(), "search": topic}
        news_data = fetch_news("https://api.thenewsapi.com/v1/news/top", params)
        if news_data and news_data.get("data"):
            aggregated_news[topic] = [{"source": "TheNewsAPI", "title": a.get("title", ""), "content": a.get("snippet", ""),
                                       "url": a.get("url", ""), "published_at": a.get("published_at", datetime.now(timezone.utc).isoformat())}
                                      for a in news_data["data"]]
        time.sleep(0.5)

    with open("aggregated_news.json", "w") as f:
        json.dump({"top_stories": top_stories, "aggregated_news": aggregated_news}, f, indent=4)
    print("News saved.")

if __name__ == "__main__":
    process_news()
