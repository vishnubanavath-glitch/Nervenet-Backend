import os
import time
import logging
import asyncio
import sys
import json
from dotenv import load_dotenv
from openai import OpenAI, APIError
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

# Get the API key (using OPENAI_API from user's .env file, or fallback to OPENAI_API_KEY)
api_key = os.getenv("OPENAI_API") or os.getenv("OPENAI_API_KEY")

if api_key:
    # Log key loading status without exposing the key secret
    masked_key = f"{api_key[:8]}...{api_key[-8:]}" if len(api_key) > 16 else "***"
    logger.info(f"API Key loaded successfully: {masked_key}")
else:
    logger.warning("API Key not found in environment variables.")

# Default model if the caller doesn't specify one. GPT-5.2 is OpenAI's current
# flagship general-purpose model as of mid-2026; verify against your OpenAI
# dashboard / docs before relying on this for production, since model
# availability and naming can change.
DEFAULT_MODEL = "gpt-5.2"

# System Prompt guiding Nervenet on how to interact with the database tools
SYSTEM_PROMPT = """You are Nervenet, a professional AI customer support, database management, and data visualization specialist for the electricity meter department.
You have access to a database of customer records via custom tools for CRUD (Create, Read, Update, Delete) operations.

Here is how you should handle requests:
1. Query / Search / Read Field:
   - To find records by any field, filter by multiple criteria, or perform a general search, use the `query_customers` tool.
   - To understand what fields are available in the database, use the `get_database_schema` tool.
   - To get a complete customer record by their UID, use `get_customer` or search with `query_customers`.
2. Create:
   - To add a new customer, gather the details (especially a unique `uidNo`) and use the `create_customer` tool.
3. Update:
   - To modify details for a customer, identify their `uidNo` and the fields to change, then use the `update_customer` tool.
4. Delete:
   - To remove a customer record, use the `delete_customer` tool with the corresponding `uidNo`.

==================================================
DATA VISUALIZATION & SVG ENGINE RULES
==================================================
When requested to visualize data (e.g. charts, flow diagrams, status indicators, dashboards), generate valid, high-quality, self-contained SVG code directly inline within your response.

SVG Output Rules:
- Return raw SVG. Do NOT wrap the SVG inside markdown code fences (e.g., do not write ```xml or ```svg). It must be raw <svg>...</svg> so the Streamlit UI can render it.
- The root element must always be <svg> and completely self-contained.
- Never output HTML, Canvas, external CSS, external JS, or external fonts.

SVG Requirements & Styling:
- Clean styling inside a <style> block inside the SVG. Use transitions, keyframe animations, opacity, transforms, and gradients.
- Scalable and responsive using viewBox (avoid absolute pixel assumptions).
- Use semantic grouping with <g>, readable IDs/class names, and clean indentation.
- Interactivity: include hover effects, active/focus states, and animated chart loading.
- Tooltips: implement SVG-only tooltips using CSS hover on a `<g class="tooltip">` element containing `<rect>` and `<text>`. DO NOT use HTML or JavaScript for tooltips.
- Charts: include a title, axes, labels, legends, grid lines, and subtle staggered animations (draw, grow, fade in).
- Supported Visualizations: histogram, bar chart, line chart, area chart, scatter plot, pie/donut chart, radial chart, gauge, heatmap, timeline, flow diagram, sankey, network graph, tree diagram.
- Accessibility: include `<title>` and `<desc>` tags.

Ensure text has sufficient contrast. Return only the raw SVG for the visualization component.
==================================================

When presenting information, format it professionally, clearly, and concisely. Do not mention the names of the tools themselves in your final response to the user. Always verify UIDs and ensure changes are saved properly.
"""


def _mcp_tool_to_openai_tool(tool):
    """
    Converts an MCP tool definition into an OpenAI 'function' tool definition.
    MCP tools carry name/description/inputSchema; OpenAI wraps that schema
    under a function block with type: "function".
    """
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": tool.inputSchema or {"type": "object", "properties": {}},
        },
    }


def _get_text_from_content(content):
    """
    Extracts plain text from an OpenAI-style message content field, which may
    be a plain string or (in some message shapes) a list of content parts.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = [part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text"]
        return " ".join(texts)
    return ""


def decrypt_tool_args(args, privacy_engine):
    if not privacy_engine:
        return args
    if isinstance(args, dict):
        return {k: decrypt_tool_args(v, privacy_engine) for k, v in args.items()}
    elif isinstance(args, list):
        return [decrypt_tool_args(v, privacy_engine) for v in args]
    elif isinstance(args, str):
        return privacy_engine.token_to_value.get(args, args)
    return args

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

async def run_mcp_tool_loop(messages, privacy_engine, model):
    """
    Asynchronous runner that launches the MCP server as a subprocess,
    queries its tools, and manages the OpenAI API tool-calling loop.

    privacy_engine is expected to expose:
      - tokenize_text(text: str) -> str      (mask sensitive values before they leave the app)
      - detokenize_text(text: str) -> str    (restore real values for on-screen display)
      - token_to_value: dict                 (current token -> real value mapping, for the debug inspector)
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

            # Map tools to OpenAI format
            openai_tools = [_mcp_tool_to_openai_tool(tool) for tool in mcp_tools.tools]

            # Initialize OpenAI client
            client = OpenAI(api_key=api_key)

            # Identify the latest user message index
            latest_user_idx = -1
            for idx, msg in enumerate(messages):
                if msg.get("role") == "user":
                    latest_user_idx = idx

            # Tokenize user inputs in messages list in-place to ensure session history stays encrypted
            if privacy_engine:
                for idx, msg in enumerate(messages):
                    if msg.get("role") == "user":
                        content = msg.get("content")
                        if isinstance(content, str):
                            raw_content = privacy_engine.detokenize_text(content)
                            tokenized_content = privacy_engine.tokenize_text(raw_content)
                            
                            # Only log the tokenization for the latest user prompt of this turn
                            if idx == latest_user_idx:
                                logger.info("==================== STEP 1: USER PROMPT ====================")
                                logger.info(raw_content)
                                logger.info("==================== STEP 2: ENCRYPTED PROMPT (SENT TO LLM) ====================")
                                logger.info(tokenized_content)
                                logger.info("================================================================")
                            
                            msg["content"] = tokenized_content

            # Prepare conversation history in OpenAI's message format, starting
            # with the system prompt as the first message in the array (OpenAI
            # has no separate top-level "system" parameter the way Anthropic does).
            history = [{"role": "system", "content": SYSTEM_PROMPT}]

            for msg in messages:
                role = msg.get("role")
                content = msg.get("content")

                if role == "user":
                    text = _get_text_from_content(content)
                    history.append({"role": "user", "content": text})

                elif role == "assistant":
                    # Our own stored history uses Anthropic-style block lists for
                    # assistant turns (text / tool_use). Convert those into a
                    # single OpenAI assistant message with content + tool_calls.
                    if isinstance(content, str):
                        history.append({"role": "assistant", "content": content})
                    elif isinstance(content, list):
                        text_parts = []
                        tool_calls = []
                        for block in content:
                            if not isinstance(block, dict):
                                continue
                            if block.get("type") == "text":
                                text_parts.append(block.get("text", ""))
                            elif block.get("type") == "tool_use":
                                tool_calls.append({
                                    "id": block.get("id"),
                                    "type": "function",
                                    "function": {
                                        "name": block.get("name"),
                                        "arguments": json.dumps(block.get("input", {})),
                                    },
                                })
                        assistant_msg = {"role": "assistant", "content": "".join(text_parts) or None}
                        if tool_calls:
                            assistant_msg["tool_calls"] = tool_calls
                        history.append(assistant_msg)

            # Start loop
            max_turns = 10
            turn = 0

            # Keep track of metrics
            input_tokens = 0
            output_tokens = 0
            start_api_time = time.time()

            while turn < max_turns:
                turn += 1
                logger.info(f"Calling OpenAI API (Turn {turn}, Model: {model})...")
                logger.info("--- CONVERSATION HISTORY SENT TO LLM ---")
                for msg in history:
                    content_str = str(msg.get("content") or "")
                    if len(content_str) > 150:
                        content_str = content_str[:150] + " ... [TRUNCATED]"
                    logger.info(f"  [{msg.get('role').upper()}]: {content_str}")
                logger.info("----------------------------------------")

                # Setup call parameters
                params = {
                    "model": model,
                    "max_completion_tokens": 1024,
                    "messages": history,
                }
                if openai_tools:
                    params["tools"] = openai_tools

                response = client.chat.completions.create(**params)

                # Accumulate token metrics if available (OpenAI's usage field
                # names differ from Anthropic's: prompt_tokens / completion_tokens)
                if getattr(response, "usage", None):
                    input_tokens += response.usage.prompt_tokens
                    output_tokens += response.usage.completion_tokens

                choice = response.choices[0]
                message = choice.message

                response_text = message.content or ""
                if response_text:
                    logger.info("==================== DATA LLM GIVES BEFORE DECRYPT ====================")
                    logger.info(response_text)
                    logger.info("=======================================================================")

                response_blocks = []
                tool_calls = list(message.tool_calls or [])

                if response_text:
                    response_blocks.append({"type": "text", "text": response_text})

                for tool_call in tool_calls:
                    try:
                        tool_input = json.loads(tool_call.function.arguments) if tool_call.function.arguments else {}
                    except json.JSONDecodeError:
                        logger.error(f"Failed to parse arguments for tool '{tool_call.function.name}': {tool_call.function.arguments}")
                        tool_input = {}
                    
                    logger.info(f"DATA LLM GIVES BEFORE DECRYPT (Tool Call): '{tool_call.function.name}' with raw inputs {tool_input}")
                    response_blocks.append({
                        "type": "tool_use",
                        "id": tool_call.id,
                        "name": tool_call.function.name,
                        "input": tool_input,
                    })

                # Append assistant's response to history in native OpenAI shape
                assistant_history_msg = {"role": "assistant", "content": message.content}
                if tool_calls:
                    assistant_history_msg["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                        }
                        for tc in tool_calls
                    ]
                history.append(assistant_history_msg)

                # If no tool calls are requested, we have the final answer!
                if not tool_calls:
                    # Detokenize before handing the final text back to the UI layer
                    final_text = privacy_engine.detokenize_text(response_text) if privacy_engine else response_text

                    total_latency = time.time() - start_api_time
                    debug_info = {
                        "model": model,
                        "payload_sent": history,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "latency_seconds": round(total_latency, 3),
                        "status": "Success"
                    }
                    logger.info(f"Finished tool loop. Latency: {total_latency:.3f}s. Input tokens: {input_tokens}, Output tokens: {output_tokens}")

                    # Keep raw response blocks containing tokens in the updated history
                    updated_history = messages + [{"role": "assistant", "content": response_blocks}]
                    return final_text, debug_info, updated_history

                # Process tool calls
                tool_results = []
                for tool_call in tool_calls:
                    tool_name = tool_call.function.name
                    try:
                        tool_input = json.loads(tool_call.function.arguments) if tool_call.function.arguments else {}
                    except json.JSONDecodeError:
                        tool_input = {}

                    # Decrypt arguments before passing to MCP
                    decrypted_args = decrypt_tool_args(tool_input, privacy_engine)

                    logger.info(f"--- INCOMING MCP QUERY: Running '{tool_name}' with decrypted args: {decrypted_args} ---")
                    tool_start_time = time.time()

                    try:
                        mcp_result = await session.call_tool(tool_name, arguments=decrypted_args)

                        # Extract content from result
                        result_text = ""
                        for item in mcp_result.content:
                            if item.type == "text":
                                result_text += item.text

                        # Encrypt the result returned by MCP before sending to OpenAI
                        encrypted_result = encrypt_tool_result(result_text, privacy_engine)

                        logger.info("==================== DATA MCP PROVIDES TO LLM ====================")
                        logger.info(f"Tool '{tool_name}' returned (Tokenized): {encrypted_result}")
                        logger.info("==================================================================")

                        tool_results.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": encrypted_result,
                        })

                    except Exception as e:
                        logger.error(f"--- MCP ERROR: Tool '{tool_name}' failed: {str(e)} ---")
                        tool_results.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": f"Error calling tool: {str(e)}",
                        })

                # OpenAI expects each tool result as its own message with role "tool",
                # unlike Anthropic's single user-role message containing a list of
                # tool_result blocks.
                history.extend(tool_results)

            raise RuntimeError("Exceeded maximum tool-calling turns.")


def get_claude_response(messages, privacy_engine, model=DEFAULT_MODEL):
    """
    Sends conversation history to OpenAI, automatically invokes the local MCP server
    to resolve any tool calls, and returns the final response, metrics, and updated history.

    Kept the name get_claude_response for drop-in compatibility with app.py's existing
    `from claude import get_claude_response, api_key` style import. Rename both the
    import in app.py and this function if you'd rather it read get_openai_response.
    """
    if not api_key:
        logger.error("API call aborted: API key is missing.")
        raise ValueError("OpenAI API key is missing. Please set OPENAI_API in your .env file.")

    try:
        # Run the asynchronous loop synchronously using asyncio.run
        return asyncio.run(run_mcp_tool_loop(messages, privacy_engine, model))
    except Exception as e:
        logger.exception("Error in get_claude_response tool loop")
        raise