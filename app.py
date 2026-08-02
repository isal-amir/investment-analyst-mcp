import os
import json
import uuid
import asyncio
import sqlite3
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from pydantic import BaseModel

# Import MCP Client and LangGraph agent graph
from mcp_client import MCPClientManager, get_langchain_mcp_tools
from agent_graph import create_agent_graph

# Load environment variables
load_dotenv()

# Initialize MCP Client Manager
mcp_client = MCPClientManager()

# Create Lifespan (sebuah fungsi u/ start dan stop process)
@asynccontextmanager # mengubah fungsi biasa mjd asynchronous context manager
async def lifespan(app: FastAPI):
    # Startup: Start MCP client subprocess
    print("Starting MCP Client and launching MCP Server...")
    await mcp_client.start()
    yield # penjeda antara start dan stop, akan lanjut (fungsi lifespan) setelah ctrl + 
    # Shutdown: Stop MCP client and subprocess
    print("Stopping MCP Client...")
    await mcp_client.stop()

# Create FastAPI instance
app = FastAPI(
    title="AI Agentic Portfolio Risk Analyzer",
    description="Multi-agent financial analyst using LangGraph and custom MCP Server",
    version="1.0.0",
    lifespan=lifespan
)

# Enable CORS Cross Origin Resource Sharing (satpam penjaga koneksi API transfer data)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # semua url boleh konek
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class Holding(BaseModel):
    ticker: str
    company_name: str
    shares: int
    avg_cost: float

# Endpoint: Retrieve holdings directly from the SQLite DB managed by the MCP server
@app.get("/api/holdings")
def get_holdings():
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "db", "database.sqlite")
    if not os.path.exists(db_path):
        return []
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT ticker, company_name, shares, avg_cost FROM holdings")
        rows = cursor.fetchall()
        conn.close()
        return [
            {"ticker": r[0], "company": r[1], "shares": r[2], "avg_cost": r[3]}
            for r in rows
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/holdings")
def add_holding(holding: Holding):
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "db", "database.sqlite")
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO holdings (ticker, company_name, shares, avg_cost) VALUES (?, ?, ?, ?)",
            (holding.ticker.upper(), holding.company_name, holding.shares, holding.avg_cost)
        )
        conn.commit()
        conn.close()
        return {"message": "Holding added successfully"}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Ticker already exists in holdings.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/holdings/{ticker}")
def update_holding(ticker: str, holding: Holding):
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "db", "database.sqlite")
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE holdings SET company_name = ?, shares = ?, avg_cost = ? WHERE ticker = ?",
            (holding.company_name, holding.shares, holding.avg_cost, ticker.upper())
        )
        if cursor.rowcount == 0:
            conn.close()
            raise HTTPException(status_code=404, detail="Holding not found.")
        conn.commit()
        conn.close()
        return {"message": "Holding updated successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/holdings/{ticker}")
def delete_holding(ticker: str):
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "db", "database.sqlite")
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM holdings WHERE ticker = ?", (ticker.upper(),))
        if cursor.rowcount == 0:
            conn.close()
            raise HTTPException(status_code=404, detail="Holding not found.")
        conn.commit()
        conn.close()
        return {"message": "Holding deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Endpoint: Retrieve saved risk assessment reports from SQLite
@app.get("/api/reports")
def get_reports():  # tidak menggunakan async karena justru akan mengganggu main thread -> def dilempar ke 'thread pool'
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "db", "database.sqlite")
    if not os.path.exists(db_path):
        return []
    try:
        conn = sqlite3.connect(db_path) # koneksi sync. ke DB, biasanya akan lama -> mengganggu thread
        cursor = conn.cursor()
        cursor.execute("SELECT id, ticker, risk_level, analysis, created_at FROM risk_reports ORDER BY created_at DESC")
        rows = cursor.fetchall()
        conn.close()
        return [
            {"id": r[0], "ticker": r[1], "risk_level": r[2], "analysis": r[3], "created_at": r[4]}
            for r in rows
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Endpoint: Stream LangGraph multi-agent execution events in real-time (SSE)
@app.post("/api/analyze/{ticker}")
async def analyze_ticker(ticker: str):
    ticker = ticker.strip().upper()
        
    async def event_generator():
        try:
            # Create LangGraph compile instance
            graph = create_agent_graph() # ini import-an dari agent_graph
            
            # Wrap the live MCP Client connection into LangChain Tools
            mcp_tools = get_langchain_mcp_tools(mcp_client) # ini import-an dari mcp_client
            
            # Thread/session configuration
            thread_id = str(uuid.uuid4())
            config = {  # Kotak alat untuk membawa tools dari server
                "configurable": {
                    "thread_id": thread_id,
                    "mcp_tools": mcp_tools
                }
            }
            
            # Initial state
            inputs = {
                "target_ticker": ticker,
                "revision_count": 0,
                "current_step": "Initializing Agents...",
                "messages": []
            }
            
            # Send initial event
            yield f"data: {json.dumps({'node': 'system', 'step': 'Starting workflow...', 'ticker': ticker})}\n\n"
            await asyncio.sleep(0.5)

            # Stream steps from LangGraph
            async for chunk in graph.astream(inputs, config, stream_mode="updates"): #tangkap setiap output stream asinkron (bentuk: dict)
                node_name = list(chunk.keys())[0] #ini untuk nangkap node mana yang sedang berjalan
                node_output = chunk[node_name] # ini untuk nangkap output nodenya
                
                current_step = node_output.get("current_step", node_name.capitalize()) #ini untuk nangkap current step
                # fallback ke kapital nama node kalau 'current_step' ga ada

                # Payload containing intermediate results
                event_data = { # data ini akan dikirim ke frontend dan akan disebar di tampilan
                    "node": node_name,
                    "step": current_step,
                    "ticker": ticker,
                    "risk_level": node_output.get("risk_level", ""),
                    "final_report": node_output.get("final_report", ""),
                    "feedback": node_output.get("feedback", ""),
                    "revision_count": node_output.get("revision_count", 0),
                    "portfolio_data": node_output.get("portfolio_data", ""),
                    "news_data": node_output.get("news_data", ""),
                    "analysis_notes": node_output.get("analysis_notes", ""),
                }
                # yield: seperti return tapi outputnya bertahap. dipanggil pake next()
                yield f"data: {json.dumps(event_data)}\n\n"
                await asyncio.sleep(0.5)  # Slight delay to ensure the UI animations feel smooth
                
        except Exception as e:
            import traceback
            error_trace = traceback.format_exc()
            print(f"Error executing agent graph: {e}\n{error_trace}")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

# Mount Static Files (Sleek UI)
static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
os.makedirs(static_dir, exist_ok=True)  # buat folder jk blm ada -> agar app tdk crash
app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    # Read port from env or default to 8000 (Cloud Run sets PORT env variable)
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True)
