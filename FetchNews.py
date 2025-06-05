import requests
import json
import time
from datetime import datetime, timezone, timedelta
import sqlite3
import os
from dotenv import load_dotenv
import google.generativeai as genai
import urllib.parse
from db_utils import get_liked_articles

load_dotenv()

THENEWS_API_KEY = os.getenv("THENEWS_API_KEY")
model = genai.GenerativeModel('gemini-2.0-flash')

import trafilatura

def fetch_full_article(url):
    try:
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            return trafilatura.extract(downloaded, include_comments=False, include_tables=False)
    except Exception as e:
        print(f"❌ Error fetching full article from {url}: {e}")
    return None


def is_clickbait(title, content):
    clickbait_phrases = [
        "you won’t believe", "what happened next", "this one trick",
        "shocking truth", "top 10", "reasons why", "secret to", "goes viral",
        "click here", "read more", "insane", "mind-blowing"
    ]
    if len(content) < 100:
        return True
    title_lower = title.lower()
    if any(phrase in title_lower for phrase in clickbait_phrases):
        return True
    if sum(1 for w in title.split() if w.isupper()) > 3:
        return True
    return False

def llm_quality_check(title, content):
    prompt = f"""
You're a journalism quality assistant. Rate the following article as 'high-quality' or 'low-quality'. Only return one of those phrases.

Title: {title}
Content: {content}
"""
    try:
        response = model.generate_content(prompt)
        result = response.text.strip().lower()
        return result == "high-quality"
    except Exception as e:
        print(f"LLM check failed: {e}")
        return True

def should_store_article(title, content):
    if not is_clickbait(title, content):
        return True
    return llm_quality_check(title, content)

def fetch_similar_articles(uuids, region):
    similar_articles = []
    seen_titles = set()

    conn = sqlite3.connect('user_profiles.db')
    c = conn.cursor()
    c.execute("SELECT title FROM articles WHERE used = 1")
    seen_titles = set(row[0] for row in c.fetchall())
    conn.close()

    for uuid in uuids:
        url = f"https://api.thenewsapi.com/v1/news/similar/{uuid}"
        params = {"api_token": THENEWS_API_KEY, "locale": region}
        response = fetch_news(url, params)

        if response and response.get("data"):
            for article in extract_articles(response):
                if article["title"] not in seen_titles:
                    similar_articles.append(article)
        time.sleep(0.5)

    store_similar_articles_to_db(similar_articles)
    return similar_articles

def store_similar_articles_to_db(articles, source_prefix="similar_liked"):
    conn = sqlite3.connect("user_profiles.db")
    c = conn.cursor()
    timestamp = datetime.utcnow().isoformat()
    for article in articles:
        try:
            if should_store_article(article["title"], article["content"]):
                c.execute("""
                    INSERT OR IGNORE INTO articles (uuid, title, content, url, published_at, source, used, timestamp_fetched)
                    VALUES (?, ?, ?, ?, ?, ?, 0, ?)
                """, (
                    article.get("uuid"),
                    article.get("title"),
                    article.get("content"),
                    article.get("url"),
                    article.get("published_at"),
                    source_prefix,
                    timestamp
                ))
            else:
                print(f"❌ Rejected spammy article: {article['title']}")
        except Exception as e:
            print(f"❌ Failed to insert article: {e}")
    conn.commit()
    conn.close()

def suggest_alternative_topics(original_topic):
    prompt = f"""
The topic '{original_topic}' returned no recent news articles. Suggest 2 to 3 alternative but related news topics that might yield more results.
Respond as a comma-separated list, no explanation.
"""
    try:
        response = model.generate_content(prompt)
        if response and hasattr(response, 'text'):
            return [t.strip() for t in response.text.split(',')]
    except Exception as e:
        print(f"LLM error suggesting alternatives for '{original_topic}': {e}")
    return []

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
            "published_at": a.get("published_at", ""),
            "uuid": a.get("uuid", "")
        }
        for a in data["data"]
    ]

def fetch_top_stories(region):
    url = "https://api.thenewsapi.com/v1/news/top"
    one_week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).strftime('%Y-%m-%d')
    params = {
        "api_token": THENEWS_API_KEY,
        "locale": region,
        "language": "en",
        "published_after": one_week_ago
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

    _, topics, _, _, _, location, _, _, _, categories = user

    topics_list = [t.strip() for t in topics.split(',') if t.strip()]
    category_list = [c.strip() for c in categories.split(',') if c.strip()]
    REGION = location if location else "us"

    aggregated_news = {}

    liked_uuids = get_liked_articles()
    print(f"🧠 Fetching similar articles for liked UUIDs: {liked_uuids}")
    similar_articles = fetch_similar_articles(liked_uuids, REGION)
    aggregated_news["similar_liked"] = similar_articles

    for keyword in topics_list:
        print(f"🔎 Searching news for topic: {keyword}")
        params = {
            "api_token": THENEWS_API_KEY,
            "locale": REGION,
            "language": "en",
            "published_after": get_previous_month(),
            "search": f'"{keyword}"'
        }
        news_data = fetch_news("https://api.thenewsapi.com/v1/news/all", params)

        if news_data and news_data.get("data"):
            articles = extract_articles(news_data)
            aggregated_news[keyword.lower()] = [a for a in articles if should_store_article(a['title'], a['content'])]
        else:
            print(f"⚠️ No results for '{keyword}', asking LLM for similar topics...")
            alternatives = suggest_alternative_topics(keyword)
            found = False
            for alt in alternatives:
                retry_params = params.copy()
                retry_params["search"] = f'"{alt}"'
                retry_data = fetch_news("https://api.thenewsapi.com/v1/news/all", retry_params)
                if retry_data and retry_data.get("data"):
                    print(f"✅ Replaced '{keyword}' with alternative topic '{alt}'")
                    articles = extract_articles(retry_data)
                    aggregated_news[keyword.lower()] = [a for a in articles if should_store_article(a['title'], a['content'])]
                    found = True
                    break
            if not found:
                print(f"❌ No useful results for '{keyword}' or any suggested alternatives.")
        time.sleep(0.5)

    for cat in category_list:
        print(f"📰 Fetching news for category: {cat}")
        params = {
            "api_token": THENEWS_API_KEY,
            "locale": REGION,
            "language": "en",
            "published_after": get_previous_month(),
            "categories": cat
        }
        category_data = fetch_news("https://api.thenewsapi.com/v1/news/all", params)
        if category_data and category_data.get("data"):
            articles = extract_articles(category_data)
            aggregated_news[cat.lower()] = [a for a in articles if should_store_article(a['title'], a['content'])]
        else:
            print(f"❌ No results found for category '{cat}'")
        time.sleep(0.5)

    local_stories = fetch_top_stories(REGION)

    if not local_stories or "data" not in local_stories:
        print("Failed to fetch top stories.")
        return False

    # Filter top stories first
    filtered_top = [a for a in extract_articles(local_stories) if should_store_article(a['title'], a['content'])]

    # Filter each topic/category in aggregated_news
    filtered_aggregated = {}
    for key, articles in aggregated_news.items():
        filtered_aggregated[key] = [a for a in articles if should_store_article(a['title'], a['content'])]

    final_data = {
        "top_stories": filtered_top,
        "aggregated_news": filtered_aggregated
    }


    def store_articles_flat(aggregated_news):
        import sqlite3
        from datetime import datetime
        conn = sqlite3.connect("user_profiles.db")
        c = conn.cursor()
        timestamp = datetime.utcnow().isoformat()

        for source, article_list in aggregated_news.items():
            for a in article_list:
                try:
                    c.execute("""
                        INSERT OR IGNORE INTO articles (uuid, title, content, url, published_at, source, timestamp_fetched)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (
                        a.get("uuid"),
                        a.get("title"),
                        a.get("content"),
                        a.get("url"),
                        a.get("published_at"),
                        source,
                        timestamp
                    ))
                except Exception as e:
                    print(f"Failed to insert article: {e}")
        conn.commit()
        conn.close()

    store_articles_flat(final_data["aggregated_news"])
    store_articles_flat({"top": final_data["top_stories"]})

    print(f"📦 Saved {len(final_data['top_stories'])} top stories and {len(final_data['aggregated_news'])} topic groups.")
    print("✅ Saved aggregated news.")
    return True

if __name__ == "__main__":
    success = process_news()
    if not success:
        print("News fetch failed.")
