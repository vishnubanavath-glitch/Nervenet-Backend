import os
import sys
import logging
from dotenv import load_dotenv
from openai import OpenAI

# Configure standard python logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("NervenetClient")

# Load environment variables
load_dotenv()
api_key = os.getenv("OPENAI_API") or os.getenv("OPENAI_API_KEY")

if api_key:
    os.environ["OPENAI_API_KEY"] = api_key

# System Prompt for the dedicated SVG Chart Generation Engine
SVG_SYSTEM_PROMPT = """You are a professional SVG chart generation engine.

Your only job is to generate beautiful, valid SVG charts from the structured JSON input provided to you.

### Rules
* Return only raw SVG.
* Do not wrap the SVG in Markdown code blocks (no ```svg or ```xml).
* The response must start with `<svg>` and end with `</svg>`.

### Design Requirements
* Create modern, clean, and professional dashboard-quality charts.
* Make the SVG responsive.
* Default size:
  - Width: `100%` (or `900` if fixed)
  - Height: `560`
* Use a dark theme by default unless another theme is specified.
* Use modern fonts like:
  - `Inter`
  - `Outfit`
  - `system-ui`
* Use premium color palettes with gradients instead of plain colors.
* Add proper chart titles and subtitles.
* Clearly label both X and Y axes when applicable.
* X-Axis Labels: You MUST explicitly print the category name/value (such as the `uidNo` or `subDiv` value) as a label text element directly underneath each individual bar or data point. Never leave graphical elements/bars unlabeled on the X-axis.
* Draw subtle grid lines to improve readability.
* Automatically scale the chart based on the data.
* Prevent labels, legends, and chart elements from overlapping.
* Include legends whenever multiple categories or series are displayed.
* Add smooth hover animations using SVG/CSS (scale, brightness, opacity, etc.).
* Use rounded corners and modern styling where appropriate.
* Keep spacing balanced and visually appealing.

### Supported Charts
Generate:
* Bar Chart
* Horizontal Bar Chart
* Line Chart
* Area Chart
* Pie Chart
* Donut Chart
* Scatter Plot
Choose the most suitable layout for the requested chart type.

### Empty Data
If the provided dataset is empty:
* Generate a beautiful empty-state SVG.
* Display a message such as:
  - "No data available"
  - "No matching records found"
* Do not return plain text.

### Important
* Never change or calculate the provided data.
* Never invent values.
* Simply render the chart using the supplied data.
* Ensure the generated SVG is valid and can be rendered directly by a browser or Streamlit application.
"""

def generate_svg_chart(chart_json_str: str) -> str:
    """
    Dedicated generator that invokes OpenAI to draw a complete, valid SVG
    chart from a structured JSON dataset.
    """
    if not api_key:
        logger.error("SVG Engine: OpenAI API key is missing.")
        raise ValueError("OpenAI API key is missing. Please set OPENAI_API or OPENAI_API_KEY in your .env file.")
        
    logger.info("SVG Engine: Starting SVG generation request via OpenAI...")
    logger.info(f"SVG Engine: Input JSON Data: {chart_json_str}")
    
    try:
        client = OpenAI(api_key=api_key)
        
        response = client.chat.completions.create(
            model="gpt-5.2",  # Sane headroom for complex SVG coordinates
            messages=[
                {"role": "system", "content": SVG_SYSTEM_PROMPT},
                {"role": "user", "content": f"Generate a chart for this dataset:\n{chart_json_str}"}
            ]
        )
        
        svg_text = response.choices[0].message.content or ""
        svg_text = svg_text.strip()
        
        # Clean any accidental leading/trailing spaces or quotes
        if svg_text.startswith("```"):
            lines = svg_text.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            svg_text = "\n".join(lines).strip()
            
        # Extract usage metrics
        usage_metrics = {
            "input_tokens": response.usage.prompt_tokens if hasattr(response, "usage") and response.usage else 0,
            "output_tokens": response.usage.completion_tokens if hasattr(response, "usage") and response.usage else 0
        }
        
        logger.info("SVG Engine: SVG generation request completed successfully.")
        return svg_text, usage_metrics
        
    except Exception as e:
        logger.error(f"SVG Engine: Failed to generate SVG: {str(e)}")
        # Raise real error for Streamlit UI handler to display
        raise
