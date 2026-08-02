import asyncio
import sqlite3
import os
import json
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
    
    # Populate default mock holdings if empty
    cursor.execute("SELECT COUNT(*) FROM holdings")
    if cursor.fetchone()[0] == 0:
        mock_holdings = [
            ("AAPL", "Apple Inc.", 50, 175.50),
            ("TSLA", "Tesla Inc.", 20, 220.00),
            ("NVDA", "NVIDIA Corporation", 100, 450.25),
            ("MSFT", "Microsoft Corporation", 30, 380.00),
            ("AMZN", "Amazon.com Inc.", 40, 145.10)
        ]
        cursor.executemany("INSERT INTO holdings VALUES (?, ?, ?, ?)", mock_holdings)
        conn.commit()
    conn.close()

# Initialize DB on load
init_db()

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
            
        # Realistic mock data containing clear signals for agents to analyze
        news_data = {
            "AAPL": [
                {
                    "title": "Apple launches new iPhone with integrated edge AI capabilities",
                    "source": "TechCrunch",
                    "summary": "Apple announced its latest iPhone line with an upgraded neural engine, enabling running smaller LLMs directly on-device. Analysts predict high upgrade cycles.",
                    "sentiment": "Positive"
                },
                {
                    "title": "Antitrust regulations tighten in Europe for Apple App Store",
                    "source": "Bloomberg",
                    "summary": "The European Union is launching a new investigation into Apple's alternative app marketplace fees, potentially impacting services revenue growth.",
                    "sentiment": "Negative"
                }
            ],
            "TSLA": [
                {
                    "title": "Tesla Q2 deliveries beat Wall Street expectations",
                    "source": "Reuters",
                    "summary": "Tesla reported deliveries of 443,956 vehicles in Q2, beating the average analyst estimate of 439,000. Operating margins show stabilization.",
                    "sentiment": "Positive"
                },
                {
                    "title": "Gigafactory Berlin faces temporary environmental audit halts",
                    "source": "Der Spiegel",
                    "summary": "Tesla's expansion plans in Germany face delay as local authorities mandate additional water preservation studies. Expected delay of 3 months.",
                    "sentiment": "Negative"
                }
            ],
            "NVDA": [
                {
                    "title": "NVIDIA unveils next-gen Blackwell Ultra AI GPUs",
                    "source": "VentureBeat",
                    "summary": "CEO Jensen Huang presented the Blackwell Ultra chip family, showcasing a 30% reduction in energy usage and 2.5x throughput for LLM training compared to Hopper.",
                    "sentiment": "Positive"
                },
                {
                    "title": "US government discusses further restrictions on AI chip exports",
                    "source": "Wall Street Journal",
                    "summary": "New export controls are being drafted which could restrict slightly lower-powered chips from being exported to certain regions, representing a potential 8% headwind on Nvidia's data center revenue.",
                    "sentiment": "Negative"
                }
            ],
            "MSFT": [
                {
                    "title": "Microsoft Copilot active users grow by 60% quarter-over-quarter",
                    "source": "CNBC",
                    "summary": "Microsoft CEO Satya Nadella announced substantial enterprise seat expansions for Office 365 Copilot, cementing Microsoft's lead in monetizing generative AI.",
                    "sentiment": "Positive"
                },
                {
                    "title": "OpenAI governance changes create uncertainty for Microsoft partnership",
                    "source": "The Verge",
                    "summary": "Recent discussions about OpenAI restructuring into a for-profit entity raise questions regarding Microsoft's long-term access to proprietary IP.",
                    "sentiment": "Neutral"
                }
            ],
            "AMZN": [
                {
                    "title": "AWS launches low-cost custom AI chips to compete with Nvidia",
                    "source": "TechRadar",
                    "summary": "Amazon Web Services announces Trainium2 instances are now generally available, offering up to 40% better price-performance for training models, attracting major startups.",
                    "sentiment": "Positive"
                },
                {
                    "title": "Consumer retail spending shows slight cooling in core sectors",
                    "source": "Financial Times",
                    "summary": "US retail sales grew by only 0.1% last month, indicating that high interest rates are starting to curb consumer spending on non-essential goods.",
                    "sentiment": "Negative"
                }
            ]
        }
        
        articles = news_data.get(ticker, [
            {
                "title": f"Market updates for {ticker}",
                "source": "Financial News",
                "summary": f"Stock ticker {ticker} shows stable trading volumes today. Analysts remain neutral pending next quarter's earnings report.",
                "sentiment": "Neutral"
            }
        ])
        
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
