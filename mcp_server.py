import asyncio
import sqlite3
import os
import json
import httpx
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from urllib.parse import urlparse
from mcp.server.models import InitializationOptions
import mcp.types as types
from mcp.server import Server, NotificationOptions
from mcp.server.stdio import stdio_server

# Create MCP Server
server = Server("financial-portfolio-mcp-server")

# Database setup
DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "db")
DB_PATH = os.path.join(DB_DIR, "database.sqlite")

def init_db():
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # Create holdings table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS holdings (
        ticker TEXT PRIMARY KEY,
        company_name TEXT,
        shares INTEGER,
        avg_cost REAL
    )
    """)
    # Create reports table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS risk_reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT,
        risk_level TEXT,
        analysis TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    # Create news cache table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS news_cache (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker TEXT,
        title TEXT,
        source TEXT,
        snippet TEXT,
        link TEXT,
        fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    # Populate default mock holdings if empty
    cursor.execute("SELECT COUNT(*) FROM holdings")
    if cursor.fetchone()[0] == 0:
        mock_holdings = [
            ("GOTO", "PT GoTo Gojek Tokopedia Tbk", 10000, 50.0),
            ("BBCA", "PT Bank Central Asia Tbk", 500, 9500.0),
            ("BMRI", "PT Bank Mandiri (Persero) Tbk", 1000, 6000.0),
            ("TLKM", "PT Telkom Indonesia (Persero) Tbk", 2000, 3800.0),
            ("ASII", "PT Astra International Tbk", 1500, 5000.0)
        ]
        cursor.executemany("INSERT INTO holdings VALUES (?, ?, ?, ?)", mock_holdings)
        conn.commit()
    conn.close()

# Initialize DB on load
init_db()

# Cache configuration
NEWS_CACHE_EXPIRY_HOURS = 2

async def fetch_news_from_rss(ticker: str) -> list[dict]:
    """Fetch real financial news from Google News RSS feed. No API key needed."""
    try:
        query = f"{ticker} saham"
        rss_url = f"https://news.google.com/rss/search?q={query}&hl=id&gl=ID&ceid=ID:id"
        
        async with httpx.AsyncClient(verify=False, timeout=15.0) as client:
            response = await client.get(rss_url)
            response.raise_for_status()
        
        soup = BeautifulSoup(response.text, "xml")
        items = soup.find_all("item", limit=5)
        
        articles = []
        for item in items:
            title = item.find("title").text if item.find("title") else ""
            link = item.find("link").text if item.find("link") else ""
            pub_date = item.find("pubDate").text if item.find("pubDate") else ""
            source_tag = item.find("source")
            source = source_tag.text if source_tag else urlparse(link).netloc.replace("www.", "")
            
            # Google News RSS doesn't have snippets, so we use the title as context
            description = item.find("description")
            snippet = ""
            if description:
                # Description contains HTML, extract text
                desc_soup = BeautifulSoup(description.text, "html.parser")
                snippet = desc_soup.get_text(strip=True)[:300]
            
            articles.append({
                "title": title,
                "source": source,
                "snippet": snippet or title,
                "link": link,
                "date": pub_date,
            })
        
        return articles
    except Exception as e:
        print(f"[Error] Google News RSS fetch failed: {e}")
        return []

@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    """Expose tools to the client."""
    return [
        types.Tool(
            name="get_portfolio_holdings",
            description="Retrieve the user's current stock portfolio holdings, including tickers, shares, and average costs.",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        types.Tool(
            name="fetch_financial_news",
            description="Fetch recent financial news articles, announcements, and sentiment for a specific stock ticker.",
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "Stock ticker symbol (e.g. AAPL, TSLA, NVDA)"
                    }
                },
                "required": ["ticker"]
            }
        ),
        types.Tool(
            name="save_risk_report",
            description="Save the finalized risk assessment report for a ticker in the portfolio database.",
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "Stock ticker symbol (e.g., NVDA)"
                    },
                    "risk_level": {
                        "type": "string",
                        "description": "Risk assessment level (Low, Medium, High)"
                    },
                    "analysis": {
                        "type": "string",
                        "description": "The detailed markdown risk assessment analysis report."
                    }
                },
                "required": ["ticker", "risk_level", "analysis"]
            }
        )
    ]

@server.call_tool()
async def handle_call_tool(
    name: str, arguments: dict | None
) -> list[types.TextContent]:
    """Execute the requested tool."""
    if name == "get_portfolio_holdings":
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT ticker, company_name, shares, avg_cost FROM holdings")
        rows = cursor.fetchall()
        conn.close()
        
        holdings = [
            {"ticker": r[0], "company": r[1], "shares": r[2], "avg_cost": r[3]}
            for r in rows
        ]
        return [types.TextContent(type="text", text=json.dumps(holdings, indent=2))]
        
    elif name == "fetch_financial_news":
        ticker = arguments.get("ticker", "").upper() if arguments else ""
        if not ticker:
            return [types.TextContent(type="text", text="Error: Missing 'ticker' argument.")]
        
        # 1. Check cache — return if fresh data exists
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cache_cutoff = (datetime.now() - timedelta(hours=NEWS_CACHE_EXPIRY_HOURS)).isoformat()
        cursor.execute(
            "SELECT title, source, snippet, link FROM news_cache WHERE ticker = ? AND fetched_at > ?",
            (ticker, cache_cutoff)
        )
        cached_rows = cursor.fetchall()
        conn.close()
        
        if cached_rows:
            print(f"[Cache HIT] Returning {len(cached_rows)} cached articles for {ticker}")
            articles = [
                {"title": r[0], "source": r[1], "snippet": r[2], "link": r[3]}
                for r in cached_rows
            ]
            return [types.TextContent(type="text", text=json.dumps(articles, indent=2))]
        
        # 2. Cache miss — fetch from Google News RSS
        print(f"[Cache MISS] Fetching live news for {ticker} from Google News RSS...")
        articles = await fetch_news_from_rss(ticker)
        
        # 3. If search failed, try returning stale cached data as fallback
        if not articles:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT title, source, snippet, link FROM news_cache WHERE ticker = ? ORDER BY fetched_at DESC LIMIT 5",
                (ticker,)
            )
            stale_rows = cursor.fetchall()
            conn.close()
            
            if stale_rows:
                print(f"[Fallback] Returning {len(stale_rows)} stale cached articles for {ticker}")
                articles = [
                    {"title": r[0], "source": r[1], "snippet": r[2], "link": r[3]}
                    for r in stale_rows
                ]
                return [types.TextContent(type="text", text=json.dumps(articles, indent=2))]
            
            # No cache, no search results
            return [types.TextContent(type="text", text=json.dumps([{
                "title": f"No recent news found for {ticker}",
                "source": "System",
                "snippet": f"Unable to fetch news for {ticker} at this time.",
                "link": ""
            }], indent=2))]
        
        # 4. Save fresh results to cache
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM news_cache WHERE ticker = ?", (ticker,))
        for article in articles:
            cursor.execute(
                "INSERT INTO news_cache (ticker, title, source, snippet, link) VALUES (?, ?, ?, ?, ?)",
                (ticker, article["title"], article["source"], article["snippet"], article.get("link", ""))
            )
        conn.commit()
        conn.close()
        print(f"[Cache SAVED] Stored {len(articles)} articles for {ticker}")
        
        return [types.TextContent(type="text", text=json.dumps(articles, indent=2))]
        
    elif name == "save_risk_report":
        if not arguments:
            return [types.TextContent(type="text", text="Error: Missing arguments.")]
            
        ticker = arguments.get("ticker", "").upper()
        risk_level = arguments.get("risk_level", "")
        analysis = arguments.get("analysis", "")
        
        if not ticker or not risk_level or not analysis:
            return [types.TextContent(type="text", text="Error: Missing required arguments (ticker, risk_level, analysis).")]
            
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO risk_reports (ticker, risk_level, analysis) VALUES (?, ?, ?)",
            (ticker, risk_level, analysis)
        )
        conn.commit()
        conn.close()
        
        return [types.TextContent(type="text", text=f"Successfully saved risk report for {ticker} (Risk: {risk_level}) to database.")]
        
    else:
        raise ValueError(f"Unknown tool: {name}")

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="financial-portfolio-mcp-server",
                server_version="1.0.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )

if __name__ == "__main__":
    asyncio.run(main())
