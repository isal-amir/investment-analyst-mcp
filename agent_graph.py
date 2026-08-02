import os
import json
from typing import Annotated, Sequence, TypedDict, List, Dict, Any, Literal
from pydantic import BaseModel, Field
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage, AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import StructuredTool
from langgraph.graph import StateGraph, END, START
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver

# Define the State Schema
class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    target_ticker: str
    portfolio_data: str
    news_data: str
    analysis_notes: str
    risk_level: str
    final_report: str
    feedback: str
    revision_count: int
    current_step: str  # UI status updates (e.g., "Researching", "Analyzing", "Drafting", "Reviewing")

# LLM Factory with Fallback
def get_llm():
    """Initializes the LLM. Prioritizes OpenRouter if configured, otherwise falls back to Vertex AI or standard Gemini."""
    # 1. OpenRouter (Development fallback / user requested)
    openrouter_api_key = os.environ.get("OPENROUTER_API_KEY")
    if openrouter_api_key:
        from langchain_openai import ChatOpenAI
        model_name = os.environ.get("OPENROUTER_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")
        return ChatOpenAI(
            model=model_name,
            api_key=openrouter_api_key,
            base_url="https://openrouter.ai/api/v1",
            temperature=0.1
        )

    # 2. Check if we should use Vertex AI (GCP) or standard Gemini API
    if os.environ.get("USE_VERTEX_AI", "false").lower() == "true":
        try:
            from langchain_google_vertexai import ChatVertexAI
            # This automatically picks up credentials when deployed on GCP or locally authenticated
            return ChatVertexAI(
                model_name="gemini-2.5-flash",
                temperature=0.1
            )
        except Exception as e:
            print(f"Vertex AI initialization failed: {e}. Falling back to standard Gemini API.")
            
    # 3. Fallback/Local Gemini API using langchain-google-genai
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("[Warning] GOOGLE_API_KEY not found in environment. Model calls will fail unless GCP credentials are active.")
    
    from langchain_google_genai import ChatGoogleGenerativeAI
    return ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        temperature=0.1
    )

# 1. Researcher Node
async def researcher_node(state: AgentState, config: RunnableConfig):
    """Gathers portfolio holdings and financial news for the target ticker."""
    llm = get_llm()
    mcp_tools = config["configurable"]["mcp_tools"]
    
    # We bind only the tools needed for research
    research_tools = [t for t in mcp_tools if t.name in ["get_portfolio_holdings", "fetch_financial_news"]]
    llm_with_tools = llm.bind_tools(research_tools)
    
    # Run the model to decide which tools to call
    prompt = [
        SystemMessage(content=(
            "You are the Financial Researcher Agent.\n"
            "Your objective is to collect current portfolio holdings and the latest financial news for the target stock ticker.\n"
            "1. Use 'get_portfolio_holdings' first to check if the user holds this ticker (and see their entry price).\n"
            "2. Use 'fetch_financial_news' to retrieve the latest articles for the stock.\n"
            "Once you have called both tools, summarize the findings and stop."
        )),
        HumanMessage(content=f"Perform research on ticker: {state['target_ticker']}")
    ]
    
    # Let's run a loop to handle potential sequential tool calls
    messages = list(prompt)
    portfolio_str = ""
    news_str = ""
    
    # We allow up to 3 turns of tool calling
    for _ in range(3):
        response = await llm_with_tools.ainvoke(messages)
        messages.append(response)
        
        if response.tool_calls:
            tool_messages = []
            for tc in response.tool_calls:
                # Find the corresponding tool, pakai next() cek satu2 pada object iterable, next() ga bisa di list biasa
                tool_fn = next((t for t in research_tools if t.name == tc["name"]), None) # pakai () -> generator expression
                if tool_fn:
                    tool_output = await tool_fn.ainvoke(tc["args"])
                    if tc["name"] == "get_portfolio_holdings":
                        portfolio_str = tool_output
                    elif tc["name"] == "fetch_financial_news":
                        news_str = tool_output
                    tool_messages.append(ToolMessage(  # ToolMessage: kurir yang bawa hasil tool ke LLM
                        content=str(tool_output),
                        name=tc["name"],
                        tool_call_id=tc["id"]
                    ))
            messages.extend(tool_messages)
        else: # jika datanya dirasa cukup oleh LLM, maka stop
            break
            
    # If the last response was a tool call, we need a final invocation to get the summary
    if messages and isinstance(messages[-1], ToolMessage):
        final_response = await llm.ainvoke(messages)
        summary_msg = final_response.content
    else:
        summary_msg = messages[-1].content
        
    if isinstance(summary_msg, list):
        texts = []
        for part in summary_msg:
            if isinstance(part, dict) and "text" in part:
                texts.append(part["text"])
            elif isinstance(part, str):
                texts.append(part)
        summary_msg = "\n".join(texts)
    if not isinstance(summary_msg, str):
        summary_msg = str(summary_msg)
        
    # return = memasukkan hasil tool ke dalam state
    return {
        "portfolio_data": portfolio_str or "No holding data found.",
        "news_data": news_str or "No news data found.",
        "messages": [AIMessage(content=f"Researcher completed. Summary:\n{summary_msg}")],
        "current_step": "Research Complete"
    }

# 2. Portfolio Analyst Node
async def analyst_node(state: AgentState):
    """Analyzes the correlation between the news sentiment and the user's portfolio exposure."""
    llm = get_llm()
    
    portfolio_data = state.get("portfolio_data", "")
    news_data = state.get("news_data", "")
    ticker = state.get("target_ticker", "")
    
    prompt = [
        SystemMessage(content=(
            "You are the Portfolio Analyst Agent.\n"
            "Analyze the market news and correlate it with the user's holdings database:\n"
            "1. Identify the holding details (shares, average cost) for the stock ticker.\n"
            "2. Assess whether the news articles represent a positive, negative, or neutral catalyst.\n"
            "3. Calculate the potential exposure (e.g., current value of holding vs. cost basis).\n"
            "4. Summarize the quantitative and qualitative impact on the user's portfolio."
        )),
        HumanMessage(content=(
            f"Ticker: {ticker}\n"
            f"Holdings Data:\n{portfolio_data}\n\n"
            f"Financial News Data:\n{news_data}"
        ))
    ]
    
    response = await llm.ainvoke(prompt)
    
    # return = memasukkan hasil tool ke dalam state
    return {
        "analysis_notes": response.content,
        "messages": [AIMessage(content=f"Analyst completed analysis:\n{response.content}")],
        "current_step": "Analysis Complete"
    }

# 3. Risk Manager / Report Writer Node
async def risk_manager_node(state: AgentState, config: RunnableConfig):
    """Drafts the risk assessment report and saves it to the portfolio database via MCP."""
    llm = get_llm()
    mcp_tools = config["configurable"]["mcp_tools"]
    save_tool = next((t for t in mcp_tools if t.name == "save_risk_report"), None)
    
    ticker = state.get("target_ticker", "")
    analysis_notes = state.get("analysis_notes", "")
    feedback = state.get("feedback", "")
    
    feedback_instruction = f"\nInclude corrections based on compliance feedback:\n{feedback}" if feedback else ""
    
    prompt = [
        SystemMessage(content=(
            "You are the Risk Manager Agent.\n"
            "Based on the analyst notes, draft a professional 'Portfolio Risk Assessment Report'.\n"
            "The report MUST include:\n"
            "- Executive Summary\n"
            "- Holdings Exposure Analysis\n"
            "- Risk Category Assessment (Low, Medium, or High)\n"
            "- Actionable Recommendation (e.g., Buy, Sell, Hold, Monitor)\n\n"
            "Format the report in clean Markdown."
        )),
        HumanMessage(content=f"Ticker: {ticker}\nAnalysis Notes:\n{analysis_notes}{feedback_instruction}")
    ]
    
    # We first ask the LLM to write the report and determine the risk level
    # We will bind the save_risk_report tool so it saves it
    llm_with_tools = llm.bind_tools([save_tool]) if save_tool else llm
    response = await llm_with_tools.ainvoke(prompt)
    
    report_content = response.content
    if isinstance(report_content, list):
        texts = []
        for part in report_content:
            if isinstance(part, dict) and "text" in part:
                texts.append(part["text"])
            elif isinstance(part, str):
                texts.append(part)
        report_content = "\n".join(texts)
    if not isinstance(report_content, str):
        report_content = str(report_content)
        
    tool_was_called = False
    if response.tool_calls and save_tool:
        tool_call = response.tool_calls[0]
        await save_tool.ainvoke(tool_call["args"])
        tool_was_called = True
        if not report_content.strip():
            report_content = tool_call["args"].get("analysis", "")
            
    risk_level = "Medium"  # Default
    if "high risk" in report_content.lower() or "risk: high" in report_content.lower():
        risk_level = "High"
    elif "low risk" in report_content.lower() or "risk: low" in report_content.lower():
        risk_level = "Low"
        
    if not tool_was_called and save_tool:
        await save_tool.ainvoke({
            "ticker": ticker,
            "risk_level": risk_level,
            "analysis": report_content
        })
            
    return {
        "risk_level": risk_level,
        "final_report": report_content,
        "messages": [AIMessage(content=f"Risk Manager generated report (Risk: {risk_level}) and saved to database.")],
        "current_step": "Draft Report Complete"
    }

# 4. Compliance Auditor Node (Reviewer)
async def auditor_node(state: AgentState):
    """Reviews the generated report. If it passes, it approves it. Otherwise, provides feedback."""
    llm = get_llm()
    
    report = state.get("final_report", "")
    revision_count = state.get("revision_count", 0)
    ticker = state.get("target_ticker", "")
    
    prompt = [
        SystemMessage(content=(
            "You are the Compliance Auditor Agent.\n"
            "Your job is to audit the Portfolio Risk Assessment Report for quality control.\n"
            "Verify that:\n"
            "1. The report clearly defines the risk category (Low, Medium, or High).\n"
            "2. It references the user's current shares/exposure details.\n"
            "3. The tone is professional, technical, and objective.\n\n"
            "If the report meets all criteria, output only the word: 'APPROVED'.\n"
            "If the report fails (e.g. lacks data, lacks clear risk rating), provide specific feedback on what is missing."
        )),
        HumanMessage(content=f"Report to review:\n{report}")
    ]
    
    response = await llm.ainvoke(prompt)
    result_text = response.content.strip()
    
    if "APPROVED" in result_text or revision_count >= 2:
        return {
            "feedback": "",
            "messages": [AIMessage(content="Compliance Auditor: Report approved.")],
            "current_step": "Approved"
        }
    else:
        return {
            "feedback": result_text,
            "revision_count": revision_count + 1,
            "messages": [AIMessage(content=f"Compliance Auditor: Report rejected. Feedback:\n{result_text}")],
            "current_step": "Revision Required"
        }

# Routing logic
def should_continue(state: AgentState) -> Literal["writer", "end"]:
    """Determines whether to end or send back to the writer for revision."""
    feedback = state.get("feedback", "")
    revision_count = state.get("revision_count", 0)
    
    if not feedback or revision_count >= 2:
        return "end"
    return "writer"

# Build the Graph
def create_agent_graph(): # this object will be imported by app.py
    workflow = StateGraph(AgentState)
    
    # Add nodes
    workflow.add_node("researcher", researcher_node)
    workflow.add_node("analyst", analyst_node)
    workflow.add_node("writer", risk_manager_node)
    workflow.add_node("auditor", auditor_node)
    
    # Add edges
    workflow.add_edge(START, "researcher")
    workflow.add_edge("researcher", "analyst")
    workflow.add_edge("analyst", "writer")
    workflow.add_edge("writer", "auditor")
    
    # Add conditional router from auditor
    workflow.add_conditional_edges(
        "auditor",
        should_continue,
        {
            "writer": "writer",
            "end": END
        }
    )
    
    # Checkpointer for conversational memory
    memory = MemorySaver()
    return workflow.compile(checkpointer=memory)
