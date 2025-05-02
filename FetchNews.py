import requests
import json
import time
from datetime import datetime, timezone, timedelta
import sqlite3
import google.generativeai as genai

import os
from dotenv import load_dotenv

load_dotenv()

THENEWS_API_KEY = os.getenv("THENEWS_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SECRET_KEY = os.getenv("SECRET_KEY")

# Configure Gemini API
genai.configure(api_key=GEMINI_API_KEY)

# API Key for TheNewsAPI

REGION = "us"  # Default region, you can later fetch this from user preferences if needed


def fetch_news(api_url, params):
    response = requests.get(api_url, params=params)
    if response.status_code == 200:
        return response.json()
    else:
        print(f"Error fetching data: {response.status_code}")
        return None


def get_previous_month():
    previous_month = (datetime.now(timezone.utc).replace(day=1) - timedelta(days=1)).strftime('%Y-%m')
    return previous_month


def check_spelling(keyword):
    prompt = f"Check the spelling of the keyword '{keyword}' and ONLY return the corrected single word. No explanation."
    model = genai.GenerativeModel('gemini-1.5-pro')
    response = model.generate_content(prompt)

    if response and hasattr(response, 'text'):
        corrected_text = response.text.strip()
        if ":" in corrected_text:
            corrected_text = corrected_text.split(":")[-1].strip()
        corrected_text = corrected_text.strip(' "\'.')
        return corrected_text
    return keyword


def suggest_related_keywords(keyword):
    prompt = f"Suggest broader or related keywords for '{keyword}'. Provide a comma-separated list."
    model = genai.GenerativeModel('gemini-1.5-pro')
    response = model.generate_content(prompt)

    if response and hasattr(response, 'text'):
        suggestions_text = response.text
        suggestions = suggestions_text.split(',')
        return [s.strip() for s in suggestions]
    return [keyword]


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
    # Fetch user preferences from the database
    conn = sqlite3.connect('user_profiles.db')
    c = conn.cursor()
    c.execute('SELECT * FROM profiles ORDER BY id DESC LIMIT 1')
    user = c.fetchone()
    conn.close()

    if not user:
        print("No user profile found.")
        return None

    _, topics, _, _, _, location = user  # Now unpack location too
    topics_list = [t.strip() for t in topics.split(',')]

    REGION = location if location else "us"  # fallback to 'us' if not set


    aggregated_news = {}

    # Fetch general top stories first
    top_stories = []
    top_stories_data = fetch_top_stories(REGION)
    if top_stories_data and "data" in top_stories_data:
        top_stories = [
            {
                "title": article.get("title", "No title"),
                "content": article.get("snippet", "No content available"),
                "url": article.get("url", ""),
                "published_at": article.get("published_at", datetime.now(timezone.utc).isoformat())
            }
            for article in top_stories_data["data"]
        ]

    # Fetch personalized news based on user topics
    for keyword in topics_list:
        corrected_keyword = check_spelling(keyword)
        aggregated_news[corrected_keyword] = []

        params = {
            "api_token": THENEWS_API_KEY,
            "locale": REGION,
            "language": "en",
            "published_after": get_previous_month(),
            "search": corrected_keyword
        }

        news_data = fetch_news("https://api.thenewsapi.com/v1/news/top", params)

        if not news_data or "data" not in news_data or len(news_data["data"]) < 3:
            print(f"Few or no results for '{corrected_keyword}'. Asking Gemini for better keywords.")
            alternative_keywords = suggest_related_keywords(corrected_keyword)
            for new_keyword in alternative_keywords:
                params["search"] = new_keyword
                news_data = fetch_news("https://api.thenewsapi.com/v1/news/top", params)
                if news_data and "data" in news_data and len(news_data["data"]) > 2:
                    break

        if news_data and "data" in news_data:
            for article in news_data["data"]:
                title = article.get("title", "No title")
                snippet = article.get("snippet", "No content available")
                url = article.get("url", "")
                published_at = article.get("published_at", datetime.now(timezone.utc).isoformat())

                aggregated_news[corrected_keyword].append({
                    "source": "TheNewsAPI",
                    "title": title,
                    "content": snippet,
                    "url": url,
                    "published_at": published_at,
                })

                time.sleep(0.5)  # Slight delay to avoid rate limits

    final_data = {
        "top_stories": top_stories,
        "aggregated_news": aggregated_news
    }

    if any(aggregated_news.values()) or top_stories:
        with open("aggregated_news.json", "w") as f:
            json.dump(final_data, f, indent=4)
        print("Aggregated news and top stories saved to aggregated_news.json")
    else:
        print("No news articles or top stories were retrieved. Check your API query.")

    return final_data


if __name__ == "__main__":
    process_news()
