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
    try:
        response = requests.get(api_url, params=params)
        if response.status_code == 200:
            return response.json()
        else:
            print(f"Error fetching data: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        print(f"Exception during API call: {e}")
        return None

def get_previous_month():
    today = datetime.now(timezone.utc)
    first_of_this_month = today.replace(day=1)
    last_month = first_of_this_month - timedelta(days=1)
    return last_month.strftime('%Y-%m-%d')

def extract_articles(data):
    if not data or "data" not in data:
        return []
    return [
        {
            "title": a.get("title", "No title"),
            "content": a.get("snippet", "No content"),
            "url": a.get("url", ""),
            "published_at": a.get("published_at", "")
        }
        for a in data["data"]
    ]

def fetch_top_stories(region):
    url = "https://api.thenewsapi.com/v1/news/top"
    params = {
        "api_token": THENEWS_API_KEY,
        "locale": region,
        "language": "en",
        "published_after": get_previous_month()
    }
    return fetch_news(url, params)

def process_news():
    conn = sqlite3.connect('user_profiles.db')
    c = conn.cursor()
    c.execute('SELECT * FROM profiles ORDER BY id DESC LIMIT 1')
    user = c.fetchone()
    conn.close()

    if not user:
        print("No user profile found.")
        return False

    _, topics, _, _, _, location = user
    topics_list = [t.strip() for t in topics.split(',')]

    REGION = location if location else "us"

    aggregated_news = {}
    for keyword in topics_list:
        params = {
            "api_token": THENEWS_API_KEY,
            "locale": REGION,
            "language": "en",
            "published_after": get_previous_month(),
            "search": keyword
        }
        news_data = fetch_news("https://api.thenewsapi.com/v1/news/top", params)
        if news_data and "data" in news_data:
            aggregated_news[keyword] = extract_articles(news_data)
        time.sleep(0.5)

    local_stories = fetch_top_stories(REGION)

    if not local_stories or "data" not in local_stories:
        print("Failed to fetch top stories.")
        return False

    final_data = {
        "top_stories": extract_articles(local_stories),
        "aggregated_news": aggregated_news
    }

    with open("aggregated_news.json", "w") as f:
        json.dump(final_data, f, indent=4)
    print("Saved aggregated news.")

    return True

if __name__ == "__main__":
    success = process_news()
    if not success:
        print("News fetch failed.")
