import asyncio
import os
import sys
import sqlite3
from dotenv import load_dotenv

# Load env variables (for local testing via GOOGLE_API_KEY or application default credentials)
load_dotenv()

# Set test environment if needed
os.environ.setdefault("USE_VERTEX_AI", "false")

async def test_workflow():
    print("=== STARTING AGENTIC MCP TEST ===")
    
    # 1. Import local files
    try:
        from mcp_client import MCPClientManager, get_langchain_mcp_tools
        from agent_graph import create_agent_graph
    except ImportError as e:
        print(f"Import Error: {e}. Make sure you are running this from the project root folder.")
        sys.exit(1)
        
    # 2. Check environment
    google_key = os.environ.get("GOOGLE_API_KEY")
    gcp_credentials = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    
    print(f"Environment Check:")
    print(f"  USE_VERTEX_AI: {os.environ.get('USE_VERTEX_AI')}")
    print(f"  GOOGLE_API_KEY: {'Found' if google_key else 'Not Found'}")
    print(f"  GOOGLE_APPLICATION_CREDENTIALS: {'Found' if gcp_credentials else 'Not Found'}")
    
    if not google_key and not gcp_credentials and os.environ.get("USE_VERTEX_AI") == "false":
        print("\n[Warning] Neither GOOGLE_API_KEY nor GCP Credentials are set.")
        print("Model execution will fail. Please set GOOGLE_API_KEY in a .env file or authenticate GCP.")
        print("=================================\n")
        
    # 3. Spin up MCP Server subprocess
    client_manager = MCPClientManager()
    print("Initializing local MCP Server via stdio...")
    try:
        await client_manager.start()
        print("MCP Server handshake completed successfully.")
    except Exception as e:
        print(f"Failed to start MCP Server: {e}")
        return

    try:
        # 4. Wrap MCP Tools
        print("Wrapping MCP tools for LangChain...")
        mcp_tools = get_langchain_mcp_tools(client_manager)
        print(f"Tools wrapped: {[t.name for t in mcp_tools]}")
        
        # 5. Compile LangGraph
        print("Compiling LangGraph StateGraph workflow...")
        graph = create_agent_graph()
        
        # 6. Execute Graph for NVDA (Nvidia)
        ticker = "NVDA"
        inputs = {
            "target_ticker": ticker,
            "revision_count": 0,
            "current_step": "Starting...",
            "messages": []
        }
        
        config = {
            "configurable": {
                "thread_id": "test-thread-1",
                "mcp_tools": mcp_tools
            }
        }
        
        print(f"\nRunning Multi-Agent analysis on stock: {ticker}")
        print("-" * 50)
        
        async for chunk in graph.astream(inputs, config, stream_mode="updates"):
            node_name = list(chunk.keys())[0]
            node_output = chunk[node_name]
            print(f"\n[Node: {node_name}] -> Status: {node_output.get('current_step')}")
            
            if "portfolio_data" in node_output and node_output["portfolio_data"]:
                print(f"  * Portfolio Data loaded: {node_output['portfolio_data']}")
            if "news_data" in node_output and node_output["news_data"]:
                print(f"  * News Data loaded: {len(node_output['news_data'])} characters")
            if "risk_level" in node_output and node_output["risk_level"]:
                print(f"  * Identified Risk: {node_output['risk_level']}")
            if "feedback" in node_output and node_output["feedback"]:
                print(f"  * Compliance Auditor Feedback: {node_output['feedback']}")
        
        print("-" * 50)
        print("\nWorkflow completed. Verifying database updates...")
        
        # 7. Query Database to verify save_risk_report was called
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "db", "database.sqlite")
        if os.path.exists(db_path):
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT ticker, risk_level, created_at FROM risk_reports ORDER BY created_at DESC LIMIT 1")
            row = cursor.fetchone()
            conn.close()
            if row:
                print(f"Database Verification: SUCCESS")
                print(f"  Saved report found for: {row[0]}")
                print(f"  Risk rating saved: {row[1]}")
                print(f"  Timestamp: {row[2]}")
            else:
                print("Database Verification: FAILED (No report row found)")
        else:
            print("Database Verification: FAILED (Database file not created)")
            
    except Exception as e:
        print(f"Error during workflow execution: {e}")
        import traceback
        traceback.print_exc()
        
    finally:
        # 8. Clean up subprocesses
        print("\nStopping MCP Server subprocess...")
        await client_manager.stop()
        print("=== TEST COMPLETED ===")

if __name__ == "__main__":
    asyncio.run(test_workflow())
