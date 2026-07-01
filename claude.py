import os
import time
import logging
import asyncio
import sys
import json
from dotenv import load_dotenv
import anthropic
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from privacy_engine import PrivacyEngine

# Configure standard python logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("NervenetClient")

# Load environment variables from .env file
load_dotenv()

# Get the API key (using CLAUDE_API from user's .env file, or fallback to ANTHROPIC_API_KEY)
api_key = os.getenv("CLAUDE_API") or os.getenv("ANTHROPIC_API_KEY")

if api_key:
    # Log key loading status without exposing the key secret
    masked_key = f"{api_key[:8]}...{api_key[-8:]}" if len(api_key) > 16 else "***"
    logger.info(f"API Key loaded successfully: {masked_key}")
else:
    logger.warning("API Key not found in environment variables.")

# System Prompt guiding Nervenet on how to interact with the database tools
SYSTEM_PROMPT = """You are Nervenet, a professional AI customer support, database management, and data visualization specialist for the electricity meter department.

Your primary interface to retrieve and manipulate data is through the database query engine MCP server. Do not assume any customer information; always use the query engine tools to verify details.

==================================================
PRIVACY & TOKEN PRESERVATION RULES (CRITICAL):
All sensitive customer data (such as UIDs and mobile numbers) returned by tools is encrypted/tokenized in the format `<//PREFIX-UUID//>` (e.g. `<//UID-4bdf4b468e55//>`).
* You MUST print and return these tokens EXACTLY as you receive them.
* Do NOT strip, modify, or format the delimiters `<//` and `//>` under any circumstances (never write them as `UID-4bdf4b468e55`).
* They must remain exactly as `<//UID-4bdf4b468e55//>` in all parts of your text responses and JSON blocks so the client engine can decrypt them locally for the user.

==================================================
MCP DATABASE ENGINE TOOL RULES:
1. Minimizing Payload Size (CRITICAL):
   - Only request the specific columns you need to answer the user's question by passing the `columns` list parameter.
   - For example: if a user asks for "mobile number for uid 123", query with columns=["mobileNo"] and filter by uidNo=123.
   - Never retrieve more records than necessary. Use the `limit` parameter to paginate or restrict results.
2. Data Aggregations:
   - For queries asking for calculations (e.g. "maximum step count", "average duration by subdivision", "total rows"), NEVER retrieve raw data to calculate locally.
   - ALWAYS delegate these mathematical calculations directly to the `aggregate` tool, passing the appropriate `operation` and `column`.
3. Discovering Schema:
   - Call `discover_schema` first if you need to know which columns exist and which are searchable or aggregatable.
4. Searches:
   - Use `search` if you are trying to match records containing free-text keywords across multiple fields.
5. Mutations:
   - Use `create` (requires unique uidNo), `update` (using uid), and `delete` (using uid) for modifications.
6. Statistics:
   - Use the `statistics` tool to get high-level metadata such as missing values count, duplicate records count, and row/col counts.
7. Visualizations:
   - Call `prepare_chart_data` to perform all filtering, grouping, aggregation, Top-N sorting, and limit operations on the server.
   - Use the returned structured JSON dataset as the data source to render the final SVG chart code in your response.

==================================================
SVG CHART DECOUPLED GENERATION RULES:
When requested to visualize data (e.g. charts, flow diagrams, status indicators, dashboards), call `prepare_chart_data` first to obtain the optimized, aggregated dataset from the server.
Then, you MUST output the returned JSON dataset wrapped inside `<chart_data>` and `</chart_data>` tags exactly as returned.
Do NOT try to write any SVG, HTML, or raw XML code yourself. The client UI will intercept this tag and pass it to a dedicated SVG chart generation engine.

Example format:
<chart_data>{"type": "bar", "title": "...", "x_axis": "...", "y_axis": "...", "data": [...]}</chart_data>

You MUST speak in a professional, courteous, and concise manner.
"""

def decrypt_tool_args(tool_input, privacy_engine):
    if not privacy_engine or not tool_input:
        return tool_input
    decrypted = {}
    for k, v in tool_input.items():
        if k == "filters" and isinstance(v, list):
            decrypted_filters = []
            for f in v:
                if isinstance(f, dict) and "value" in f:
                    val_str = str(f["value"])
                    decrypted_val = privacy_engine.detokenize_text(val_str)
                    # Convert back to numeric if it was originally a digit
                    if decrypted_val.isdigit():
                        decrypted_val = int(decrypted_val)
                    else:
                        try:
                            decrypted_val = float(decrypted_val)
                        except ValueError:
                            pass
                    
                    new_f = f.copy()
                    new_f["value"] = decrypted_val
                    decrypted_filters.append(new_f)
                else:
                    decrypted_filters.append(f)
            decrypted["filters"] = decrypted_filters
        elif k == "uid" or k == "uidNo":
            decrypted[k] = privacy_engine.detokenize_text(str(v))
        elif k == "record" and isinstance(v, dict):
            decrypted_record = {}
            for rk, rv in v.items():
                if rk in ["uidNo", "mobileNo"]:
                    decrypted_record[rk] = privacy_engine.detokenize_text(str(rv))
                else:
                    decrypted_record[rk] = rv
            decrypted["record"] = decrypted_record
        elif k == "updates" and isinstance(v, dict):
            decrypted_updates = {}
            for rk, rv in v.items():
                if rk in ["uidNo", "mobileNo"]:
                    decrypted_updates[rk] = privacy_engine.detokenize_text(str(rv))
                else:
                    decrypted_updates[rk] = rv
            decrypted["updates"] = decrypted_updates
        else:
            decrypted[k] = v
    return decrypted

def encrypt_tool_result(result_text, privacy_engine):
    if not privacy_engine:
        return result_text
    try:
        try:
            data = json.loads(result_text)
        except json.JSONDecodeError:
            import ast
            data = ast.literal_eval(result_text)
            
        if isinstance(data, list):
            encrypted_data = [privacy_engine.encrypt_record(r) for r in data]
        elif isinstance(data, dict):
            encrypted_data = privacy_engine.encrypt_record(data)
        else:
            encrypted_data = data
        return json.dumps(encrypted_data)
    except Exception:
        return privacy_engine.tokenize_text(result_text)

def _mcp_tool_to_anthropic_tool(tool):
    return {
        "name": tool.name,
        "description": tool.description or "",
        "input_schema": tool.inputSchema
    }

async def run_mcp_tool_loop(messages, model, privacy_engine):
    """
    Asynchronous runner that launches the MCP server as a subprocess,
    queries its tools, and manages the Anthropic API tool-calling loop.
    """
    python_cmd = sys.executable or "python"
    
    # Configure parameters to launch our server
    server_params = StdioServerParameters(
        command=python_cmd,
        args=["mcp_server/server.py"],
        env=os.environ.copy()
    )
    
    logger.info("Connecting to MCP server subprocess...")
    start_mcp_time = time.time()
    
    # Establish connection to the server
    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            # Initialize the session
            await session.initialize()
            logger.info(f"MCP server initialized in {time.time() - start_mcp_time:.3f}s.")
            
            # Fetch tools
            mcp_tools = await session.list_tools()
            logger.info(f"Fetched {len(mcp_tools.tools)} tools from MCP server.")
            
            # Map tools to Anthropic format
            anthropic_tools = [_mcp_tool_to_anthropic_tool(tool) for tool in mcp_tools.tools]
            
            # Setup ANTHROPIC_API_KEY environment variable for the client
            if api_key:
                os.environ["ANTHROPIC_API_KEY"] = api_key
            
            client = anthropic.Anthropic()
            
            # Identify the latest user message index
            latest_user_idx = -1
            for idx, msg in enumerate(messages):
                if msg.get("role") == "user":
                    latest_user_idx = idx

            # Log step 1 and step 2 prompts, performing tokenization if privacy_engine is present
            for idx, msg in enumerate(messages):
                if msg.get("role") == "user":
                    content = msg.get("content")
                    if isinstance(content, str):
                        raw_content = privacy_engine.detokenize_text(content) if privacy_engine else content
                        tokenized_content = privacy_engine.tokenize_text(raw_content) if privacy_engine else content
                        
                        # Only log the tokenization for the latest user prompt of this turn
                        if idx == latest_user_idx:
                            logger.info("==================== STEP 1: USER PROMPT ====================")
                            logger.info(raw_content)
                            logger.info("==================== STEP 2: PROMPT (SENT TO LLM) ====================")
                            logger.info(tokenized_content)
                            logger.info("================================================================")
                        
                        msg["content"] = tokenized_content

            # Prepare conversation history (cleaning structure and anonymizing for Anthropic payload)
            history = []
            for msg in messages:
                history.append({
                    "role": msg["role"],
                    "content": msg["content"]
                })
            
            input_tokens = 0
            output_tokens = 0
            max_turns = 10
            turn = 0
            
            while turn < max_turns:
                turn += 1
                logger.info(f"Calling Anthropic API (Turn {turn}, Model: {model})...")
                logger.info("--- CONVERSATION HISTORY SENT TO LLM ---")
                for msg in history:
                    content_str = str(msg.get("content") or "")
                    if len(content_str) > 150:
                        content_str = content_str[:150] + " ... [TRUNCATED]"
                    logger.info(f"  [{msg.get('role').upper()}]: {content_str}")
                logger.info("----------------------------------------")
                
                # Setup Anthropic parameters
                params = {
                    "model": model,
                    "max_tokens": 1024,
                    "messages": history,
                    "system": SYSTEM_PROMPT
                }
                if anthropic_tools:
                    params["tools"] = anthropic_tools
                
                response = client.messages.create(**params)
                
                # Accumulate and log token metrics
                if hasattr(response, "usage") and response.usage:
                    input_tokens += response.usage.input_tokens
                    output_tokens += response.usage.output_tokens
                    logger.info(f"--- Turn {turn} Token Metrics: Input: {response.usage.input_tokens}, Output: {response.usage.output_tokens} (Cumulative Input: {input_tokens}, Cumulative Output: {output_tokens}) ---")
                
                response_text = ""
                response_blocks = []
                tool_calls = []
                
                # Parse content blocks
                for block in response.content:
                    if block.type == "text":
                        response_text += block.text
                        response_blocks.append({
                            "type": "text",
                            "text": block.text
                        })
                    elif block.type == "tool_use":
                        tool_calls.append(block)
                        response_blocks.append({
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input
                        })

                # Log the raw responses before decryption
                if response_text:
                    logger.info("==================== DATA LLM GIVES BEFORE DECRYPT ====================")
                    logger.info(response_text)
                    logger.info("=======================================================================")
                for block in response.content:
                    if block.type == "tool_use":
                        logger.info(f"DATA LLM GIVES BEFORE DECRYPT (Tool Call): '{block.name}' with raw inputs {block.input}")
                
                # Append assistant's response to history
                history.append({
                    "role": "assistant",
                    "content": response_blocks
                })
                
                # If no tool calls are requested, we have the final answer!
                if not tool_calls:
                    # Keep the final response tokenized/encrypted so that SVG generation can process it securely.
                    # The client app (app.py) will decrypt it locally just-in-time before rendering.
                    final_text = response_text
                    logger.info(f"Finished tool loop. Latency: {time.time() - start_mcp_time:.3f}s. Input tokens: {input_tokens}, Output tokens: {output_tokens}")
                    return final_text, {
                        "model": model,
                        "payload_sent": history,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "latency_seconds": round(time.time() - start_mcp_time, 3),
                        "status": "Success"
                    }, history
                
                # Process tool calls
                tool_results = []
                for tool in tool_calls:
                    decrypted_args = decrypt_tool_args(tool.input, privacy_engine)
                    logger.info(f"--- INCOMING MCP QUERY: Running '{tool.name}' with decrypted args: {decrypted_args} ---")
                    
                    try:
                        mcp_result = await session.call_tool(tool.name, arguments=decrypted_args)
                        
                        # Extract content from result
                        result_text = ""
                        for item in mcp_result.content:
                            if item.type == "text":
                                result_text += item.text
                        
                        # Encrypt the result returned by MCP before sending to Claude
                        encrypted_result = encrypt_tool_result(result_text, privacy_engine)
                                
                        logger.info("==================== DATA MCP PROVIDES TO LLM (BEFORE SENDING TO LLM) ====================")
                        logger.info(f"Tool '{tool.name}' returned (Tokenized): {encrypted_result}")
                        logger.info("==========================================================================================")
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tool.id,
                            "content": encrypted_result
                        })
                        
                    except Exception as e:
                        logger.error(f"--- MCP ERROR: Tool '{tool.name}' failed: {str(e)} ---")
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tool.id,
                            "content": f"Error calling tool: {str(e)}",
                            "is_error": True
                        })
                
                # Append tool result messages back to history in Anthropic structure
                history.append({
                    "role": "user",
                    "content": tool_results
                })
                
            raise RuntimeError("Exceeded maximum tool-calling turns.")

def get_claude_response(messages, privacy_engine, model="claude-opus-4-8"):
    """
    Sends conversation history to Claude, automatically invokes the local MCP server
    to resolve any tool calls, and returns the final response, metrics, and updated history.
    """
    if not api_key:
        logger.error("API call aborted: API key is missing.")
        raise ValueError("Anthropic API key is missing. Please set CLAUDE_API in your .env file.")
        
    try:
        # Run the asynchronous loop synchronously using asyncio.run
        return asyncio.run(run_mcp_tool_loop(messages, model, privacy_engine))
    except Exception as e:
        logger.exception("Error in get_claude_response tool loop")
        # Unpack the exception group to get the actual underlying error (e.g. AuthenticationError)
        def unpack_exception(err):
            if hasattr(err, "exceptions") and err.exceptions:
                return unpack_exception(err.exceptions[0])
            return err
        raise unpack_exception(e)
