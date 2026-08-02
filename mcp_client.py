import sys
import os
import json
import asyncio
from typing import Dict, Any, List
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

class TickerInput(BaseModel):
    ticker: str = Field(description="The stock ticker symbol, e.g. AAPL, TSLA, NVDA")

class SaveReportInput(BaseModel):
    ticker: str = Field(description="The stock ticker symbol, e.g. NVDA")
    risk_level: str = Field(description="Risk assessment level (Low, Medium, High)")
    analysis: str = Field(description="The detailed markdown risk assessment analysis report.")

class MCPClientManager:
    """Manages the lifecycle and tool calls for the custom stdio MCP Server."""
    def __init__(self):
        self.session = None
        self.exit_stack = None
        self.read_stream = None
        self.write_stream = None

    async def start(self):
        current_dir = os.path.dirname(os.path.abspath(__file__))
        server_path = os.path.join(current_dir, "mcp_server.py")
        
        # Ensure we run using the active virtual environment's python interpreter
        python_exe = sys.executable
        
        server_params = StdioServerParameters(
            command=python_exe,
            args=[server_path],
            env=os.environ.copy()
        )
        
        from contextlib import AsyncExitStack
        self.exit_stack = AsyncExitStack()
        
        # Connect to the stdio streams of the server subprocess
        self.read_stream, self.write_stream = await self.exit_stack.enter_async_context(
            stdio_client(server_params)
        )
        
        self.session = await self.exit_stack.enter_async_context(
            ClientSession(self.read_stream, self.write_stream)
        )
        
        # Complete the initialization handshake
        await self.session.initialize()
        
    async def stop(self):
        if self.exit_stack:
            await self.exit_stack.aclose()
            self.session = None
            self.exit_stack = None

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> str:
        if not self.session:
            raise RuntimeError("MCP session not started. Call start() first.")
        result = await self.session.call_tool(name, arguments)
        text_contents = [content.text for content in result.content if content.type == "text"]
        return "\n".join(text_contents)

def get_langchain_mcp_tools(client_manager: MCPClientManager) -> List[StructuredTool]:
    """Wraps MCP server tools into standard LangChain StructuredTools."""
    
    async def get_portfolio_holdings_tool() -> str:
        return await client_manager.call_tool("get_portfolio_holdings", {})
        
    async def fetch_financial_news_tool(ticker: str) -> str:
        return await client_manager.call_tool("fetch_financial_news", {"ticker": ticker})
        
    async def save_risk_report_tool(ticker: str, risk_level: str, analysis: str) -> str:
        return await client_manager.call_tool("save_risk_report", {
            "ticker": ticker,
            "risk_level": risk_level,
            "analysis": analysis
        })
        
    return [
        StructuredTool.from_function(
            name="get_portfolio_holdings",
            coroutine=get_portfolio_holdings_tool,
            description="Retrieve the user's current stock portfolio holdings (tickers, company names, shares, average costs). Takes no arguments.",
        ),
        StructuredTool.from_function(
            name="fetch_financial_news",
            coroutine=fetch_financial_news_tool,
            description="Fetch recent financial news articles and market sentiment for a specific stock ticker.",
            args_schema=TickerInput,
        ),
        StructuredTool.from_function(
            name="save_risk_report",
            coroutine=save_risk_report_tool,
            description="Save a finalized risk assessment report for a ticker in the portfolio database.",
            args_schema=SaveReportInput,
        )
    ]
