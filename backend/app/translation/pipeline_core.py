"""
pipeline_core.py — 论文翻译流水线核心函数

步骤：
  A     parse_pdf           → {"markdown": str}
  A.5   fix_heading_levels  → corrected markdown str
  B     cleanup_chunk       → cleaned markdown str          （并行，每块一个）
  C     classify_chunk      → list[dict] 结构对象列表       （并行，每块一个）
  D     translate_paragraph → str 中文译文                  （并行，每段一个）
"""

import json
import logging
import re

import httpx

from app.config import settings
from app.translation.tools.mineru_tool import MineruParseTool

logger = logging.getLogger(__name__)


# ── LaTeX 测量值转纯文本 ───────────────────────────────────────────────────────

# 确认是「真正数学公式」的 LaTeX 命令（遇到则保留 $...$，不转换）
_MATH_ONLY_RE = re.compile(
    r'\\(?:frac|int[oil]?|iint|iiint|oint|sum|prod|lim(?:sup|inf)?|'
    r'rightarrow|leftarrow|Rightarrow|Leftarrow|leftrightarrow|Leftrightarrow|'
    r'partial|nabla|sqrt|binom|'
    r'alpha|beta|gamma|delta|epsilon|varepsilon|zeta|eta|theta|vartheta|'
    r'iota|kappa|lambda|mu|nu|xi|pi\b|varpi|rho|varrho|sigma|varsigma|'
    r'tau|upsilon|phi|varphi|chi|psi|omega|'
    r'Gamma|Delta|Theta|Lambda|Xi|Pi\b|Sigma|Upsilon|Phi|Psi|Omega|'
    r'forall|exists|notin|subset|subseteq|supset|supseteq|'
    r'cup|cap|wedge|vee|hat|bar|vec|ddot|'
    r'mathbb|mathbf|mathcal)'
)

_SUP_TABLE = str.maketrans('0123456789+-.', '⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻·')
_SUB_TABLE = str.maketrans('0123456789+-',  '₀₁₂₃₄₅₆₇₈₉₊₋')


def _to_superscript(s: str) -> str:
    try:
        return s.translate(_SUP_TABLE)
    except Exception:
        return f'^{{{s}}}'


def _to_subscript(s: str) -> str:
    try:
        return s.translate(_SUB_TABLE)
    except Exception:
        return f'_{{{s}}}'


def _convert_latex_inner(inner: str) -> str | None:
    if _MATH_ONLY_RE.search(inner):
        return None

    text = inner
    text = re.sub(r'\\mathrm\{~-~\}', ' - ', text)
    text = re.sub(r'\\mathrm\{~\}', ' ', text)
    text = text.replace(r'\circ', '°')
    text = re.sub(r'\\mathrm\{([^}]*)\}', r'\1', text)
    text = re.sub(r'\\text\{([^}]*)\}', r'\1', text)
    text = text.replace(r'\times', '×')
    text = text.replace(r'\cdot', '·')
    text = text.replace(r'\pm', '±')
    text = text.replace(r'\mp', '∓')
    text = text.replace(r'\approx', '≈')
    text = text.replace(r'\sim', '~')
    text = text.replace(r'\leq', '≤')
    text = text.replace(r'\geq', '≥')
    text = text.replace(r'\neq', '≠')
    text = text.replace(r'\infty', '∞')
    text = re.sub(r'\^\{([0-9+\-. ]+)\}', lambda m: _to_superscript(m.group(1).strip()), text)
    text = re.sub(r'\^([0-9+\-])', lambda m: _to_superscript(m.group(1)), text)
    text = re.sub(r'_\{([0-9+\-. ]+)\}', lambda m: _to_subscript(m.group(1).strip()), text)
    text = re.sub(r'_([0-9+\-])', lambda m: _to_subscript(m.group(1)), text)
    text = re.sub(r'\\[,;!]', '', text)
    text = text.replace(r'\ ', ' ')
    text = text.replace('~', '\u00a0')
    text = text.replace('\u00a0', ' ')
    text = text.strip()

    if re.search(r'\\[a-zA-Z]', text):
        return None

    return text


def delatex_measurements(text: str) -> str:
    """将 $...$ 内联 LaTeX 中属于「数字/单位」类型的片段转换为 Unicode；真正的数学公式保持不变。"""
    def _replace(m: re.Match) -> str:
        inner = m.group(1)
        converted = _convert_latex_inner(inner)
        return converted if converted is not None else m.group(0)

    return re.sub(r'(?<!\$)\$(?!\$)([^$]+?)(?<!\$)\$(?!\$)', _replace, text)


# ── 基础 LLM 调用（Qwen via DashScope） ───────────────────────────────────────

def _call_llm(
    messages: list[dict],
    log_tag: str = "",
    temperature: float = 0.2,
    max_tokens: int = 4096,
) -> str:
    """调用 Qwen Chat API（DashScope OpenAI 兼容接口），返回 content 字符串。"""
    if log_tag:
        prompt_text = "\n".join(f"[{m['role']}] {m['content']}" for m in messages)
        logger.warning(
            "[LLM/%s] ── 请求 ──────────────────────────────\n%s\n────────────────────────────────────────",
            log_tag, prompt_text,
        )

    with httpx.Client(timeout=120) as client:
        resp = client.post(
            f"{settings.DEEPSEEK_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.DEEPSEEK_MODEL,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
        )
        resp.raise_for_status()

    content = resp.json()["choices"][0]["message"]["content"]

    if log_tag:
        logger.warning(
            "[LLM/%s] ── 响应 ──────────────────────────────\n%s\n────────────────────────────────────────",
            log_tag, content,
        )
    return content


def _extract_json(raw: str, array: bool = False):
    """从 LLM 输出中提取第一个完整的 JSON 对象或数组（括号计数法）。"""
    opener, closer = ('[', ']') if array else ('{', '}')
    empty = [] if array else {}

    search_from = 0
    while True:
        start = raw.find(opener, search_from)
        if start == -1:
            return empty

        depth = 0
        in_string = False
        escape = False
        end = -1
        for i in range(start, len(raw)):
            ch = raw[i]
            if escape:
                escape = False
                continue
            if ch == '\\' and in_string:
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    end = i
                    break

        if end == -1:
            return empty

        candidate = raw[start:end + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            search_from = start + 1


# ── 阶段 A：PDF 解析 ───────────────────────────────────────────────────────────

def parse_pdf(pdf_bytes: bytes, filename: str = "document.pdf", paper_id: str = "") -> dict:
    """调用 MinerU 解析 PDF，返回 {"markdown": str}"""
    tool = MineruParseTool(pdf_bytes=pdf_bytes, filename=filename, paper_id=paper_id)
    return tool._call_mineru()


# ── 阶段 A.5：标题层级修正 ────────────────────────────────────────────────────

def fix_heading_levels(markdown: str, paper_title: str) -> str:
    """
    提取所有 # 开头的行，让 LLM 重新整理层级并移除主标题，
    然后对 Markdown 全文做整行精确替换。失败时返回原 markdown。
    """
    lines = markdown.splitlines()
    heading_lines = [l for l in lines if re.match(r"^#{1,6}\s", l)]
    if not heading_lines:
        return markdown

    headings_text = "\n".join(heading_lines)
    messages = [
        {
            "role": "system",
            "content": (
                "You are an expert at restructuring heading hierarchies in academic papers. "
                "Output ONLY a JSON array. No explanations, no extra text."
            ),
        },
        {
            "role": "user",
            "content": (
                f"The paper's main title is: \"{paper_title}\"\n\n"
                "Below are all heading lines (starting with #) extracted from the PDF:\n"
                f"```\n{headings_text}\n```\n\n"
                "Reassign heading levels according to these rules:\n"
                "1. If a heading line matches (or closely resembles) the paper's main title, map it to an empty string (remove it).\n"
                "2. Use only levels # ## ### (1–3). Collapse level 4+ into ###.\n"
                "3. Infer the correct level from numbering patterns (1. / 1.1 / 1.1.1) and semantic hierarchy.\n\n"
                "Output a JSON array where each element is a single-key object: "
                "key = original heading line (exact string), value = corrected heading line "
                "(or empty string to remove).\n\n"
                "Example output:\n"
                '[{"# Introduction to NLP": ""}, {"## 1. Background": "# 1. Background"}]'
            ),
        },
    ]

    try:
        raw = _call_llm(messages, log_tag="fix_headings", temperature=0.1, max_tokens=2000)
        corrections = _extract_json(raw, array=True)
        if not isinstance(corrections, list):
            return markdown
    except Exception as e:
        logger.warning("[fix_heading_levels] 失败，使用原始 markdown: %s", e)
        return markdown

    result_lines = []
    for line in lines:
        replacement = None
        for item in corrections:
            if isinstance(item, dict):
                for orig, new in item.items():
                    if line == orig:
                        replacement = new
                        break
            if replacement is not None:
                break

        if replacement is None:
            result_lines.append(line)
        elif replacement == "":
            pass
        else:
            result_lines.append(replacement)

    return "\n".join(result_lines)


# ── Markdown 切块工具 ─────────────────────────────────────────────────────────

def split_by_window(markdown: str, max_words: int = 1500) -> list[str]:
    """
    固定窗口切块：累计达到 max_words 词时在行尾切块。
    Markdown 标题行（# 开头）强制开启新块。
    """
    lines = markdown.splitlines()
    chunks: list[str] = []
    current: list[str] = []
    current_words = 0

    for line in lines:
        is_heading = bool(re.match(r"^#{1,6}\s", line))

        if is_heading and current:
            chunks.append("\n".join(current))
            current = [line]
            current_words = len(line.split())
            continue

        line_words = len(line.split())

        if current_words + line_words > max_words and current:
            chunks.append("\n".join(current))
            current = [line]
            current_words = line_words
        else:
            current.append(line)
            current_words += line_words

    if current:
        chunks.append("\n".join(current))

    return [c for c in chunks if c.strip()]


# ── 阶段 B：文本清理 ──────────────────────────────────────────────────────────

def cleanup_chunk(chunk: str, chunk_idx: int = 0) -> str:
    """
    阶段 B：纯文本清理。
    合并 PDF 错误断行、修正明显 OCR 错误、删除噪声。
    不提取参考文献，不提取术语。
    返回整理后的 Markdown 字符串。
    """
    messages = [
        {
            "role": "system",
            "content": (
                "You are an expert academic paper pre-processor. "
                "Output ONLY the cleaned Markdown text. No explanations, no code fences."
            ),
        },
        {
            "role": "user",
            "content": (
                "Clean the following academic paper Markdown fragment:\n\n"
                "=== PART 1: TEXT CLEANING RULES ===\n"
                "1. Merge broken lines: if a line ends without a sentence terminator (. ? !) and the next\n"
                "   line is a clear continuation, join them into one line.\n"
                "2. Fix obvious OCR errors (e.g. rn→m, cl→d when unambiguous).\n"
                "3. Remove garbled characters and noise.\n"
                "4. Collapse runs of 3+ blank lines into a single blank line.\n"
                "5. Preserve ALL Markdown headings (#) and image syntax ![...](...) exactly as-is.\n"
                "6. Never split existing paragraphs or add new line breaks — only merge, never split.\n"
                "7. Inline citation normalization: identify bare integers that function as inline\n"
                "   citation markers and convert them to bracket notation [N].\n"
                "   A number is likely a citation marker when it:\n"
                "     - appears directly attached to the end of a word with no space (e.g. \"deposition17.\")\n"
                "     - OR appears as a seemingly out-of-place number right before punctuation or at the\n"
                "       end of a clause/sentence, with no natural numerical meaning in context\n"
                "       (e.g. \"during sediment deposition 17.\" or \"environmental changes,23 which suggest\")\n"
                "   Examples of conversions:\n"
                "     \"deposition17.\" → \"deposition[17].\"\n"
                "     \"conditions 23,\" → \"conditions [23],\"\n"
                "     \"these findings 4,5 demonstrate\" → \"these findings [4],[5] demonstrate\"\n"
                "   Do NOT convert numbers that have clear standalone meaning:\n"
                "     - ANY 4-digit number (1000–2999): these are always years, never citation markers.\n"
                "       CRITICAL: '1992', '1998', '2003' etc. must NEVER become '[1]', '[1992]' or anything else.\n"
                "       This includes years inside author-date citations such as:\n"
                "       \"(Proctor et al., 1992)\" → keep exactly as-is, do not touch any part of it\n"
                "       \"(van Kaam-Peters et al.1998)\" → keep exactly as-is\n"
                "       \"(Smith, 2020)\" → keep exactly as-is\n"
                "     - Entire parenthetical author-date citation groups, e.g. \"(Author, YEAR)\" or\n"
                "       \"(Author et al., YEAR)\" — leave the whole group untouched.\n"
                "     - Figure/Table/Step labels: \"Figure 3\", \"Table 2\", \"step 2\"\n"
                "     - Numbers immediately followed by a Greek letter or chemical suffix:\n"
                "       \"17β\", \"21β(H)\", \"5α-reductase\", \"3H\"\n"
                "     - Numbers preceded by a Greek letter (isotope/geochemical notation):\n"
                "       \"δ13C\", \"δ18O\", \"δ15N\" — the 13/18/15 here are NOT citations\n"
                "     - Chemical formulas, units, measurements:\n"
                "       \"CO₂\", \"H₂O\", \"at 25 °C\", \"pH 7.4\", \"over 10 years\"\n\n"

                "=== PART 2: LaTeX RULES FOR FORMULAS, UNITS, AND NUMBERS ===\n"
                "Use plain Unicode (NO $ wrapping) for:\n"
                "  - Simple chemical formulas: CH4→CH₄, H2O→H₂O, CO2→CO₂\n"
                "  - Chemical formulas written with braces/underscores: {CH}_4→CH₄, H_{2}O→H₂O\n"
                "    Rule: if braces/underscores contain ONLY element symbols (capital letters) and\n"
                "    digits, it is a chemical formula → convert to Unicode, do NOT wrap in $\n"
                "  - Isotope notation: δ13C→δ¹³C, δ18O→δ¹⁸O, δ15N→δ¹⁵N (plain text, no $)\n"
                "  - Temperatures: 40 °C, -25 ℉ (plain text)\n"
                "  - Units and measurements: 50 m, 0.12 μm, 22 min (plain text)\n"
                "  - Ordinary numbers, ratios, coordinates\n"
                "Use $ wrapping for math expressions that contain ANY of:\n"
                "  - LaTeX commands (backslash notation): \\frac, \\sum, \\int, \\alpha, \\beta,\n"
                "    \\{, \\}, \\le, \\ge, \\neq, \\approx, \\in, \\subset, \\cup, \\cap, etc.\n"
                "  - Subscripts or superscripts with braces containing variables, indices, or\n"
                "    nested expressions (NOT element symbols): x_{i}, a^{n}, p_{y_{i}}^{k}\n"
                "  - Display math (already $...$): keep as-is\n"
                "  - Chemical equations with arrows or complex structure\n"
                "  - Fractions, integrals, sums, products, square roots\n"
                "  - Multi-character operators like \\rightarrow, \\Leftrightarrow\n"
                "  Examples that need $ wrapping:\n"
                "    p_{y_{i}}^{k}              →  $p_{y_{i}}^{k}$\n"
                "    \\{(x_i, y_i)\\}_{i=1}^n  →  $\\{(x_i, y_i)\\}_{i=1}^n$\n"
                "    f(x) = \\frac{1}{2}        →  $f(x) = \\frac{1}{2}$\n"
                "  Examples that must NOT be wrapped (chemical, use Unicode):\n"
                "    {CH}_4  →  CH₄       H_{2}O  →  H₂O       {CO}_{2}  →  CO₂\n"
                "CLEANUP: if plain text (temperatures, units, chemical formulas like CH₄)\n"
                "  is incorrectly wrapped in $...$, strip the $ and restore it as plain text.\n\n"

                f"=== CONTENT TO CLEAN ===\n```\n{chunk}\n```\n\n"
                "Output the cleaned Markdown text directly (no ``` fences):"
            ),
        },
    ]
    try:
        result = _call_llm(messages, log_tag=f"B{chunk_idx}", temperature=0.1, max_tokens=4096)
        result = result.strip()
        # 剥离 LLM 可能加上的代码块包裹
        if result.startswith("```"):
            result = re.sub(r'^```[^\n]*\n?', '', result)
            result = re.sub(r'\n?```\s*$', '', result)
        result = result.strip()
        if not result:
            return chunk
        return delatex_measurements(result)
    except Exception as e:
        logger.error("[cleanup_chunk B%d] 失败: %s", chunk_idx, e, exc_info=True)
        return chunk


def _strip_ref_number(text: str) -> str:
    """剥除参考文献条目开头的序号，支持常见格式：
    1. text  /  [1] text  /  (1) text  /  1) text
    """
    return re.sub(r'^\s*(?:\[\d+\]|\(\d+\)|\d+[.)]\s*)\s*', '', text)


# ── 阶段 B.5：术语提取 ────────────────────────────────────────────────────────

def extract_terms_chunk(
    chunk: str,
    existing_keys: set[str],
    domain: str = "学术",
    chunk_idx: int = 0,
) -> list[dict]:
    """
    阶段 B.5：从已清理的文本块中提取领域专业术语，返回 [{"en": str, "zh": str}, ...]。

    - 只返回 existing_keys 中尚不存在的新术语
    - zh 为 LLM 建议的中文译名，用户可在词库页面修改
    """
    if len(chunk.strip()) < 150:
        return []

    existing_sample = ", ".join(list(existing_keys)[:40]) if existing_keys else "无"

    messages = [
        {
            "role": "system",
            "content": (
                "You are an expert at identifying genuinely ambiguous terminology in academic papers. "
                "Output ONLY a JSON array. No explanations, no extra text."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Domain: {domain}\n\n"
                "From the academic paper excerpt below, identify terms whose Chinese translation is\n"
                "genuinely uncertain or ambiguous, and suggest a Chinese rendering.\n\n"
                "=== EXTRACT ONLY IF ALL CONDITIONS ARE MET ===\n"
                "1. Translation is genuinely ambiguous: the same word has multiple valid Chinese\n"
                "   renderings depending on context or sub-field, OR the standard dictionary translation\n"
                "   differs from the convention in this specific domain.\n"
                "2. Non-obvious: a general-purpose translation tool would likely get it wrong.\n"
                "3. Good candidates:\n"
                "   - Common/folk names for chemicals or minerals (e.g. vivianite, sphagnum) whose\n"
                "     formal Chinese name is not immediately apparent.\n"
                "   - Instrument or method acronyms with domain-specific meaning\n"
                "     (e.g. XRF, TOC mean different things in geology vs. medicine).\n"
                "   - Words used with a specialized meaning in this paper that differs from general usage.\n"
                "   - Multi-word phrases that function as an inseparable proper concept\n"
                "     (e.g. 'peatland carbon feedback').\n\n"
                "=== DO NOT EXTRACT ===\n"
                "- Common academic vocabulary (analysis, method, concentration, temperature, result…)\n"
                "- Terms with a single, unambiguous standard Chinese translation\n"
                "  (e.g. photosynthesis, sediment, pH)\n"
                "- Person names, place names, institution names, journal names\n"
                "- Pure acronyms that are kept in English as-is (DNA, ATP, etc.)\n\n"
                "QUANTITY LIMIT: at most 5 terms per excerpt. Return [] if nothing qualifies.\n\n"
                f"Already known terms (skip these): {existing_sample}\n\n"
                'Output format (JSON array): [{"en": "term", "zh": "suggested Chinese"}, ...]\n\n'
                "Excerpt:\n"
                + chunk[:3000]
            ),
        },
    ]

    try:
        raw = _call_llm(messages, log_tag=f"terms/B{chunk_idx}", temperature=0.1, max_tokens=1024)
        items = _extract_json(raw, array=True)
        if not isinstance(items, list):
            return []
        result = []
        for item in items:
            if not isinstance(item, dict):
                continue
            en = (item.get("en") or "").strip()
            zh = (item.get("zh") or "").strip()
            if en and zh and en.lower() not in existing_keys:
                result.append({"en": en, "zh": zh})
        return result
    except Exception as e:
        logger.error("[extract_terms_chunk] chunk %d 失败: %s", chunk_idx, e)
        return []


# ── 行内公式合并：将 MinerU 输出的多行展示公式 $\nformula\n$ 转为 $$formula$$ ──

def normalize_display_math(text: str) -> str:
    """
    将 MinerU 输出的多行展示公式格式：
        $
        <formula line(s)>
        $
    规范化为单行 $$<formula>$$ 格式，防止 classify_chunk 把三行当成三个独立块。
    只合并「孤行 $」包围的块，不触碰已有的 $...$ 内联公式。
    """
    lines = text.splitlines()
    result: list[str] = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        # 匹配孤行 $ 或 $$（MinerU 两种展示公式格式）
        if stripped in ('$', '$$') and i + 2 < len(lines):
            delimiter = stripped
            formula_lines = []
            j = i + 1
            while j < len(lines):
                if lines[j].strip() == delimiter:
                    break
                formula_lines.append(lines[j])
                j += 1
            if j < len(lines) and lines[j].strip() == delimiter and formula_lines:
                formula = ' '.join(l.strip() for l in formula_lines if l.strip())
                result.append(f"$${formula}$$")
                i = j + 1
                continue
        result.append(lines[i])
        i += 1
    return '\n'.join(result)


# ── 阶段 C：结构分类 ──────────────────────────────────────────────────────────

def classify_chunk(chunk: str, chunk_idx: int = 0) -> list[dict]:
    """
    阶段 C：对已清理的 Markdown 块进行结构分类。

    返回对象列表，每个对象为以下格式之一：
      {"type": "heading",   "level": 1|2|3, "text": "标题文字（不含#）"}
      {"type": "paragraph", "text": "段落完整文字"}
      {"type": "reference", "text": "文献条目原文"}
      {"type": "image",     "url": "图片URL"}
    """
    # 预处理：提取图片行，防止 LLM 修改 URL
    lines = chunk.splitlines()
    image_map: dict[str, str] = {}
    processed_lines = []
    img_idx = 0
    for line in lines:
        if line.strip().startswith("!["):
            ph = f"__IMG_{img_idx}__"
            image_map[ph] = line.strip()
            processed_lines.append(ph)
            img_idx += 1
        else:
            processed_lines.append(line)
    text_chunk = "\n".join(processed_lines)

    messages = [
        {
            "role": "system",
            "content": (
                "You are an expert in academic paper structure analysis. "
                "Output ONLY a JSON array in the exact format requested. No explanations, no extra text."
            ),
        },
        {
            "role": "user",
            "content": (
                "Analyze the following academic paper Markdown fragment and classify each element.\n\n"
                "=== CLASSIFICATION RULES ===\n\n"
                "1. HEADING — lines starting with # ## ###\n"
                '   → {"type":"heading","level":1|2|3,"text":"heading text without # prefix"}\n\n'
                "2. PARAGRAPH — body text of the paper\n"
                '   → {"type":"paragraph","text":"full paragraph text"}\n'
                "   • One physical line = one object. Do NOT split or merge lines.\n\n"
                "3. REFERENCE — a standalone, complete bibliographic entry\n"
                '   → {"type":"reference","text":"entry text, stripping any leading number like \'1.\' or \'[1]\'  "}\n'
                "   A reference entry MUST contain BOTH author name(s) AND an article/book title.\n"
                "   It typically also includes: publication year, journal/publisher/conference, pages/DOI.\n"
                "   Common formats (many styles exist — these are examples only):\n"
                "     • Nature style:   Zachos, J. C. et al. A transient rise... Science 302, 1551–1554 (2003).\n"
                "     • APA style:      Smith, J. A., & Jones, B. (2020). Title. Journal, 45(3), 123–145.\n"
                "     • IEEE style:     J. Smith, \"Title,\" IEEE Trans., vol. 45, pp. 123–145, 2020.\n"
                "     • Vancouver:      Smith JA, Jones B. Title. J Example. 2020;45(3):123–145.\n"
                "     • Book/report:    IPCC. Climate Change 2021. Cambridge University Press, 2021.\n"
                "   NOT a reference — classify as paragraph instead:\n"
                "     • Inline citation markers embedded in a sentence: [1], ¹, (Smith, 2020)\n"
                "     • Author mention in body text: 'Smith and Jones (2020) showed that...'\n"
                "     • A line containing ONLY author names and/or institutional affiliations with NO article\n"
                "       title — this is the paper's own author list, which is body text:\n"
                "       e.g. 'Zhang Wei, Li Ming, Wang Fang' or 'J. Smith¹, B. Jones²'\n"
                "     • The paper's own main title (a standalone title heading without publication info)\n"
                "   !! CRITICAL: Every standalone bibliographic entry MUST be typed as 'reference'.\n"
                "   !! NEVER leave a reference entry typed as 'paragraph'.\n\n"
                "4. IMAGE — __IMG_N__ placeholder\n"
                '   → {"type":"image","placeholder":"__IMG_N__"}\n\n'
                "=== CONTENT TO ANALYZE ===\n"
                f"```\n{text_chunk}\n```\n\n"
                "Output the JSON array now:"
            ),
        },
    ]

    try:
        raw = _call_llm(messages, log_tag=f"C{chunk_idx}", temperature=0.1, max_tokens=4096)
        items = _extract_json(raw, array=True)
        if not isinstance(items, list) or not items:
            logger.warning("[classify_chunk C%d] 解析失败，使用兜底分类", chunk_idx)
            return _fallback_classify(chunk)

        # 替换图片占位符
        result = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "image":
                ph = item.get("placeholder", "")
                orig = image_map.get(ph, "")
                url = _extract_image_url(orig) if orig else ph
                result.append({"type": "image", "url": url})
            elif item.get("type") == "reference":
                result.append({"type": "reference", "text": _strip_ref_number(item.get("text", ""))})
            else:
                result.append(item)
        return result

    except Exception as e:
        logger.error("[classify_chunk C%d] 失败: %s", chunk_idx, e, exc_info=True)
        return _fallback_classify(chunk)


def _fallback_classify(chunk: str) -> list[dict]:
    """classify_chunk 失败时的兜底：基于规则简单分类，不识别参考文献。"""
    result = []
    for line in chunk.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("!["):
            url = _extract_image_url(stripped)
            result.append({"type": "image", "url": url})
        elif re.match(r"^(#{1,3})\s", stripped):
            m = re.match(r"^(#{1,3})\s+", stripped)
            level = len(m.group(1))
            text = stripped[m.end():].strip()
            result.append({"type": "heading", "level": level, "text": text})
        else:
            result.append({"type": "paragraph", "text": stripped})
    return result


# ── 阶段 C2：参考文献二次校验 ────────────────────────────────────────────────

def verify_references(objects: list[dict], chunk_idx: int = 0) -> list[dict]:
    """
    阶段 C2：对分类结果中所有 paragraph 条目做二次判断，
    将被 classify_chunk 误判为正文的参考文献条目重新标记为 reference。
    返回修正后的对象列表。
    """
    para_indices = [i for i, obj in enumerate(objects) if obj.get("type") == "paragraph"]
    if not para_indices:
        return objects

    para_lines = "\n".join(
        f"{seq}. {objects[idx]['text'][:300]}"
        for seq, idx in enumerate(para_indices)
    )

    messages = [
        {
            "role": "system",
            "content": (
                "You are an expert at identifying bibliographic reference entries in academic papers. "
                "Output ONLY a JSON array as specified. No extra text."
            ),
        },
        {
            "role": "user",
            "content": (
                "The following items were extracted from an academic paper and classified as body-text paragraphs.\n"
                "Some of them may actually be bibliographic reference entries that were misclassified.\n"
                "For each item, decide: is it a standalone bibliographic reference entry?\n\n"
                "=== WHAT COUNTS AS A REFERENCE ENTRY ===\n"
                "A self-contained line or block that contains BOTH author name(s) AND an article/book title.\n"
                "It typically also includes: publication year, journal/publisher/conference, pages/DOI.\n\n"
                "Common formats (not exhaustive):\n"
                "  • Zachos, J. C. et al. A transient rise... Science 302, 1551–1554 (2003).\n"
                "  • Smith, J. A., & Jones, B. (2020). Title. Journal, 45(3), 123–145. https://doi.org/...\n"
                "  • J. Smith, \"Title,\" IEEE Trans., vol. 45, pp. 123–145, 2020.\n"
                "  • Smith JA, Jones B. Title. J Example. 2020;45(3):123–145.\n"
                "  • IPCC. Climate Change 2021. Cambridge University Press, 2021.\n\n"
                "=== WHAT IS NOT A REFERENCE ENTRY (answer false) ===\n"
                "  • Inline citation marker inside a sentence, e.g. [1], ¹, (Smith, 2020)\n"
                "  • Author mention in running text, e.g. 'Smith and Jones (2020) showed that...'\n"
                "  • A line with ONLY author names and/or affiliations but NO article title —\n"
                "    this is the paper's own author list (body text), NOT a bibliography entry.\n"
                "    e.g. 'Zhang Wei, Li Ming, Wang Fang' or 'J. Smith¹, B. Jones², C. Lee³'\n"
                "  • The paper's own standalone title line (without publication/venue info)\n"
                "  • Any regular body-text sentence, abstract, figure caption, heading, etc.\n\n"
                f"=== ITEMS TO JUDGE ===\n{para_lines}\n\n"
                "Return a JSON array, one entry per item:\n"
                '[{"idx": 0, "is_ref": false}, {"idx": 1, "is_ref": true}, ...]'
            ),
        },
    ]

    try:
        raw = _call_llm(messages, log_tag=f"V{chunk_idx}", temperature=0.1, max_tokens=1024)
        verdicts = _extract_json(raw, array=True)
        if not isinstance(verdicts, list):
            return objects

        result = list(objects)
        for verdict in verdicts:
            if not isinstance(verdict, dict):
                continue
            seq = verdict.get("idx")
            is_ref = verdict.get("is_ref", False)
            if is_ref and isinstance(seq, int) and 0 <= seq < len(para_indices):
                obj_idx = para_indices[seq]
                result[obj_idx] = {"type": "reference", "text": _strip_ref_number(result[obj_idx]["text"])}

        return result

    except Exception as e:
        logger.error("[verify_references V%d] 失败: %s", chunk_idx, e, exc_info=True)
        return objects


# ── 阶段 D：段落翻译 ──────────────────────────────────────────────────────────

def translate_paragraph(
    text: str,
    glossary_list: list[dict],
    domain: str = "学术",
    para_idx: int = 0,
) -> str:
    """
    阶段 D：翻译单个段落或标题，返回中文字符串。
    """
    text_lower = text.lower()
    relevant = [t for t in glossary_list if t["en"].lower() in text_lower]

    # 按处理策略分组
    only_translate = [(t["en"], t.get("zh", "")) for t in relevant
                      if t.get("status", "translate") == "translate" and t.get("zh")]
    keep_original  = [t["en"] for t in relevant
                      if t.get("status") == "never_translate"]
    with_annot     = [(t["en"], t.get("zh", "")) for t in relevant
                      if t.get("status") == "translate_with_annotation" and t.get("zh")]

    glossary_parts = []
    if only_translate:
        lines = "\n".join(f"  {en} → {zh}" for en, zh in only_translate)
        glossary_parts.append(f"[TRANSLATE ONLY — render these terms as the specified Chinese; do not keep the English]\n{lines}")
    if keep_original:
        lines = "\n".join(f"  {en}" for en in keep_original)
        glossary_parts.append(f"[KEEP ORIGINAL — do NOT translate these terms; leave the English exactly as-is]\n{lines}")
    if with_annot:
        lines = "\n".join(f"  {en} → {zh}（{en}）" for en, zh in with_annot)
        glossary_parts.append(f"[TRANSLATE + ANNOTATE — render as 'Chinese（English）', strictly this format]\n{lines}")

    glossary_text = "\n\n".join(glossary_parts) if glossary_parts else "(No custom glossary applies to this passage.)"

    messages = [
        {
            "role": "system",
            "content": (
                f"You are an expert Chinese academic translator specializing in {domain}. "
                "Output ONLY the Chinese translation. No explanations, no quotation marks, no extra content."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Translate the following {domain} academic paper passage into Chinese.\n\n"
                "=== CUSTOM GLOSSARY (highest priority — follow strictly) ===\n"
                f"{glossary_text}\n\n"
                "=== GENERAL TRANSLATION RULES ===\n"
                "- URLs, DOIs, and personal names: keep in original language\n"
                "- LaTeX formulas $...$ and $$...$$ : preserve exactly, do not modify\n"
                "- Institution names: render as Chinese name (original) format\n"
                "- Internationally standard acronyms (DNA, AI, WHO, etc.): keep in English\n"
                "- Inline citation markers [N] (e.g. [1], [17], [1,2]): keep exactly as-is,\n"
                "  do not translate, remove, or rewrite them\n\n"
                f"=== PASSAGE TO TRANSLATE ===\n{text}"
            ),
        },
    ]

    try:
        result = _call_llm(messages, log_tag=f"D{para_idx}", temperature=0.2, max_tokens=2048)
        result = result.strip()
        # 剥除 LLM 可能加的引号包裹
        if len(result) >= 2 and result[0] == '"' and result[-1] == '"':
            result = result[1:-1]
        return result or text
    except Exception as e:
        logger.error("[translate_paragraph D%d] 失败: %s", para_idx, e, exc_info=True)
        return ""


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _extract_image_url(md_image_line: str) -> str:
    """从 ![alt](url) 提取 url"""
    m = re.search(r"!\[([^\]]*)\]\(([^)]+)\)", md_image_line)
    return m.group(2) if m else md_image_line


def _fallback_structure(chunk: str) -> list[dict]:
    """translate_chunk 失败时的兜底（供外部兼容调用）：每行作为未翻译段落"""
    result = []
    for line in chunk.splitlines():
        if not line.strip():
            continue
        if line.strip().startswith("!["):
            url = _extract_image_url(line.strip())
            result.append({"图片地址": url, "中文图片地址": url})
        elif re.match(r"^(#{1,3})\s", line):
            hashes = re.match(r"^(#{1,3})\s", line).group(1)
            text = line[len(hashes):].strip()
            result.append({"标题等级": len(hashes), "文本": text, "中文文本": ""})
        else:
            result.append({"标题等级": 0, "文本": line.strip(), "中文文本": ""})
    return result
