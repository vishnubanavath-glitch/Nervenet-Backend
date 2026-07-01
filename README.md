# Nervenet MVP: Independent Dual Model-Flavor Database Assistants

This repository contains two completely **independent** and **unconnected** Streamlit chat applications that share the same local database and helper utility structure. The difference between them is the underlying Language Model and API client they use. They do not share sessions, converse with each other, or have any active connection.

* **[app.py](file:///e:/BSS/Nervenet%20MVP/app.py)** (Claude Version): Powered by Anthropic's Claude API (`claude-opus-4-8`).
* **[appV2.py](file:///e:/BSS/Nervenet%20MVP/appV2.py)** (ChatGPT Version): Powered by OpenAI's ChatGPT API (`gpt-4o`).

---

## Design Rationale

The project is intentionally structured as two standalone entry points (`app.py` and `appV2.py`) rather than a single unified application with a model-selector toggle. This approach was chosen for the following reasons:
* **Distinct API SDKs**: Anthropic (`anthropic`) and OpenAI (`openai`) utilize completely different client SDK interfaces, credential setups, and connection structures. 
* **Tool-Calling Conventions**: The loop structures for tool execution and response returns differ significantly between the Anthropic API (which uses a single message block-list with `tool_use` and `tool_result` roles) and the OpenAI API (which uses specific `tool_calls` attributes and independent `tool` roles).
* **Prompting Behaviors**: Claude and GPT models respond best to customized system prompt formatting and guidelines (e.g., Claude uses a decoupled JSON strategy for charts, while GPT excels at drawing and injecting SVGs directly inline).

Separating the applications allows both client interfaces to remain clean, highly optimized, and easy to maintain independently.

---

## Architectural Layout

Both apps are alternative implementations of a customer support assistant for the electricity meter department. Although they share the underlying helper modules and the local Excel database, they run completely standalone from each other.

```mermaid
graph TD
    subgraph App 1: Claude Version (app.py)
        UI1[Streamlit UI 1]
        PE1[Privacy Engine]
        SE1[SVG Engine]
        Anthropic[Anthropic Claude API]
        MCP1[FastMCP Server Subprocess]
    end

    subgraph App 2: ChatGPT Version (appV2.py)
        UI2[Streamlit UI 2]
        PE2[Privacy Engine]
        SE2[SVG Engine]
        OpenAI[OpenAI ChatGPT API]
        MCP2[FastMCP Server Subprocess]
    end

    subgraph Shared Local Assets
        DB[(Excel Database: tpcodl_Test.xlsx)]
    end

    UI1 --> PE1
    PE1 --> Anthropic
    UI1 --> MCP1
    MCP1 --> DB
    UI1 --> SE1
    SE1 --> OpenAI

    UI2 --> PE2
    PE2 --> OpenAI
    UI2 --> MCP2
    MCP2 --> DB
    UI2 --> SE2
    SE2 --> OpenAI
```

### Shared Under-the-Hood Components
Although the frontends are completely separate, they both utilize the following local utilities and database files:

1. **Local Database & MCP Server**
   - The database records are kept in a local Excel file ([tpcodl_Test.xlsx](file:///e:/BSS/Nervenet%20MVP/tpcodl_Test.xlsx)).
   - Both apps spawn their own instance of the local Model Context Protocol (MCP) server ([mcp_server/server.py](file:///e:/BSS/Nervenet%20MVP/mcp_server/server.py)) in a subprocess to run queries and updates.
2. **Privacy Shield Engine ([privacy_engine.py](file:///e:/BSS/Nervenet%20MVP/privacy_engine.py))**
   - Implements local PII encryption and tokenization for sensitive columns like `uidNo`, `mobileNo`, and coordinates.
   - De-tokenizes the encrypted values locally at render-time, so sensitive information is never sent to Anthropic or OpenAI.
3. **SVG Visualizer Engine ([svg_engine.py](file:///e:/BSS/Nervenet%20MVP/svg_engine.py))**
   - Renders animated, responsive SVG charts from structured JSON using a separate OpenAI API call.

---

## Technical Comparison

| Feature | Claude App (`app.py`) | ChatGPT App (`appV2.py`) |
| :--- | :--- | :--- |
| **Connection** | Independent (Does not connect to `appV2.py`) | Independent (Does not connect to `app.py`) |
| **Model Used** | `claude-opus-4-8` | `gpt-4o` |
| **API Provider** | Anthropic | OpenAI |
| **API Key Needed** | `CLAUDE_API` or `ANTHROPIC_API_KEY` | `OPENAI_API` or `OPENAI_API_KEY` |
| **Tool Calling Loop** | Custom Anthropic tool calling protocol | Standard OpenAI chat completions tool calling |
| **Visualization Prompt** | Instructs Claude to return structured JSON | Instructs ChatGPT to render inline SVG |

---

## File Structure

```
Nervenet MVP/
├── mcp_server/
│   └── server.py              # FastMCP Database server (Exposes CRUD, search, aggregates)
├── .env                       # Local environment variables containing API keys (ignored)
├── .gitignore                 # Configured to ignore caches, virtual envs, and secrets
├── app.py                     # Standalone Streamlit app using Claude
├── appV2.py                   # Standalone Streamlit app using ChatGPT
├── claude.py                  # API handler and MCP connection logic for app.py
├── chatGpt.py                 # API handler and MCP connection logic for appV2.py
├── privacy_engine.py          # Shared utility: Client-side PII tokenization shield
├── requirements.txt           # Shared Python dependencies
├── svg_engine.py              # Shared utility: Isolated SVG chart compiler (uses OpenAI)
├── test_crud.py               # Database verification suite
└── tpcodl_Test.xlsx           # Local database (Excel sheet)
```

---

## Installation & Setup

### 1. Set Up Virtual Environment

- **Windows (PowerShell)**:
  ```powershell
  python -m venv venv
  .\venv\Scripts\Activate.ps1
  ```
- **Windows (CMD)**:
  ```cmd
  python -m venv venv
  .\venv\Scripts\activate.bat
  ```
- **macOS/Linux**:
  ```bash
  python3 -m venv venv
  source venv/bin/activate
  ```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure Environment Variables

Create a `.env` file in the root folder of the project:
```env
# Required for app.py (Claude version)
CLAUDE_API=your_anthropic_api_key_here

# Required for appV2.py (ChatGPT version) and svg_engine.py (visualizations)
OPENAI_API=your_openai_api_key_here
```

---

## Running the Applications

Since the applications are completely independent, run whichever version you prefer:

### Run the Claude Version (`app.py`)
```bash
streamlit run app.py
```

### Run the ChatGPT Version (`appV2.py`)
```bash
streamlit run appV2.py
```

Each command opens a separate Streamlit server session in your browser (typically defaulting to `http://localhost:8501`).

---

## Verification Testing

To test the database repo and MCP tools without launching Streamlit, execute the verification suite:
```bash
python test_crud.py
```
This tests all 10 base query capabilities (caching, querying, filtering, aggregates, stats, search, and CRUD) against the local [tpcodl_Test.xlsx](file:///e:/BSS/Nervenet%20MVP/tpcodl_Test.xlsx) database.
