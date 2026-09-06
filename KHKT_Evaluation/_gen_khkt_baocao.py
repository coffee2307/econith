# -*- coding: utf-8 -*-
"""
Reorganize ECONITH-KHKT.docx into KHKT research report structure (~15 pages).
Preserve voice, terms, numbering. Condense redundancy; do not rewrite in AI style.
"""
from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

BLACK = RGBColor(0x11, 0x11, 0x11)
DARK = RGBColor(0x2A, 0x2A, 0x2E)
GRAY = RGBColor(0x5A, 0x5A, 0x62)
MUTED = RGBColor(0x7A, 0x7A, 0x82)
ACCENT = RGBColor(0x1F, 0x4E, 0x79)
LINE = "D0D5DD"
SOFT_BG = "F5F7FA"
FONT = "Times New Roman"
BODY = 14

OUT = Path(__file__).resolve().parent / "ECONITH_KHKT_BaoCao.docx"


def set_run_font(run, *, name=FONT, size=BODY, bold=False, italic=False, color=BLACK):
    run.font.name = name
    run._element.rPr.rFonts.set(qn("w:eastAsia"), name)
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    run.font.color.rgb = color


def add_para(
    doc,
    text="",
    *,
    size=BODY,
    bold=False,
    italic=False,
    color=BLACK,
    align=WD_ALIGN_PARAGRAPH.JUSTIFY,
    space_before=0,
    space_after=4,
    first_line_indent=None,
):
    p = doc.add_paragraph()
    p.alignment = align
    pf = p.paragraph_format
    pf.space_before = Pt(space_before)
    pf.space_after = Pt(space_after)
    pf.line_spacing = 1.0
    pf.line_spacing_rule = WD_LINE_SPACING.SINGLE
    if first_line_indent is not None:
        pf.first_line_indent = Cm(first_line_indent)
    if text:
        run = p.add_run(text)
        set_run_font(run, size=size, bold=bold, italic=italic, color=color)
    return p


def h1(doc, text):
    p = add_para(
        doc,
        text,
        bold=True,
        color=ACCENT,
        align=WD_ALIGN_PARAGRAPH.LEFT,
        space_before=12,
        space_after=6,
    )
    pPr = p._p.get_or_add_pPr()
    pBdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "10")
    bottom.set(qn("w:space"), "2")
    bottom.set(qn("w:color"), "1F4E79")
    pBdr.append(bottom)
    pPr.append(pBdr)
    return p


def h2(doc, text):
    return add_para(
        doc,
        text,
        bold=True,
        color=DARK,
        align=WD_ALIGN_PARAGRAPH.LEFT,
        space_before=8,
        space_after=3,
    )


def body(doc, text, *, indent=True):
    return add_para(doc, text, first_line_indent=0.75 if indent else None)


def bullet(doc, text, *, level=0):
    p = doc.add_paragraph(style="List Bullet")
    pf = p.paragraph_format
    pf.space_before = Pt(0)
    pf.space_after = Pt(2)
    pf.line_spacing = 1.0
    pf.line_spacing_rule = WD_LINE_SPACING.SINGLE
    pf.left_indent = Cm(0.75 + level * 0.35)
    run = p.add_run(text)
    set_run_font(run)
    return p


def set_cell_shading(cell, hex_color):
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:fill"), hex_color)
    shd.set(qn("w:val"), "clear")
    tcPr.append(shd)


def set_cell_borders(cell, color=LINE, sz="4"):
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    tcBorders = OxmlElement("w:tcBorders")
    for edge in ("top", "left", "bottom", "right"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), sz)
        el.set(qn("w:color"), color)
        tcBorders.append(el)
    tcPr.append(tcBorders)


def fill_cell(cell, text, *, bold=False, size=11, color=BLACK, bg=None):
    cell.text = ""
    p = cell.paragraphs[0]
    pf = p.paragraph_format
    pf.space_before = Pt(1)
    pf.space_after = Pt(1)
    pf.line_spacing = 1.0
    run = p.add_run(text)
    set_run_font(run, size=size, bold=bold, color=color)
    if bg:
        set_cell_shading(cell, bg)
    set_cell_borders(cell)


def make_table(doc, headers, rows):
    table = doc.add_table(rows=1 + len(rows), cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, h in enumerate(headers):
        fill_cell(table.rows[0].cells[i], h, bold=True, size=11, color=RGBColor(0xFF, 0xFF, 0xFF), bg="1F4E79")
    for r_i, row in enumerate(rows):
        bg = SOFT_BG if r_i % 2 else None
        for c_i, val in enumerate(row):
            fill_cell(table.rows[r_i + 1].cells[c_i], val, size=11, bg=bg)
    add_para(doc, "", space_after=4, align=WD_ALIGN_PARAGRAPH.LEFT)
    return table


def setup_page(doc):
    section = doc.sections[0]
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.left_margin = Cm(3.0)
    section.right_margin = Cm(2.0)
    section.top_margin = Cm(2.0)
    section.bottom_margin = Cm(2.0)

    header = section.header
    header.is_linked_to_previous = False
    hp = header.paragraphs[0]
    hp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    run = hp.add_run("ECONITH  ·  Báo cáo nghiên cứu khoa học kỹ thuật")
    set_run_font(run, size=11, color=MUTED)

    footer = section.footer
    footer.is_linked_to_previous = False
    fp = footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = fp.add_run("Trang ")
    set_run_font(run, size=11, color=MUTED)
    fld_begin = OxmlElement("w:fldChar")
    fld_begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = " PAGE "
    fld_end = OxmlElement("w:fldChar")
    fld_end.set(qn("w:fldCharType"), "end")
    run2 = fp.add_run()
    run2._r.append(fld_begin)
    run2._r.append(instr)
    run2._r.append(fld_end)
    set_run_font(run2, size=11, color=MUTED)


def build():
    doc = Document()
    styles = doc.styles
    normal = styles["Normal"]
    normal.font.name = FONT
    normal.font.size = Pt(BODY)
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
    setup_page(doc)

    # ----- Cover (compact) -----
    add_para(doc, "ECONITH", bold=True, size=22, color=ACCENT, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=2)
    add_para(
        doc,
        "Economic Intelligence Through Simulation & Quant Research",
        italic=True,
        size=12,
        color=GRAY,
        align=WD_ALIGN_PARAGRAPH.CENTER,
        space_after=8,
    )
    add_para(doc, "BÁO CÁO ĐỀ TÀI KHOA HỌC KỸ THUẬT", bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=4)
    add_para(doc, "Giáo viên hướng dẫn: Nguyễn Thành Đô", align=WD_ALIGN_PARAGRAPH.CENTER, space_after=2)
    add_para(doc, "Người thực hiện: Phạm Nhật Quang", align=WD_ALIGN_PARAGRAPH.CENTER, space_after=10)
    make_table(
        doc,
        ["Hạng mục", "Nội dung"],
        [
            ["Phiên bản", "1.2 (tái cấu trúc KHKT)"],
            ["Phạm vi", "Nghiên cứu · Phương pháp · Kiến trúc · Khung thực nghiệm"],
            ["Chế độ chính", "SIM / DEMO (nghiên cứu & thử nghiệm)"],
            ["Phân loại", "Research Platform — không phải hệ giao dịch tự động"],
        ],
    )

    # =====================================================================
    # 1. TÓM TẮT
    # =====================================================================
    h1(doc, "1. TÓM TẮT ĐỀ TÀI")
    body(
        doc,
        "ECONITH là nền tảng nghiên cứu mô phỏng tài chính–kinh tế, kết hợp mô hình đa tác nhân "
        "(ECONITH World) với AI định lượng (ECONITH Quant) nhằm nghiên cứu ảnh hưởng của các cú sốc "
        "kinh tế vĩ mô (lãi suất, lạm phát, thuế quan) đến phản ứng thị trường tài chính trong môi trường "
        "có kiểm soát, có thể đo lường và tái lập. Hai miền được cô lập REALITY / SIMULATION để dữ liệu "
        "mô phỏng không nhiễm đường quan sát thực tế.",
    )
    h2(doc, "1.1. Mục tiêu")
    body(
        doc,
        "Xây dựng môi trường thí nghiệm thống nhất để đặt giả thuyết về shock vĩ mô, mô phỏng lan truyền "
        "qua hệ đa tác nhân, và đánh giá thay đổi thị trường / rủi ro bằng chỉ số định lượng (baseline, "
        "control, ablation, reproducibility).",
    )

    h2(doc, "1.2. Đóng góp")
    bullet(doc, "Kết hợp mô phỏng kinh tế đa tác nhân và AI Quant trong cùng runtime event-driven.")
    bullet(doc, "Cơ chế Cross Impact (macro ↔ micro) phục vụ thí nghiệm so sánh với baseline/control.")
    bullet(doc, "Quy trình giả thuyết: tạo kịch bản → mô phỏng → ghi nhận (Hypothesis Runner, sealed rollout).")
    bullet(doc, "Giám sát rủi ro độc lập (Sentinel) và protocol đánh giá có tái lập.")

    h2(doc, "1.3. Phạm vi")
    body(
        doc,
        "Đề tài tập trung môi trường thí nghiệm nghiên cứu. Không hướng tới hệ giao dịch tự động kiếm lợi nhuận, "
        "không thay chuyên gia/chính sách, không mô phỏng đầy đủ nền kinh tế thế giới, và không tuyên bố dự đoán "
        "chính xác tương lai thị trường.",
    )

    # =====================================================================
    # 2. TỔNG QUAN
    # =====================================================================
    h1(doc, "2. TỔNG QUAN")
    h2(doc, "2.1. Bối cảnh")
    body(
        doc,
        "Các kỹ thuật giao dịch định lượng, mô phỏng kinh tế và phân tích rủi ro thường được phát triển "
        "trong quỹ đầu tư, tổ chức tài chính lớn và phòng thí nghiệm chuyên sâu. Việc tiếp cận một hệ thống "
        "hoàn chỉnh đòi hỏi kiến thức chuyên môn, dữ liệu và hạ tầng tính toán lớn.",
    )
    h2(doc, "2.2. Vấn đề")
    body(doc, "Các công cụ hiện nay thường tách rời từng khía cạnh:")
    bullet(doc, "Phân tích kinh tế vĩ mô và đánh giá chính sách.")
    bullet(doc, "Mô phỏng đa tác nhân (tương tác giữa các agent).")
    bullet(doc, "AI Quant / backtest trên dữ liệu thị trường lịch sử.")
    bullet(doc, "Quản lý rủi ro tách biệt khỏi vòng kiểm chứng giả thuyết.")
    body(
        doc,
        "Sự phân tách này gây khó khăn khi nghiên cứu câu hỏi dạng: biến động kinh tế lan truyền thế nào "
        "qua hệ thống và ảnh hưởng đến thị trường tài chính ra sao.",
    )
    h2(doc, "2.3. Khoảng trống nghiên cứu")
    body(
        doc,
        "Thiếu môi trường thống nhất kết nối mô phỏng vĩ mô với đánh giá định lượng thị trường, "
        "có kiểm soát, đo lường và tái lập được.",
    )
    h2(doc, "2.4. Lý do xây dựng ECONITH")
    body(
        doc,
        "ECONITH được xây dựng để nối các thành phần trên trong một môi trường nghiên cứu, giúp "
        "nghiên cứu viên, kỹ sư và người học thử nghiệm giả thuyết tài chính–kinh tế qua mô phỏng "
        "có kiểm soát. Đối tượng sử dụng chính không phải giao dịch tự động.",
    )

    # =====================================================================
    # 3. VẤN ĐỀ NGHIÊN CỨU
    # =====================================================================
    h1(doc, "3. VẤN ĐỀ NGHIÊN CỨU")
    h2(doc, "3.1. Phát biểu vấn đề")
    body(
        doc,
        "Trong nghiên cứu tài chính–kinh tế hiện nay, vẫn còn thiếu một môi trường thử nghiệm có khả năng "
        "mô phỏng sự lan truyền của các biến động kinh tế vĩ mô đến thị trường tài chính một cách có kiểm soát, "
        "có thể đo lường và tái lập. Các phương pháp truyền thống thường chỉ tập trung từng khía cạnh riêng lẻ.",
    )

    h2(doc, "3.2. Hạn chế phương pháp hiện có")
    make_table(
        doc,
        ["Phương pháp", "Điểm mạnh", "Hạn chế liên quan đến đề tài"],
        [
            [
                "Phân tích dữ liệu lịch sử (Backtesting)",
                "Sử dụng dữ liệu thị trường thực tế",
                "Khó chủ động tạo kịch bản biến động kinh tế có cấu trúc",
            ],
            [
                "Mô hình kinh tế vĩ mô",
                "Phân tích yếu tố kinh tế và chính sách",
                "Thường chưa kết nối trực tiếp phản ứng thị trường tài chính",
            ],
            [
                "Mô phỏng đa tác nhân",
                "Tương tác giữa nhiều tác nhân",
                "Cần thêm cơ chế đánh giá tác động lên dữ liệu thị trường",
            ],
            [
                "Hệ thống giao dịch định lượng",
                "Tối ưu tín hiệu và chiến lược",
                "Tập trung hiệu quả giao dịch hơn cơ chế lan truyền rủi ro",
            ],
        ],
    )

    h2(doc, "3.3. Khoảng trống nghiên cứu")
    body(doc, "ECONITH hướng tới môi trường nghiên cứu thống nhất nhằm:")
    bullet(doc, "Mô phỏng kịch bản biến động kinh tế và quan sát lan truyền trong hệ thống.")
    bullet(doc, "Đánh giá thay đổi tín hiệu thị trường dưới điều kiện khác nhau.")
    bullet(doc, "So sánh mô hình có và không có tác động từ mô phỏng kinh tế.")
    bullet(doc, "Phân tách rõ dữ liệu mô phỏng và dữ liệu thực để tăng tin cậy thí nghiệm.")

    h2(doc, "3.4. Câu hỏi nghiên cứu (RQ1–RQ4)")
    body(
        doc,
        "RQ1. Việc bổ sung bối cảnh kinh tế vĩ mô từ mô phỏng có tạo ra sự khác biệt có thể đo lường được "
        "trong quá trình đánh giá biến động thị trường so với mô hình chỉ sử dụng dữ liệu lịch sử hay không?",
    )
    body(
        doc,
        "RQ2. Việc hiệu chỉnh các tham số mô phỏng dựa trên dữ liệu kinh tế thực có giúp kết quả mô phỏng "
        "phản ánh tốt hơn các đặc điểm thống kê của dữ liệu quan sát hay không?",
    )
    body(
        doc,
        "RQ3. Khi thay đổi trạng thái kết nối giữa mô phỏng kinh tế và phân tích thị trường, mức độ thay đổi "
        "của các chỉ số rủi ro trong cùng một kịch bản biến động có thể được đo lường như thế nào?",
    )
    body(
        doc,
        "RQ4. Quy trình thực nghiệm của ECONITH có bảo đảm tính tái lập, nghĩa là tạo ra các kết quả nhất quán "
        "khi được thực hiện nhiều lần trong cùng điều kiện dữ liệu, cấu hình và tham số hay không?",
    )

    h2(doc, "3.5. Giả thuyết khoa học (H1–H4)")
    body(
        doc,
        "H1. Việc bổ sung bối cảnh kinh tế vĩ mô từ mô phỏng đa tác nhân sẽ tạo ra sự khác biệt có thể đo lường "
        "được trong đánh giá biến động thị trường so với mô hình chỉ dùng dữ liệu lịch sử (sai số dự báo biến động, "
        "nhận diện xu hướng biến động hoặc chỉ số định lượng khác).",
    )
    body(
        doc,
        "H2. Việc hiệu chỉnh tham số mô phỏng dựa trên dữ liệu kinh tế thực sẽ giảm sai khác giữa dữ liệu mô phỏng "
        "và dữ liệu quan sát (mức biến động, phân phối, hành vi chuỗi thời gian).",
    )
    body(
        doc,
        "H3. Cơ chế kết nối giữa mô phỏng kinh tế và phân tích thị trường sẽ tạo ra sự thay đổi có thể đo lường "
        "trong các chỉ số rủi ro khi cùng một kịch bản biến động, so với trường hợp không dùng thông tin mô phỏng.",
    )
    body(
        doc,
        "H4. Quy trình thực nghiệm của ECONITH sẽ tạo ra kết quả nhất quán và có thể tái lập khi lặp lại với cùng "
        "dữ liệu, cấu hình và tham số.",
    )

    h2(doc, "3.6. Mục tiêu nghiên cứu")
    body(
        doc,
        "Mục tiêu tổng quát: xây dựng nền tảng nghiên cứu mô phỏng tài chính–kinh tế cho phép thiết lập kịch bản "
        "biến động vĩ mô, mô phỏng lan truyền qua hệ đa tác nhân, và đánh giá thay đổi thị trường trong môi trường "
        "có kiểm soát, đo lường và tái lập.",
    )
    body(doc, "Mục tiêu kỹ thuật (thành phần liên quan):", indent=False)
    make_table(
        doc,
        ["#", "Mục tiêu", "Thành phần liên quan"],
        [
            ["T1", "Môi trường mô phỏng kinh tế đa tác nhân", "WorldKernel, Sovereign Graph"],
            ["T2", "Mô hình hóa yếu tố vĩ mô (lãi suất, lạm phát, thương mại…)", "Macro vectors, Trade matrix"],
            ["T3", "Cơ chế kết nối biến động vĩ mô → phản ứng thị trường", "Cross Impact"],
            ["T4", "Pipeline AI Quant thu thập–xử lý–đánh giá phản ứng thị trường", "Feature pipeline, Predictor, AIBridge"],
            ["T5", "Quy trình thử nghiệm giả thuyết có ghi nhận và tái lập", "Hypothesis Runner, Sealed rollouts"],
            ["T6", "Kiểm soát rủi ro và phân tách REALITY / SIMULATION", "Sentinel, EventBus mode gates"],
            ["T7", "Thu thập và quản lý dữ liệu thị trường / macro", "Collectors, raw lake"],
        ],
    )

    # =====================================================================
    # 4. KIẾN TRÚC (gộp 5+6+7+8 cũ)
    # =====================================================================
    h1(doc, "4. KIẾN TRÚC HỆ THỐNG")
    h2(doc, "4.1. Kiến trúc tổng thể")
    body(
        doc,
        "ECONITH được thiết kế theo mô hình phân lớp; mỗi lớp đảm nhiệm thu thập dữ liệu, mô phỏng kinh tế, "
        "phân tích thị trường hoặc đánh giá kết quả. Hệ thống dùng kiến trúc hướng sự kiện (event-driven): "
        "các thành phần giao tiếp qua EventBus thay vì phụ thuộc trực tiếp.",
    )
    make_table(
        doc,
        ["Lớp", "Vai trò", "Module tiêu biểu"],
        [
            ["Data Layer", "Thu thập, lưu trữ, chuẩn hóa market/macro/tradfi", "Collectors"],
            ["World Simulation Layer", "Môi trường đa tác nhân và kịch bản biến động", "WorldKernel, SovereignWorldGraph"],
            ["Macro–Micro Interaction", "Truyền dẫn vĩ mô ↔ phản ứng thị trường", "Cross Impact"],
            ["Quant Intelligence Layer", "Đặc trưng, suy luận, đánh giá phản ứng", "Feature Pipeline, Predictor, AIBridge"],
            ["Risk & Evaluation Layer", "Đánh giá thí nghiệm, kiểm soát rủi ro", "Sentinel, Evaluation, Backtest"],
            ["Dashboard Layer", "Quan sát mô phỏng và kết quả", "World / Quant Dashboard"],
        ],
    )
    body(doc, "Thành phần vận hành cốt lõi:", indent=False)
    bullet(doc, "EventBus: truyền nhận dữ liệu giữa các thành phần.")
    bullet(doc, "TimeEngine và TickPipeline: chu kỳ cập nhật mô phỏng và phân tích.")
    bullet(doc, "REALITY / SIMULATION Mode: phân tách dữ liệu thực và mô phỏng.")
    bullet(doc, "Backend và Dashboard: xử lý logic và quan sát kết quả.")

    h2(doc, "4.2. ECONITH World")
    body(
        doc,
        "ECONITH World là môi trường mô phỏng kinh tế vĩ mô dựa trên Agent-Based Simulation, dùng để nghiên cứu "
        "lan truyền biến động và phản ứng giữa các tác nhân. Hệ thống không nhằm tái tạo chính xác toàn bộ nền "
        "kinh tế thế giới, mà xây môi trường thử nghiệm có kiểm soát.",
    )
    body(doc, "Mỗi quốc gia/hub trong Sovereign Graph có bốn vai trò agent:", indent=False)
    bullet(doc, "GovernmentAgent — chính sách tài khóa, thuế, thuế quan.")
    bullet(doc, "CentralBankAgent — chính sách tiền tệ (ví dụ quy tắc kiểu Taylor), lãi suất, FX.")
    bullet(doc, "EnterpriseAgent — hành vi doanh nghiệp / chuỗi cung ứng.")
    bullet(doc, "PublicAgent — niềm tin tiêu dùng, bất ổn xã hội.")
    body(
        doc,
        "Các hub nối bằng ma trận thương mại và thuế quan; đột biến cấu trúc lan truyền qua tick mô phỏng; "
        "chronology fork lưu lịch sử kịch bản. Mỗi quốc gia mang vector Monetary / Fiscal–Trade / Labor / "
        "Industrial / Geopolitical (lãi suất, CPI, tăng trưởng, thất nghiệp…). Backend mutate live tập trung "
        "hub đã khai báo (USA, CHN, VNM, JPN, IND, DEU,…), ưu tiên độ sâu thí nghiệm.",
    )
    body(doc, "Hypothesis Engine:", indent=False)
    bullet(doc, "Sinh giả thuyết shock (combinatorial mặc định; LLM tùy chọn, không bắt buộc cho thí nghiệm khoa học).")
    bullet(doc, "Debate / thinker hỗ trợ cấu trúc câu hỏi thí nghiệm.")
    bullet(doc, "Chạy scenario trên WorldKernel, đo pre/post macro deltas.")
    bullet(doc, "Ghi sealed JSONL rollout phục vụ phân tích và (sau này) dataset.")

    h2(doc, "4.3. ECONITH Quant")
    body(
        doc,
        "ECONITH Quant không phải trading bot. Đây là hệ thống AI nghiên cứu phản ứng thị trường dưới các điều "
        "kiện khác nhau — gồm điều kiện stress do World tạo trong SIMULATION. Trong khung đề tài, Quant là công cụ "
        "đo lường và quan sát; câu hỏi trung tâm là hiệu lực bối cảnh macro mô phỏng, không phải tối ưu Sharpe trên vốn thật.",
    )
    make_table(
        doc,
        ["Bước", "Mô tả", "Ghi chú kỹ thuật"],
        [
            ["Collect", "Thu thập market / macro / tradfi", "collectors/ — zero-ML, deploy độc lập"],
            ["Feature", "Đồng bộ đa tần số, chống look-ahead", "join_asof backward trong feature_pipeline"],
            ["Label", "Nhãn theo symbol, split thời gian", "label_symbol tránh cross-symbol contamination"],
            ["Train", "Huấn luyện desk (ví dụ PPO) khi có dữ liệu/GPU", "train_ppo; artifact không mặc định sẵn"],
            ["Infer", "Ensemble desk + regime → ai.signal", "Predictor; fallback heuristic nếu thiếu checkpoint"],
            ["Bridge", "Định cỡ / chuyển thành order.intent", "AIBridge + portfolio VaR haircut"],
            ["Risk", "Veto độc lập", "Sentinel (equity từ quant.fills)"],
            ["Evaluate", "Backtest offline, deploy gate", "evaluation/backtest, deploy_gate"],
        ],
    )

    h2(doc, "4.4. Cơ chế World ↔ Quant")
    body(
        doc,
        "Điểm khác biệt phương pháp nằm ở lớp dịch shock hai chiều (Cross Impact): cho phép thí nghiệm "
        "“macro context → market reaction” và phản hồi ngược trong SIMULATION. Coupling không chứng minh dự báo "
        "chắc chắn, mà tạo điều kiện thí nghiệm có cấu trúc để so sánh với baseline.",
    )
    body(doc, "Macro → Micro:", indent=False)
    bullet(doc, "Interest rate / tariff / inflation shock trên World")
    bullet(doc, "→ aggregate geo state → MicrostructuralVolatilityVector (vol multiplier, order-flow shock, liquidity drain, regime pressure)")
    bullet(doc, "→ Predictor điều chỉnh perception feature / regime (khi coupling được phép)")
    bullet(doc, "→ thay đổi tín hiệu AI Quant và metric quan sát")
    body(
        doc,
        "Ví dụ minh họa (stylized): thay đổi lãi suất → thanh khoản / kỳ vọng rủi ro thay đổi → biến động thị trường "
        "tăng → AI Quant điều chỉnh tín hiệu và Sentinel ghi nhận metric rủi ro. Chuỗi này dùng để thiết kế thí nghiệm, "
        "không khẳng định nhân quả tuyệt đối ngoài đời thực.",
    )
    body(doc, "Micro → Macro:", indent=False)
    bullet(doc, "Market snapshot (conviction, vol, liquidation pressure…) → MacroFeedback theo fragility quốc gia")
    bullet(doc, "→ cập nhật World (áp lực FX / unrest / yield) → trạng thái mô phỏng tick tiếp theo")
    body(doc, "Ràng buộc an toàn nghiên cứu:", indent=False)
    bullet(doc, "REALITY: EventBus có thể chặn world.* tới DOMAIN_QUANT; producer air-gap bổ sung.")
    bullet(doc, "SIMULATION: cho phép World tác động Quant phục vụ thí nghiệm.")
    bullet(doc, "Bốn tầng cô lập: consumer gate, producer air-gap, execution air-gap, anomaly gate.")

    h2(doc, "4.5. Luồng hoạt động")
    bullet(doc, "Thu thập dữ liệu thị trường và dữ liệu kinh tế vĩ mô.")
    bullet(doc, "Xây dựng trạng thái mô phỏng và tạo kịch bản biến động.")
    bullet(doc, "Mô phỏng lan truyền biến động qua các tác nhân.")
    bullet(doc, "Quan sát phản ứng thị trường qua Quant.")
    bullet(doc, "Đánh giá theo chỉ số biến động, rủi ro và tái lập; lưu kết quả để so sánh.")
    body(doc, "Thứ tự ưu tiên theo vai trò nghiên cứu: World Simulation → Cross Impact → Quant Intelligence → Risk System → AI Training (hỗ trợ).", indent=False)

    # =====================================================================
    # 5. PHƯƠNG PHÁP
    # =====================================================================
    h1(doc, "5. PHƯƠNG PHÁP NGHIÊN CỨU")
    h2(doc, "5.1. Khung phương pháp")
    body(
        doc,
        "Áp dụng quy trình Research & Development (R&D) kết hợp thiết kế thí nghiệm so sánh có đối chứng. "
        "Biến độc lập tiêu biểu: có/không World coupling, loại shock (rate/tariff/inflation), mức severity, "
        "có/không calibration. Biến phụ thuộc tiêu biểu: sai số dự báo biến động, độ chính xác hướng, metric rủi ro, "
        "moment error, tỷ lệ tái lập.",
    )

    h2(doc, "5.2. Thiết kế thí nghiệm (Baseline / Experimental / Control)")
    make_table(
        doc,
        ["Nhánh", "Cấu hình", "Mục đích"],
        [
            ["B0 — Baseline", "Market data only; World coupling OFF", "Đối chứng tiêu chuẩn"],
            ["E1 — ECONITH", "Market data + simulated macro context (coupling ON)", "Điều kiện thí nghiệm"],
            ["C1 — Control", "Cùng magnitude nhưng shock ngẫu nhiên", "Loại trừ “đoán bừa”"],
        ],
    )
    body(
        doc,
        "Điều kiện kết luận tích cực tối thiểu: E1 cải thiện so với B0 trên metric đã công bố và đồng thời vượt C1 "
        "(random control). Nếu E1 ≤ C1, không kết luận mô phỏng “đúng”; chỉ kết luận cấu trúc shock chưa mang lại "
        "tín hiệu vượt nhiễu trong thiết lập đó.",
    )

    h2(doc, "5.3. Quy trình thực nghiệm")
    bullet(doc, "Bước 1 — Thu thập: market crypto + macro (FRED/…) + gắn nhãn sự kiện lịch sử.")
    bullet(doc, "Bước 2 — Calibration: moment-matching (OU + jump) → stochastic coefficients.")
    bullet(doc, "Bước 3 — Historical replay: inject shock khớp loại sự kiện; đo metric.")
    bullet(doc, "Bước 4 — Scenario testing: rate / inflation / tariff shocks với severity khác nhau.")
    bullet(doc, "Bước 5 — Ablation: World ON vs OFF; báo cáo bảng so sánh.")
    bullet(doc, "Bước 6 — Reproducibility: lặp fingerprint/seed; ghi tỷ lệ tái lập.")

    h2(doc, "5.4. Metrics")
    make_table(
        doc,
        ["Nhóm", "Metric ví dụ"],
        [
            ["World / Calibration", "Moment error (μ, σ, skew, kurtosis); direction accuracy trên case study"],
            ["Quant / Reaction", "MAE/RMSE biến động; directional accuracy của Δvol"],
            ["Risk", "Max drawdown; số lần Sentinel freeze / VaR breach (trong SIM)"],
            ["System", "Reproducibility rate; invariant isolation REALITY"],
        ],
    )

    h2(doc, "5.5. Dataset và nguyên tắc dữ liệu")
    bullet(doc, "Market: order-flow / ticker crypto (collectors.market_coin).")
    bullet(doc, "Macro: chuỗi vĩ mô theo lịch (collectors.macro_global; FRED là nguồn chính khi có khóa).")
    bullet(doc, "TradFi tham chiếu: collectors.tradfi_assets.")
    bullet(doc, "Event library (nghiên cứu): COVID crash, chu kỳ tăng lãi suất, stress ngân hàng, tin thuế quan…")
    bullet(doc, "Raw lake append-only; feature store tách collectors; join_asof backward chống look-ahead; label theo symbol.")

    h2(doc, "5.6. Calibration và reproducibility")
    body(
        doc,
        "Calibration dùng moment-matching OU + jump để đưa tham số stochastic gần dữ liệu quan sát. "
        "Đánh giá khoa học cần đo trên tín hiệu/policy thực của hệ thống; các cổng kỹ thuật nội bộ "
        "(ví dụ gate deploy dùng tín hiệu giáo viên đơn giản) không thay protocol thí nghiệm. "
        "Thí nghiệm khoa học nên đặt HYPOTHESIS_USE_LLM=false để tăng tái lập.",
    )

    h2(doc, "5.7. Thành phần kỹ thuật chính")
    make_table(
        doc,
        ["Thành phần", "Chức năng ngắn"],
        [
            ["WorldKernel", "Máy trạng thái macro/micro theo tick"],
            ["SovereignWorldGraph", "Đồ thị quốc gia + 4 agent/hub"],
            ["Cross Impact", "Dịch shock hai chiều"],
            ["HypothesisRunner", "Vòng thí nghiệm giả thuyết"],
            ["Predictor", "Suy luận ensemble + regime"],
            ["AIBridge", "Tín hiệu → intent dưới ràng buộc rủi ro"],
            ["Sentinel", "Circuit breaker / VaR / equity truth"],
            ["Dashboard", "Quan sát /quant và /world"],
        ],
    )

    # =====================================================================
    # 6. KẾT QUẢ — khung chèn số liệu
    # =====================================================================
    h1(doc, "6. KẾT QUẢ THỰC NGHIỆM VÀ ĐÁNH GIÁ")
    body(
        doc,
        "Số liệu dưới đây lấy từ từng `metrics.json` trong KHKT_Evaluation/experiments/*/results/ "
        "(protocol oos_beta cho RQ1; moment L1 cho RQ2; ablation World ON/OFF cho RQ3; fingerprint lặp lại cho RQ4). "
        "Bảng tổng hợp `results/research_summary.md` chỉ hợp lệ sau khi regenerate từ các file đó. Không bịa số liệu.",
        indent=False,
    )

    h2(doc, "6.1. Kết quả RQ1")
    body(
        doc,
        "So sánh B0 (market only) / E1 (World coupling, oos_beta) / C1 (random control) trên holdout test "
        "(EXP_001, n_test = 8940).",
        indent=False,
    )
    make_table(
        doc,
        ["Model", "MAE", "RMSE", "Direction Accuracy"],
        [
            ["B0 — Baseline", "0.0015783", "0.0021302", "0.3450"],
            ["E1 — ECONITH", "0.0015793", "0.0021313", "0.3455"],
            ["C1 — Random control", "0.0015784", "0.0021303", "0.3772"],
        ],
    )
    body(
        doc,
        "Trên full-panel test: E1 không tốt hơn B0 và không vượt C1 (passes_random_control = False). "
        "Trên cửa sổ sự kiện lịch sử (EXP_006, pha during, 4 sự kiện): E1 thắng B0 ở 2/4 và thắng C1 ở 3/4 cửa sổ — "
        "tín hiệu cục bộ trong điều kiện stress, chưa đủ để kết luận cải thiện phổ quát.",
        indent=False,
    )

    h2(doc, "6.2. Kết quả RQ2")
    body(
        doc,
        "So sánh Moment L1 (|Δmean| + |Δstd| + |Δskew|) giữa mô phỏng default và calibrated với chuỗi macro thực "
        "(EXP_002). Moment L1 thấp hơn là gần dữ liệu quan sát hơn. Cả 4/4 biến được đánh giá đều có Moment L1 "
        "thấp hơn sau hiệu chỉnh.",
        indent=False,
    )
    make_table(
        doc,
        ["Biến", "Moment L1 (default)", "Moment L1 (calibrated)", "Calibrated tốt hơn?"],
        [
            ["interest_rate", "0.8555", "0.2425", "Có"],
            ["yield_10y", "0.7503", "0.0108", "Có"],
            ["gdp_growth", "0.0710", "0.0110", "Có"],
            ["inflation_cpi", "1.3100", "1.1419", "Có"],
        ],
    )
    body(
        doc,
        "Tóm tắt: calibrated_wins_moment_l1 = 4/4. Cùng dt = 1/252 và cùng seed mô phỏng giữa default và calibrated.",
        indent=False,
    )

    h2(doc, "6.3. Kết quả RQ3")
    body(
        doc,
        "Ablation World OFF vs ON dưới các scenario interest rate / inflation / trade (EXP_007). "
        "Tín hiệu vị thế dùng cột chuẩn `indicator_obi` (`sign(indicator_obi)`); metrics ghi "
        "`signal.used_real_obi=true` — chỉ khi đó mới được mô tả là thí nghiệm dùng OBI thực "
        "(không dùng dấu lợi suất trễ làm thay thế). "
        "3/3 scenario cho thấy khác biệt đo được trên portfolio volatility "
        "(đọc Δvol từng scenario trong `EXP_007/.../metrics.json` sau lần chạy có OBI thật). "
        "Đây là bằng chứng ảnh hưởng của coupling, không phải bằng chứng tối ưu lợi nhuận. "
        "Số liệu trong chương kết quả phải khớp từng `metrics.json` đã xác nhận; "
        "không lấy từ `research_summary.md` cũ nếu lệch.",
        indent=False,
    )

    h2(doc, "6.4. Kết quả RQ4")
    body(
        doc,
        "Tỷ lệ tái lập 100% được hiểu theo số lần chạy cố định seed/dataset/cấu hình, không phải tuyên bố tuyệt đối:",
        indent=False,
    )
    bullet(doc, "EXP_005: lặp EXP_001 hai lần (repeats = 2); fingerprint SHA256 trùng nhau → reproducible = true (2/2).")
    bullet(
        doc,
        "EXP_009: lặp pipeline oos_beta năm lần (repeats = 5); unique_fingerprints = 1 → reproducible_rate = 1.0 (5/5); "
        "phương sai MAE(E1) giữa các lần ≈ 0.",
    )
    bullet(
        doc,
        "EXP_009 bổ sung block bootstrap (B = 200) và Diebold–Mariano trên holdout OOS; đọc kèm p-value/CI, "
        "không suy diễn vượt ngưỡng đã chọn.",
    )

    h2(doc, "6.5. Phân tích kết quả")
    body(
        doc,
        "RQ2 cho bằng chứng dương rõ (4/4 Moment L1). RQ3 cho thấy coupling tạo khác biệt đo được. "
        "RQ1 trên full panel chưa vượt B0/C1; trên một số event window có tín hiệu E1 tốt hơn. "
        "RQ4 đạt tái lập 2/2 và 5/5 trong các lần chạy đã thiết kế.",
        indent=False,
    )

    h2(doc, "6.6. Thảo luận")
    body(
        doc,
        "Kết quả âm hoặc hỗn hợp vẫn hợp lệ nếu protocol đúng. Cần phân biệt ảnh hưởng đo được của coupling "
        "(RQ3) với cải thiện dự báo phổ quát (RQ1). Giới hạn: offline proxy, một tài sản chính (BTC), "
        "event window là nhãn lịch sử nghiên cứu.",
        indent=False,
    )

    # =====================================================================
    # 7. TÍNH MỚI
    # =====================================================================
    h1(doc, "7. TÍNH MỚI VÀ ĐÓNG GÓP")
    bullet(doc, "Kiến trúc tích hợp macro simulation và financial AI trong runtime event-driven.")
    bullet(doc, "Tương tác hai chiều macro ↔ micro với vector shock có cấu trúc (Cross Impact).")
    bullet(doc, "Hypothesis-driven simulation kèm sealed log phục vụ tái lập.")
    bullet(doc, "Môi trường thử nghiệm có ablation và isolation mode — đánh giá dưới stress có chủ đích.")
    body(doc, "Không tính là sáng tạo khoa học: chỉ “có AI/dashboard/nhiều module”; tích hợp thư viện không phục vụ RQ; claim “đầu tiên” thiếu bằng chứng.", indent=False)

    # =====================================================================
    # 8. ỨNG DỤNG VÀ GIỚI HẠN
    # =====================================================================
    h1(doc, "8. ỨNG DỤNG VÀ GIỚI HẠN")
    h2(doc, "8.1. Ứng dụng")
    bullet(doc, "Nghiên cứu tài chính–kinh tế: kiểm tra giả thuyết lan truyền shock.")
    bullet(doc, "Giáo dục: minh họa agent-based simulation và pipeline Quant.")
    bullet(doc, "Phân tích rủi ro / stress testing có chủ đích trên kịch bản.")
    bullet(doc, "Hỗ trợ quyết định nghiên cứu (bằng chứng thí nghiệm), không thay chuyên gia.")

    h2(doc, "8.2. Giới hạn và trạng thái kỹ thuật")
    bullet(doc, "Desk AI có thể heuristic nếu chưa có checkpoint hợp lệ.")
    bullet(doc, "World mặc định có thể tắt trên máy yếu; coupling nghiên cứu chủ yếu trong SIMULATION.")
    bullet(doc, "Calibration và historical validation cần đủ thí nghiệm để thành bằng chứng đầy đủ.")
    bullet(doc, "FULLY_AUTONOMOUS (retrain→deploy) là hướng tương lai, chưa hoàn tất.")
    bullet(doc, "Phạm vi hub live hữu hạn — không tương đương mô hình toàn cầu đầy đủ.")
    body(
        doc,
        "Việc nêu giới hạn giúp hội đồng phân biệt phần đã chứng minh bằng kiến trúc/code và phần còn cần số liệu "
        "thực nghiệm. Mọi suy luận từ mô phỏng cần kèm calibration, đối chứng baseline và thừa nhận giới hạn stylized model.",
    )

    # =====================================================================
    # 9. HƯỚNG PHÁT TRIỂN
    # =====================================================================
    h1(doc, "9. HƯỚNG PHÁT TRIỂN")
    bullet(doc, "Ngắn hạn: hoàn thiện calibration; event library + historical replay + random control; ablation World; mở rộng dataset.")
    bullet(doc, "Trung hạn: sensitivity analysis; đánh giá policy Quant trên holdout (không oracle); reproducibility kit.")
    bullet(doc, "Dài hạn: mở rộng quy mô mô phỏng/thị trường; nâng agent và truyền dẫn khi đã có validation nền.")

    # =====================================================================
    # 10. KẾT LUẬN
    # =====================================================================
    h1(doc, "10. KẾT LUẬN")
    body(
        doc,
        "ECONITH đóng góp nền tảng kỹ thuật để nghiên cứu lan truyền shock vĩ mô tới phản ứng thị trường trong "
        "môi trường mô phỏng đa tác nhân có kết nối AI Quant. Giá trị cốt lõi nằm ở protocol thí nghiệm có kiểm soát "
        "(calibration, baseline, ablation, reproducibility), không ở lời hứa dự đoán hay tự động hóa lợi nhuận. "
        "Các bước tiếp theo ưu tiên bằng chứng số liệu (bảng B0/E1/C1, moment error, event replay) hơn mở rộng giao diện.",
    )

    doc.save(OUT)
    print(f"Wrote {OUT}")
    return OUT


if __name__ == "__main__":
    # Fix the accidental English word in 1.1 before save — edit file content properly
    build()
