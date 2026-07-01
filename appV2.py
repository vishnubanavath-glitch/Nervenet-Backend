import streamlit as st
import uuid
import re
import logging
import streamlit.components.v1 as components
from chatGpt import get_claude_response, api_key
from privacy_engine import PrivacyEngine
import svg_engine

logger = logging.getLogger("NervenetClient")

# Page setup for a premium feel matching the custom Nervenet branding
st.set_page_config(
    page_title="Nervenet",
    page_icon="💬",
    layout="centered",
)

# Custom CSS written by a UI designer for premium light/dark adaptability and spacious elegance
st.markdown("""
<style>
    :root {
        --font-main: 'Outfit', sans-serif;
        --border-subtle: 1px solid rgba(128, 128, 128, 0.15);
    }

    /* Target the app container directly */
    .stApp { font-family: var(--font-main); }

    /* Fix the sidebar layout without fragile selectors */
    [data-testid="stSidebar"] { padding-top: 1rem; }
    
    /* Elegant Chat Messages */
    [data-testid="stChatMessage"] {
        padding: 1.5rem 0;
        border-bottom: var(--border-subtle);
        background: transparent;
    }
    
    /* Fix Chat Input - Use flex-box logic */
    .stChatInputContainer {
        border-radius: 12px;
        box-shadow: 0 4px 20px rgba(0,0,0,0.05);
    }

    /* Better Button UX */
    div.stButton > button {
        border-radius: 8px;
        transition: all 0.2s;
    }
</style>
""", unsafe_allow_html=True)


def _extract_viewbox_ratio(svg_str: str):
    """
    Returns (width, height) numbers parsed from the viewBox attribute, if present.
    Nervenet's system prompt instructs the model to always use viewBox (not fixed
    pixel width/height), so this is the primary signal for sizing the SVG.
    Falls back to explicit width/height attributes, then to None if nothing usable.
    """
    vb_match = re.search(r'viewBox=["\']\s*([\-\d.]+)\s+([\-\d.]+)\s+([\-\d.]+)\s+([\-\d.]+)\s*["\']', svg_str, re.IGNORECASE)
    if vb_match:
        try:
            min_x, min_y, vb_w, vb_h = (float(g) for g in vb_match.groups())
            if vb_w > 0 and vb_h > 0:
                return vb_w, vb_h
        except ValueError:
            pass

    # Fallback: explicit width/height attributes on the <svg> tag (numeric only, ignore % or other units)
    w_match = re.search(r'\bwidth=["\']\s*([\d.]+)\s*(?:px)?["\']', svg_str, re.IGNORECASE)
    h_match = re.search(r'\bheight=["\']\s*([\d.]+)\s*(?:px)?["\']', svg_str, re.IGNORECASE)
    if w_match and h_match:
        try:
            w, h = float(w_match.group(1)), float(h_match.group(1))
            if w > 0 and h > 0:
                return w, h
        except ValueError:
            pass

    return None


def render_svg_block(svg_str: str):
    """
    Renders a single self-contained SVG (which may include <style> animations,
    keyframes, hover tooltips, etc. per the visualization system prompt) inside
    an auto-sizing iframe so animated/interactive content never gets clipped
    or left with dead whitespace.
    """
    # Reasonable initial guess for height before the live-resize script runs,
    # based on the SVG's own aspect ratio rendered at a fixed nominal width.
    NOMINAL_WIDTH = 700
    ratio = _extract_viewbox_ratio(svg_str)
    if ratio:
        vb_w, vb_h = ratio
        initial_height = int(NOMINAL_WIDTH * (vb_h / vb_w))
        # Keep the initial guess within a sane range so a weird aspect ratio
        # doesn't flash a tiny sliver or a huge blank panel before resize fires.
        initial_height = max(200, min(initial_height, 900))
    else:
        initial_height = 450

    # The wrapper script measures the actual rendered SVG height after layout/paint
    # and asks the Streamlit host frame to resize to fit -- this is what correctly
    # handles CSS keyframe animations, hover-expanding tooltips, and any mismatch
    # between the viewBox guess and real rendered size.
    html_content = f"""
    <div id="nervenet-svg-wrap" style="display: flex; justify-content: center; align-items: flex-start; width: 100%; overflow: visible;">
        {svg_str}
    </div>
    <script>
        function nervenetResize() {{
            try {{
                const wrap = document.getElementById('nervenet-svg-wrap');
                if (!wrap) return;
                const rect = wrap.getBoundingClientRect();
                const height = Math.ceil(rect.height) + 24;
                if (window.Streamlit && height > 0) {{
                    window.Streamlit.setFrameHeight(height);
                }}
            }} catch (e) {{ /* no-op: best-effort resize */ }}
        }}
        // Run after initial paint, then again shortly after to catch animation-driven layout shifts
        window.addEventListener('load', nervenetResize);
        nervenetResize();
        setTimeout(nervenetResize, 150);
        setTimeout(nervenetResize, 600);
        new ResizeObserver(nervenetResize).observe(document.getElementById('nervenet-svg-wrap'));
    </script>
    """
    components.html(html_content, height=initial_height, scrolling=False)


def render_assistant_response(text: str, debug_info=None):
    # Split text into text parts and <chart_data> parts (keep it encrypted upfront)
    pattern = re.compile(r'(<chart_data>.*?</chart_data>)', re.DOTALL | re.IGNORECASE)
    parts = pattern.split(text)

    svg_metrics_list = []

    for part in parts:
        part_stripped = part.strip()
        if part_stripped.lower().startswith('<chart_data>') and part_stripped.lower().endswith('</chart_data>'):
            # Extract the encrypted JSON content
            json_str = part_stripped[12:-13].strip()
            # Call SVG generation engine in separate model run, passing the ENCRYPTED data
            with st.spinner("Generating SVG Visualization..."):
                try:
                    svg_code, svg_metrics = svg_engine.generate_svg_chart(json_str)
                    svg_metrics_list.append(svg_metrics)
                    
                    # Decrypt the generated SVG chart code locally before rendering
                    if "privacy_engine" in st.session_state:
                        svg_code = st.session_state.privacy_engine.detokenize_text(svg_code)
                        
                    # Log the decrypted SVG code directly in the terminal
                    logger.info("==================== GENERATED SVG CHART CODE (DECRYPTED) ====================")
                    logger.info(svg_code)
                    logger.info("=============================================================================")
                    
                    render_svg_block(svg_code)
                except Exception as e:
                    st.error(f"Failed to generate visualization: {str(e)}")
        else:
            if part_stripped:
                # Decrypt conversational text parts before rendering
                text_to_show = part
                if "privacy_engine" in st.session_state:
                    text_to_show = st.session_state.privacy_engine.detokenize_text(text_to_show)
                    
                # Fallback: also check for any inline raw SVGs if any (for backward compatibility)
                svg_pattern = re.compile(r'(<svg.*?>.*?</svg>)', re.DOTALL | re.IGNORECASE)
                subparts = svg_pattern.split(text_to_show)
                for subpart in subparts:
                    subpart_stripped = subpart.strip()
                    if subpart_stripped.lower().startswith('<svg') and subpart_stripped.lower().endswith('</svg>'):
                        # Log fallback SVG as well
                        logger.info("==================== FALLBACK SVG CHART CODE ====================")
                        logger.info(subpart_stripped)
                        logger.info("================================================================")
                        render_svg_block(subpart_stripped)
                    else:
                        if subpart_stripped:
                            st.markdown(subpart, unsafe_allow_html=True)

    # Log total session token usage at the very end
    if debug_info:
        main_input = debug_info.get("input_tokens", 0)
        main_output = debug_info.get("output_tokens", 0)
        
        svg_input = sum(m.get("input_tokens", 0) for m in svg_metrics_list)
        svg_output = sum(m.get("output_tokens", 0) for m in svg_metrics_list)
        
        total_input = main_input + svg_input
        total_output = main_output + svg_output
        
        logger.info("==================== CUMULATIVE TOKEN METRICS ====================")
        logger.info(f"Main LLM:   Input: {main_input:<6} | Output: {main_output:<6}")
        if svg_metrics_list:
            logger.info(f"SVG Engine: Input: {svg_input:<6} | Output: {svg_output:<6}")
            logger.info(f"Combined:   Input: {total_input:<6} | Output: {total_output:<6}")
        logger.info("==================================================================")

# Initialize session state variables
if "sessions" not in st.session_state:
    st.session_state.sessions = {}
if "privacy_engine" not in st.session_state:
    st.session_state.privacy_engine = PrivacyEngine()

# Ensure at least one session exists
if "current_session_id" not in st.session_state or st.session_state.current_session_id not in st.session_state.sessions:
    new_id = str(uuid.uuid4())
    st.session_state.sessions[new_id] = {
        "title": "New Chat",
        "messages": [],
        "last_debug_info": None
    }
    st.session_state.current_session_id = new_id

# Helper references for current session
current_id = st.session_state.current_session_id
current_session = st.session_state.sessions[current_id]
messages = current_session["messages"]

# Left Panel (Sidebar) - Brand name, New Chat button, and Recent Chats list
with st.sidebar:
    # Logo / Title
    st.markdown("""
    <div style="padding: 10px 4px 15px 4px;">
        <span style="font-family: 'Lora', 'Georgia', serif; font-size: 1.45rem; font-weight: 500; letter-spacing: -0.5px;">Nervenet</span>
    </div>
    """, unsafe_allow_html=True)
    
    # "+ New chat" Button
    if st.button("+ New chat", type="primary", use_container_width=True):
        new_id = str(uuid.uuid4())
        st.session_state.sessions[new_id] = {
            "title": "New Chat",
            "messages": [],
            "last_debug_info": None
        }
        st.session_state.current_session_id = new_id
        st.rerun()

    st.markdown("---")
    
    # Recent Chats Header
    st.markdown("""
    <div style="padding: 0px 12px 10px 12px; color: #7a7a7a; font-size: 0.75rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;">
        Recent Chats
    </div>
    """, unsafe_allow_html=True)
    
    # List all chat sessions
    for sid, sess in list(st.session_state.sessions.items()):
        is_active = (sid == current_id)
        display_title = sess["title"]
        
        # Display select & delete options side by side
        col_btn, col_del = st.columns([0.88, 0.12])
        with col_btn:
            # Active session is primary, inactive is secondary
            if st.button(
                display_title,
                key=f"select_{sid}",
                use_container_width=True,
                type="primary" if is_active else "secondary"
            ):
                st.session_state.current_session_id = sid
                st.rerun()
                
        with col_del:
            # Allow deletion if there's more than one session
            if len(st.session_state.sessions) > 1:
                if st.button("×", key=f"del_{sid}", help="Delete chat"):
                    del st.session_state.sessions[sid]
                    # If we deleted the active session, switch to the first remaining one
                    if sid == current_id:
                        st.session_state.current_session_id = list(st.session_state.sessions.keys())[0]
                    st.rerun()

# Right Side (Main Chat Interface)
if not messages:
    # Beautiful landing greeting with clean, spacious layout (no redundant headers or lines)
    st.markdown("""
    <div style='text-align: center; padding: 12vh 0 8vh 0;'>
        <h1 style="font-family: 'Lora', 'Georgia', serif; font-size: 2.8rem; font-weight: 400; color: var(--text-color); letter-spacing: -1px; margin-bottom: 1.5rem;">How can I help you today?</h1>
        <p style='color: var(--text-color); opacity: 0.65; font-size: 1.1rem; max-width: 480px; margin: 0 auto; line-height: 1.6;'>
            Ask Nervenet to write code, analyze data, or simply have a conversation.
        </p>
    </div>
    """, unsafe_allow_html=True)
else:
    # Display conversation messages (Clean, no-bubble list format)
    for message in messages:
        with st.chat_message(message["role"]):
            content = message["content"]
            if isinstance(content, str):
                render_assistant_response(content)
            elif isinstance(content, list):
                # Render structured blocks (with text, tool calls, and tool outputs)
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            render_assistant_response(block.get("text", ""))
                        elif block.get("type") == "tool_use":
                            # Display a clean indicator of the tool being called
                            st.markdown(f"<div style='color: var(--text-color); opacity: 0.65; font-size: 0.85rem; padding: 4px 0;'>🔧 <b>Nervenet tool call:</b> <code>{block.get('name')}</code></div>", unsafe_allow_html=True)
                        elif block.get("type") == "tool_result":
                            # Render the output of the tool in a clean, scrollable json/code expander
                            with st.expander("📥 Data Result", expanded=False):
                                content_to_show = block.get("content", "")
                                if "privacy_engine" in st.session_state:
                                    content_to_show = st.session_state.privacy_engine.detokenize_text(content_to_show)
                                st.code(content_to_show, language="json")

    # Privacy Engine Debugger Expander at the bottom of the chat list
    if current_session.get("last_debug_info"):
        st.markdown("---")
        with st.expander("🛡️ Privacy & Tokenization Inspector", expanded=False):
            st.markdown("##### Dynamic Token Mapping")
            if "privacy_engine" in st.session_state and st.session_state.privacy_engine.token_to_value:
                mapping_data = [
                    {"Token": token, "Real Value": val}
                    for token, val in st.session_state.privacy_engine.token_to_value.items()
                ]
                st.table(mapping_data)
            else:
                st.info("No tokens generated yet in this session.")
                
            st.markdown("##### Raw Payload Sent to LLM (Tokenized)")
            debug_info = current_session["last_debug_info"]
            st.json(debug_info.get("payload_sent", []))
            
            st.markdown("##### Raw LLM Response (Before Decryption)")
            last_assistant_msg = next(
                (msg for msg in reversed(messages) if msg.get("role") == "assistant"),
                None
            )
            if last_assistant_msg:
                content = last_assistant_msg.get("content")
                if isinstance(content, str):
                    st.code(content)
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            st.code(block.get("text", ""))
            else:
                st.info("No assistant response generated yet.")

# User Input Box
if user_input := st.chat_input("Write a message..."):
    # Check if API Key is configured before sending
    if not api_key:
        st.error("Cannot send message: OpenAI API Key is missing. Please set `OPENAI_API` in your `.env` file and refresh.")
    else:
        # Display user message
        with st.chat_message("user"):
            st.markdown(user_input)
        
        # Add user message to current session messages list
        messages.append({"role": "user", "content": user_input})
        
        # Auto-generate dynamic session title if this is the first user message
        if len(messages) == 1:
            generated_title = user_input[:30] + "..." if len(user_input) > 30 else user_input
            current_session["title"] = generated_title
        
        # Generate assistant response
        with st.chat_message("assistant"):
            message_placeholder = st.empty()
            # Spinning loading feedback
            with st.spinner(""):
                try:
                    # Pass the entire history to OpenAI and get the response text, debug info, and complete updated history
                    response, debug_info, updated_history = get_claude_response(messages, st.session_state.privacy_engine, model="gpt-4o")
                    with message_placeholder.container():
                        render_assistant_response(response, debug_info=debug_info)
                    
                    # Store complete conversation history (including tool steps) inside the session
                    current_session["messages"] = updated_history
                    current_session["last_debug_info"] = debug_info
                    
                    # Refresh page to display the updated state
                    st.rerun()
                    
                except Exception as e:
                    message_placeholder.empty()
                    st.error(f"Something went wrong: {str(e)}")
                    # Store failed debug state if possible
                    if 'debug_info' in locals():
                        current_session["last_debug_info"] = debug_info
                        st.rerun()

# Small disclaimer footer placed at the very bottom
st.markdown("""
<div style="text-align: center; margin-top: 25px; font-size: 0.75rem; color: var(--text-color); opacity: 0.45; letter-spacing: 0.2px;">
    Nervenet is AI and can make mistakes. Please double-check responses.
</div>
""", unsafe_allow_html=True)